from datetime import datetime, timezone as dt_timezone
from unittest import mock

from django.test import TestCase

from .models import PostRegistro
from .views import posts_de_hoje


class PostsDeHojeTest(TestCase):
    def test_conta_posts_do_dia_local_depois_das_21h(self):
        """Regressão: o contador usava meia-noite UTC e zerava às 21h de Brasília."""
        post = PostRegistro.objects.create(item_id="1", nome="Teste")
        PostRegistro.objects.filter(pk=post.pk).update(
            data_postagem=datetime(2026, 10, 8, 23, 0, tzinfo=dt_timezone.utc))  # 20h em Brasília
        agora = datetime(2026, 10, 9, 1, 30, tzinfo=dt_timezone.utc)             # 22h30 em Brasília
        with mock.patch("django.utils.timezone.now", return_value=agora):
            self.assertEqual(posts_de_hoje().count(), 1)
