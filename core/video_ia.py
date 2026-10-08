"""
Geracao AUTOMATICA de video para Shopee Video (imagem do produto -> video 9:16).

Substitui o fluxo manual "copiar prompt -> colar no Google Flow": o proprio
sistema manda a foto do produto + um prompt para o Veo (API do Gemini),
espera ficar pronto, baixa o .mp4 e deixa na fila "Videos prontos".

Como funciona (sem Celery / sem thread):
  1. iniciar_geracao()  -> dispara o job no Google e guarda o nome da operacao no banco.
  2. verificar_geracao() -> chamado a cada consulta de status; se o Google terminou,
                            baixa o arquivo. Sobrevive a reinicio do servidor.

Configuracao (settings.py OU variaveis de ambiente — tudo opcional):
  VEO_MODELO        lite (padrao) | fast | padrao
  VEO_DURACAO       4 | 6 | 8   (padrao 8)
  VEO_RESOLUCAO     720p (padrao) | 1080p (so 8s; custa mais)
  VEO_LIMITE_DIARIO maximo de videos por dia (padrao 10) — trava de custo
  VIDEOS_DIR        pasta dos .mp4 (padrao: <BASE_DIR>/videos_gerados)
"""
import io
import logging
import math
import os
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# chave -> (codigo do modelo na API, US$ por segundo em 720p, com audio)
# Precos conferidos em paginas de precos de terceiros em set/2026 — confirme no
# painel oficial do Google AI Studio antes de depender do numero.
MODELOS = {
    "lite": ("veo-3.1-lite-generate-preview", Decimal("0.05")),
    "fast": ("veo-3.1-fast-generate-preview", Decimal("0.10")),
    "padrao": ("veo-3.1-generate-preview", Decimal("0.40")),
}

LARGURA, ALTURA = 720, 1280          # 9:16
MINUTOS_MAX_GERANDO = 20             # passou disso o job e dado como perdido


class VideoErro(Exception):
    """Erro com mensagem ja amigavel para mostrar ao usuario."""


# ---------------------------------------------------------------- config
def _cfg(nome, padrao=""):
    valor = None
    try:
        from django.conf import settings
        valor = getattr(settings, nome, None)
    except Exception:
        pass
    return valor or os.getenv(nome, padrao)


def escolhas():
    """(chave_modelo, duracao_s, resolucao) ja validados."""
    chave = str(_cfg("VEO_MODELO", "lite")).strip().lower()
    if chave not in MODELOS:
        chave = "lite"
    try:
        duracao = int(_cfg("VEO_DURACAO", "8"))
    except (TypeError, ValueError):
        duracao = 8
    if duracao not in (4, 6, 8):
        duracao = 8
    resolucao = str(_cfg("VEO_RESOLUCAO", "720p")).strip().lower()
    if resolucao not in ("720p", "1080p"):
        resolucao = "720p"
    if resolucao == "1080p":
        duracao = 8                   # exigencia da API
    return chave, duracao, resolucao


def custo_estimado(chave, duracao):
    return (MODELOS[chave][1] * duracao).quantize(Decimal("0.01"))


def limite_diario():
    try:
        return max(0, int(_cfg("VEO_LIMITE_DIARIO", "10")))
    except (TypeError, ValueError):
        return 10


def videos_hoje():
    """Videos cobraveis de hoje (erro/bloqueio nao e cobrado)."""
    from django.utils import timezone
    from .models import VideoGerado
    return (VideoGerado.objects.filter(criado_em__date=timezone.localdate())
            .exclude(status="erro").count())


def pasta_videos():
    from django.conf import settings
    base = getattr(settings, "VIDEOS_DIR", None) or (Path(settings.BASE_DIR) / "videos_gerados")
    pasta = Path(base)
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


