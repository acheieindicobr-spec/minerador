"""
Descobre TODOS os campos disponiveis no tipo ProductOfferV2 da API.
Se existir um campo de vendas do periodo (ex.: sold30d, trendingScore),
podemos usa-lo para chegar mais perto do ranking da pagina de afiliados.

Rodar:  python teste_schema.py
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

def montar_header(payload_str, app_id, secret):
    timestamp = int(time.time())
    factor = f"{app_id}{timestamp}{payload_str}{secret}"
    signature = hashlib.sha256(factor.encode("utf-8")).hexdigest()
    return {
        "Content-Type": "application/json",
        "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}",
    }

# Consulta 1: campos do tipo ProductOfferV2
query_tipo = """query {
  __type(name: "ProductOfferV2") {
    fields {
      name
      type { name kind ofType { name kind } }
    }
  }
}"""

payload = {"query": query_tipo}
payload_str = json.dumps(payload, separators=(",", ":"))
headers = montar_header(payload_str, settings.SHOPEE_APP_ID, settings.SHOPEE_SECRET)
resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=20)
print("=" * 60)
print("CAMPOS DO TIPO ProductOfferV2:")
print("=" * 60)
print(resp.text[:4000])