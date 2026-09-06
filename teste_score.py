"""
Verifica se o score de divulgacao esta sendo calculado e se muda a ordem.
Rodar:  python teste_score.py
"""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meu_sharefy.settings")
django.setup()

# Ajuste o import conforme a estrutura do seu projeto:
try:
    from core.services.shopee_api import ShopeeService
except ImportError:
    try:
        from core.shopee_api import ShopeeService
    except ImportError:
        from shopee_api import ShopeeService

service = ShopeeService()

print("=" * 70)
print("ORDENACAO POR SCORE (a nova)")
print("=" * 70)
produtos_score = service.buscar_mais_vendidos(nicho="", total_desejado=10, ordenar_por="score")
for i, p in enumerate(produtos_score, 1):
    comissao = round(float(p.get("commissionRate") or 0) * 100, 2)
    print(f"{i}. score={p.get('score'):>7} | vendas={p.get('sales'):>7} | comissao={comissao:>5}% | {p.get('productName', '')[:45]}")

print()
print("=" * 70)
print("ORDENACAO POR VENDAS (a antiga, para comparar)")
print("=" * 70)
produtos_vendas = service.buscar_mais_vendidos(nicho="", total_desejado=10, ordenar_por="vendas")
for i, p in enumerate(produtos_vendas, 1):
    comissao = round(float(p.get("commissionRate") or 0) * 100, 2)
    print(f"{i}. vendas={p.get('sales'):>7} | comissao={comissao:>5}% | {p.get('productName', '')[:45]}")