from django.urls import path
from . import views, views_video

urlpatterns = [
    path('', views.pagina_mineracao, name='home'),  # 🔥 RAIZ -> abre direto a página de produtos
    path('dashboard/', views.pagina_mineracao, name='pagina_mineracao'),
    path('api/salvar-preparar/', views.salvar_e_preparar_produto, name='salvar_e_preparar_produto'),
    path('api/gerar-prompt-shopee/', views.gerar_prompt_shopee, name='gerar_prompt_shopee'),
    path('api/marcar-postado/', views.marcar_postado, name='marcar_postado'),
    path('api/definir-meta/', views.definir_meta, name='definir_meta'),
    path('api/adicionar-vitrine/', views.adicionar_a_vitrine, name='adicionar_a_vitrine'),
    path('vitrine/', views.minha_vitrine, name='minha_vitrine'),
    # ===== FASE P0 — R-95 (painel de divulgações) =====
    path('divulgacoes/', views.minhas_divulgacoes, name='minhas_divulgacoes'),
    path('vender-sem-aparecer/', views.pagina_vender_sem_aparecer, name='vender_sem_aparecer'),
    path('comissoes/', views.pagina_escolinha_comissao, name='escolinha_comissoes'),
    path('api/registrar-divulgacao/', views.registrar_divulgacao, name='registrar_divulgacao'),
    path('produto_detalhes/<int:produto_id>/', views.produto_detalhes, name='produto_detalhes'),
    path('gerar_conteudo/', views.gerar_conteudo, name='gerar_conteudo'),
    # ===== VÍDEO PRONTO EM 1 CLIQUE (Veo) =====
    path('api/video/iniciar/', views_video.iniciar_video, name='iniciar_video'),
    path('api/video/status/<int:video_id>/', views_video.status_video, name='status_video'),
    path('video/<int:video_id>/ver/', views_video.ver_video, name='ver_video'),
    path('video/<int:video_id>/baixar/', views_video.baixar_video, name='baixar_video'),
    path('videos/', views_video.videos_prontos, name='videos_prontos'),
]