"""
Integracao com a API de Afiliados da Shopee (GraphQL).
Busca a LISTA GERAL de ofertas, ordena por MAIS VENDIDOS e junta varias paginas.
"""
import hashlib
import json
import logging
import os
import time
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

class ShopeeService:
    def __init__(self):
        self.app_id = getattr(settings, 'SHOPEE_APP_ID', None) or os.getenv('SHOPEE_APP_ID')
        self.secret = getattr(settings, 'SHOPEE_SECRET', None) or os.getenv('SHOPEE_SECRET')
        if not self.app_id or not self.secret:
            raise ValueError("SHOPEE_APP_ID e/ou SHOPEE_SECRET nao configurados.")

    # ---------- Assinatura ----------

    def _gerar_headers(self, payload_str):
        timestamp = int(time.time())
        factor = f"{self.app_id}{timestamp}{payload_str}{self.secret}"
        signature = hashlib.sha256(factor.encode('utf-8')).hexdigest()
        return {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={timestamp}, Signature={signature}"
        }

    def _fazer_requisicao_graphql(self, query, variables=None):
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        payload_str = json.dumps(payload, separators=(',', ':'))
        headers = self._gerar_headers(payload_str)
        try:
            resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.error("[ERRO API SHOPEE] Status %s: %s", resp.status_code, resp.text[:300])
                return None
            data = resp.json()
            if "errors" in data:
                logger.error("[ERRO API SHOPEE GRAPHQL]: %s", data["errors"])
            return data
        except Exception as e:
            logger.exception("[ERRO API GRAPHQL SHOPEE]: %s", e)
            return None

    # ---------- Conversao de "vendas" (ex.: '34 mil', '27,1mil', '1.2k') ----------

    def _converter_vendas(self, vendas):
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
        try:
            return int(Decimal(texto.replace(",", ".")) * multiplicador)
        except Exception:
            return 0

    # ---------- Busca de UMA pagina ----------

    def _buscar_pagina(self, page=1, limite=50, keyword=""):
        """
        Busca UMA pagina da API.
        sortType 2 = ordenado por MAIS VENDIDOS (igual ao ranking do painel).
        keyword vazio = LISTA GERAL de ofertas (onde estao os '34 mil').
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
        if dados and dados.get("data", {}).get("productOfferV2", {}).get("nodes"):
            nodes = dados["data"]["productOfferV2"]["nodes"]
        for node in nodes:
                node["sales"] = self._converter_vendas(node.get("sales"))
        return nodes
        return []

    # ---------- Busca multiplas paginas + ranking (o metodo principal) ----------

    def buscar_mais_vendidos(self, nicho="", total_desejado=100, limite_por_pagina=50):
        """
        Junta varias paginas (ate 100 produtos), sem duplicar,
        ordena por vendas e devolve o ranking. SEM filtros que cortem itens.
        """
        keyword = nicho.strip() if nicho and nicho.strip() else ""

        produtos_por_id = {}
        paginas = min(5, max(1, -(-total_desejado // limite_por_pagina)))

        for page in range(1, paginas + 1):
            nodes = self._buscar_pagina(page=page, limite=limite_por_pagina, keyword=keyword)
            if not nodes:
                break
            # MELHORIA 4 — pausa entre páginas para não parecer robô (anti-bloqueio)
            if page < paginas:
                time.sleep(2)
            for node in nodes:
                item_id = str(node.get("itemId", ""))
                if item_id and item_id not in produtos_por_id:
                    produtos_por_id[item_id] = node
            if len(nodes) < limite_por_pagina:
                break

        produtos = list(produtos_por_id.values())
        produtos.sort(key=lambda p: p.get("sales") or 0, reverse=True)
                # ===== MELHORIA 7 — mapa de categorias do nicho (diagnóstico) =====
        from collections import Counter
        contagem_cats = Counter()
        for p in produtos:
            for c in (p.get("productCatIds") or []):
                contagem_cats[c] += 1
        print(f"[CATS] nicho={nicho!r} | {dict(contagem_cats.most_common(10))}")

               # ===== MELHORIA 11 — busca AMPLA (opção a: não cortar nada) =====
        # O usuário escolheu mostrar TUDO que a API devolveu. O filtro por
        # categoria agora acontece na interface (dropdown), sobre os produtos
        # já buscados — assim nenhum item oportuno se perde.
        # Os catids (productCatIds) já vêm na query e seguem em cada produto
        # para o dropdown funcionar no front.
        if keyword:
            from collections import Counter
            contagem_cats = Counter()
            for p in produtos:
                for c in (p.get("productCatIds") or []):
                    contagem_cats[c] += 1
            print(f"[CATS] nicho={nicho!r} | {dict(contagem_cats.most_common(10))}")
        return produtos[:total_desejado]
         
    # ---------- Compatibilidade (caso a view ainda chame buscar_produtos) ----------

    def buscar_produtos(self, nicho="", limite=50, page=1):
        produtos = self._buscar_pagina(page=page, limite=limite, keyword=nicho.strip() if nicho else "")
        from types import SimpleNamespace
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
        if res and res.get("data", {}).get("generateShortLink", {}).get("shortLink"):
            return res
        logger.warning("[AVISO SHOPEE]: Falha ao gerar link curto. Usando link original.")
        return {"data": {"generateShortLink": {"shortLink": link_original}}}

    # ---------- Salvamento no banco (opcional, para historico) ----------

    def salvar_produtos(self, nodes, nicho):
        from .models import ProdutoValidado

        criados = 0
        atualizados = 0
        for node in nodes:
            preco = Decimal(str(node.get("price") or 0))
            comissao = Decimal(str(node.get("commissionRate") or 0))
            _, was_created = ProdutoValidado.objects.update_or_create(
                item_id=str(node.get("itemId")),
                defaults={
                    "nome": node.get("productName") or "Produto sem nome",
                    "nicho": nicho,
                    "imagem_url": node.get("imageUrl") or "",
                    "link_original": node.get("productLink") or "",
                    "link_afiliado": node.get("offerLink") or "",
                    "preco": preco,
                    "comissao_percentual": comissao,
                    "vendas": self._converter_vendas(node.get("sales")),
                    "comissao_estimada": round(preco * comissao / 100, 2),
                },
            )
            if was_created:
                criados += 1
            else:
                atualizados += 1
        return {"criados": criados, "atualizados": atualizados}