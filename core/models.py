from django.db import models

class ProdutoValidado(models.Model):
    item_id = models.CharField(max_length=100, unique=True, verbose_name="ID do item")
    nome = models.CharField(max_length=255, verbose_name="Nome do produto")
    nicho = models.CharField(max_length=100, blank=True, default='', db_index=True, verbose_name="Nicho")
    categoria = models.CharField(max_length=100, blank=True, default='', db_index=True, verbose_name="Categoria")
    imagem_url = models.URLField(max_length=500, blank=True, verbose_name="URL da imagem")
    link_original = models.URLField(max_length=500, blank=True, verbose_name="Link original")
    link_afiliado = models.URLField(max_length=500, blank=True, verbose_name="Link de afiliado")
    prompt_video_ia = models.TextField(blank=True, default='', verbose_name="Prompt de vídeo gerado pela IA")
    em_vitrine = models.BooleanField(default=False, verbose_name="Está na vitrine")

    # Inteligência de vendas
    preco = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Preço (R$)")
    comissao_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Comissão (%)")
    vendas = models.IntegerField(null=True, blank=True, db_index=True, verbose_name="Número de vendas")
    comissao_estimada = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Comissão estimada (R$)")

    atualizado_em = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")

    class Meta:
        verbose_name = "Produto validado"
        verbose_name_plural = "Produtos validados"
        indexes = [
            models.Index(fields=['nicho', 'vendas'], name='idx_nicho_vendas'),
            models.Index(fields=['atualizado_em'], name='idx_atualizado_em'),
        ]

    def __str__(self):
        return self.nome

class GrupoDivulgacao(models.Model):
    nome = models.CharField(max_length=150, verbose_name="Nome do grupo")
    nicho = models.CharField(max_length=100, verbose_name="Nicho")
    link_grupo = models.URLField(max_length=500, unique=True, verbose_name="Link do grupo")
    plataforma = models.CharField(max_length=50, default='Facebook', verbose_name="Plataforma")
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")

    class Meta:
        verbose_name = "Grupo de divulgação"
        verbose_name_plural = "Grupos de divulgação"
        ordering = ['nome']
        constraints = [
            models.UniqueConstraint(fields=['nome', 'plataforma'], name='uniq_grupo_plataforma'),
        ]

    def __str__(self):
        return f"{self.nome} ({self.plataforma})"

# ============================================================
# HISTÓRICO DE POSTS — registra cada postagem para o contador
# diário e o histórico de consistência (Melhoria 1)
# ============================================================
class PostRegistro(models.Model):
    item_id = models.CharField(max_length=100, db_index=True, verbose_name="ID do item")
    nome = models.CharField(max_length=255, blank=True, default='', verbose_name="Nome do produto")
    data_postagem = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Data da postagem")

    class Meta:
        verbose_name = "Registro de post"
        verbose_name_plural = "Registros de posts"
        ordering = ['-data_postagem']
        indexes = [
            models.Index(fields=['item_id', 'data_postagem'], name='idx_item_data'),
        ]

    def __str__(self):
        return f"{self.nome} - {self.data_postagem.strftime('%d/%m/%Y %H:%M')}"

    @classmethod
    def quantidade_hoje(cls):
        """Retorna quantos posts foram feitos hoje (usado no contador da dashboard)."""
        from django.utils import timezone
        hoje = timezone.localdate()
        return cls.objects.filter(data_postagem__date=hoje).count()

# ============================================================
# SNAPSHOT DE VENDAS — histórico a cada busca para calcular
# o badge "🔥 Em alta" (+vendas e +posições) (Melhoria 2)
# ============================================================
class SnapshotVendas(models.Model):
    item_id = models.CharField(max_length=100, db_index=True)
    vendas = models.IntegerField(null=True, blank=True)
    posicao = models.IntegerField(null=True, blank=True)
    capturado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Snapshot de vendas"
        verbose_name_plural = "Snapshots de vendas"
        ordering = ['-capturado_em']
        indexes = [
            models.Index(fields=['item_id', 'capturado_em'], name='idx_snap_item_data'),
        ]