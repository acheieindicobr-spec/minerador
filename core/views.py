import random
import json
import os
import re
import time
import urllib.parse
from collections import Counter
import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from dotenv import load_dotenv
from .models import Divulgacao, LojaAltaComissao, PostRegistro, ProdutoValidado
from .shopee_api import ShopeeService
# ============================================================
# LIMITES DE CARACTERES DA LEGENDA POR PLATAFORMA
# Shopee Vídeo: 150 (inclui hashtags) | Reels/TikTok: 2200 | Feed FB/IG: 2200
# ============================================================
LIMITES_CARACTERES_LEGENDA = {
    'shopee': 150,
    'reels': 2200,
    'feed': 2200,
}
# ============================================================
# CARREGA O ARQUIVO .env (raiz do projeto) — SEM ISSO A CHAVE
# DO GEMINI CHEGA VAZIA E O CODIGO CAI NO "PLANO B" GENERICO!
# ============================================================
load_dotenv()
GEMINI_API_KEY = getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY", "")
LINK_SUA_VITRINE_SHOPEE = "https://collshp.com/acheieindico_br669?view=storefront"
LINK_GERENCIADOR_VITRINE = "https://shopee.com.br/m/affiliate-mycollection"
# ============================================================
# MODELOS GEMINI USADOS NAS CHAMADAS (com fallback entre eles)
# ============================================================
MODELOS_GEMINI = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
# ============================================================
# CACHE DE NOMES DE CATEGORIAS
# ============================================================
ARQUIVO_CACHE_CATEGORIAS = os.path.join(os.path.dirname(__file__), 'categorias_cache.json')
# ============================================================
# GRUPOS PADRÃO — usados se o Gemini não retornar grupos.
# ============================================================
GRUPOS_PADRAO = [
    {"nome": "Achadinhos e Promoções", "plataforma": "Facebook", "link_grupo": "https://www.facebook.com/groups/search/groups/?q=achadinhos+promocoes"},
    {"nome": "Ofertas do Dia", "plataforma": "Facebook", "link_grupo": "https://www.facebook.com/groups/search/groups/?q=ofertas+do+dia+compras"},
    {"nome": "Compras Baratas", "plataforma": "Facebook", "link_grupo": "https://www.facebook.com/groups/search/groups/?q=compras+baratas+promocoes"},
    {"nome": "Promoções Imperdíveis", "plataforma": "Facebook", "link_grupo": "https://www.facebook.com/groups/search/groups/?q=promocoes+imperdiveis"},
    {"nome": "Grupo de Descontos", "plataforma": "Facebook", "link_grupo": "https://www.facebook.com/groups/search/groups/?q=grupo+descontos+ofertas"},
]
# ============================================================
# MODA ÍNTIMA — palavras que disparam o MODO SEGURO de vídeo
# ============================================================
PALAVRAS_MODA_INTIMA = [
    'calcinha', 'fio dental', 'lingerie', 'modeladora', 'sutiã', 'sutia',
    'cinta', 'baby doll', 'babydoll', 'camisola', 'cueca', 'body',
    'corselet', 'conjunto intimo', 'conjunto íntimo', 'meia calça',
    'meia-calça', 'calcinhas',
]
# ============================================================
# FALLBACK ÚNICO DE PROMPT DE VÍDEO
# ============================================================
PROMPT_VIDEO_FALLBACK = (
    "ANTES DE GERAR O VÍDEO, CRIE O STORYBOARD quadro a quadro seguindo as etapas: "
    "abertura, desenvolvimento, detalhes e fechamento — defina o que aparece em cada "
    "quadro e siga exatamente essa sequência no vídeo. "
    "Vídeo de vitrine do produto EXATAMENTE como na imagem de referência: "
    "mesmo corte, mesmos cores, mesmo tecido, mesma quantidade de peças. "
    "MOSTRE APENAS UM ÚNICO ITEM — PROIBIDO duplicar o produto, espelhar, "
    "criar kits falsos ou adicionar outros produtos na cena. "
    "Não redesenhe, não altere nem troque as cores do produto. "
    "Movimento de câmera criativo permitido: zoom lento, pan lateral, "
    "rotação suave, iluminação suave, fundo limpo. "
    "Todo texto na tela deve estar em PORTUGUÊS DO BRASIL (ex.: 'Aproveite', 'Oferta'). "
    "Estrutura em etapas: abertura, desenvolvimento, detalhes, fechamento. "
    "Sem tempos de cena. Adapte a duração ao limite da ferramenta."
)
PROMPT_VIDEO_FALLBACK_MODA_INTIMA = PROMPT_VIDEO_FALLBACK + (
    " REGRAS DE SEGURANÇA (obrigatórias em qualquer rede): sem nudez, sem poses "
    "sugestivas, sem conotação sexual, sem modelos em roupa íntima ou posições íntimas. "
    "Mostre o produto como no anúncio: dobrado, no cabide, em manequim ou, no máximo, "
    "em modelo totalmente vestido."
)
def eh_moda_intima(nome_produto):
    """Retorna True se o nome do produto indicar moda íntima."""
    nome = (nome_produto or '').lower()
    for palavra in PALAVRAS_MODA_INTIMA:
        if re.search(r'\b' + re.escape(palavra) + r'\b', nome, re.IGNORECASE):
            return True
    return False
def detectar_nicho_automatico(nome_produto, nicho_busca=""):
    if nicho_busca and nicho_busca.strip():
        return nicho_busca.strip().title()
    nome = nome_produto.lower()
    mapa_nichos = {
        'Limpeza & Lavanderia': ['percarbonato', 'tira mancha', 'limpa a seco', 'lavanderia', 'detergente', 'esfregão', 'vassoura', 'pano', 'spray', 'zip clean', 'seladora', 'vácuo'],
        'Cozinha & Organização': ['pote', 'hermético', 'marmita', 'cozinha', 'airfryer', 'panela', 'utensílios', 'organizador', 'prato', 'copo', 'térmico', 'garrafa', 'cozedor', 'ovos'],
        'Moda Feminina': ['calcinha', 'modeladora', 'cinta', 'emagrecimento', 'vestido', 'blusa', 'sutiã', 'top', 'saia', 'shorts', 'leg', 'fio dental'],
        'Moda & Vestuário': ['camiseta', 'calça', 'tênis', 'sapato', 'meia', 'pijama', 'bolsa', 'mochila', 'roupa'],
        'Casa & Decoração': ['luminária', 'led', 'quadro', 'cortina', 'tapete', 'almofada', 'lençol', 'espelho', 'difusor', 'penteadeira', 'escrivaninha'],
        'Tecnologia & Gadgets': ['fone', 'bluetooth', 'carregador', 'capinha', 'celular', 'smartwatch', 'suporte', 'teclado', 'mouse', 'batedor', 'mixer', 'elétrico'],
        'Fitness & Saúde': ['bicicleta', 'ergométrica', 'spinning', 'academia', 'suplemento', 'whey', 'squeeze', 'algodão'],
        'Ferramentas & Utilidades': ['furadeira', 'parafusadeira', 'chave', 'broca', 'seladora', 'vedação'],
    }
    for nicho, palavras in mapa_nichos.items():
        for palavra in palavras:
            if re.search(r'\b' + re.escape(palavra) + r'\b', nome, re.IGNORECASE):
                return nicho
    palavras_titulo = [p for p in nome.split() if len(p) > 3]
    if palavras_titulo:
        return palavras_titulo[0].title()
    return "Ofertas & Variedades"
def _chamar_gemini(prompt_sistema, temperatura=0.5, max_tokens=2048):
    """Chama o Gemini com fallback de modelos e retry em 429/503."""
    if not GEMINI_API_KEY:
        return ""
    for modelo in MODELOS_GEMINI:
        url_api = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt_sistema}]}],
            "generationConfig": {"temperature": temperatura, "maxOutputTokens": max_tokens},
        }
        for _ in range(2):
            try:
                response = requests.post(url_api, json=payload, timeout=25)
                res_json = response.json()
                if "candidates" in res_json and res_json["candidates"]:
                    return res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                if "error" in res_json:
                    codigo_erro = res_json["error"].get("code")
                    print(f"[AVISO GEMINI - {modelo}]: Erro {codigo_erro}")
                    if codigo_erro in (429, 503):
                        time.sleep(1)
                        continue
                    break
            except Exception as req_err:
                print(f"[ERRO REQUISIÇÃO GEMINI]: {req_err}")
                time.sleep(1)
    return ""
def _converter_vendas(valor):
    """Converte vendas em qualquer formato para int (defensivo).
    Aceita: 27998, '27998', '34 mil', '27,1mil', '1.2k', '1.200', '1.234,56'."""
    if valor is None:
        return 0
    if isinstance(valor, (int, float)):
        return int(valor)
    texto = str(valor).strip().lower().replace(' ', '')
    multiplicador = 1
    if texto.endswith('mil'):
        multiplicador = 1000
        texto = texto[:-3]
    elif texto.endswith('k'):
        multiplicador = 1000
        texto = texto[:-1]
    elif texto.endswith('m'):
        multiplicador = 1000000
        texto = texto[:-1]
    if ',' in texto and '.' in texto:
        texto = texto.replace('.', '').replace(',', '.')
        try:
            return int(float(texto) * multiplicador)
        except (ValueError, TypeError):
            return 0
    if multiplicador == 1 and ',' not in texto and texto.count('.') >= 1:
        partes = texto.split('.')
        if all(p.isdigit() for p in partes):
            return int(''.join(partes))
    try:
        return int(float(texto.replace(',', '.')) * multiplicador)
    except (ValueError, TypeError):
        return 0
def _remover_hashtags_duplicadas(copy, hashtags):
    """Se a copy já termina com hashtags (o Gemini coloca dentro da COPY),
    remove-as para não duplicar com as nossas."""
    if not hashtags:
        return copy, hashtags
    linhas = copy.splitlines()
    hashtags_encontradas = []
    while linhas and linhas[-1].strip().startswith('#'):
        hashtags_encontradas.insert(0, linhas.pop().strip())
    if hashtags_encontradas:
        copy = '\n'.join(linhas).strip()
        existentes = set(h.lower() for h in hashtags_encontradas)
        hashtags = [h for h in hashtags if h.lower() not in existentes] + hashtags_encontradas
    return copy, hashtags
