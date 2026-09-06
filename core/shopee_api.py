"""
Integracao com a API de Afiliados da Shopee (GraphQL).
Busca a LISTA GERAL de ofertas, ordena por MAIS VENDIDOS e junta varias paginas.
"""
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

PAUSA_ENTRE_PAGINAS = 2    # segundos — anti-bloqueio
MAX_PAGINAS = 5
LIMITE_PADRAO = 50

# Palavras que não ajudam na busca (ruído de títulos colados)
PALAVRAS_RUIDO = {
    "com", "de", "da", "do", "das", "dos", "para", "em", "no", "na",
    "um", "uma", "uns", "umas", "kit", "original", "novo", "nova",
    "promocao", "oferta", "feminina", "feminino", "masculino",
    "2023", "2024", "2025", "2026",
}

class ShopeeService:
    def __init__(self):
        self.app_id = getattr(settings, "SHOPEE_APP_ID", None) or os.getenv("SHOPEE_APP_ID")
        self.secret = getattr(settings, "SHOPEE_SECRET", None) or os.getenv("SHOPEE_SECRET")
        if not self.app_id or not self.secret:
            raise ValueError("SHOPEE_APP_ID e/ou SHOPEE_SECRET nao configurados.")

    # ---------- Assinatura ----------

    def _gerar_headers(self, payload_str):
        timestamp = int(time.time())
        factor = f"{self.app_id}{timestamp}{payload_str}{self.secret}"
        signature = hashlib.sha256(factor.encode("utf-8")).hexdigest()
        return {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={timestamp}, Signature={signature}",
        }

    # ---------- Requisicao GraphQL centralizada ----------

    def _fazer_requisicao_graphql(self, query, variables=None):
        """Executa a query e devolve o dict de sucesso, ou None em qualquer falha."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        payload_str = json.dumps(payload, separators=(",", ":"))
        headers = self._gerar_headers(payload_str)
        try:
            resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=20)
        except Exception:
            logger.exception("[API SHOPEE] Falha de conexao/timeout")
            return None
        if resp.status_code != 200:
            logger.error("[API SHOPEE] HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        try:
            dados = resp.json()
        except ValueError:
            logger.error("[API SHOPEE] Resposta nao-JSON: %s", resp.text[:300])
            return None
        # GraphQL pode responder HTTP 200 com "errors" e "data": null
        if dados.get("errors"):
            logger.error("[API SHOPEE] Erros GraphQL: %s", dados["errors"])
            return None
        if not dados.get("data"):
            logger.warning("[API SHOPEE] Resposta sem 'data': %s", str(dados)[:300])
            return None
        return dados

    # ---------- Sanitizacao da keyword (corrige busca vazia) ----------

    @staticmethod
    def _sanitizar_keyword(keyword):
        """
        Limpa o termo de busca antes de mandar para a API.

        PROBLEMA REAL: o usuario cola o TITULO COMPLETO do produto (ex.:
        "Calcinha de Emagrecimento / Cinta Modeladora / Roupa Intima / Afina
        a Barriga Leggings Calca Feminina 2024") e a API retorna 0 resultados.

        Estrategia:
          1. Pega so a parte ANTES da primeira barra "/" (titulos colados
             usam barras para separar variacoes).
          2. Remove numeros de ano, pontuacao e palavras genericas.
          3. Limita a 4 palavras principais.
          4. Se sobrar vazio, devolve "" (lista geral).
        """
        if not keyword or not keyword.strip():
            return ""
        texto = keyword.strip()
        # 1. Corta na primeira barra (variacoes do titulo colado)
        if "/" in texto:
            texto = texto.split("/")[0]
        # 2. Remove anos e pontuacao/simbolos
        texto = re.sub(r"\b(19|20)\d{2}\b", " ", texto)
        texto = re.sub(r"[^\w\sÀ-ÿ]", " ", texto)
        # 3. Remove palavras genericas e mantem as principais
        palavras = [
            p for p in texto.split()
            if p.lower() not in PALAVRAS_RUIDO and len(p) > 2
        ]
        # 4. Limita a 4 palavras
        palavras = palavras[:4]
        if not palavras:
            return ""
        return " ".join(palavras)

    # ---------- Conversao de vendas ----------

    @staticmethod
    def _converter_vendas(vendas):
        """Converte '34 mil', '27,1mil', '1.2k', '1.200', int ou float para int."""
        if vendas is None:
            return 0
        if isinstance(vendas, (int, float)):
            return int(vendas)
        texto = str(vendas).strip().lower().replace(" ", "")
        multiplicador = 1
        if texto.endswith("mil"):
            multiplicador = 1000
            texto = texto[:-3]
        elif texto.endswith("k"):
            multiplicador = 1000
            texto = texto[:-1]
        elif texto.endswith("m"):
            multiplicador = 1000000
            texto = texto[:-1]
        # separador de milhar pt-BR sem sufixo: "1.200" -> 1200
        if multiplicador == 1 and "," not in texto and texto.count(".") >= 1:
            partes = texto.split(".")
            if all(p.isdigit() for p in partes):
                return int("".join(partes))
        try:
            return int(Decimal(texto.replace(",", ".")) * multiplicador)
        except Exception:
            return 0

    # ---------- Busca de UMA pagina ----------

    def _buscar_pagina(self, page=1, limite=LIMITE_PADRAO, keyword=""):
        """
        sortType 2 = ordenado por MAIS VENDIDOS (ranking do painel).
        keyword vazio = LISTA GERAL de ofertas.
        """
        query = """query ProductOfferV2($keyword: String, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, page: $page, limit: $limit, listType: 0, sortType: 2) {
    nodes {
      itemId
      productCatIds
      productName
      price
      commissionRate
      commission
      sales
      imageUrl
      shopName
      productLink
      offerLink
      ratingStar
      priceDiscountRate
    }
  }
}"""
        variables = {"keyword": keyword, "page": page, "limit": limite}
        dados = self._fazer_requisicao_graphql(query, variables)
        nodes = []
        if dados:
            nodes = (dados.get("data") or {}).get("productOfferV2", {}).get("nodes") or []
        for node in nodes:
            node["sales"] = self._converter_vendas(node.get("sales"))
        return nodes

    # ---------- Busca multiplas paginas + ranking (metodo principal) ----------

    def buscar_mais_vendidos(self, nicho="", total_desejado=100,
                             limite_por_pagina=LIMITE_PADRAO):
        """Junta varias paginas, deduplica por itemId, ordena por vendas.
        Sanitiza a keyword e, se a busca retornar vazio, cai na lista geral."""
        keyword = self._sanitizar_keyword(nicho)
        produtos_por_id = {}
        paginas = min(MAX_PAGINAS, max(1, -(-total_desejado // limite_por_pagina)))

        def coletar_paginas(keyword_busca):
            """Coleta as paginas de uma keyword e devolve o dict de produtos."""
            coletados = {}
            for page in range(1, paginas + 1):
                try:
                    nodes = self._buscar_pagina(page=page, limite=limite_por_pagina,
                                                keyword=keyword_busca)
                except Exception:
                    logger.exception("[SHOPEE] Falha ao buscar pagina %s", page)
                    nodes = []
                if not nodes:
                    break
                for node in nodes:
                    item_id = str(node.get("itemId") or "")
                    if item_id and item_id not in coletados:
                        coletados[item_id] = node
                if page < paginas:
                    time.sleep(PAUSA_ENTRE_PAGINAS)
                if len(nodes) < limite_por_pagina:
                    break
            return coletados

        # 1a tentativa: com a keyword sanitizada
        produtos_por_id = coletar_paginas(keyword)

        # FALLBACK: busca vazia -> tenta a LISTA GERAL (keyword vazia).
        # Assim o usuario nunca mais ve "Nenhum produto" sem motivo,
        # mesmo colando um titulo gigante.
        if not produtos_por_id and keyword:
            logger.info("[SHOPEE] Busca '%s' vazia — tentando lista geral", keyword)
            produtos_por_id = coletar_paginas("")

        produtos = list(produtos_por_id.values())
        produtos.sort(key=lambda p: p.get("sales") or 0, reverse=True)

        # Diagnostico: categorias presentes no resultado (um unico ponto)
        contagem_cats = Counter(
            c for p in produtos for c in (p.get("productCatIds") or [])
        )
        logger.info("[CATS] nicho=%r sanitizado=%r total=%d cats=%s",
                    nicho, keyword, len(produtos),
                    dict(contagem_cats.most_common(10)))

        return produtos[:total_desejado]

    # ---------- Compatibilidade (view antiga) ----------

    def buscar_produtos(self, nicho="", limite=LIMITE_PADRAO, page=1):
        produtos = self._buscar_pagina(page=page, limite=limite,
                                       keyword=self._sanitizar_keyword(nicho))
        return {"data": {"productOfferV2": {"nodes": produtos}}}

    # ---------- Link curto de afiliado ----------

    def gerar_link_afiliado(self, link_original):
        query = """mutation GenerateShortLink($input: ShortLinkInput!) {
  generateShortLink(input: $input) {
    shortLink
  }
}"""
        variables = {"input": {"originUrl": link_original}}
        res = self._fazer_requisicao_graphql(query, variables)
        if res:
            short = (res.get("data") or {}).get("generateShortLink", {}).get("shortLink")
            if short:
                return {"data": {"generateShortLink": {"shortLink": short}}}
        logger.warning("[AVISO SHOPEE]: Falha ao gerar link curto. Usando original.")
        return {"data": {"generateShortLink": {"shortLink": link_original}}}

    # ---------- Salvamento no banco (historico) ----------

    def salvar_produtos(self, nodes, nicho):
        """
        CONVENCAO: a API devolve commissionRate como FRACAO (0.26 = 26%).
        O banco/dashboard trabalha com PERCENTUAL (26.0).
        """
        from .models import ProdutoValidado

        criados = 0
        atualizados = 0
        for node in nodes:
            preco = Decimal(str(node.get("price") or 0))
            taxa = Decimal(str(node.get("commissionRate") or 0))
            percentual = (taxa * 100).quantize(Decimal("0.01"))
            _, was_created = ProdutoValidado.objects.update_or_create(
                item_id=str(node.get("itemId")),
                defaults={
                    "nome": node.get("productName") or "Produto sem nome",
                    "nicho": nicho,
                    "imagem_url": node.get("imageUrl") or "",
                    "link_original": node.get("productLink") or "",
                    "link_afiliado": node.get("offerLink") or "",
                    "preco": preco,
                    "comissao_percentual": percentual,
                    "vendas": self._converter_vendas(node.get("sales")),
                    "comissao_estimada": round(preco * percentual / 100, 2),
                },
            )
            criados += int(was_created)
            atualizados += int(not was_created)
        return {"criados": criados, "atualizados": atualizados}