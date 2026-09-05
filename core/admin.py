from django.contrib import admin
from .models import ProdutoValidado, GrupoDivulgacao

@admin.register(ProdutoValidado)
class ProdutoValidadoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'nicho', 'item_id', 'criado_em')
    search_fields = ('nome', 'nicho', 'item_id')
    list_filter = ('nicho',)

@admin.register(GrupoDivulgacao)
class GrupoDivulgacaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'plataforma', 'nicho')
    search_fields = ('nome', 'nicho', 'plataforma')
    list_filter = ('plataforma', 'nicho')