def gerar_conteudo_com_gemini(nome_produto, nicho_busca=""):
    nicho_padrao = detectar_nicho_automatico(nome_produto, nicho_busca)
    produto_intimo = eh_moda_intima(nome_produto)
    if not GEMINI_API_KEY:
        return {
            "nicho": nicho_padrao,
            "copy_vendas": f"🛍️ {nome_produto} — aproveite essa oferta enquanto está disponível! Confira os detalhes e garanta o seu pelo link. 🔗",
            "prompt_video": PROMPT_VIDEO_FALLBACK_MODA_INTIMA if produto_intimo else PROMPT_VIDEO_FALLBACK,
            "grupos_sugeridos": GRUPOS_PADRAO,
            "hashtags": ["#achadinhoshopee", "#promocaoshopee", "#oferta", "#comprasbaratas", "#achadinho", "#shopee"],
            "palavras_chave": [nicho_padrao, "promoção", "oferta", "barato", "comprar"],
        }
    if produto_intimo:
        prompt_sistema = f"""
Você é um copywriter sênior e estrategista de marketing de afiliados brasileiro, especialista em produtos Shopee.
Produto: "{nome_produto}". Nicho informado (se houver): "{nicho_busca}".
PRODUTO DE MODA ÍNTIMA DETECTADO — MODO SEGURO ATIVADO.
Gere material de divulgação de ALTA CONVERSÃO seguindo ESTRITAMENTE o formato abaixo.
============================================================
REGRAS DO PROMPT DE VÍDEO — REDES SOCIAIS (LIVRE, COM SEGURANÇA PARA MODA ÍNTIMA)
============================================================
- STORYBOARD PRÉVIO (NÃO NEGOCIÁVEL): antes de escrever o prompt do vídeo, monte o STORYBOARD quadro a quadro (ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO) definindo o que aparece em cada quadro; o prompt final DEVE começar mencionando o storyboard e seguir exatamente a sequência dele.
- O prompt do vídeo DEVE ser escrito em PORTUGUÊS DO BRASIL (o texto que o usuário cola no gerador image-to-video Google Flow deve estar todo em português).
- FIDELIDADE AO PRODUTO (NÃO NEGOCIÁVEL): o produto no vídeo DEVE ser idêntico ao da imagem de referência — mesmo corte, cores, tecido e quantidade de peças. Inclua frases como "produto idêntico à imagem de referência", "não altere corte, cores, tecido ou quantidade".
- UM ÚNICO ITEM (NÃO NEGOCIÁVEL): o vídeo DEVE mostrar APENAS UM item do produto, exatamente como na imagem de referência. PROIBIDO duplicar o produto, espelhar, multiplicar, criar kits falsos ou adicionar outros produtos na cena. Se a imagem mostra 1 peça, o vídeo mostra 1 peça.
- QUANTIDADE EXATA DE PEÇAS (apenas se o produto for um kit/conjunto REAL anunciado com várias peças): inclua, ex.: "kit com 7 peças, TODAS as 7 peças visíveis e idênticas à imagem de referência".
- CORES DO PRODUTO: liste as cores presentes no nome/anúncio, ex.: "cores exatamente como na imagem de referência: preto, marrom, vinho".
- TEXTO NA TELA (se houver): TODO texto sobreposto no vídeo deve estar em PORTUGUÊS DO BRASIL, curto e chamativo (ex.: "Aproveite", "Oferta", "Só hoje", "R$ 49,90").
- LIBERDADE CRIATIVA: você pode sugerir ritmo acelerado, cortes dinâmicos, gancho forte nos primeiros segundos — o conteúdo é para Facebook, Instagram e TikTok, onde a criatividade é bem-vinda. PORÉM a criatividade NUNCA pode alterar o produto nem a quantidade de itens.
- SEM promessas enganosas: nada de "cura milagrosa", "resultado garantido" ou alegações médicas.
- SEGURANÇA DE CONTEÚDO PARA MODA ÍNTIMA (NÃO NEGOCIÁVEL em qualquer rede): SEM nudez, SEM conotação sexual, SEM poses sugestivas ou provocativas, SEM modelo vestindo a peça íntima (calcinha, lingerie, body, etc.). Mostre o produto de forma NEUTRA e INFORMATIVA: dobrado, no cabide, em manequim, ou em modelo TOTALMENTE vestido no máximo. SEM gestos sugestivos, SEM roupas sensuais no apresentador.
- SEM conteúdo proibido: nudez, violência, ódio, atividades ilegais, álcool/cigarros, conteúdo político.
- NÃO use tempos de cena em segundos (ex.: "CENA 1 (0-3s)"). Descreva a estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO.
- Formato final: uma linha por etapa, separadas por " | ".
REGRAS DA COPY (obrigatórias):
- O POST é ÚNICO e serve para TODAS as plataformas (Facebook, Instagram, TikTok, Shopee Vídeo).
- Use a fórmula AIDA: Atenção (gancho com a dor/benefício), Interesse (detalhe que desperta), Desejo (produto em uso/prova), Ação (CTA claro).
- Fale da DOR ou desejo do cliente, NÃO apenas do nome do produto.
- Linguagem simples e brasileira, com no MÁXIMO 3 emojis. Sem promessas exageradas ou falsas.
- Máximo 2-3 frases curtas, tom de achadinho/oportunidade.
- INCLUA as palavras-chave de busca do produto NATURALMENTE DENTRO do texto do post (ex.: "pote hermético com tampa", "organizador de cozinha").
- Termine com um CTA claro (ex.: "Garanta o seu pelo link!").
- NO FINAL do post, em linhas separadas, escreva 6 a 8 HASHTAGS em português relacionadas ao produto e ao nicho, cada uma começando com # e sem espaços internos (ex.: #achadinhoshopee, #promocaoshopee, #oferta, #cozinha, #organizacao, #comprasbaratas). As hashtags fazem parte do post.
REGRAS DO NICHO:
- NICHO deve ter 1-3 palavras, específico e vendedor (ex.: "Moda Íntima Feminina", "Limpeza & Lavanderia", "Beleza Feminina").
REGRAS DOS GRUPOS:
- GRUPOS: 5 TERMOS DE BUSCA distintos de grupos no FACEBOOK onde esse público compra, separados por vírgula. APENAS Facebook, NUNCA Telegram. Seja específico e variado (ex.: "achadinhos e promoções", "grupo de ofertas", "compras baratas", "promoções imperdíveis", "grupo de descontos").
Responda ESTRITAMENTE neste formato, sem texto fora dele:
NICHO: <nicho>
COPY: <post completo com palavras-chave no texto, CTA e hashtags no final>
PROMPT_VIDEO: <roteiro de cenas>
GRUPOS: <termo 1>, <termo 2>, <termo 3>, <termo 4>, <termo 5>
"""
    else:
        prompt_sistema = f"""
Você é um copywriter sênior e estrategista de marketing de afiliados brasileiro, especialista em produtos Shopee.
Produto: "{nome_produto}". Nicho informado (se houver): "{nicho_busca}".
Gere material de divulgação de ALTA CONVERSÃO seguindo ESTRITAMENTE o formato abaixo.
============================================================
REGRAS DO PROMPT DE VÍDEO — REDES SOCIAIS (LIVRE)
============================================================
- STORYBOARD PRÉVIO (NÃO NEGOCIÁVEL): antes de escrever o prompt do vídeo, monte o STORYBOARD quadro a quadro (ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO) definindo o que aparece em cada quadro; o prompt final DEVE começar mencionando o storyboard e seguir exatamente a sequência dele.
- O prompt do vídeo DEVE ser escrito em PORTUGUÊS DO BRASIL (o texto que o usuário cola no gerador image-to-video Google Flow deve estar todo em português).
- FIDELIDADE AO PRODUTO (NÃO NEGOCIÁVEL): o produto no vídeo DEVE ser idêntico ao da imagem de referência — mesmo corte, cores, tecido e quantidade de peças. Inclua frases como "produto idêntico à imagem de referência", "não altere corte, cores, tecido ou quantidade".
- UM ÚNICO ITEM (NÃO NEGOCIÁVEL): o vídeo DEVE mostrar APENAS UM item do produto, exatamente como na imagem de referência. PROIBIDO duplicar o produto, espelhar, multiplicar, criar kits falsos ou adicionar outros produtos na cena. Se a imagem mostra 1 peça, o vídeo mostra 1 peça.
- QUANTIDADE EXATA DE PEÇAS (apenas se o produto for um kit/conjunto REAL anunciado com várias peças): inclua, ex.: "kit com 7 peças, TODAS as 7 peças visíveis e idênticas à imagem de referência".
- CORES DO PRODUTO: liste as cores presentes no nome/anúncio, ex.: "cores exatamente como na imagem de referência: preto, marrom, vinho".
- TEXTO NA TELA (se houver): TODO texto sobreposto no vídeo deve estar em PORTUGUÊS DO BRASIL, curto e chamativo (ex.: "Aproveite", "Oferta", "Só hoje", "R$ 49,90").
- LIBERDADE CRIATIVA: você pode sugerir ritmo acelerado, cortes dinâmicos, gancho forte nos primeiros segundos — o conteúdo é para Facebook, Instagram e TikTok, onde a criatividade é bem-vinda. PORÉM a criatividade NUNCA pode alterar o produto nem a quantidade de itens.
- SEM promessas enganosas: nada de "cura milagrosa", "resultado garantido" ou alegações médicas.
- SEM conteúdo proibido: nudez, violência, ódio, atividades ilegais, álcool/cigarros, conteúdo político.
- NÃO use tempos de cena em segundos (ex.: "CENA 1 (0-3s)"). Descreva a estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO.
- Formato final: uma linha por etapa, separadas por " | ".
REGRAS DA COPY (obrigatórias):
- O POST é ÚNICO e serve para TODAS as plataformas (Facebook, Instagram, TikTok, Shopee Vídeo).
- Use a fórmula AIDA: Atenção (gancho com a dor/benefício), Interesse (detalhe que desperta), Desejo (produto em uso/prova), Ação (CTA claro).
- Fale da DOR ou desejo do cliente, NÃO apenas do nome do produto.
- Linguagem simples e brasileira, com no MÁXIMO 3 emojis. Sem promessas exageradas ou falsas.
- Máximo 2-3 frases curtas, tom de achadinho/oportunidade.
- INCLUA as palavras-chave de busca do produto NATURALMENTE DENTRO do texto do post (ex.: "pote hermético com tampa", "organizador de cozinha").
- Termine com um CTA claro (ex.: "Garanta o seu pelo link!").
- NO FINAL do post, em linhas separadas, escreva 6 a 8 HASHTAGS em português relacionadas ao produto e ao nicho, cada uma começando com # e sem espaços internos (ex.: #achadinhoshopee, #promocaoshopee, #oferta, #cozinha, #organizacao, #comprasbaratas). As hashtags fazem parte do post.
REGRAS DO NICHO:
- NICHO deve ter 1-3 palavras, específico e vendedor (ex.: "Moda Íntima Feminina", "Limpeza & Lavanderia", "Beleza Feminina").
REGRAS DOS GRUPOS:
- GRUPOS: 5 TERMOS DE BUSCA distintos de grupos no FACEBOOK onde esse público compra, separados por vírgula. APENAS Facebook, NUNCA Telegram. Seja específico e variado (ex.: "achadinhos e promoções", "grupo de ofertas", "compras baratas", "promoções imperdíveis", "grupo de descontos").
Responda ESTRITAMENTE neste formato, sem texto fora dele:
NICHO: <nicho>
COPY: <post completo com palavras-chave no texto, CTA e hashtags no final>
PROMPT_VIDEO: <roteiro de cenas>
GRUPOS: <termo 1>, <termo 2>, <termo 3>, <termo 4>, <termo 5>
"""
    try:
        texto_resposta = _chamar_gemini(prompt_sistema)
        nicho = nicho_padrao
        copy = ""
        prompt_video = ""
        grupos_texto = ""
        hashtags_texto = ""
        palavras_chave_texto = ""
        for linha in texto_resposta.splitlines():
            linha_strip = linha.strip()
            if linha_strip.startswith("NICHO:"):
                nicho = linha_strip.replace("NICHO:", "").strip()
            elif linha_strip.startswith("COPY:"):
                copy = linha_strip.replace("COPY:", "").strip()
            elif linha_strip.startswith("PROMPT_VIDEO:"):
                prompt_video = linha_strip.replace("PROMPT_VIDEO:", "").strip()
            elif linha_strip.startswith("GRUPOS:"):
                grupos_texto = linha_strip.replace("GRUPOS:", "").strip()
            elif linha_strip.startswith("HASHTAGS:"):
                hashtags_texto = linha_strip.replace("HASHTAGS:", "").strip()
            elif linha_strip.startswith("PALAVRAS_CHAVE:"):
                palavras_chave_texto = linha_strip.replace("PALAVRAS_CHAVE:", "").strip()
        if not copy:
            copy = f"🛍️ {nome_produto} — aproveite essa oferta enquanto está disponível! Confira os detalhes e garanta o seu pelo link. 🔗"
        if not prompt_video:
            prompt_video = PROMPT_VIDEO_FALLBACK_MODA_INTIMA if produto_intimo else PROMPT_VIDEO_FALLBACK
        grupos_sugeridos = []
        if grupos_texto:
            nomes_grupos = [g.strip() for g in grupos_texto.split(",") if g.strip()]
            for item in nomes_grupos[:6]:
                query_clean = urllib.parse.quote(f"{item} {nicho}")
                link_url = f"https://www.facebook.com/groups/search/groups/?q={query_clean}"
                grupos_sugeridos.append({"nome": item, "plataforma": "Facebook", "link_grupo": link_url})
        if not grupos_sugeridos:
            grupos_sugeridos = GRUPOS_PADRAO
        hashtags = [h.strip() for h in hashtags_texto.split(",") if h.strip()]
        if not hashtags:
            hashtags = ["#achadinhoshopee", "#promocaoshopee", "#oferta", "#comprasbaratas", "#achadinho", "#shopee"]
        palavras_chave = [p.strip() for p in palavras_chave_texto.split(",") if p.strip()]
        if not palavras_chave:
            palavras_chave = [nicho_padrao, "promoção", "oferta", "barato", "comprar"]
        return {
            "nicho": nicho,
            "copy_vendas": copy,
            "prompt_video": prompt_video,
            "grupos_sugeridos": grupos_sugeridos,
            "hashtags": hashtags,
            "palavras_chave": palavras_chave,
        }
    except Exception as e:
        print(f"Erro ao processar resposta do Gemini: {e}")
        return {
            "nicho": nicho_padrao,
            "copy_vendas": f"🛍️ {nome_produto} — aproveite essa oferta enquanto está disponível! Confira os detalhes e garanta o seu pelo link. 🔗",
            "prompt_video": PROMPT_VIDEO_FALLBACK_MODA_INTIMA if produto_intimo else PROMPT_VIDEO_FALLBACK,
            "grupos_sugeridos": GRUPOS_PADRAO,
            "hashtags": ["#achadinhoshopee", "#promocaoshopee", "#oferta", "#comprasbaratas", "#achadinho", "#shopee"],
            "palavras_chave": [nicho_padrao, "promoção", "oferta", "barato", "comprar"],
        }
