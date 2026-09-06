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

def rodar_query(query, app_id, secret, rotulo):
    print()
    print("=" * 60)
    print(rotulo)
    print("=" * 60)
    payload = {"query": query}
    payload_str = json.dumps(payload, separators=(",", ":"))
    headers = montar_header(payload_str, app_id, secret)
    try:
        resp = requests.post(API_URL, data=payload_str, headers=headers, timeout=15)
        print("Status HTTP:", resp.status_code)
        print("Resposta da API:")
        print(resp.text[:2000])
        return resp
    except Exception as e:
        print("Erro de conexao:", e)
        return None

def main():
    app_id = settings.SHOPEE_APP_ID
    secret = settings.SHOPEE_SECRET

    print("=" * 60)
    print("1) Credenciais carregadas do .env")
    print("=" * 60)
    print("App ID carregado:", repr(app_id))
    print("Secret carregado:", "SIM" if secret else "NAO - VAZIO!")
    print("Tamanho do secret:", len(secret) if secret else 0)

    # TESTE 1: query simples (igual a que ja funcionou) com o termo que falha no site
    query_simples = 'query { productOfferV2(keyword: "calcinha de emagrecimento", page: 1, limit: 5) { nodes { itemId productName } } }'
    rodar_query(query_simples, app_id, secret, "2) TESTE 1 - Query SIMPLES com 'calcinha de emagrecimento'")

    # TESTE 2: query completa com campos de preco, comissao, vendas e imagem
    query_completa = '''query {
  productOfferV2(keyword: "calcinha de emagrecimento", page: 1, limit: 5) {
    nodes {
      itemId
      productName
      price
      commissionRate
      sales
      imageUrl
    }
  }
}'''
    rodar_query(query_completa, app_id, secret, "3) TESTE 2 - Query COMPLETA (price, commissionRate, sales, imageUrl)")

if __name__ == "__main__":
    main()