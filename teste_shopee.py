"""
Teste rapido da integracao com a API de Afiliados da Shopee.
Como rodar (na pasta do projeto, com o ambiente ativado):
    python teste_shopee.py
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

def main():
    app_id = settings.SHOPEE_APP_ID
    secret = settings.SHOPEE_SECRET

    print("=" * 60)
    print("1) Credenciais carregadas do .env")
    print("=" * 60)
    print("App ID carregado:", repr(app_id))
    print("Secret carregado:", "SIM" if secret else "NAO - VAZIO!")
    print("Tamanho do secret:", len(secret) if secret else 0)
    if secret:
        print("Pontas do secret:", repr(secret[:4] + "..." + secret[-4:]))

    query = 'query { productOfferV2(keyword: "fone bluetooth", page: 1, limit: 2) { nodes { itemId productName } } }'
    payload = {"query": query}
    payload_str = json.dumps(payload, separators=(",", ":"))

    print()
    print("=" * 60)
    print("2) Enviando requisicao de teste...")
    print("=" * 60)

    headers = montar_header(payload_str, app_id, secret)

    try:
        resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=15)
        print("Status HTTP:", resp.status_code)
        print()
        print("Resposta da API:")
        print(resp.text[:1000])
    except Exception as e:
        print("Erro de conexao:", e)

if __name__ == "__main__":
    main()