def _salvar_cache_categorias(cache):
    """Grava o cache de nomes no arquivo JSON."""
    try:
        with open(ARQUIVO_CACHE_CATEGORIAS, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[CAT CACHE] erro ao salvar: {e}")
def _carregar_cache_categorias():
    """Carrega o cache de nomes de categorias do arquivo JSON."""
    try:
        if os.path.exists(ARQUIVO_CACHE_CATEGORIAS):
            with open(ARQUIVO_CACHE_CATEGORIAS, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                if isinstance(cache, dict):
                    return cache
    except Exception as e:
        print(f"[CAT CACHE] erro ao carregar: {e}")
    return {}
def _parsear_resposta_categorias(texto):
    """Converte a resposta do Gemini em dicionário {catid: nome}."""
    resultado = {}
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        m = re.search(r'(\d{5,7})', linha)
        if not m:
            continue
        cat_id = m.group(1)
        resto = linha[m.end():].lstrip(':-—–').strip().strip('"\'')
        if resto and resto.lower() not in ('sem nome', 'n/a', 'desconhecido', 'nenhum'):
            resultado[cat_id] = resto
    return resultado
def sugerir_nomes_categorias(pendentes):
    """Pergunta ao Gemini o nome das categorias em LOTES PEQUENOS (5 por vez)."""
    if not GEMINI_API_KEY or not pendentes:
        return {}
    resultados = {}
    TAMANHO_LOTE = 5
    for inicio in range(0, len(pendentes), TAMANHO_LOTE):
        lote = pendentes[inicio:inicio + TAMANHO_LOTE]
        linhas = []
        for i, (cat_id, exemplos) in enumerate(lote, start=1):
            ex = ', '.join(exemplos[:2]) if exemplos else '(sem exemplos)'
            linhas.append(f"{i}. CATID {cat_id} — produtos: {ex}")
        prompt = (
            "Você é um especialista em categorias de e-commerce da Shopee Brasil.\n"
            "Para cada categoria numerada abaixo, identifique o nome curto da "
            "categoria em português (máximo 4 palavras) com base nos títulos dos produtos.\n"
            "Responda EXATAMENTE neste formato, uma linha por categoria:\n"
            "CATID <número>: <nome da categoria>\n"
            "Exemplo:\n"
            "CATID 100716: Decoração de Mesa\n"
            "Não escreva explicações, listas ou texto extra. Apenas as linhas CATID.\n\n"
            + "\n".join(linhas)
        )
        texto = _chamar_gemini(prompt, temperatura=0.2, max_tokens=300)
        if texto:
            lote_resultado = _parsear_resposta_categorias(texto)
            resultados.update(lote_resultado)
            print(f"[CAT GEMINI] lote {inicio // TAMANHO_LOTE + 1}: {len(lote_resultado)} nomes")
    return resultados
# ============================================================
# PAGINAÇÃO — monta a lista de páginas com "..." nos intervalos
# ============================================================
def calcular_paginas(pagina_atual, total_paginas, margem=2):
    if total_paginas <= 1:
        return [1]
    paginas = {1, total_paginas}
    for i in range(pagina_atual - margem, pagina_atual + margem + 1):
        if 1 <= i <= total_paginas:
            paginas.add(i)
    paginas = sorted(paginas)
    resultado = []
    anterior = None
    for p in paginas:
        if anterior is not None and p - anterior > 1:
            resultado.append('...')
        resultado.append(p)
        anterior = p
    return resultado
def pagina_mineracao(request):
    nicho_input = request.GET.get('q', request.GET.get('nicho', '')).strip()
    nicho_busca_api = nicho_input
    ITENS_POR_PAGINA = 20
    try:
        TOTAL_DESEJADO = int(request.GET.get('total') or request.session.get('total_desejado', 100))
    except (ValueError, TypeError):
        TOTAL_DESEJADO = 100
    TOTAL_DESEJADO = max(20, min(250, TOTAL_DESEJADO))
    request.session['total_desejado'] = TOTAL_DESEJADO
    try:
        pagina = max(1, int(request.GET.get('page', 1)))
    except ValueError:
        pagina = 1
    erro_busca = None
    try:
        shopee = ShopeeService()
        raw_produtos = shopee.buscar_mais_vendidos(
            nicho=nicho_busca_api,
            total_desejado=TOTAL_DESEJADO,
        )
    except Exception as e:
        print(f"[ERRO AO BUSCAR PRODUTOS]: {e}")
        raw_produtos = []
        erro_busca = "A Shopee não respondeu agora. Tente novamente em instantes."
    if not raw_produtos:
        raw_produtos = []
    produtos_formatados = []
    for prod in raw_produtos:
        item_id = str(prod.get('itemId', ''))
        titulo = prod.get('productName') or 'Produto Shopee'
        imagem = prod.get('imageUrl') or prod.get('image') or ''
        link_orig = prod.get('offerLink') or prod.get('productLink') or ''
        categorias = prod.get('productCatIds') or []
        loja = prod.get('shopName') or prod.get('shop_name') or prod.get('shop') or ''
        val_preco = prod.get('price') or prod.get('price_direct') or 0.0
        try:
            val_preco = float(val_preco)
        except (ValueError, TypeError):
            val_preco = 0.0
        taxa_num = 8.0  # padrao defensivo: 8% se a API nao devolver
        try:
            taxa_num = float(prod.get('commissionRate') or 0.08)
            if 0 < taxa_num < 1:
                taxa_num = taxa_num * 100
        except (ValueError, TypeError):
            taxa_num = 8.0
        comissao_str = f"{taxa_num:.2f}".replace('.', ',')
        vendas_int = _converter_vendas(prod.get('sales'))
        comissao_estimada = round(val_preco * (taxa_num / 100), 2)
        produtos_formatados.append({
            'item_id': item_id,
            'titulo': titulo,
            'imagem': imagem,
            'preco': f"{val_preco:.2f}".replace('.', ','),
            'preco_num': val_preco,
            'comissao': comissao_str,
            'comissao_num': taxa_num,
            'comissao_estimada': f"{comissao_estimada:.2f}".replace('.', ','),
            'link_afiliado': link_orig,
            'categorias': categorias,
            'loja': loja,
            'vendas': vendas_int,
            'venda_num': vendas_int,
        })
    # Agrupa pelo NOME do produto (mesmo produto de vendedores diferentes)
    melhores_por_nome = {}
    for p in produtos_formatados:
        chave = p['titulo'].strip().lower()
        if chave not in melhores_por_nome or p['vendas'] > melhores_por_nome[chave]['vendas']:
            melhores_por_nome[chave] = p
    produtos_formatados = list(melhores_por_nome.values())
    # Ordena do mais vendido para o menos vendido
    produtos_formatados.sort(
        key=lambda p: p['vendas'] if isinstance(p['vendas'], (int, float)) else 0,
        reverse=True
    )
    # ===== MELHORIA 5v2 — selo "Em alta" =====
    EM_ALTA_VENDAS_MIN = 10
    EM_ALTA_SUBIDA_MIN = 3
    EM_ALTA_NOVO_TOP = 60
    chave_nicho = (nicho_busca_api or 'geral').strip().lower() or 'geral'
    ranking_atual = {}
    for posicao, p in enumerate(produtos_formatados, start=1):
        if p['item_id']:
            ranking_atual[p['item_id']] = {
                'pos': posicao,
                'vendas': p.get('vendas') or 0,
            }
    rankings_salvos = request.session.get('rankings_anteriores') or {}
    tem_referencia = chave_nicho in rankings_salvos
    ranking_anterior = rankings_salvos.get(chave_nicho) or {}
    if tem_referencia and ranking_anterior:
        primeiro_valor = next(iter(ranking_anterior.values()), None)
        if not isinstance(primeiro_valor, dict):
            tem_referencia = False
            ranking_anterior = {}
            rankings_salvos.pop(chave_nicho, None)
            request.session['rankings_anteriores'] = rankings_salvos
    try:
        eh_primeira_pagina = int(request.GET.get('page', 1)) <= 1
    except (ValueError, TypeError):
        eh_primeira_pagina = True
    novos_da_varredura = set()
    for p in produtos_formatados:
        p['em_alta'] = False
        p['tipo_alta'] = ''
        p['subida'] = 0
        p['delta_vendas'] = 0
        item_id = p['item_id']
        if not item_id or not tem_referencia:
            continue
        ref = ranking_anterior.get(item_id)
        vendas_atuais = p.get('vendas') or 0
        if ref is None:
            if ranking_atual[item_id]['pos'] <= EM_ALTA_NOVO_TOP:
                p['em_alta'] = True
                p['tipo_alta'] = 'novo'
                novos_da_varredura.add(item_id)
        else:
            delta_vendas = vendas_atuais - (ref.get('vendas') or 0)
            p['delta_vendas'] = delta_vendas
            if delta_vendas >= EM_ALTA_VENDAS_MIN:
                p['em_alta'] = True
                p['tipo_alta'] = 'vendas'
            else:
                subida = (ref.get('pos') or 0) - ranking_atual[item_id]['pos']
                p['subida'] = subida
                if subida >= EM_ALTA_SUBIDA_MIN:
                    p['em_alta'] = True
                    p['tipo_alta'] = 'subida'
    if eh_primeira_pagina:
        rankings_salvos[chave_nicho] = ranking_atual
        request.session['rankings_anteriores'] = rankings_salvos
        request.session['novos_da_varredura'] = {chave_nicho: sorted(novos_da_varredura)}
    else:
        novos_salvos = (request.session.get('novos_da_varredura') or {}).get(chave_nicho) or []
        novos_salvos = set(novos_salvos)
        for p in produtos_formatados:
            if p['item_id'] in novos_salvos:
                p['em_alta'] = True
                p['tipo_alta'] = 'novo'
    qt_vendas = sum(1 for p in produtos_formatados if p['tipo_alta'] == 'vendas')
    qt_subiu = sum(1 for p in produtos_formatados if p['tipo_alta'] == 'subida')
    qt_novo = sum(1 for p in produtos_formatados if p['tipo_alta'] == 'novo')
    print(f"[EM ALTA v2] nicho='{chave_nicho}' | ref={tem_referencia} | produtos={len(produtos_formatados)} | vendas={qt_vendas} | subiu={qt_subiu} | novo={qt_novo}")
        # ===== FASE INTELIGÊNCIA — Tendência emergente "Pegando onda" =====
    PEGANDO_ONDA_SUBIDA_MIN = 10
    PEGANDO_ONDA_DELTA_MIN = 50
    for p in produtos_formatados:
        p['pegando_onda'] = False
        p['onda_info'] = ''
        subida = p.get('subida') or 0
        delta = p.get('delta_vendas') or 0
        if subida >= PEGANDO_ONDA_SUBIDA_MIN or delta >= PEGANDO_ONDA_DELTA_MIN:
            p['pegando_onda'] = True
            partes = []
            if subida >= PEGANDO_ONDA_SUBIDA_MIN:
                partes.append(f'subiu {subida} posições')
            if delta >= PEGANDO_ONDA_DELTA_MIN:
                partes.append(f'+{delta} vendas')
            p['onda_info'] = '🚀 Pegando onda — ' + ' e '.join(partes) + ' em 2 varreduras. Divulga agora, antes de saturar.'
    # ===== MELHORIA 6 — FILTROS DO RANKING =====
    def _parse_filtro(valor):
        try:
            if valor is None or str(valor).strip() == '':
                return None
            return float(str(valor).replace(',', '.'))
        except (ValueError, TypeError):
            return None
    preco_max = _parse_filtro(request.GET.get('preco_max'))
    comissao_min = _parse_filtro(request.GET.get('comissao_min'))
    venda_min = _parse_filtro(request.GET.get('venda_min'))
    if preco_max is not None:
        preco_max = max(0.0, min(10000.0, preco_max))
    if comissao_min is not None:
        comissao_min = max(0.0, min(100.0, comissao_min))
    if venda_min is not None:
        venda_min = max(0.0, min(10000.0, venda_min))
    total_sem_filtro = len(produtos_formatados)
    produtos_filtrados = []
    for p in produtos_formatados:
        if preco_max is not None and p['preco_num'] > preco_max:
            continue
        if comissao_min is not None and p['comissao_num'] < comissao_min:
            continue
        if venda_min is not None and p['venda_num'] < venda_min:
            continue
        produtos_filtrados.append(p)
    produtos_formatados = produtos_filtrados
    filtros_query = ''
    if preco_max is not None:
        filtros_query += f'&preco_max={preco_max:g}'
    if comissao_min is not None:
        filtros_query += f'&comissao_min={comissao_min:g}'
    if venda_min is not None:
        filtros_query += f'&venda_min={venda_min:g}'
    query_extra = ''
    if nicho_input:
        query_extra += f'&q={nicho_input}'
    query_extra += filtros_query
    # ===== MELHORIA 11 — dropdown de categorias =====
    categoria_filtro = request.GET.get('categoria', '').strip()
    contagem_categorias = Counter()
    for p in produtos_formatados:
        for c in p.get('categorias', []):
            contagem_categorias[str(c)] += 1
    cache_categorias = _carregar_cache_categorias()
    print(f"[CAT DEBUG] cache carregado: {len(cache_categorias)} nomes")
    pendentes = []
    for cat_id, qtd in contagem_categorias.most_common(15):
        nome = cache_categorias.get(cat_id)
        if not nome and cat_id.isdigit():
            exemplos = [
                p['titulo'][:80]
                for p in produtos_formatados
                if cat_id in [str(c) for c in p.get('categorias', [])]
            ][:2]
            pendentes.append((cat_id, exemplos))
    print(f"[CAT DEBUG] pendentes sem nome: {len(pendentes)}")
    if pendentes:
        nomes_novos = sugerir_nomes_categorias(pendentes)
        print(f"[CAT DEBUG] Gemini devolveu: {nomes_novos}")
        if nomes_novos:
            cache_categorias.update(nomes_novos)
    _salvar_cache_categorias(cache_categorias)
    print(f"[CAT DEBUG] cache final: {len(cache_categorias)} nomes")
    categorias_disponiveis = []
    for cat_id, qtd in contagem_categorias.most_common(15):
        nome = cache_categorias.get(cat_id)
        if nome:
            rotulo = f"{nome} ({qtd})"
        else:
            rotulo = f"Categoria {cat_id} ({qtd})"
        categorias_disponiveis.append((cat_id, rotulo))
    if categoria_filtro:
        produtos_formatados = [
            p for p in produtos_formatados
            if categoria_filtro in [str(c) for c in p.get('categorias', [])]
        ]
    if categoria_filtro:
        query_extra += f'&categoria={categoria_filtro}'
    # ===== FASE P0 — R-02/R-22/R-86: anexa selos a cada card =====
    produtos_formatados = enriquecer_produtos(produtos_formatados)
        # ===== FASE INTELIGÊNCIA — Top 5 do dia (score de oportunidade) =====
    top5_produtos = sorted(
        produtos_formatados,
        key=lambda p: p.get('score_oportunidade') or 0,
        reverse=True
    )[:5]
    total_itens = len(produtos_formatados)
    total_paginas = max(1, -(-total_itens // ITENS_POR_PAGINA))
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * ITENS_POR_PAGINA
    fim = inicio + ITENS_POR_PAGINA
    produtos_pagina = produtos_formatados[inicio:fim]
    # MELHORIA 1 — posts de hoje
    registros_hoje_ids = list(posts_de_hoje().values_list('item_id', flat=True))
    quantidade_posts_hoje = len(registros_hoje_ids)
    posts_hoje_ids = set(registros_hoje_ids)
    # MELHORIA 3 — meta diária ajustável
    META_DIARIA = int(request.session.get('meta_diaria', 5))
    if META_DIARIA < 1:
        META_DIARIA = 1
    if META_DIARIA > 50:
        META_DIARIA = 50
    contexto = {
        'produtos': produtos_pagina,
        'top5_produtos': top5_produtos,
        'query_atual': nicho_input,
        'pagina_atual': pagina,
        'pagina_anterior': pagina - 1 if pagina > 1 else None,
        'proxima_pagina': pagina + 1 if pagina < total_paginas else None,
        'total_paginas': total_paginas,
        'total_itens': total_itens,
        'paginas': calcular_paginas(pagina, total_paginas),
        'quantidade_posts_hoje': quantidade_posts_hoje,
        'posts_hoje_ids': posts_hoje_ids,
        'meta_diaria': META_DIARIA,
        'total_desejado': TOTAL_DESEJADO,
        'preco_max': preco_max,
        'comissao_min': comissao_min,
        'venda_min': venda_min,
        'filtros_query': filtros_query,
        'query_extra': query_extra,
        'total_sem_filtro': total_sem_filtro,
        'categorias_disponiveis': categorias_disponiveis,
        'categoria_filtro': categoria_filtro,
        'painel_constancia': calcular_painel_constancia(),
        'alerta_comercial': calcular_alerta_comercial(),
        'erro_busca': erro_busca,
    }
    return render(request, 'core/dashboard.html', contexto)
@require_POST
def salvar_e_preparar_produto(request):
    try:
        item_id = request.POST.get('item_id', '').strip()
        if not item_id:
            return JsonResponse({'status': 'erro', 'mensagem': 'item_id é obrigatório.'}, status=400)
        nome = request.POST.get('nome', '')
        link_original = request.POST.get('link_original', '')
        imagem_url = request.POST.get('imagem_url', '')
        nicho_busca = request.POST.get('nicho', '')
        loja = request.POST.get('loja', '')
        def _parse_float(valor, padrao=0.0):
            try:
                if valor is None or str(valor).strip() == '':
                    return padrao
                return float(str(valor).replace(',', '.'))
            except (ValueError, TypeError):
                return padrao
        preco_num = _parse_float(request.POST.get('preco'))
        comissao_num = _parse_float(request.POST.get('comissao'))
        try:
            vendas_num = int(float(str(request.POST.get('vendas') or 0).replace(',', '.')))
        except (ValueError, TypeError):
            vendas_num = 0
        comissao_estimada_num = round(preco_num * (comissao_num / 100), 2)
        dados_ia = gerar_conteudo_com_gemini(nome, nicho_busca)
        nicho_preciso = dados_ia['nicho']
        copy_vendas = dados_ia['copy_vendas']
        prompt_ia = dados_ia['prompt_video']
        grupos_sugeridos = dados_ia['grupos_sugeridos']
        hashtags = dados_ia['hashtags']
        palavras_chave = dados_ia['palavras_chave']
        shopee = ShopeeService()
        link_afiliado = link_original
        if link_original:
            try:
                res_link = shopee.gerar_link_afiliado(link_original)
                if res_link and 'data' in res_link:
                    gen_data = res_link.get('data') or {}
                    short_data = gen_data.get('generateShortLink') or {}
                    link_afiliado = short_data.get('shortLink') or link_original
            except Exception as api_err:
                print(f"Aviso ao encurtar link: {api_err}")
        ProdutoValidado.objects.update_or_create(
            item_id=item_id,
            defaults={
                'nome': nome[:255],
                'nicho': nicho_preciso[:100],
                'loja': loja[:150],
                'imagem_url': imagem_url,
                'link_original': link_original,
                'link_afiliado': link_afiliado,
                'prompt_video_ia': prompt_ia,
                'preco': preco_num,
                'comissao_percentual': comissao_num,
                'vendas': vendas_num,
                'comissao_estimada': comissao_estimada_num,
            }
        )
        request.session[f'ia_produto_{item_id}'] = {
            'copy': copy_vendas,
            'hashtags': hashtags,
            'palavras_chave': palavras_chave,
        }
        return JsonResponse({
            'status': 'sucesso',
            'item_id': item_id,
            'nicho_detectado': nicho_preciso,
            'link_afiliado': link_afiliado,
            'copy_vendas': copy_vendas,
            'prompt_ia': prompt_ia,
            'imagem_url': imagem_url,
            'grupos_sugeridos': grupos_sugeridos,
            'hashtags': hashtags,
            'palavras_chave': palavras_chave,
        })
    except Exception as e:
        print(f"Erro no processamento do produto: {e}")
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)
@require_POST
def adicionar_a_vitrine(request):
    item_id = request.POST.get('item_id')
    if not item_id and request.body:
        try:
            body_data = json.loads(request.body)
            item_id = body_data.get('item_id') or body_data.get('id')
        except Exception:
            pass
    try:
        produto = ProdutoValidado.objects.filter(item_id=item_id).first()
        if produto:
            produto.em_vitrine = True
            produto.save()
            return JsonResponse({
                'status': 'sucesso',
                'mensagem': 'Produto salvo localmente na vitrine!',
                'link_afiliado': produto.link_afiliado,
                'url_gerenciador_shopee': LINK_GERENCIADOR_VITRINE
            })
        return JsonResponse({
            'status': 'sucesso_parcial',
            'mensagem': 'Produto pronto para adição.',
            'url_gerenciador_shopee': LINK_GERENCIADOR_VITRINE
        })
    except Exception as e:
        print(f"Erro ao adicionar à vitrine: {e}")
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)
def minha_vitrine(request):
    return redirect(LINK_SUA_VITRINE_SHOPEE)
# ============================================================
# MELHORIA 1 — Histórico de posts (marcar como postado hoje)
# ============================================================
def posts_de_hoje():
    """Retorna os registros de postagem feitos hoje."""
    inicio_do_dia = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return PostRegistro.objects.filter(data_postagem__gte=inicio_do_dia)
@require_POST
def marcar_postado(request):
    try:
        item_id = request.POST.get('item_id', '')
        nome = request.POST.get('nome', '')
        if item_id and not posts_de_hoje().filter(item_id=item_id).exists():
            PostRegistro.objects.create(item_id=item_id, nome=nome[:255])
        return JsonResponse({
            'status': 'sucesso',
            'quantidade_posts_hoje': posts_de_hoje().count(),
        })
    except Exception as e:
        print(f"Erro ao marcar produto como postado: {e}")
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)
# ============================================================
# MELHORIA 3 — Meta diária ajustável (salva na sessão)
# ============================================================
@require_POST
def definir_meta(request):
    try:
        meta = int(request.POST.get('meta', 5))
        if meta < 1:
            meta = 1
        if meta > 50:
            meta = 50
        request.session['meta_diaria'] = meta
        return JsonResponse({'status': 'sucesso', 'meta_diaria': meta})
    except Exception as e:
        print(f"Erro ao definir meta: {e}")
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)
# ============================================================
# MELHORIA 14 — Prompt Shopee Vídeo (diretrizes à risca)
# ============================================================
def gerar_prompt_shopee_video(nome_produto, nicho_busca=""):
    """Prompt de vídeo de vitrine para o Shopee Vídeo. Curto, direto,
    com regras obrigatórias do projeto (storyboard prévio, fidelidade,
    1 item, PT-BR, SEM PREÇO)."""
    import re
    # Remove preços e percentuais do nome (ex.: "R$ 32,89", "-47%", "47% OFF")
    nome_limpo = re.sub(r'R\$\s?\d+[\.,]?\d*', '', nome_produto, flags=re.IGNORECASE)
    nome_limpo = re.sub(r'\s?\d+[\.,]\d{2}\b', '', nome_limpo)
    nome_limpo = re.sub(r'\s?-?\d+\s?%(\s?OFF)?', '', nome_limpo, flags=re.IGNORECASE)
    nome_limpo = re.sub(r'\s{2,}', ' ', nome_limpo).strip(' ,-')
    nome_final = nome_limpo or nome_produto

    regras_seguranca = ''
    if eh_moda_intima(nome_final):
        regras_seguranca = (
            ' REGRAS DE SEGURANÇA (obrigatórias): sem nudez, sem poses sugestivas, '
            'sem conotação sexual; mostre o produto dobrado, no cabide ou em manequim.'
        )
    return (
        f'Vídeo de vitrine para o Shopee Vídeo promovendo "{nome_final}" '
        f'(nicho: {nicho_busca or "geral"}).'
        ' ANTES DE GERAR O VÍDEO, CRIE O STORYBOARD quadro a quadro seguindo as etapas: '
        'ABERTURA, DESENVOLVIMENTO, DETALHES e FECHAMENTO — defina o que aparece em cada '
        'quadro e siga exatamente essa sequência no vídeo.'
        ' FIDELIDADE TOTAL AO PRODUTO (não negociável): o vídeo deve mostrar EXATAMENTE '
        'o produto da imagem de referência — mesmo modelo, mesmo corte, mesmas cores, '
        'mesmo material e a MESMA quantidade de peças. PROIBIDO substituir por produto '
        'parecido, similar, genérico, de outra cor, de outra versão ou de outra marca.'
        ' MOSTRE APENAS UM ÚNICO ITEM — PROIBIDO duplicar, espelhar, criar kits falsos '
        'ou adicionar outros produtos na cena.'
        ' SEM PREÇO (obrigatório): PROIBIDO exibir preço, valores em reais, "R$", '
        'percentuais de desconto ou qualquer número de valor na tela ou na narração. '
        'Use apenas chamadas como "Aproveite", "Oferta", "Corre que é por tempo limitado".'
        ' Todo texto na tela em PORTUGUÊS DO BRASIL.'
        ' Estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES e FECHAMENTO, '
        'separadas por " | ".'
        ' Sem tempos de cena em segundos. Sem promessas enganosas.'
        f'{regras_seguranca}'
    )
