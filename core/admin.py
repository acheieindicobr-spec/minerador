from django.contrib import admin
from .models import (Divulgacao, GrupoDivulgacao, LojaAltaComissao, PostRegistro,
                     ProdutoValidado, SnapshotVendas, VideoGerado)

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


@admin.register(LojaAltaComissao)
class LojaAltaComissaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'tipo', 'comissao_maxima', 'link_loja')
    list_editable = ('comissao_maxima', 'link_loja')  # ajusta a comissão sem mexer no código
    list_filter = ('tipo',)
    search_fields = ('nome',)


@admin.register(Divulgacao)
class DivulgacaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'plataforma', 'item_id', 'criado_em')
    list_filter = ('plataforma',)
    search_fields = ('nome', 'item_id')


@admin.register(PostRegistro)
class PostRegistroAdmin(admin.ModelAdmin):
    list_display = ('nome', 'item_id', 'data_postagem')
    search_fields = ('nome', 'item_id')


@admin.register(SnapshotVendas)
class SnapshotVendasAdmin(admin.ModelAdmin):
    list_display = ('item_id', 'vendas', 'posicao', 'capturado_em')
    search_fields = ('item_id',)


@admin.register(VideoGerado)
class VideoGeradoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'status', 'modelo', 'duracao', 'custo_estimado_usd', 'criado_em')
    list_filter = ('status', 'modelo')
    search_fields = ('nome', 'item_id')
    readonly_fields = ('operation_name', 'arquivo', 'prompt', 'erro')
