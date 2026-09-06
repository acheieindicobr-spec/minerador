"""
Teste decisivo: sera que o TERMO GIGANTE (titulo colado) e o culpado?
Compara o termo exato da tela que falhou com versoes curtas.
Rodar:  python teste_termo_exato.py
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
    nodes { itemId productName sales commissionRate }
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

def rodar(keyword):
    payload = {"query": QUERY, "variables": {"keyword": keyword, "page": 1, "limit": 5}}
    payload_str = json.dumps(payload, separators=(",", ":"))
    headers = montar_header(payload_str, settings.SHOPEE_APP_ID, settings.SHOPEE_SECRET)
    resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=20)
    try:
        dados = resp.json()
        nodes = (dados.get("data") or {}).get("productOfferV2", {}).get("nodes") or []
        qtd = len(nodes)
    except Exception:
        qtd = "ERRO ao ler resposta"
    print(f"KEYWORD: {keyword[:70]}...")
    print(f"  -> produtos retornados: {qtd}")
    if isinstance(qtd, int) and qtd > 0:
        for n in nodes[:3]:
            print(f"     - {n.get('productName', '')[:60]} | vendas: {n.get('sales')}")
    print()

rodar("Calcinha de Emagrecimento / Cinta Modeladora / Roupa Íntima / Afina a Barriga Leggings Calça Feminina 2024")
rodar("Calcinha de Emagrecimento")
rodar("calcinha de emagrecimento")
rodar("cinta modeladora")