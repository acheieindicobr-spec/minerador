"""
Minera, escolhe os melhores produtos e deixa os vídeos prontos — sem clique nenhum.

    python manage.py gerar_videos_do_dia --quantidade 3

Escolha "inteligente": pega os produtos mais fortes da mineração, descarta o que
já virou vídeo ou já foi postado, e reordena por GANHO ESPERADO
(preço x comissão x popularidade x nota) — não só por vendas.
Respeita a trava VEO_LIMITE_DIARIO.
"""
import time

from django.core.management.base import BaseCommand

from core import video_ia
from core.models import PostRegistro, ProdutoValidado, VideoGerado
from core.shopee_api import ShopeeService


class Command(BaseCommand):
    help = "Gera vídeos prontos para os melhores produtos do dia."

    def add_arguments(self, parser):
        parser.add_argument("--quantidade", type=int, default=3, help="Quantos vídeos gerar (padrão 3).")
        parser.add_argument("--candidatos", type=int, default=60, help="Quantos produtos minerar para escolher.")
        parser.add_argument("--incluir-intimos", action="store_true",
                            help="Inclui moda íntima (o filtro do Google costuma bloquear).")
        parser.add_argument("--sem-esperar", action="store_true",
                            help="Só dispara; os vídeos serão baixados ao abrir /videos/.")

    def handle(self, *args, **opts):
        from core.views import (_p23_legenda_curta, eh_moda_intima,
                                montar_legenda_para_plataforma)

        restante = video_ia.limite_diario() - video_ia.videos_hoje()
        quantidade = min(opts["quantidade"], restante)
        if quantidade <= 0:
            self.stdout.write(self.style.WARNING("Limite diário atingido. Nada a fazer."))
            return

        servico = ShopeeService()
        self.stdout.write("Minerando produtos...")
        nodes = servico.buscar_mais_vendidos(nicho="", total_desejado=opts["candidatos"], ordenar_por="score")

        ja_usados = set(VideoGerado.objects.exclude(status="erro").values_list("item_id", flat=True))
        ja_usados |= set(PostRegistro.objects.values_list("item_id", flat=True))
        candidatos = []
        for n in nodes:
            item_id = str(n.get("itemId") or "")
            if not item_id or item_id in ja_usados or not n.get("imageUrl") or not n.get("productName"):
                continue
            if not opts["incluir_intimos"] and eh_moda_intima(n["productName"]):
                continue
            candidatos.append(n)
        candidatos.sort(key=video_ia.pontuar_candidato, reverse=True)
        escolhidos = candidatos[:quantidade]
        if not escolhidos:
            self.stdout.write(self.style.WARNING("Nenhum candidato novo encontrado."))
            return

        iniciados = []
        for n in escolhidos:
            item_id, nome = str(n["itemId"]), n["productName"]
            servico.salvar_produtos([n], nicho="")
            produto = ProdutoValidado.objects.get(item_id=item_id)
            if n.get("productLink"):
                try:
                    res = servico.gerar_link_afiliado(n["productLink"])
                    curto = (((res or {}).get("data") or {}).get("generateShortLink") or {}).get("shortLink")
                    if curto:
                        produto.link_afiliado = curto
                        produto.save(update_fields=["link_afiliado"])
                except Exception as exc:
                    self.stderr.write(f"  aviso: link curto falhou ({type(exc).__name__})")
            preco = f"{float(n.get('price') or 0):.2f}".replace(".", ",")
            legenda = _p23_legenda_curta(nome, "", preco)
            legenda = montar_legenda_para_plataforma(legenda, [], "shopee")[0]

            video = VideoGerado.objects.create(
                item_id=item_id, nome=nome[:255], imagem_url=n["imageUrl"],
                link_afiliado=produto.link_afiliado, legenda=legenda, status="gerando")
            try:
                video_ia.iniciar_geracao(video)
                iniciados.append(video)
                self.stdout.write(self.style.SUCCESS(f"  iniciado: {nome[:60]}"))
            except Exception as exc:  # VideoErro ou qualquer falha inesperada: segue para o próximo
                video.status, video.erro = "erro", str(exc) or type(exc).__name__
                video.save()
                self.stderr.write(self.style.ERROR(f"  falhou: {nome[:50]} — {video.erro}"))

        if opts["sem_esperar"] or not iniciados:
            return
        self.stdout.write("Aguardando o Google terminar (até 12 min)...")
        limite = time.time() + 12 * 60
        pendentes = list(iniciados)
        while pendentes and time.time() < limite:
            time.sleep(20)
            for v in list(pendentes):
                video_ia.verificar_geracao(v)
                if v.status != "gerando":
                    pendentes.remove(v)
                    marca = self.style.SUCCESS("pronto") if v.status == "pronto" else self.style.ERROR(v.erro)
                    self.stdout.write(f"  {v.nome[:50]}: {marca}")
        self.stdout.write(self.style.SUCCESS("Concluído. Veja em /videos/"))
