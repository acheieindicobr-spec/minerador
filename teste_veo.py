"""
Teste do video automatico (Veo) — rode ANTES de usar no site.

    python teste_veo.py                 SIMULACAO: monta o quadro 9:16 + prompt. Nao gasta nada.
    python teste_veo.py --gerar         Gera 1 clipe REAL de 4s (Veo Lite, ~US$ 0,20) e baixa.
    python teste_veo.py --item 123456   Usa um produto especifico (senao, o mais recente do banco).
"""
import os
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meu_sharefy.settings")
django.setup()

from core import video_ia  # noqa: E402
from core.models import ProdutoValidado, VideoGerado  # noqa: E402

gerar = "--gerar" in sys.argv
item = sys.argv[sys.argv.index("--item") + 1] if "--item" in sys.argv else None

qs = ProdutoValidado.objects.exclude(imagem_url="")
produto = qs.filter(item_id=item).first() if item else qs.order_by("-atualizado_em").first()
if not produto:
    sys.exit("Nenhum produto com imagem no banco. Abra um produto no minerador antes.")

print("Produto:", produto.nome[:80])
print("Imagem :", produto.imagem_url)

quadro = video_ia.preparar_imagem_vertical(produto.imagem_url)
with open("teste_quadro.jpg", "wb") as f:
    f.write(quadro)
print(f"\nQuadro 9:16 salvo em teste_quadro.jpg ({len(quadro) // 1024} KB) — ABRA E CONFIRA se o produto esta inteiro.")

prompt = video_ia.montar_prompt_veo(produto.nome)
print("\n--- PROMPT ENVIADO AO VEO ---\n" + prompt + "\n")

chave, duracao, resolucao = video_ia.escolhas()
print(f"Config atual: modelo={chave} duracao={duracao}s resolucao={resolucao} "
      f"custo estimado=US$ {video_ia.custo_estimado(chave, duracao)}")

if not gerar:
    print("\n(Simulacao — nada foi enviado. Use --gerar para gerar um clipe real.)")
    sys.exit(0)

os.environ["VEO_MODELO"] = "lite"
os.environ["VEO_DURACAO"] = "4"
print("\nGERANDO de verdade (Lite, 4s, ~US$ 0,20)...")
video = VideoGerado.objects.create(
    item_id=produto.item_id, nome=produto.nome[:255], imagem_url=produto.imagem_url,
    link_afiliado=produto.link_afiliado, status="gerando")
try:
    video_ia.iniciar_geracao(video)
except video_ia.VideoErro as exc:
    sys.exit(f"ERRO: {exc}")

inicio = time.time()
while video.status == "gerando" and time.time() - inicio < 600:
    time.sleep(10)
    video_ia.verificar_geracao(video)
    print(f"  ... {int(time.time() - inicio)}s — {video.status}")

if video.status == "pronto":
    print("\nPRONTO:", video_ia.pasta_videos() / video.arquivo)
else:
    print("\nNAO FINALIZOU:", video.status, video.erro)
