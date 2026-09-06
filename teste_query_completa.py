"""
Valida a query EXATA de producao + parametros de paginacao.
Se um campo nao existir no schema, o GraphQL aponta QUAL e o minerador
mostra 'Nenhum produto' por causa disso.
Rodar:  python teste_query_completa.py
"""
import hashlib
import json
import os
import time

import django
import requests

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meu_sharefy.settings")
django.setup()

from django.conf import settings  # noqa: E402

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"

QUERY = """query ProductOfferV2($keyword: String, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, page: $page, limit: $limit, listType: 0, sortType: 2) {
    nodes {
      itemId productCatIds productName price commissionRate commission
      sales imageUrl shopName productLink offerLink ratingStar priceDiscountRate
    }
  }
}"""

def montar_header(payload_str, app_id, secret):
    timestamp = int(time.time())
    factor = f"{app_id}{timestamp}{payload_str}{secret}"
    signature = hashlib.sha256(factor.encode("utf-8")).hexdigest()
    return {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}",
    }

def rodar(keyword, limite):
    payload = {"query": QUERY, "variables": {"keyword": keyword, "page": 1, "limit": limite}}
    payload_str = json.dumps(payload, separators=(",", ":"))
    headers = montar_header(payload_str, settings.SHOPEE_APP_ID, settings.SHOPEE_SECRET)
    resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=20)
    print(f"--- keyword={keyword!r} limit={limite} | HTTP {resp.status_code}")
    print(resp.text[:1500])
    print()

rodar("calcinha de emagrecimento", 5)
rodar("calcinha de emagrecimento", 50)   # confirma se limit=50 e aceito
rodar("", 5)                              # lista geral (sem keyword)