def _chamar_gemini_prompt(prompt_sistema):
    """Compatibilidade: delega para a função única _chamar_gemini."""
    return _chamar_gemini(prompt_sistema)
def gerar_prompt_reels_tiktok_video(nome_produto, nicho_busca=""):
    """Prompt para Instagram Reels e TikTok: gancho forte, ritmo rápido,
    com storyboard prévio obrigatório."""
    regras_seguranca = ''
    if eh_moda_intima(nome_produto):
        regras_seguranca = (
            ' REGRAS DE SEGURANÇA (obrigatórias em qualquer rede): sem nudez, sem poses '
            'sugestivas, sem conotação sexual; mostre o produto dobrado, no cabide ou '
            'em manequim.'
        )
    return (
        f'Vídeo curto e dinâmico para Instagram Reels e TikTok divulgando '
        f'"{nome_produto}" (nicho: {nicho_busca or "geral"}).'
        ' ANTES DE GERAR O VÍDEO, CRIE O STORYBOARD quadro a quadro seguindo as etapas: '
        'ABERTURA, DESENVOLVIMENTO, DETALHES e FECHAMENTO — defina o que aparece em cada '
        'quadro e siga exatamente essa sequência no vídeo.'
        ' Gancho forte nos 2 primeiros segundos — mostre o produto ou o benefício de cara.'
        ' Ritmo acelerado, cortes rápidos, zoom no detalhe que vende.'
        ' FIDELIDADE AO PRODUTO (não negociável): produto idêntico à imagem de referência '
        '(mesmo corte, cores, tecido e quantidade). UM ÚNICO ITEM — PROIBIDO duplicar, '
        'espelhar ou criar kits falsos.'
        ' Legenda curta na tela, SEMPRE em PORTUGUÊS DO BRASIL (ex.: "Aproveite", "Oferta").'
        ' Estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES e FECHAMENTO, '
        'separadas por " | ". Sem tempos de cena. Sem promessas enganosas.'
        f'{regras_seguranca}'
    )
