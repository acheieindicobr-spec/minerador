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
from .models import PostRegistro, ProdutoValidado
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
# ATENÇÃO: confirme que "gemini-3.6-flash" e "gemini-3.5-flash-lite"
# são nomes válidos na sua chave. Se a API responder 400/404 com
# "model not found", troque pelos nomes atuais da sua conta.
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
    "Vídeo de vitrine do produto EXATAMENTE como na imagem de referência: "
    "mesmo corte, mesmas cores, mesmo tecido, mesma quantidade de peças. "
    "PRIMEIRO, crie um STORYBOARD (imagem com os quadros da cena) mostrando o "
    "produto idêntico à imagem de referência, para o usuário conferir e aprovar; "
    "SÓ DEPOIS gere o vídeo final. "
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
- O prompt do vídeo DEVE ser escrito em PORTUGUÊS DO BRASIL (o texto que o usuário cola no gerador image-to-video Google Flow deve estar todo em português).
- STORYBOARD ANTES DO VÍDEO (OBRIGATÓRIO): o prompt DEVE pedir que o gerador crie PRIMEIRO um storyboard (imagem com os quadros da cena) mostrando o produto idêntico à imagem de referência, para o usuário conferir e aprovar, e SÓ DEPOIS gerar o vídeo final. O storyboard e o vídeo devem mostrar UM único item idêntico à imagem.
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
- O prompt do vídeo DEVE ser escrito em PORTUGUÊS DO BRASIL (o texto que o usuário cola no gerador image-to-video Google Flow deve estar todo em português).
- STORYBOARD ANTES DO VÍDEO (OBRIGATÓRIO): o prompt DEVE pedir que o gerador crie PRIMEIRO um storyboard (imagem com os quadros da cena) mostrando o produto idêntico à imagem de referência, para o usuário conferir e aprovar, e SÓ DEPOIS gerar o vídeo final. O storyboard e o vídeo devem mostrar UM único item idêntico à imagem.
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
    # ShopeeService() DENTRO do try — se as credenciais faltarem,
    # mostra o aviso amigável em vez de erro 500.
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
        val_preco = prod.get('price') or prod.get('price_direct') or 0.0
        try:
            val_preco = float(val_preco)
        except (ValueError, TypeError):
            val_preco = 0.0
        # taxa_num inicializada ANTES do try — nunca fica indefinida
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
        # valores numéricos prontos (evita re-parse de string no filtro)
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
    # usa os campos numéricos já calculados (sem re-parse de string)
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
        # ===== Campos numéricos vindos do card da dashboard =====
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
                'imagem_url': imagem_url,
                'link_original': link_original,
                'link_afiliado': link_afiliado,
                'prompt_video_ia': prompt_ia,
                # ===== Campos numéricos — banco rico para relatórios =====
                'preco': preco_num,
                'comissao_percentual': comissao_num,
                'vendas': vendas_num,
                'comissao_estimada': comissao_estimada_num,
            }
        )
        # guarda copy/hashtags na sessão para o modal NÃO chamar o Gemini de novo
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
    """Gera um prompt de vídeo para a SHOPEE VÍDEO seguindo as diretrizes
    da plataforma à risca, em português, com storyboard antes do vídeo."""
    fallback = (
        "Vídeo vertical 9:16 do produto EXATAMENTE como na imagem de referência. "
        "PRIMEIRO, crie um STORYBOARD (imagem com os quadros da cena) mostrando o "
        "produto idêntico à imagem — mesmo corte, cores, tecido e quantidade — para o "
        "usuário conferir e aprovar; SÓ DEPOIS gere o vídeo final. "
        "MOSTRE APENAS UM ÚNICO ITEM — proibido duplicar o produto ou adicionar outros. "
        "Abertura: produto em foco com texto chamativo na tela. "
        "Desenvolvimento: close-up mostrando o principal benefício. "
        "Detalhes: tamanho, material e diferenciais. "
        "Fechamento: CTA para comprar pelo link da Shopee. "
        "Sem marca d'água de outras plataformas. Produto idêntico ao link. "
        "Todo texto na tela em português do Brasil. "
        "Adapte a duração ao limite da ferramenta."
    )
    if not GEMINI_API_KEY:
        return fallback
    prompt_sistema = f"""
Você é um especialista em vídeos para a SHOPEE VÍDEO, a plataforma de vídeos curtos da Shopee.
Produto: "{nome_produto}". Nicho informado (se houver): "{nicho_busca}".
Gere UM prompt de vídeo (image-to-video) escrito em PORTUGUÊS DO BRASIL, seguindo À RISCA as diretrizes da Shopee Vídeo.
============================================================
DIRETRIZES OBRIGATÓRIAS DA SHOPEE VÍDEO (NÃO NEGOCIÁVEL)
============================================================
0. STORYBOARD ANTES DO VÍDEO (OBRIGATÓRIO): o prompt deve instruir o gerador a criar PRIMEIRO um storyboard (imagem com os quadros da cena), mostrando o produto idêntico à imagem de referência e com UM único item, para o usuário conferir e aprovar; SÓ DEPOIS gerar o vídeo final.
1. PRODUTO COERENTE E FIEL (regra mais importante):
   - O vídeo DEVE ser gerado a partir da IMAGEM DE REFERÊNCIA do produto.
   - O produto mostrado DEVE ser IDÊNTICO ao do link: mesmo corte, cores, quantidade de peças, tecido, acabamento.
   - PROIBIDO: reimaginar, redesenhar, trocar cores, mudar a quantidade de peças, duplicar o produto ou mostrar produto diferente do anunciado.
2. UM ÚNICO PRODUTO POR VÍDEO: foque apenas no produto anunciado, mostrando UM único item (ou as peças reais do kit anunciado).
3. FORMATO VERTICAL (9:16), sem bordas ou espaços vazios.
4. SEM MARCA D'ÁGUA de outras plataformas (TikTok, YouTube, Instagram, Reels) e sem logos de concorrentes.
5. SEM PROMESSAS ENGANOSAS: nada de "cura milagrosa", "resultado garantido", alegações médicas ou benefícios irreais.
6. SEM CLICKBAIT: o que aparece no vídeo deve corresponder exatamente ao produto do link.
7. SEM CONTEÚDO PROIBIDO: nudez, violência, ódio, menores em situações inadequadas, atividades ilegais, álcool/cigarros, conteúdo político.
8. CONTEÚDO ORIGINAL: sem vídeos copiados de terceiros.
9. SEM ATOS PERIGOSOS: sem uso incorreto ou perigoso do produto.
============================================================
REGRAS DE ESCRITA DO PROMPT (OBRIGATÓRIAS)
============================================================
- Escreva o prompt do vídeo em PORTUGUÊS DO BRASIL (o texto que o usuário cola no gerador deve estar em português).
- NÃO use tempos de cena em segundos (ex.: "CENA 1 (0-3s)"). Descreva a ESTRUTURA em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO.
- FIDELIDADE OBRIGATÓRIA: inclua frases como "produto idêntico à imagem de referência", "não altere corte, cores, tecido ou quantidade".
- UM ÚNICO ITEM: inclua "mostre apenas UM item, idêntico à imagem de referência — proibido duplicar ou adicionar outros produtos".
- QUANTIDADE EXATA DE PEÇAS (se kit/conjunto real): inclua, ex.: "kit com 7 peças, TODAS as 7 peças visíveis e idênticas à imagem de referência".
- CORES DO PRODUTO: liste as cores presentes no nome/anúncio, ex.: "cores exatamente como na imagem de referência: preto, marrom, vinho".
- Texto na tela (se houver): curto, legível, em PORTUGUÊS DO BRASIL, ex.: "R$ 49,90 | Frete grátis".
- CTA final: mencione comprar pelo link da Shopee (sem inventar URL).
- Formato final: uma linha por etapa, separadas por " | ".
Responda APENAS com o prompt do vídeo, sem explicações, sem títulos, sem texto extra.
"""
    texto = _chamar_gemini(prompt_sistema)
    if texto:
        return texto
    return fallback
