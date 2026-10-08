"""Testes das views de vídeo (precisam do Django): python manage.py test core"""
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import video_ia
from .models import VideoGerado


class IniciarVideoTest(TestCase):
    def _post(self, **extra):
        dados = {'item_id': '111', 'nome': 'Organizador', 'imagem_url': 'http://x/i.jpg', 'legenda': 'Achei!'}
        dados.update(extra)
        return self.client.post('/api/video/iniciar/', dados)

    @mock.patch('core.video_ia.iniciar_geracao')
    def test_inicia_e_devolve_id(self, iniciar):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['ok'])
        iniciar.assert_called_once()

    @mock.patch('core.video_ia.iniciar_geracao')
    def test_clique_duplo_nao_gera_duas_vezes(self, iniciar):
        self._post()
        r = self._post()
        self.assertTrue(r.json().get('reaproveitado'))
        self.assertEqual(iniciar.call_count, 1)
        self.assertEqual(VideoGerado.objects.count(), 1)

    @override_settings(VEO_LIMITE_DIARIO=1)
    @mock.patch('core.video_ia.iniciar_geracao')
    def test_trava_de_custo_diaria(self, iniciar):
        VideoGerado.objects.create(item_id='999', nome='Outro', status='pronto')
        r = self._post()
        self.assertEqual(r.status_code, 429)
        iniciar.assert_not_called()

    @mock.patch('core.video_ia.iniciar_geracao', side_effect=video_ia.VideoErro('Sem imagem'))
    def test_erro_nao_conta_no_limite(self, _):
        r = self._post()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(VideoGerado.objects.get().status, 'erro')
        self.assertEqual(video_ia.videos_hoje(), 0)

    def test_item_id_obrigatorio(self):
        self.assertEqual(self.client.post('/api/video/iniciar/', {}).status_code, 400)


class ServirVideoTest(TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.conteudo = bytes(range(256)) * 40          # 10240 bytes
        (Path(self.pasta) / 'v.mp4').write_bytes(self.conteudo)
        self.video = VideoGerado.objects.create(item_id='1', nome='Teste', status='pronto', arquivo='v.mp4')

    def test_range_206_para_iphone(self):
        with override_settings(VIDEOS_DIR=self.pasta):
            r = self.client.get(f'/video/{self.video.pk}/ver/', HTTP_RANGE='bytes=100-199')
            self.assertEqual(r.status_code, 206)
            self.assertEqual(b''.join(r.streaming_content), self.conteudo[100:200])
            self.assertEqual(r['Content-Range'], f'bytes 100-199/{len(self.conteudo)}')

    def test_sem_range_200_e_download(self):
        with override_settings(VIDEOS_DIR=self.pasta):
            r = self.client.get(f'/video/{self.video.pk}/baixar/')
            self.assertEqual(r.status_code, 200)
            self.assertIn('attachment', r['Content-Disposition'])

    def test_arquivo_sumiu_devolve_410(self):
        with override_settings(VIDEOS_DIR=tempfile.mkdtemp()):
            self.assertEqual(self.client.get(f'/video/{self.video.pk}/ver/').status_code, 410)

    def test_status_de_video_inexistente(self):
        self.assertEqual(self.client.get('/api/video/status/9999/').status_code, 404)