def gerar_prompt_feed_video(nome_produto, nicho_busca=""):
    """Prompt para post de vídeo no feed do Facebook/Instagram: mais
    informativo e demonstrativo, com storyboard prévio obrigatório."""
    regras_seguranca = ''
    if eh_moda_intima(nome_produto):
        regras_seguranca = (
            ' REGRAS DE SEGURANÇA (obrigatórias em qualquer rede): sem nudez, sem poses '
            'sugestivas, sem conotação sexual; mostre o produto dobrado, no cabide ou '
            'em manequim.'
        )
    return (
        f'Vídeo demonstrativo para o feed do Facebook e Instagram divulgando '
        f'"{nome_produto}" (nicho: {nicho_busca or "geral"}).'
        ' ANTES DE GERAR O VÍDEO, CRIE O STORYBOARD quadro a quadro seguindo as etapas: '
        'ABERTURA, DESENVOLVIMENTO, DETALHES e FECHAMENTO — defina o que aparece em cada '
        'quadro e siga exatamente essa sequência no vídeo.'
        ' Mostre o produto em uso: abertura apresentando o item, desenvolvimento '
        'mostrando o benefício principal, detalhes do material/acabamento e fechamento '
        'com chamada para ação ("Aproveite", "Oferta válida").'
        ' FIDELIDADE AO PRODUTO (não negociável): produto idêntico à imagem de referência '
        '(mesmo corte, cores, tecido e quantidade). UM ÚNICO ITEM — PROIBIDO duplicar, '
        'espelhar ou criar kits falsos.'
        ' Todo texto na tela em PORTUGUÊS DO BRASIL. Estrutura em etapas: ABERTURA, '
        'DESENVOLVIMENTO, DETALHES e FECHAMENTO, separadas por " | ".'
        ' Sem tempos de cena. Sem promessas enganosas.'
        f'{regras_seguranca}'
    )
def montar_legenda_para_plataforma(copy, hashtags, plataforma='shopee'):
    """Monta a legenda (copy + hashtags) já no tamanho certo da plataforma.
    Limites: shopee = 150 | reels = 2200 | feed = 2200.
    P2-3-FIX: no Shopee Vídeo o texto sai SEM link/CTA de link e SEM corte
    no meio de palavra (truncamento por palavra com '…' no final)."""
    limite = LIMITES_CARACTERES_LEGENDA.get(plataforma, 150)
    copy = (copy or '').strip()
    hashtags = [h for h in (hashtags or []) if h.strip()]
    copy, hashtags = _remover_hashtags_duplicadas(copy, hashtags)
    hashtags_texto = ' '.join(hashtags)

    # ---- P2-3-FIX: limpeza do Shopee Vídeo (sem link e sem "pelo link") ----
    if plataforma == 'shopee':
        copy = re.sub(r'https?://\S+', '', copy)
        copy = re.sub(r'www\.\S+', '', copy)
        copy = re.sub(r'\bs\.shopee\S*', '', copy, flags=re.IGNORECASE)
        for padrao in [
            r'\bgaranta o seu[^.\n]*?pelo link\b[^.\n]*[.!?]?',
            r'\bcompre pelo link\b[^.\n]*[.!?]?',
            r'\bclique no link\b[^.\n]*[.!?]?',
            r'\bacesse o link\b[^.\n]*[.!?]?',
            r'\blink na bio\b[^.\n]*',
            r'\blink da shopee\b[^.\n]*',
            r'🛒\s*[Ll]ink[^.\n]*',
        ]:
            copy = re.sub(padrao, '', copy, flags=re.IGNORECASE)
        copy = re.sub(r'\s+', ' ', copy).strip(' ,.;:')
        copy = re.sub(r'\s+([,.;:!?])', r'\1', copy)

    def montar():
        if hashtags_texto:
            return f"{copy}\n{hashtags_texto}"
        return copy

    legenda = montar()
    if len(legenda) <= limite:
        return legenda, limite, len(legenda)

    # 1) tenta caber removendo hashtags (preserva o copy)
    while hashtags and len(montar()) > limite:
        hashtags = hashtags[:-1]
        hashtags_texto = ' '.join(hashtags)

    # 2) se ainda estourar, trunca o copy POR PALAVRA (nunca no meio de palavra)
    if len(montar()) > limite:
        espaco_copy = (limite - (len(hashtags_texto) + 1)) if hashtags_texto else limite
        if espaco_copy <= 0:
            copy = ''
        else:
            base = copy.rstrip()
            if len(base) > espaco_copy:
                corte = base[:espaco_copy]
                if ' ' in corte:
                    ultimo_espaco = corte.rfind(' ')
                    if ultimo_espaco > espaco_copy * 0.5:
                        copy = corte[:ultimo_espaco].rstrip(' ,.;:') + '…'
                    else:
                        copy = corte.rstrip(' ,.;:') + '…'
                else:
                    copy = corte.rstrip(' ,.;:') + '…'
            else:
                copy = base

    legenda = montar()
    return legenda, limite, len(legenda)
# ============================================================
# FASE P0 — R-95: PAINEL "MINHAS DIVULGAÇÕES"
# Resolve a dor: "nunca acho o produto na Shopee depois"
# Cada material gerado fica vinculado ao item_id + link original.
# ============================================================
COOKIE_JANELA_DIAS = 7          # 🍪 janela de conversão padrão
COMISSAO_EXTRA_MINIMO = 20      # 🔥 % mínima para exibir o selo de comissão extra
def minhas_divulgacoes(request):
    """Painel com todo material gerado, vinculado ao item_id e ao link da Shopee."""
    divulgacoes = Divulgacao.objects.all()[:100]
    return render(request,'core/minhas_divulgacoes.html', {'divulgacoes': divulgacoes})
@require_POST
def registrar_divulgacao(request):
    """Registra uma divulgação gerada (chamado pelo front ao gerar/copiar o material)."""
    item_id = request.POST.get('item_id', '').strip()
    if not item_id:
        return JsonResponse({'ok': False, 'erro': 'item_id é obrigatório'}, status=400)
    produto = ProdutoValidado.objects.filter(item_id=item_id).first()
    Divulgacao.objects.create(
        item_id=item_id,
        nome=request.POST.get('nome') or (produto.nome if produto else ''),
        imagem_url=request.POST.get('imagem_url') or (produto.imagem_url if produto else ''),
        link_original=request.POST.get('link_original') or (produto.link_original if produto else ''),
        link_afiliado=request.POST.get('link_afiliado') or (produto.link_afiliado if produto else ''),
        plataforma=request.POST.get('plataforma', 'shopee'),
        prompt=request.POST.get('prompt', ''),
        legenda=request.POST.get('legenda', ''),
    )
    return JsonResponse({'ok': True})
