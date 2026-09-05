"""
Descobre o valor CRU de commissionRate que a API entrega.
Rodar: python teste_comissao.py
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meu_sharefy.settings")
django.setup()

from core.shopee_api import ShopeeService  # noqa: E402

s = ShopeeService()
produtos = s.buscar_mais_vendidos(nicho="", total_desejado=20)

print(f"Total recebido: {len(produtos)}\n")
for p in produtos[:10]:
    print("-", (p.get("productName") or "")[:45])
    print("   commissionRate CRU da API:", repr(p.get("commissionRate")))
    print("   price CRU:", repr(p.get("price")), "| sales:", p.get("sales"))