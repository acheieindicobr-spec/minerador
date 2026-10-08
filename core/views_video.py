"""
Views do "Vídeo pronto em 1 clique".
Rotas (ver urls.py):
  POST /api/video/iniciar/            dispara a geração
  GET  /api/video/status/<id>/        consulta (e baixa quando terminar)
  GET  /video/<id>/ver/               toca no navegador (com suporte a Range p/ iPhone)
  GET  /video/<id>/baixar/            baixa o .mp4
  GET  /videos/                       fila "Vídeos prontos para postar"
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from . import video_ia
from .models import ProdutoValidado, VideoGerado

logger = logging.getLogger(__name__)


def _json_video(video):
    dados = {
        'ok': True,
        'video_id': video.pk,
        'status': video.status,
        'erro': video.erro,
        'nome': video.nome,
        'legenda': video.legenda,
        'link_afiliado': video.link_afiliado,
        'custo_estimado_usd': str(video.custo_estimado_usd) if video.custo_estimado_usd is not None else None,
    }
    if video.status == 'pronto':
        dados['url_video'] = reverse('ver_video', args=[video.pk])
        dados['url_download'] = reverse('baixar_video', args=[video.pk])
    return dados


@require_POST
def iniciar_video(request):
    item_id = request.POST.get('item_id', '').strip()
    if not item_id:
        return JsonResponse({'ok': False, 'erro': 'item_id é obrigatório.'}, status=400)

    produto = ProdutoValidado.objects.filter(item_id=item_id).first()
    nome = (request.POST.get('nome') or (produto.nome if produto else '')).strip()
    imagem = request.POST.get('imagem_url') or (produto.imagem_url if produto else '')
    link = request.POST.get('link_afiliado') or (produto.link_afiliado if produto else '')
    legenda = (request.POST.get('legenda') or '').strip()
    forcar = request.POST.get('forcar') == '1'

    # Anti-clique-duplo: se já tem um job rodando para este produto, reaproveita.
    limite_recente = timezone.now() - timedelta(minutes=video_ia.MINUTOS_MAX_GERANDO)
    existente = (VideoGerado.objects.filter(item_id=item_id, status='gerando', criado_em__gte=limite_recente).first()
                 or (None if forcar else VideoGerado.objects.filter(item_id=item_id, status='pronto').first()))
    if existente:
        dados = _json_video(existente)
        dados['reaproveitado'] = True
        return JsonResponse(dados)

    if video_ia.videos_hoje() >= video_ia.limite_diario():
        return JsonResponse({
            'ok': False,
            'erro': f'Limite diário de {video_ia.limite_diario()} vídeos atingido (trava de custo). '
                    f'Ajuste VEO_LIMITE_DIARIO se quiser gerar mais.',
        }, status=429)

    video = VideoGerado.objects.create(
        item_id=item_id, nome=nome[:255] or 'Produto', imagem_url=imagem,
        link_afiliado=link, legenda=legenda, status='gerando')
    try:
        video_ia.iniciar_geracao(video)
    except video_ia.VideoErro as exc:
        video.status, video.erro = 'erro', str(exc)
        video.save()
        return JsonResponse({'ok': False, 'erro': str(exc)}, status=502)
    except Exception:
        logger.exception('[VIDEO] erro inesperado ao iniciar item %s', item_id)
        video.status, video.erro = 'erro', 'Erro inesperado ao iniciar o vídeo.'
        video.save()
        return JsonResponse({'ok': False, 'erro': video.erro}, status=500)
    return JsonResponse(_json_video(video))


@require_GET
def status_video(request, video_id):
    video = VideoGerado.objects.filter(pk=video_id).first()
    if not video:
        return JsonResponse({'ok': False, 'erro': 'Vídeo não encontrado.'}, status=404)
    if video.status == 'gerando':
        try:
            video_ia.verificar_geracao(video)
        except Exception:
            logger.exception('[VIDEO] erro ao verificar vídeo %s', video_id)
    return JsonResponse(_json_video(video))


def _ler_trecho(caminho, inicio, fim, bloco=256 * 1024):
    with open(caminho, 'rb') as arquivo:
        arquivo.seek(inicio)
        restante = fim - inicio + 1
        while restante > 0:
            dados = arquivo.read(min(bloco, restante))
            if not dados:
                break
            restante -= len(dados)
            yield dados


def _servir(request, video_id, baixar):
    video = VideoGerado.objects.filter(pk=video_id, status='pronto').first()
    if not video or not video.arquivo:
        return HttpResponse('Vídeo não encontrado.', status=404)
    caminho = video_ia.pasta_videos() / video.arquivo
    if not caminho.exists():
        return HttpResponse(
            'O arquivo não existe mais neste servidor (disco temporário?). Gere o vídeo de novo.', status=410)

    nome_arquivo = f"{slugify(video.nome)[:60] or 'video'}.mp4"
    tamanho = caminho.stat().st_size
    intervalo = None if baixar else video_ia.interpretar_range(request.headers.get('Range'), tamanho)

    if intervalo == 'invalido':
        resposta = HttpResponse(status=416)
        resposta['Content-Range'] = f'bytes */{tamanho}'
        return resposta
    if intervalo:
        inicio, fim = intervalo
        resposta = StreamingHttpResponse(_ler_trecho(caminho, inicio, fim), status=206, content_type='video/mp4')
        resposta['Content-Range'] = f'bytes {inicio}-{fim}/{tamanho}'
        resposta['Content-Length'] = str(fim - inicio + 1)
    else:
        resposta = FileResponse(open(caminho, 'rb'), content_type='video/mp4', as_attachment=baixar,
                                filename=nome_arquivo)
        resposta['Content-Length'] = str(tamanho)
    resposta['Accept-Ranges'] = 'bytes'
    return resposta


@require_GET
def ver_video(request, video_id):
    return _servir(request, video_id, baixar=False)


@require_GET
def baixar_video(request, video_id):
    return _servir(request, video_id, baixar=True)


@require_GET
def videos_prontos(request):
    """Fila de vídeos prontos para postar (também confere os que ainda estão gerando)."""
    for video in VideoGerado.objects.filter(status='gerando')[:10]:
        try:
            video_ia.verificar_geracao(video)
        except Exception:
            logger.exception('[VIDEO] erro ao verificar vídeo %s', video.pk)

    videos = list(VideoGerado.objects.all()[:60])
    gastos = (VideoGerado.objects.filter(criado_em__gte=timezone.now() - timedelta(days=30))
              .exclude(status='erro').aggregate(total=Sum('custo_estimado_usd'))['total'] or Decimal('0'))
    return render(request, 'core/videos_prontos.html', {
        'videos': videos,
        'tem_gerando': any(v.status == 'gerando' for v in videos),
        'usados_hoje': video_ia.videos_hoje(),
        'limite_dia': video_ia.limite_diario(),
        'gasto_30d': gastos,
    })
