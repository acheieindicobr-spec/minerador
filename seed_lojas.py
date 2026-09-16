from core.models import LojaAltaComissao

LOJAS = [
    ('Xiaomi', 12, 'xtra'), ('Samsung', 10, 'xtra'), ('Philips', 9, 'xtra'),
    ('Intelbras', 8, 'xtra'), ('Positivo', 10, 'xtra'), ('Multilaser', 9, 'xtra'),
    ('Mondial', 8, 'xtra'), ('Midea', 10, 'xtra'), ('LG', 9, 'xtra'),
    ('Electrolux', 8, 'xtra'),
    ('Cristal Elegance', 78, 'bonus'), ('Sanja Bijus', 53, 'bonus'),
    ('Brincalhão Oficial', 48, 'bonus'), ('Lar e Decor', 33, 'bonus'),
    ('Van Hout', 33, 'bonus'), ('Lalouí Cosmetics', 33, 'bonus'),
    ('Lojas Kingdom', 33, 'bonus'), ('Gato Preto Arte Laser', 28, 'bonus'),
    ('Mangueart', 28, 'bonus'), ('Izzom Shop', 28, 'bonus'),
]

criadas = 0
for nome, comissao, tipo in LOJAS:
    _, foi_criada = LojaAltaComissao.objects.get_or_create(
        nome=nome,
        defaults={'comissao_maxima': comissao, 'tipo': tipo},
    )
    if foi_criada:
        criadas += 1

print(f'Pronto! {criadas} lojas criadas. Total no banco: {LojaAltaComissao.objects.count()}')