# ---------------------------------------------------------------- imagem
def preparar_imagem_vertical(url):
    """Baixa a foto do produto e monta um quadro 720x1280 (9:16):
    produto inteiro e centralizado sobre fundo desfocado da propria foto.
    Esse quadro vira o PRIMEIRO FRAME do video — e por isso o produto sai fiel."""
    from PIL import Image, ImageFilter, ImageOps

    if not url:
        raise VideoErro("Este produto nao tem imagem — nao da para gerar o video.")
    try:
        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        raise VideoErro(f"Nao consegui baixar a imagem do produto ({type(exc).__name__}).")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(resp.content))).convert("RGB")
    except Exception:
        raise VideoErro("A imagem do produto esta corrompida ou em formato invalido.")

    fundo = ImageOps.fit(img, (LARGURA, ALTURA), method=Image.LANCZOS)
    fundo = fundo.filter(ImageFilter.GaussianBlur(40))
    frente = ImageOps.contain(img, (LARGURA - 60, int(ALTURA * 0.62)), method=Image.LANCZOS)
    fundo.paste(frente, ((LARGURA - frente.width) // 2, (ALTURA - frente.height) // 2))

    saida = io.BytesIO()
    fundo.save(saida, format="JPEG", quality=92)
    return saida.getvalue()


# ---------------------------------------------------------------- prompt
GUARDRAILS = (
    "The product must stay exactly identical to the first frame: same shape, colors, "
    "material, proportions and quantity. Do not add, duplicate, replace or transform any item. "
    "No people, no hands, no on-screen text, no logos, no watermarks, no prices. "
    "No speech or dialogue. Soft upbeat instrumental background music with subtle product sounds."
)
ACAO_PADRAO = (
    "Vertical 9:16 product showcase. Starting from the exact first frame, the camera slowly "
    "pushes in and gently orbits around the product. Soft studio lighting with a subtle light "
    "sweep across its surface, shallow depth of field, clean premium look."
)
EXTRA_INTIMO = " Present the product neutrally and informatively, like a catalog photo; non-suggestive."


def limpar_nome(nome):
    nome = re.sub(r"R\$\s?\d+[\.,]?\d*", "", nome or "", flags=re.IGNORECASE)
    nome = re.sub(r"\s?-?\d+\s?%(\s?OFF)?", "", nome, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", nome).strip(" ,-")


def _acao_com_gemini(nome):
    """Pede ao Gemini SO a direcao de camera/luz. As regras de fidelidade ficam
    no codigo (GUARDRAILS) — a IA nao consegue afrouxa-las."""
    try:
        from .views import _chamar_gemini
    except Exception:
        return ""
    pedido = (
        "You are a director of short vertical product ads (9:16, a few seconds).\n"
        f'Product: "{limpar_nome(nome)}".\n'
        "The video is generated from a photo of the product, which is the first frame.\n"
        "Write 2 or 3 sentences in English describing ONLY: slow camera movement "
        "(push-in, gentle orbit, slight tilt), subtle ambient motion (light sweeping the "
        "surface, soft particles, depth of field) and lighting that suit this kind of product.\n"
        "Rules: the product itself must not move, change or transform; no people or hands; "
        "no text; no new objects; never mention price. Reply with the sentences only."
    )
    try:
        texto = (_chamar_gemini(pedido, temperatura=0.7, max_tokens=300) or "").strip().strip('"')
    except Exception:
        return ""
    if not texto or len(texto) > 700 or "R$" in texto:
        return ""
    return texto


def montar_prompt_veo(nome):
    try:
        from .views import eh_moda_intima
        intimo = eh_moda_intima(nome)
    except Exception:
        intimo = False
    acao = _acao_com_gemini(nome) or ACAO_PADRAO
    return f"{acao}{EXTRA_INTIMO if intimo else ''} {GUARDRAILS}"


# ---------------------------------------------------------------- Google
def _cliente():
    chave = _cfg("GEMINI_API_KEY", "")
    if not chave:
        raise VideoErro("GEMINI_API_KEY nao esta configurada.")
    from google import genai
    return genai.Client(api_key=chave)


def traduzir_erro(exc):
    txt = f"{type(exc).__name__} {exc}".lower()
    if "429" in txt or "resource_exhausted" in txt or "quota" in txt:
        return "Limite de uso da API do Google atingido. Tente de novo em alguns minutos."
    if "403" in txt or "permission" in txt or "billing" in txt or "paid" in txt:
        return ("O Veo exige conta PAGA no Google AI Studio (faturamento ativo na chave). "
                "Confira o billing do projeto da sua GEMINI_API_KEY.")
    if "safety" in txt or "blocked" in txt or "filtered" in txt:
        return "O filtro de seguranca do Google bloqueou este produto/imagem."
    return f"Falha ao falar com o Google ({type(exc).__name__}). Tente de novo."


def iniciar_geracao(video):
    """Dispara o job. `video` ja tem nome, imagem_url, legenda, link. Levanta VideoErro."""
    from google.genai import types

    chave, duracao, resolucao = escolhas()
    modelo = MODELOS[chave][0]
    imagem = preparar_imagem_vertical(video.imagem_url)
    video.prompt = montar_prompt_veo(video.nome)
    client = _cliente()
    try:
        op = client.models.generate_videos(
            model=modelo,
            prompt=video.prompt,
            image=types.Image(image_bytes=imagem, mime_type="image/jpeg"),
            config=types.GenerateVideosConfig(
                aspect_ratio="9:16", resolution=resolucao, duration_seconds=duracao),
        )
    except Exception as exc:
        logger.exception("[VEO] falha ao iniciar item %s", video.item_id)
        raise VideoErro(traduzir_erro(exc))
    video.operation_name = op.name
    video.modelo = modelo
    video.duracao = duracao
    video.custo_estimado_usd = custo_estimado(chave, duracao)
    video.status = "gerando"
    video.erro = ""
    video.save()
    return video


def _marcar_erro(video, mensagem):
    video.status = "erro"
    video.erro = mensagem
    video.save()
    return video


def verificar_geracao(video):
    """Pergunta ao Google se terminou; se sim, baixa o .mp4. Idempotente."""
    if video.status != "gerando":
        return video
    from django.utils import timezone
    from google.genai import types

    client = _cliente()
    try:
        op = client.operations.get(types.GenerateVideosOperation(name=video.operation_name))
    except Exception as exc:
        logger.warning("[VEO] consulta falhou (%s) — tentando de novo na proxima", type(exc).__name__)
        if timezone.now() - video.criado_em > timedelta(minutes=MINUTOS_MAX_GERANDO):
            return _marcar_erro(video, "O Google nao respondeu a tempo. Gere novamente.")
        return video

    if not op.done:
        if timezone.now() - video.criado_em > timedelta(minutes=MINUTOS_MAX_GERANDO):
            return _marcar_erro(video, "A geracao demorou demais e foi cancelada. Gere novamente.")
        return video

    if getattr(op, "error", None):
        logger.error("[VEO] operacao terminou com erro: %s", str(op.error)[:300])
        return _marcar_erro(video, "O Google nao conseguiu gerar este video. Tente outro produto ou gere de novo.")

    resposta = getattr(op, "response", None)
    gerados = getattr(resposta, "generated_videos", None) if resposta else None
    if not gerados:
        motivos = getattr(resposta, "rai_media_filtered_reasons", None) or []
        extra = f" ({'; '.join(str(m) for m in motivos)[:200]})" if motivos else ""
        return _marcar_erro(
            video, "O filtro de seguranca do Google bloqueou este video" + extra +
            ". Nao foi cobrado. Tente outro produto.")

    caminho = pasta_videos() / f"{video.pk}_{re.sub(r'[^0-9A-Za-z]', '', video.item_id)[:30]}.mp4"
    try:
        client.files.download(file=gerados[0].video, destination=str(caminho))
    except Exception as exc:
        logger.exception("[VEO] falha ao baixar video %s", video.pk)
        return _marcar_erro(video, f"O video ficou pronto mas o download falhou ({type(exc).__name__}). Gere novamente.")
    if not caminho.exists() or caminho.stat().st_size == 0:
        return _marcar_erro(video, "O download do video veio vazio. Gere novamente.")

    video.arquivo = caminho.name
    video.status = "pronto"
    video.concluido_em = timezone.now()
    video.save()
    return video


# ---------------------------------------------------------------- utilidades puras
def interpretar_range(cabecalho, tamanho):
    """Header HTTP Range -> (inicio, fim) | None (sem range) | 'invalido'.
    Necessario porque o Safari/iPhone so toca <video> se o servidor aceitar Range."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (cabecalho or "").strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    ini, fim = m.groups()
    if ini == "":
        n = int(fim)
        if n == 0:
            return "invalido"
        inicio, final = max(tamanho - n, 0), tamanho - 1
    else:
        inicio = int(ini)
        final = min(int(fim), tamanho - 1) if fim else tamanho - 1
    if inicio > final or inicio >= tamanho:
        return "invalido"
    return inicio, final


def pontuar_candidato(node):
    """Quanto vale gastar um video neste produto? ~ ganho por venda x popularidade x nota.
    `node` = item bruto da API da Shopee. Usado na selecao automatica diaria."""
    try:
        preco = float(node.get("price") or 0)
        taxa = float(node.get("commissionRate") or 0)
        nota = float(node.get("ratingStar") or 0)
    except (TypeError, ValueError):
        return 0.0
    if taxa > 1:                      # veio em percentual (26) em vez de fracao (0.26)
        taxa /= 100
    vendas = node.get("sales") or 0
    ganho_por_venda = preco * taxa
    fator_nota = (nota / 5.0) if nota > 0 else 0.8
    return round(ganho_por_venda * math.log10(vendas + 10) * fator_nota, 4)