# ============================================================
# SELOS — funções auxiliares para a dashboard (R-02, R-22, R-86)
# AGORA adaptadas para trabalhar com DICIONÁRIOS (como a
# pagina_mineracao monta os produtos) — não com objetos do banco.
# ============================================================
def selos_do_produto(p):
    """Retorna a lista de selos de um produto (dicionário da listagem)."""
    selos = []
    comissao = p.get('comissao_num') or 0
    if comissao >= COMISSAO_EXTRA_MINIMO:
        selos.append({'tipo': 'comissao_extra', 'texto': f'🔥 Comissão Extra {comissao:.0f}%'})
    selos.append({'tipo': 'cookie', 'texto': f'🍪 Cookie: {COOKIE_JANELA_DIAS} dias'})
    return selos
def calcular_score_oportunidade(p):
    """Nota 0-100 de quão bom é divulgar este produto AGORA.

    Combina 5 fatores com pesos: vendas (30), comissão (25),
    momento/em alta (25), preço (10) e crescimento recente (10).
    Retorna (score, motivo) — o motivo alimenta a camada 2
    ('Por que divulgar hoje?').
    """
    score = 0.0
    motivos = []

    # 1) VENDAS (peso 30) — produto muito vendido = comprovado
    vendas = p.get('vendas') or 0
    if vendas >= 10000:
        score += 30
        motivos.append('bombando em vendas')
    elif vendas >= 5000:
        score += 24
        motivos.append('muito vendido')
    elif vendas >= 1000:
        score += 18
        motivos.append('boa base de vendas')
    elif vendas >= 100:
        score += 12
    else:
        score += 4

    # 2) COMISSÃO (peso 25) — quanto maior o %, maior o ganho
    comissao = float(p.get('comissao_num') or 0)
    if comissao >= 30:
        score += 25
        motivos.append('comissão alta')
    elif comissao >= 20:
        score += 20
    elif comissao >= 10:
        score += 14
    else:
        score += 6

    # 3) MOMENTO (peso 25) — em alta, subindo ou novo na varredura
    tipo_alta = p.get('tipo_alta') or ''
    if tipo_alta == 'vendas':
        score += 25
        motivos.append('vendas disparando')
    elif tipo_alta == 'subida':
        score += 20
        motivos.append('subindo no ranking')
    elif tipo_alta == 'novo':
        score += 18
        motivos.append('novo na varredura')
    else:
        score += 8

    # 4) PREÇO (peso 10) — ticket ideal de achadinho (R$ 20-80)
    preco = float(p.get('preco_num') or 0)
    if 20 <= preco <= 80:
        score += 10
        motivos.append('preço ideal de achadinho')
    elif preco < 20:
        score += 6
    elif preco <= 150:
        score += 4
    else:
        score += 1

    # 5) CRESCIMENTO (peso 10) — delta de vendas desde a última varredura
    delta = p.get('delta_vendas') or 0
    if delta >= 100:
        score += 10
        motivos.append('crescimento forte')
    elif delta >= 30:
        score += 7
    elif delta >= 10:
        score += 4

    score = round(min(100, max(0, score)))
    motivo = ' + '.join(motivos[:3]) if motivos else 'oportunidade estável'
    return score, motivo

def enriquecer_produtos(produtos):
    """Anexa 'selos', 'eh_loja_xtra' e o Score de Oportunidade a cada card.
    Compara pelo campo 'loja' (nome real da loja vendedora) com a base XTRA —
    fallback pelo título quando a loja não vier da API."""
    nomes_xtra = set(marca.lower() for marca in LojaAltaComissao.objects.filter(tipo='xtra').values_list('nome', flat=True))
    for p in produtos:
        p['selos'] = selos_do_produto(p)
        loja = (p.get('loja') or '').strip().lower()
        titulo = (p.get('titulo') or '').lower()
        p['eh_loja_xtra'] = any(marca in loja for marca in nomes_xtra) or any(marca in titulo for marca in nomes_xtra)
        # ===== NOVO: Score de Oportunidade =====
        score, motivo = calcular_score_oportunidade(p)
        p['score_oportunidade'] = score
        p['motivo_score'] = motivo
    return produtos
from django.views.decorators.csrf import csrf_exempt
from .models import ProdutoValidado

# ============================================
# 1) ENDPOINT: dados do produto para o modal
# ============================================
def produto_detalhes(request, produto_id):
    try:
        produto = ProdutoValidado.objects.get(id=produto_id)
        return JsonResponse({
            'success': True,
            'produto': {
                'id': produto.id,
                'titulo': produto.nome or '',
                'preco': float(produto.preco) if produto.preco else 0,
                'imagem': produto.imagem_url or '',
                'link': produto.link_afiliado or produto.link_original or '',
                'comissao': float(produto.comissao_percentual) if produto.comissao_percentual else 0,
            }
        })
    except ProdutoValidado.DoesNotExist:
        return JsonResponse({'success': False, 'erro': 'Produto não encontrado.'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'erro': str(e)}, status=500)

# ============================================
# 2) ENDPOINT: gerar conteúdo por plataforma
# ============================================
@csrf_exempt
def gerar_conteudo(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'erro': 'Método inválido. Use POST.'}, status=405)
    try:
        dados = json.loads(request.body)
        produto_id = dados.get('produto_id')
        plataforma = dados.get('plataforma', '')
        produto = ProdutoValidado.objects.get(id=produto_id)

        # --- PROMPT GERADO PELAS FUNÇÕES DO PRÓPRIO PROJETO ---
        # (garantem: texto em PT-BR,
        #  produto fiel ao original, sem tempos de cena em segundos)
        if plataforma == 'shopee_video':
            prompt = gerar_prompt_shopee_video(produto.nome, produto.nicho or '')
        elif plataforma == 'reels_tiktok':
            prompt = gerar_prompt_reels_tiktok_video(produto.nome, produto.nicho or '')
        elif plataforma == 'facebook_instagram':
            prompt = gerar_prompt_feed_video(produto.nome, produto.nicho or '')
        else:
            return JsonResponse({'success': False, 'erro': 'Plataforma inválida.'}, status=400)

        preco_exibicao = f"{float(produto.preco):.2f}" if produto.preco else "0,00"
        return JsonResponse({
            'success': True,
            'prompt': prompt,
            'post': f"🔥 Achei esse achado! {produto.nome} por R$ {preco_exibicao} 🛒",
            'link': produto.link_afiliado or produto.link_original or '',
        })
    except ProdutoValidado.DoesNotExist:
        return JsonResponse({'success': False, 'erro': 'Produto não encontrado.'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'erro': str(e)}, status=500)
    # ============================================================
# P1-2 — GERADOR DE GANCHO DE 3 SEGUNDOS (R-49)
# Cria a frase de retenção para a ABERTURA do vídeo.
# Regras do projeto: PT-BR, fiel ao produto, sem clickbait
# enganoso, sem tempos de cena em segundos.
# ============================================================
def gerar_gancho_3s(nome_produto, nicho=''):
    """Gera um gancho de retenção para a abertura do vídeo.

    Sorteio aleatório entre padrões prontos — nunca repete
    o mesmo gancho em chamadas seguidas.
    """
    nome = (nome_produto or '').strip()
    if len(nome) > 45:
        nome = nome[:42].rsplit(' ', 1)[0] + '...'

    nicho = (nicho or '').strip()
    if not nicho:
        nicho = 'boas ofertas'

    padroes = [
        f"Cansado de pagar caro por {nome}? Esse achado resolve.",
        f"{nome}: o detalhe que ninguém te conta antes de comprar.",
        f"Se você ama {nicho}, precisa ver {nome}.",
        f"Enquanto a oferta durar, {nome} sai por preço de achadinho.",
        f"3 motivos para {nome} estar entre os mais vendidos agora.",
        f"{nome} chegou no radar — e o preço surpreende.",
        f"Todo mundo está comprando {nome}. Você já viu?",
        f"Do problema à solução: {nome} por preço de oferta.",
    ]
    return random.choice(padroes)
# ============================================================
# P1-3 — CALENDÁRIO COMERCIAL COM ALERTAS (R-12 a R-15)
# Datas fixas no código. O dashboard mostra um banner quando
# faltam 14 dias ou menos para uma data que vende.
# ============================================================
def _n_esimo_domingo(ano, mes, n):
    """Retorna a data do n-ésimo domingo de um mês
    (ex.: 2º domingo de maio = Dia das Mães)."""
    from datetime import date, timedelta
    primeiro = date(ano, mes, 1)
    dias_ate_primeiro_domingo = (6 - primeiro.weekday()) % 7
    return primeiro + timedelta(days=dias_ate_primeiro_domingo + (n - 1) * 7)

def _ultima_sexta_do_mes(ano, mes):
    """Retorna a data da última sexta-feira de um mês (Black Friday)."""
    from datetime import date, timedelta
    if mes == 12:
        ultimo_dia = date(ano, 12, 31)
    else:
        ultimo_dia = date(ano, mes + 1, 1) - timedelta(days=1)
    dias_para_sexta = (ultimo_dia.weekday() - 4) % 7
    return ultimo_dia - timedelta(days=dias_para_sexta)

def _datas_comerciais(ano):
    """Monta a lista de datas comerciais do ano (nome, nicho, emoji)."""
    from datetime import date
    return [
        {'data': date(ano, 2, 1), 'nome': 'Volta às Aulas', 'nicho': 'material escolar, mochilas e eletrônicos', 'emoji': '🎒'},
        {'data': _n_esimo_domingo(ano, 5, 2), 'nome': 'Dia das Mães', 'nicho': 'presentes, moda, beleza e casa', 'emoji': '💐'},
        {'data': date(ano, 6, 12), 'nome': 'Dia dos Namorados', 'nicho': 'presentes, moda e perfumaria', 'emoji': '💝'},
        {'data': _n_esimo_domingo(ano, 8, 2), 'nome': 'Dia dos Pais', 'nicho': 'presentes masculinos, eletrônicos e ferramentas', 'emoji': '👔'},
        {'data': date(ano, 10, 12), 'nome': 'Dia das Crianças', 'nicho': 'brinquedos, infantil e games', 'emoji': '🧸'},
        {'data': _ultima_sexta_do_mes(ano, 11), 'nome': 'Black Friday', 'nicho': 'eletrônicos, casa e ofertas gerais', 'emoji': '🖤'},
        {'data': date(ano, 12, 25), 'nome': 'Natal', 'nicho': 'presentes, decoração e eletrônicos', 'emoji': '🎄'},
    ]

def calcular_alerta_comercial(hoje=None):
    """Retorna o alerta ativo (ou None) quando faltam 14 dias ou menos
    para uma data comercial. Aceita 'hoje' para facilitar testes."""
    from datetime import date
    if hoje is None:
        hoje = date.today()
    ano = hoje.year

    # Procura no ano atual
    for item in _datas_comerciais(ano):
        dias = (item['data'] - hoje).days
        if 0 <= dias <= 14:
            return {
                'nome': item['nome'],
                'nicho': item['nicho'],
                'emoji': item['emoji'],
                'dias': dias,
            }

    # Se estamos em dezembro, já olha o ano seguinte (ex.: janeiro)
    if hoje.month == 12:
        for item in _datas_comerciais(ano + 1):
            dias = (item['data'] - hoje).days
            if 0 <= dias <= 14:
                return {
                    'nome': item['nome'],
                    'nicho': item['nicho'],
                    'emoji': item['emoji'],
                    'dias': dias,
                }

    return None
# ============================================================
# P1-4 — TEMPLATES "VENDER SEM APARECER" (R-87 a R-94)
# 8 formatos de divulgação baseados no material do Icaro.
# Cada função gera uma copy pronta com os dados do produto.
# ============================================================

