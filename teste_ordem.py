"""
Teste da ORDEM dos produtos (mais vendidos).
Descobre: (1) qual sortType a API usa; (2) se a service ordena por vendas.

Rodar:  python teste_ordem.py
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

QUERY = """query ProductOfferV2($keyword: String, $page: Int, $limit: Int, $sortType: Int) {
  productOfferV2(keyword: $keyword, page: $page, limit: $limit, listType: 0, sortType: $sortType) {
    nodes { itemId productName sales }
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

def buscar_bruto(keyword, sort_type, limite=5):
    payload = {
        "query": QUERY,
        "variables": {"keyword": keyword, "page": 1, "limit": limite, "sortType": sort_type},
    }
    payload_str = json.dumps(payload, separators=(",", ":"))
    headers = montar_header(payload_str, settings.SHOPEE_APP_ID, settings.SHOPEE_SECRET)
    resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=20)
    dados = resp.json()
    nodes = (dados.get("data") or {}).get("productOfferV2", {}).get("nodes") or []
    return [(n.get("sales"), n.get("productName", "")[:50]) for n in nodes]

print("=" * 60)
print("PARTE 1 - Ordem BRUTA da API por sortType (lista geral)")
print("=" * 60)
for st in [0, 1, 2, 3, 4]:
    try:
        itens = buscar_bruto("", st)
        print(f"\nsortType={st}:")
        for i, (vendas, nome) in enumerate(itens, 1):
            print(f"  {i}. vendas={vendas} | {nome}")
    except Exception as e:
        print(f"sortType={st}: ERRO {e}")

print()
print("=" * 60)
print("PARTE 2 - Ordem da SERVICE (buscar_mais_vendidos)")
print("=" * 60)
try:
    from core.services.shopee_api import ShopeeService
except ImportError:
    try:
        from core.shopee_api import ShopeeService
    except ImportError:
        from shopee_api import ShopeeService

service = ShopeeService()
produtos = service.buscar_mais_vendidos(nicho="", total_desejado=20)
print(f"Total retornado: {len(produtos)}")
for i, p in enumerate(produtos[:10], 1):
    print(f"{i}. vendas={p.get('sales')} | {p.get('productName', '')[:60]}")