def _chamar_gemini_prompt(prompt_sistema):
    """Compatibilidade: delega para a função única _chamar_gemini."""
    return _chamar_gemini(prompt_sistema)
def gerar_prompt_reels_tiktok_video(nome_produto, nicho_busca=""):
    """Gera um prompt 9:16 estilo LIVRE (Reels/TikTok)."""
    fallback = (
        "Vídeo vertical 9:16 (tela cheia) do produto EXATAMENTE como na imagem de referência, "
        "em estilo livre e criativo para Reels e TikTok. "
        "PRIMEIRO, crie um STORYBOARD (imagem com os quadros da cena) mostrando o "
        "produto idêntico à imagem — mesmo corte, cores, tecido e quantidade — para o "
        "usuário conferir e aprovar; SÓ DEPOIS gere o vídeo final. "
        "MOSTRE APENAS UM ÚNICO ITEM — proibido duplicar o produto ou adicionar outros. "
        "Gancho forte nos primeiros 2 segundos para prender quem rola o feed. "
        "Abertura: produto em destaque com texto chamativo. "
        "Desenvolvimento: cortes dinâmicos e movimento natural de câmera (aproximação, giro leve). "
        "Detalhes: benefício principal do produto em close-up. "
        "Fechamento: CTA para comprar pelo link. "
        "Todo texto na tela em português do Brasil, curto e de impacto. "
        "Sem marca d'água de outras plataformas. "
        "Adapte a duração ao limite da ferramenta."
    )
    if not GEMINI_API_KEY:
        return fallback
    prompt_sistema = f"""
Você é um estrategista de vídeos curtos para REELS (Instagram) e TIKTOK, especialista em produtos Shopee.
Produto: "{nome_produto}". Nicho informado (se houver): "{nicho_busca}".
Gere UM prompt de vídeo (image-to-video) escrito em PORTUGUÊS DO BRASIL, em estilo LIVRE e criativo de rede social, seguindo À RISCA as regras abaixo.
============================================================
REGRAS OBRIGATÓRIAS (NÃO NEGOCIÁVEL)
============================================================
0. STORYBOARD ANTES DO VÍDEO (OBRIGATÓRIO): o prompt deve instruir o gerador a criar PRIMEIRO um storyboard (imagem com os quadros da cena), mostrando o produto idêntico à imagem de referência e com UM único item, para o usuário conferir e aprovar; SÓ DEPOIS gerar o vídeo final.
1. FORMATO: vídeo VERTICAL 9:16 (1080x1920), TELA CHEIA, sem bordas nem espaços vazios.
2. PRODUTO COERENTE E FIEL (regra mais importante): o produto DEVE ser gerado a partir da IMAGEM DE REFERÊNCIA e ser IDÊNTICO ao do link — mesmo corte, cores, quantidade de peças, tecido, acabamento. PROIBIDO reimaginar, redesenhar, trocar cores, mudar a quantidade, duplicar ou mostrar produto diferente do anunciado.
3. UM ÚNICO PRODUTO POR VÍDEO: foque apenas no produto anunciado, mostrando UM único item (ou as peças reais do kit anunciado).
4. ESTILO LIVRE: a criatividade é bem-vinda — gancho forte nos primeiros 2 segundos, cortes dinâmicos, ritmo acelerado de trend, movimento natural de câmera (aproximação, giro leve). A liberdade vale para ENQUADRAMENTO, CENÁRIO e RITMO — NUNCA para alterar o produto nem a quantidade de itens.
5. TEXTO NA TELA (se houver): TODO texto em PORTUGUÊS DO BRASIL, curto e chamativo (ex.: 'Aproveite', 'Oferta', 'Só hoje', 'R$ 49,90').
6. SEM MARCA D'ÁGUA de outras plataformas e sem logos de concorrentes.
7. SEM PROMESSAS ENGANOSAS (nada de 'cura milagrosa' ou 'resultado garantido') e SEM conteúdo proibido: nudez, violência, ódio, atividades ilegais, álcool/cigarros, conteúdo político.
8. CTA final: compre pelo link (sem inventar URL).
9. NÃO use tempos de cena em segundos. Descreva a estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO.
10. Formato final: uma linha por etapa, separadas por " | ".
Responda APENAS com o prompt do vídeo, sem explicações, sem títulos, sem texto extra.
"""
    texto = _chamar_gemini(prompt_sistema)
    if texto:
        return texto
    return fallback