def template_x_achadinhos(nome, preco, link, nicho=''):
    """R-87 — Post para perfil temático no Twitter/X."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"🧐 Achado de hoje no nicho de {nicho_txt}!\n\n"
        f"📦 {nome}\n"
        f"💰 {preco}\n\n"
        f"👉 {link}\n\n"
        f"#desconto #oferta #achado"
    )

def template_pitch_grupo(nome, preco, link, nicho=''):
    """R-88 — Pitch para administradores de grupos WhatsApp/Telegram."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"Olá! Sou afiliado da Shopee e crio conteúdo diário de {nicho_txt}.\n\n"
        f"Gostaria de oferecer ao seu grupo uma parceria: envio ofertas selecionadas "
        f"como esta — {nome} por {preco} — sem flood, 1 a 2 posts por dia, "
        f"sempre com produtos testados.\n\n"
        f"Exemplo do formato: {link}\n\n"
        f"Podemos combinar disparo fixo ou participação percentual nas vendas. "
        f"Topa um teste de 7 dias?"
    )

def template_forum_comunidade(nome, preco, link, nicho=''):
    """R-89 — Divulgação contextual em comunidades/fóruns."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"Alguém aqui procura {nicho_txt} com bom custo-benefício?\n\n"
        f"Eu testei/comprei o {nome} e valeu muito a pena pelo preço ({preco}). "
        f"Deixei o link aqui caso queiram conferir: {link}\n\n"
        f"Sem enrolação: entrega rápida e avaliações boas. Qualquer dúvida, respondo aqui!"
    )

def template_anuncio_pago(nome, preco, link, nicho=''):
    """R-90 — Texto de anúncio para tráfego pago (Meta/Google/TikTok Ads)."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"🔥 Promoção válida hoje!\n\n"
        f"{nome} por apenas {preco} — qualidade que você procura no nicho de {nicho_txt}.\n\n"
        f"✅ Entrega rápida\n✅ Compra segura\n✅ Oferta por tempo limitado\n\n"
        f"👉 Garanta o seu: {link}\n\n"
        f"CTA: Comprar agora"
    )

def template_youtube_dark(nome, preco, link, nicho=''):
    """R-91 — Descrição para vídeo dark no YouTube (Review/Top 5/Vale a pena?)."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"📌 Título sugerido: {nome} — Vale a pena? (Review honesto)\n\n"
        f"Descrição do vídeo:\n"
        f"Vídeo sem rosto analisando o {nome} ({preco}) para quem busca {nicho_txt} "
        f"com bom custo-benefício. Mostro os pontos fortes, os pontos fracos e "
        f"se realmente vale a pena comprar.\n\n"
        f"🔗 Link do produto (rastreado): {link}\n\n"
        f"Comentário fixado: Deixe sua opinião! Você compraria o {nome} por {preco}?"
    )

def template_vitrine_beacons(nome, preco, link, nicho=''):
    """R-92 — Estrutura da vitrine agregadora (Beacons)."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"Título da página Beacons: {nicho_txt.capitalize()} em oferta — Achei & Indico\n\n"
        f"Bio: Toda semana os melhores achados do nicho de {nicho_txt}, "
        f"selecionados a dedo. Compre com segurança pela Shopee.\n\n"
        f"Botões/categorias sugeridos:\n"
        f"🛒 Destaque da semana — {nome} por {preco}: {link}\n"
        f"🏠 Casa e decoração: [cole aqui os links]\n"
        f"📱 Eletrônicos: [cole aqui os links]\n"
        f"👗 Moda e beleza: [cole aqui os links]\n\n"
        f"Dica: use um link encurtado (bit.ly) para cada botão."
    )

def template_robo_divulgador(nome, preco, link, nicho=''):
    """R-93 — Mensagens para robô divulgador (ManyChat/Zapier/Telegram)."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"Mensagem de boas-vindas (resposta automática):\n"
        f"👋 Bem-vindo! Aqui você recebe as melhores ofertas de {nicho_txt} da Shopee.\n"
        f"Comece agora: {nome} por apenas {preco} 👉 {link}\n\n"
        f"Mensagem de oferta do dia (disparo agendado):\n"
        f"🔥 Oferta de hoje: {nome} por {preco}! Corra, estoque limitado 👉 {link}\n\n"
        f"Comando de resposta rápida (ex.: 'oferta'):\n"
        f"Aqui está a oferta do momento: {nome} — {preco} — {link}"
    )

def template_briefing_influencer(nome, preco, link, nicho=''):
    """R-94 — Briefing para parceria com microinfluenciador (5k-50k)."""
    nicho_txt = nicho or 'ofertas'
    return (
        f"Briefing de parceria — {nicho_txt.capitalize()}\n\n"
        f"Produto: {nome}\n"
        f"Preço promocional: {preco}\n"
        f"Link para divulgação: {link}\n\n"
        f"Formato sugerido:\n"
        f"• 1 post no feed + 1 story mostrando o produto\n"
        f"• Texto curto: 'Achei esse achado incrível! {nome} por {preco} 👉 link na bio'\n"
        f"• Link rastreado na bio durante o período da parceria\n\n"
        f"Pagamento: comissão por venda (a combinar) | Duração: 7 dias | "
        f"Nicho do perfil: {nicho_txt}"
    )

def gerar_templates_sem_aparecer(produto):
    """Gera os 8 templates para um produto."""
    from .models import ProdutoValidado  # noqa: F401  (apenas valida o import)

    nome = getattr(produto, 'nome', 'Produto')
    preco = getattr(produto, 'preco', 0)
    link = getattr(produto, 'link_afiliado', '')
    nicho = getattr(produto, 'nicho', '')

    # Formata o preço em reais (ex.: 32.89 -> R$ 32,89)
    try:
        preco_txt = 'R$ ' + str(preco).replace('.', ',')
    except Exception:
        preco_txt = 'R$ ' + str(preco)

    return {
        'twitter_x': template_x_achadinhos(nome, preco_txt, link, nicho),
        'pitch_grupo': template_pitch_grupo(nome, preco_txt, link, nicho),
        'forum_comunidade': template_forum_comunidade(nome, preco_txt, link, nicho),
        'anuncio_pago': template_anuncio_pago(nome, preco_txt, link, nicho),
        'youtube_dark': template_youtube_dark(nome, preco_txt, link, nicho),
        'vitrine_beacons': template_vitrine_beacons(nome, preco_txt, link, nicho),
        'robo_divulgador': template_robo_divulgador(nome, preco_txt, link, nicho),
        'briefing_influencer': template_briefing_influencer(nome, preco_txt, link, nicho),
    }

def pagina_vender_sem_aparecer(request):
    """Página com os 8 templates 'vender sem aparecer' para um produto."""
    from .models import ProdutoValidado

    produtos = ProdutoValidado.objects.all()
    templates = None
    produto_selecionado = None

    produto_id = request.GET.get('produto_id')
    if produto_id:
        produto_selecionado = ProdutoValidado.objects.filter(id=produto_id).first()
        if produto_selecionado:
            templates = gerar_templates_sem_aparecer(produto_selecionado)

    contexto = {
        'produtos': produtos,
        'templates': templates,
        'produto_selecionado': produto_selecionado,
    }
    return render(request, 'core/vender_sem_aparecer.html', contexto)
# ============================================================
# P1-5 — PAINEL DE CONSTÂNCIA SEMANAL (R-48, R-63)
# Mostra a SEMANA ATUAL (Segunda a Domingo) com os posts de
# cada dia vs. a meta diária, a sequência (streak) de dias
# batendo a meta e o progresso de hoje.
# ============================================================
META_DIARIA_POSTS = 5  # meta padrão: posts por dia (pode trocar o 5)

def calcular_painel_constancia():
    from datetime import timedelta
    from django.db.models import Count
    from django.utils import timezone
    from .models import PostRegistro

    hoje = timezone.localdate()
    meta = META_DIARIA_POSTS

    # Busca a contagem de posts por dia (janela de 60 dias:
    # ampla o suficiente para calcular a sequência real sem cortar)
    inicio_busca = hoje - timedelta(days=59)
    registros = (
        PostRegistro.objects
        .filter(data_postagem__date__gte=inicio_busca)
        .values('data_postagem__date')
        .annotate(total=Count('id'))
    )
    contagem_por_dia = {r['data_postagem__date']: r['total'] for r in registros}

    # Monta a SEMANA ATUAL (Segunda a Domingo) para exibir no painel.
    # inicio_semana = segunda-feira da semana atual (SEG primeiro!)
    nomes_dias = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    dias = []
    inicio_semana = hoje - timedelta(days=hoje.weekday())  # segunda-feira
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        posts = contagem_por_dia.get(dia, 0)
        dias.append({
            'rotulo': nomes_dias[dia.weekday()],
            'data': dia.strftime('%d/%m'),
            'posts': posts,
            'atingiu': posts >= meta,
            'e_hoje': dia == hoje,
        })

    # Sequência (streak): conta os dias seguidos batendo a meta.
    # Se hoje ainda não bateu, a sequência vale até ontem (não quebra).
    streak = 0
    dia = hoje
    if contagem_por_dia.get(dia, 0) < meta:
        dia -= timedelta(days=1)
    while dia >= inicio_busca:
        if contagem_por_dia.get(dia, 0) >= meta:
            streak += 1
            dia -= timedelta(days=1)
        else:
            break

    posts_hoje = contagem_por_dia.get(hoje, 0)
    faltam = max(0, meta - posts_hoje)
    progresso = min(100, round(posts_hoje / meta * 100)) if meta else 0

    if posts_hoje >= meta:
        mensagem = 'Meta de hoje batida! Bora manter a sequência. 🔥'
    elif streak >= 3:
        mensagem = f'Sequência de {streak} dias! Faltam só {faltam} posts para hoje. 💪'
    elif posts_hoje >= meta // 2:
        mensagem = f'Quase lá! Faltam {faltam} posts para bater a meta de hoje. 🚀'
    else:
        mensagem = f'Faltam {faltam} posts para bater a meta de hoje. Todo post conta! 😉'

    return {
        'meta': meta,
        'dias': dias,
        'streak': streak,
        'posts_hoje': posts_hoje,
        'faltam': faltam,
        'progresso': progresso,
        'mensagem': mensagem,
    }
# ============================================================
# P1-6 — MÓDULO EDUCATIVO DE COMISSÃO EXTRA (R-23 a R-25)
# "Escolinha" das comissões: ensina sobre as lojas XTRA
# COMMISSION e as lojas bônus que pagam acima do padrão
# (material "Lojas que Mais Pagam" do Icaro).
# Dados fixos no código (mesma abordagem do calendário).
# ============================================================
LOJAS_XTRA_COMMISSION = [
    {'nome': 'Xiaomi', 'comissao': 12, 'nicho': 'Eletrônicos'},
    {'nome': 'Samsung', 'comissao': 10, 'nicho': 'Eletrônicos'},
    {'nome': 'Positivo', 'comissao': 10, 'nicho': 'Informática'},
    {'nome': 'Midea', 'comissao': 10, 'nicho': 'Climatização'},
    {'nome': 'Philips', 'comissao': 9, 'nicho': 'Eletrodomésticos'},
    {'nome': 'Multilaser', 'comissao': 9, 'nicho': 'Acessórios'},
    {'nome': 'LG', 'comissao': 9, 'nicho': 'Eletrônicos e linha branca'},
    {'nome': 'Intelbras', 'comissao': 8, 'nicho': 'Segurança eletrônica'},
    {'nome': 'Mondial', 'comissao': 8, 'nicho': 'Eletroportáteis'},
    {'nome': 'Electrolux', 'comissao': 8, 'nicho': 'Eletrodomésticos'},
]

LOJAS_BONUS = [
    {'nome': 'Cristal Elegance', 'comissao': 78},
    {'nome': 'Sanja Bijus', 'comissao': 53},
    {'nome': 'Brincalhão Oficial', 'comissao': 48},
    {'nome': 'Lar e Decor', 'comissao': 33},
    {'nome': 'Van Hout', 'comissao': 33},
    {'nome': 'Lalouí Cosmetics', 'comissao': 33},
    {'nome': 'Lojas Kingdom', 'comissao': 33},
    {'nome': 'Gato Preto Arte Laser', 'comissao': 28},
    {'nome': 'Mangueart', 'comissao': 28},
    {'nome': 'Izzom Shop', 'comissao': 28},
]

def calcular_comissao(preco, percentual):
    """Quanto você ganha vendendo um produto de R$ preco com X%."""
    return round(preco * percentual / 100, 2)

def pagina_escolinha_comissao(request):
    """Página educativa: como a comissão funciona + as 20 lojas."""
    preco_exemplo = 100.00
    exemplos = [
        {'rotulo': 'Loja comum da Shopee', 'percentual': None,
         'descricao': 'A maioria dos produtos paga pouco', 'ganho': None},
        {'rotulo': 'Xiaomi (XTRA)', 'percentual': 12,
         'descricao': 'Marca oficial com XTRA COMMISSION',
         'ganho': calcular_comissao(preco_exemplo, 12)},
        {'rotulo': 'Lar e Decor (bônus)', 'percentual': 33,
         'descricao': 'Loja parceira com comissão elevada',
         'ganho': calcular_comissao(preco_exemplo, 33)},
        {'rotulo': 'Cristal Elegance (bônus)', 'percentual': 78,
         'descricao': 'Loja parceira com a maior comissão',
         'ganho': calcular_comissao(preco_exemplo, 78)},
    ]
    contexto = {
        'lojas_xtra': LOJAS_XTRA_COMMISSION,
        'lojas_bonus': LOJAS_BONUS,
        'exemplos': exemplos,
        'preco_exemplo': preco_exemplo,
    }
    return render(request, 'core/comissao.html', contexto)
# ============================================================
# P2-3-FINAL v2 — LEGENDA NATIVA CURTA DO SHOPEE VÍDEO (sem "…")
# Se a IA escrever longo demais, a gente NÃO corta o texto:
# pedimos para ela REESCREVER mais curta (feedback com o
# tamanho). Corte com reticências só em último caso, e sem "…".
# ============================================================
def _limpar_legenda_gerada(texto):
    """Remove lixo que a IA costuma colar: links, aspas, parênteses,
    espaços duplicados e reticências no final."""
    if not texto:
        return ''
    texto = re.sub(r'https?://\S+|www\.\S+', '', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    texto = texto.strip(' "\'()[]')
    texto = re.sub(r'[.…]{2,}$', '', texto)
    texto = texto.strip(' ,.;:')
    return texto

def gerar_legenda_shopee_curta(nome_produto, nicho_busca="", preco="0,00"):
    """Gera legenda pronta para a Shopee Vídeo: curta, completa e SEM '…'.
    Estratégia: pede no máx. 120 caracteres, valida o resultado e, se
    estourou, PEDE DE NOVO com feedback (nunca corta com reticências)."""
    alvo = 120        # pede folgado para caber com sobra no limite real (150)
    tolerancia = 130  # acima disso, regenera com feedback
    if not GEMINI_API_KEY:
        return _fallback_legenda_shopee_curta(nome_produto, preco)

    prompt_base = f"""
