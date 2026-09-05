from django.urls import path
from . import views

urlpatterns = [
    path('', views.pagina_mineracao, name='home'),  # 🔥 RAIZ -> abre direto a página de produtos
    path('dashboard/', views.pagina_mineracao, name='pagina_mineracao'),
    path('api/salvar-preparar/', views.salvar_e_preparar_produto, name='salvar_e_preparar_produto'),
    path('api/gerar-prompt-shopee/', views.gerar_prompt_shopee, name='gerar_prompt_shopee'),
    path('api/marcar-postado/', views.marcar_postado, name='marcar_postado'),
    path('api/definir-meta/', views.definir_meta, name='definir_meta'),
    path('api/adicionar-vitrine/', views.adicionar_a_vitrine, name='adicionar_a_vitrine'),
    path('vitrine/', views.minha_vitrine, name='minha_vitrine'),
]