def gerar_prompt_feed_video(nome_produto, nicho_busca=""):
    """Gera um prompt 4:5 para o FEED do Facebook e do Instagram."""
    fallback = (
        "Vídeo vertical 4:5 (1080x1350) do produto EXATAMENTE como na imagem de referência, "
        "próprio para o FEED do Facebook e do Instagram. "
        "PRIMEIRO, crie um STORYBOARD (imagem com os quadros da cena) mostrando o "
        "produto idêntico à imagem — mesmo corte, cores, tecido e quantidade — para o "
        "usuário conferir e aprovar; SÓ DEPOIS gere o vídeo final. "
        "MOSTRE APENAS UM ÚNICO ITEM — proibido duplicar o produto ou adicionar outros. "
        "Composição CENTRALIZADA: mantenha o produto e os textos dentro da área central "
        "(as bordas podem ser cortadas na visualização do feed). "
        "Abertura: produto em destaque. "
        "Desenvolvimento: benefício principal em close-up. "
        "Detalhes: tamanho, material e diferenciais. "
        "Fechamento: CTA para comprar pelo link. "
        "Textos curtos e em FONTE GRANDE, em português do Brasil, no máximo 1 a 2 frases por cena. "
        "Fundo limpo, sem poluição visual. "
        "Sem marca d'água de outras plataformas. "
        "Adapte a duração ao limite da ferramenta."
    )
    if not GEMINI_API_KEY:
        return fallback
    prompt_sistema = f"""
Você é um estrategista de vídeos para o FEED do FACEBOOK e do INSTAGRAM, especialista em produtos Shopee.
Produto: "{nome_produto}". Nicho informado (se houver): "{nicho_busca}".
Gere UM prompt de vídeo (image-to-video) escrito em PORTUGUÊS DO BRASIL, próprio para aparecer em um BLOCO do feed (não em tela cheia), seguindo À RISCA as regras abaixo.
============================================================
REGRAS OBRIGATÓRIAS (NÃO NEGOCIÁVEL)
============================================================
0. STORYBOARD ANTES DO VÍDEO (OBRIGATÓRIO): o prompt deve instruir o gerador a criar PRIMEIRO um storyboard (imagem com os quadros da cena), mostrando o produto idêntico à imagem de referência e com UM único item, para o usuário conferir e aprovar; SÓ DEPOIS gerar o vídeo final.
1. FORMATO: vídeo VERTICAL 4:5 (1080x1350). NUNCA use 9:16, 1:1 ou 16:9 — o vídeo aparece em um BLOCO dentro do feed, então tudo precisa caber na área visível.
2. COMPOSIÇÃO CENTRALIZADA E SEGURA: mantenha o produto e os textos dentro da área central do quadro; as bordas podem ser cortadas na visualização do feed.
3. PRODUTO COERENTE E FIEL (regra mais importante): o produto DEVE ser gerado a partir da IMAGEM DE REFERÊNCIA e ser IDÊNTICO ao do link — mesmo corte, cores, quantidade de peças, tecido, acabamento. PROIBIDO reimaginar, redesenhar, trocar cores, mudar a quantidade, duplicar ou mostrar produto diferente do anunciado.
4. UM ÚNICO PRODUTO POR VÍDEO: foque apenas no produto anunciado, mostrando UM único item (ou as peças reais do kit anunciado).
5. TEXTOS NA TELA: CURTOS e em FONTE GRANDE (o vídeo aparece menor no feed, então o texto precisa ser lido fácil), em PORTUGUÊS DO BRASIL, no MÁXIMO 1 a 2 frases curtas por cena (ex.: 'Aproveite', 'Oferta', 'R$ 49,90').
6. FUNDO LIMPO e sem poluição visual, produto em evidência.
7. SEM MARCA D'ÁGUA de outras plataformas e sem logos de concorrentes.
8. SEM PROMESSAS ENGANOSAS (nada de 'cura milagrosa' ou 'resultado garantido') e SEM conteúdo proibido: nudez, violência, ódio, atividades ilegais, álcool/cigarros, conteúdo político.
9. CTA final: compre pelo link (sem inventar URL).
10. NÃO use tempos de cena em segundos. Descreva a estrutura em etapas: ABERTURA, DESENVOLVIMENTO, DETALHES, FECHAMENTO.
11. Formato final: uma linha por etapa, separadas por " | ".
Responda APENAS com o prompt do vídeo, sem explicações, sem títulos, sem texto extra.
"""
    texto = _chamar_gemini(prompt_sistema)
    if texto:
        return texto
    return fallback