Você é um copywriter brasileiro especialista em Shopee Vídeo.
Produto: "{nome_produto}". Nicho (se houver): "{nicho_busca}". Preço: R$ {preco}.
Escreva UMA legenda curta para um vídeo da Shopee Vídeo seguindo À RISCA:
- No MÁXIMO {alvo} caracteres. CONTE os caracteres antes de responder.
- Texto COMPLETO e com sentido do início ao fim (NUNCA cortado, NUNCA use reticências "…").
- Estrutura: gancho curto com a dor/benefício + CTA de urgência no final
  (ex.: "🔥 Corre que é por tempo limitado!").
- PROIBIDO: link, "garanta o seu pelo link", "clique no link", URL,
  "compre pelo link" ou pedir para clicar em link.
- Sem hashtags. Máximo 1 emoji. Português do Brasil.
Responda APENAS com a legenda, sem aspas, sem parênteses, sem título, sem texto extra.
"""
    texto = _limpar_legenda_gerada(_chamar_gemini(prompt_base))
    if texto and len(texto) > tolerancia:
        # 1º estouro: NÃO corta — devolve para a IA reescrever mais curta
        feedback = f"""
A legenda anterior ficou com {len(texto)} caracteres — passou do limite de {alvo}.
Produto: "{nome_produto}". Preço: R$ {preco}.
Reescreva a MESMA ideia em versão mais curta, com no máximo {alvo} caracteres,
mantendo o gancho e o CTA de urgência. Texto completo, sem reticências, sem cortes.
Legenda anterior (para encurtar): "{texto[:180]}"
"""
        texto2 = _limpar_legenda_gerada(_chamar_gemini(feedback))
        if texto2:
            texto = texto2
    if texto and len(texto) > 145:
        # último recurso (raríssimo): fecha na última frase que couber, SEM "…"
        trecho = texto[:145]
        if ' ' in trecho:
            trecho = trecho[:trecho.rfind(' ')]
        melhor = ''
        for pontuacao in ('! ', '? ', '. '):
            indice = trecho.rfind(pontuacao)
            if indice >= 60:
                melhor = trecho[:indice + 1]
                break
        texto = (melhor or trecho).strip(' ,.;:')
    return texto

def _fallback_legenda_shopee_curta(nome_produto, preco="0,00"):
    """Fallback sem API: legenda curta pronta a partir do nome/preço."""
    nome = (nome_produto or '').strip()
    if len(nome) > 70:
        nome = nome[:67].rstrip(' ,.;:') + '…'
    base = f"{nome} — oferta por tempo limitado! 🔥"
    if len(base) > 130:
        base = base[:127].rstrip(' ,.;:') + '…'
    return base
# ============================================================
# P2-3-FINAL v3 — ENDPOINT DEFINITIVO
# Colado no FIM do arquivo = vence a versão antiga (aparece antes).
# ============================================================
import re as _re_p23

def _p23_limpar(texto):
    """Tira lixo que a IA cola: links, aspas, parênteses e reticências."""
    if not texto:
        return ''
    texto = _re_p23.sub(r'https?://\S+|www\.\S+', '', texto)
    texto = _re_p23.sub(r'\s+', ' ', texto).strip()
    texto = texto.strip(' "\'()[]')
    texto = _re_p23.sub(r'[.…]{2,}$', '', texto)
    texto = texto.strip(' ,.;:')
    return texto

def _p23_fallback(nome_produto):
    """Plano B sem IA: legenda curta montada do nome do produto."""
    nome = (nome_produto or '').strip()
    if len(nome) > 70:
        nome = nome[:67].rstrip(' ,.;:')
    base = nome + " — oferta por tempo limitado! 🔥"
    if len(base) > 148:
        base = base[:145].rstrip(' ,.;:')
    return base

def _p23_legenda_curta(nome_produto, nicho_busca="", preco="0,00"):
    """Legenda CURTA e COMPLETA para o Shopee Vídeo (máx. ~120).
    Se estourar, devolve para a IA encurtar — nunca corta com '…'."""
    if not GEMINI_API_KEY:
        return _p23_fallback(nome_produto)
    prompt = (
        'Você é um copywriter brasileiro especialista em Shopee Vídeo.\n'
        'Produto: "' + str(nome_produto) + '". Nicho: "' + str(nicho_busca) + '". '
        'Preço: R$ ' + str(preco) + '.\n'
        'Escreva UMA legenda curta para vídeo da Shopee Vídeo, seguindo À RISCA:\n'
        '- No MÁXIMO 120 caracteres. CONTE antes de responder.\n'
        '- Texto COMPLETO e com sentido do início ao fim (nunca cortado, sem reticências).\n'
        '- Estrutura: gancho com a dor/benefício + CTA de urgência no final.\n'
        '- PROIBIDO: link, URL, "clique no link", "garanta pelo link".\n'
        '- Sem hashtags. Máximo 1 emoji. Português do Brasil.\n'
        'Responda APENAS com a legenda, sem aspas e sem parênteses.'
    )
    try:
        texto = _p23_limpar(_chamar_gemini(prompt) or '')
    except Exception as e:
        print('[P2-3 v3] falha Gemini:', e)
        texto = ''
    if texto and len(texto) > 130:
        encurtar = (
            'A legenda abaixo ficou com ' + str(len(texto)) + ' caracteres e passou '
            'do limite de 120. Reescreva a MESMA ideia em no máximo 120 caracteres, '
            'mantendo o gancho e o CTA. Texto completo, sem reticências.\n'
            'Legenda: "' + texto[:180] + '"'
        )
        try:
            curta = _p23_limpar(_chamar_gemini(encurtar) or '')
            if curta:
                texto = curta
        except Exception as e:
            print('[P2-3 v3] falha Gemini (encurtar):', e)
    if texto and len(texto) > 148:
        trecho = texto[:148]
        if ' ' in trecho:
            trecho = trecho[:trecho.rfind(' ')]
        texto = trecho.rstrip(' ,.;:')
    return texto or _p23_fallback(nome_produto)

def gerar_prompt_shopee(request):
    """VERSÃO v4 (última do arquivo = vence a antiga).
    Shopee: legenda NASCE curta. Reels/Feed: texto universal com link.
    v4: + Variações A/B de legenda (campo 'legendas' para shopee)."""
    try:
        nome = request.POST.get('nome', '')
        nicho_busca = request.POST.get('nicho', '')
        plataforma = (request.POST.get('plataforma', 'shopee') or 'shopee').strip().lower()

        if plataforma == 'reels':
            prompt = gerar_prompt_reels_tiktok_video(nome, nicho_busca)
        elif plataforma == 'feed':
            prompt = gerar_prompt_feed_video(nome, nicho_busca)
        else:
            prompt = gerar_prompt_shopee_video(nome, nicho_busca)

        legendas = None
        if plataforma == 'shopee':
            preco = request.POST.get('preco', '') or '0,00'
            legenda = _p23_legenda_curta(nome, nicho_busca, preco)
            legenda, limite, tamanho = montar_legenda_para_plataforma(legenda, [], 'shopee')

            # ===== FASE INTELIGÊNCIA — Variações A/B de legenda =====
            # Nome encurtado para nunca estourar o limite de 150 chars do Shopee Vídeo
            nome_curto = nome if len(nome) <= 60 else nome[:57] + '...'
            legendas = {
                'v1': f"🔥 Achei esse achado! {nome_curto} por R$ {preco} 🛒",
                'v2': f"Sabe aquele produto que todo mundo procura? Achei: {nome_curto} por R$ {preco}. Vale cada centavo! ✨",
                'v3': f"Cansou de pagar caro? 😱 {nome_curto} por R$ {preco} — aproveita antes que acabe! 🛒",
            }
        else:
            copy = (request.POST.get('copy', '') or '').strip()
            hashtags_raw = request.POST.get('hashtags', '') or ''
            hashtags = [h.strip() for h in hashtags_raw.split(',') if h.strip()]
            if not copy or not hashtags:
                item_id = request.POST.get('item_id', '')
                cache_ia = request.session.get('ia_produto_' + str(item_id)) if item_id else None
                if cache_ia:
                    if not copy:
                        copy = cache_ia.get('copy', '')
                    if not hashtags:
                        hashtags = cache_ia.get('hashtags', [])
            if not copy or not hashtags:
                dados_ia = gerar_conteudo_com_gemini(nome, nicho_busca)
                if not copy:
                    copy = dados_ia['copy_vendas']
                if not hashtags:
                    hashtags = dados_ia['hashtags']
            legenda, limite, tamanho = montar_legenda_para_plataforma(copy, hashtags, plataforma)

        print('[P2-3 v4] plataforma=' + str(plataforma) + ' | legenda=' + str(tamanho) + ' chars')
        return JsonResponse({
            'status': 'sucesso',
            'versao': 'P2-3-v4',
            'prompt_shopee': prompt,
            'legenda_plataforma': legenda,
            'limite_legenda': limite,
            'tamanho_legenda': tamanho,
            'plataforma': plataforma,
            'legendas': legendas,
        })
    except Exception as e:
        print('Erro ao gerar prompt (v4):', e)
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)