from django.db import models

class ProdutoValidado(models.Model):
    item_id = models.CharField(max_length=100, unique=True)
    nome = models.CharField(max_length=255)
    nicho = models.CharField(max_length=100)
    imagem_url = models.URLField(max_length=500, blank=True)
    link_original = models.URLField(max_length=500, blank=True)
    link_afiliado = models.URLField(max_length=500, blank=True)
    prompt_video_ia = models.TextField(blank=True, default='')
    em_vitrine = models.BooleanField(default=False)

    # Novos campos de inteligencia de vendas
    preco = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    comissao_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    vendas = models.IntegerField(null=True, blank=True)
    comissao_estimada = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

class GrupoDivulgacao(models.Model):
    nome = models.CharField(max_length=150)
    nicho = models.CharField(max_length=100)
    link_grupo = models.URLField(max_length=500)
    plataforma = models.CharField(max_length=50, default='Facebook')
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nome} ({self.plataforma})"

    
# ============================================================
# HISTÓRICO DE POSTS — registra cada postagem para o contador
# diário e o histórico de consistência (Melhoria 1)
# ============================================================
class PostRegistro(models.Model):
    item_id = models.CharField(max_length=64, db_index=True)
    nome = models.CharField(max_length=255, blank=True, default='')
    data_postagem = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-data_postagem']

    def __str__(self):
        return f"{self.nome} - {self.data_postagem.strftime('%d/%m/%Y %H:%M')}"