def montar_legenda_para_plataforma(copy, hashtags, plataforma='shopee'):
    """Monta a legenda (copy + hashtags) já no tamanho certo da plataforma.
    Limites: shopee = 150 | reels = 2200 | feed = 2200."""
    limite = LIMITES_CARACTERES_LEGENDA.get(plataforma, 150)
    copy = (copy or '').strip()
    hashtags = [h for h in (hashtags or []) if h.strip()]
    copy, hashtags = _remover_hashtags_duplicadas(copy, hashtags)
    hashtags_texto = ' '.join(hashtags)
    def montar():
        if hashtags_texto:
            return f"{copy}\n{hashtags_texto}"
        return copy
    legenda = montar()
    if len(legenda) <= limite:
        return legenda, limite, len(legenda)
    while hashtags and len(montar()) > limite:
        hashtags = hashtags[:-1]
        hashtags_texto = ' '.join(hashtags)
    if len(montar()) > limite:
        espaco_copy = (limite - (len(hashtags_texto) + 1)) if hashtags_texto else limite
        if espaco_copy <= 0:
            copy = ''
        elif len(copy) > espaco_copy:
            if espaco_copy <= 12:
                copy = copy[:espaco_copy]
            else:
                metade = espaco_copy // 2
                copy = copy[:metade].rstrip() + '…' + copy[-(espaco_copy - metade - 1):].lstrip()
    legenda = montar()
    return legenda, limite, len(legenda)
@require_POST
def gerar_prompt_shopee(request):
    """Gera o prompt de vídeo conforme a plataforma escolhida no modal."""
    try:
        nome = request.POST.get('nome', '')
        nicho_busca = request.POST.get('nicho', '')
        plataforma = request.POST.get('plataforma', 'shopee').strip().lower()
        if plataforma == 'reels':
            prompt = gerar_prompt_reels_tiktok_video(nome, nicho_busca)
        elif plataforma == 'feed':
            prompt = gerar_prompt_feed_video(nome, nicho_busca)
        else:
            prompt = gerar_prompt_shopee_video(nome, nicho_busca)
        copy = request.POST.get('copy', '').strip()
        hashtags_raw = request.POST.get('hashtags', '')
        hashtags = [h.strip() for h in hashtags_raw.split(',') if h.strip()]
        if not copy or not hashtags:
            item_id = request.POST.get('item_id', '')
            cache_ia = request.session.get(f'ia_produto_{item_id}') if item_id else None
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
        return JsonResponse({
            'status': 'sucesso',
            'prompt_shopee': prompt,
            'legenda_plataforma': legenda,
            'limite_legenda': limite,
            'tamanho_legenda': tamanho,
            'plataforma': plataforma,
        })
    except Exception as e:
        print(f"Erro ao gerar prompt: {e}")
        return JsonResponse({'status': 'erro', 'mensagem': str(e)}, status=400)