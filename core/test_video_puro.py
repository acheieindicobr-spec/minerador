"""Testes sem banco/Django (rodam em qualquer lugar): python -m unittest core.test_video_puro"""
import io
import unittest
from unittest import mock

from core import video_ia


class IntervaloRangeTest(unittest.TestCase):
    def test_sem_header(self):
        self.assertIsNone(video_ia.interpretar_range(None, 1000))
        self.assertIsNone(video_ia.interpretar_range("", 1000))

    def test_intervalos_validos(self):
        self.assertEqual(video_ia.interpretar_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(video_ia.interpretar_range("bytes=500-", 1000), (500, 999))
        self.assertEqual(video_ia.interpretar_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(video_ia.interpretar_range("bytes=0-99999", 1000), (0, 999))   # fim estourado

    def test_iphone_pede_0_1(self):
        self.assertEqual(video_ia.interpretar_range("bytes=0-1", 1000), (0, 1))

    def test_invalidos(self):
        self.assertEqual(video_ia.interpretar_range("bytes=2000-", 1000), "invalido")
        self.assertEqual(video_ia.interpretar_range("bytes=50-10", 1000), "invalido")
        self.assertEqual(video_ia.interpretar_range("bytes=-0", 1000), "invalido")
        self.assertIsNone(video_ia.interpretar_range("lixo", 1000))


class PontuacaoTest(unittest.TestCase):
    def test_ganho_esperado_vence_so_vendas(self):
        barato_popular = {"price": 10, "commissionRate": 0.05, "sales": 50000, "ratingStar": 4.8}
        caro_boa_comissao = {"price": 120, "commissionRate": 0.20, "sales": 3000, "ratingStar": 4.7}
        self.assertGreater(video_ia.pontuar_candidato(caro_boa_comissao),
                           video_ia.pontuar_candidato(barato_popular))

    def test_comissao_em_fracao_ou_percentual_da_igual(self):
        a = {"price": 100, "commissionRate": 0.2, "sales": 100, "ratingStar": 5}
        b = {"price": 100, "commissionRate": 20, "sales": 100, "ratingStar": 5}
        self.assertEqual(video_ia.pontuar_candidato(a), video_ia.pontuar_candidato(b))

    def test_dados_ruins_nao_quebram(self):
        self.assertEqual(video_ia.pontuar_candidato({"price": "abc"}), 0.0)
        self.assertEqual(video_ia.pontuar_candidato({}), 0.0)


class PromptTest(unittest.TestCase):
    def test_guardrails_sempre_presentes_mesmo_se_ia_tentar_afrouxar(self):
        with mock.patch.object(video_ia, "_acao_com_gemini", return_value="Make the product dance and spin into a new shape."):
            prompt = video_ia.montar_prompt_veo("Organizador de cozinha")
        self.assertIn("identical to the first frame", prompt)
        self.assertIn("No people", prompt)
        self.assertIn("no on-screen text", prompt)

    def test_fallback_quando_gemini_falha(self):
        with mock.patch.object(video_ia, "_acao_com_gemini", return_value=""):
            prompt = video_ia.montar_prompt_veo("Garrafa termica")
        self.assertTrue(prompt.startswith(video_ia.ACAO_PADRAO))

    def test_limpar_nome_tira_preco_e_desconto(self):
        self.assertEqual(video_ia.limpar_nome("Fone Bluetooth R$ 32,89 -47% OFF"), "Fone Bluetooth")


class ConfigTest(unittest.TestCase):
    def test_padroes_e_validacao(self):
        with mock.patch.object(video_ia, "_cfg", side_effect=lambda n, p="": {"VEO_MODELO": "xyz", "VEO_DURACAO": "5"}.get(n, p)):
            self.assertEqual(video_ia.escolhas(), ("lite", 8, "720p"))

    def test_1080p_forca_8s(self):
        with mock.patch.object(video_ia, "_cfg", side_effect=lambda n, p="": {"VEO_RESOLUCAO": "1080p", "VEO_DURACAO": "4"}.get(n, p)):
            self.assertEqual(video_ia.escolhas()[1:], (8, "1080p"))

    def test_custo(self):
        self.assertEqual(str(video_ia.custo_estimado("lite", 8)), "0.40")
        self.assertEqual(str(video_ia.custo_estimado("fast", 8)), "0.80")


class ImagemTest(unittest.TestCase):
    def _resp(self, w, h):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
        r = mock.Mock(content=buf.getvalue())
        r.raise_for_status = lambda: None
        return r

    def test_quadro_sai_9_16(self):
        from PIL import Image
        for w, h in [(800, 800), (500, 900), (1200, 600)]:
            with mock.patch.object(video_ia.requests, "get", return_value=self._resp(w, h)):
                jpg = video_ia.preparar_imagem_vertical("http://x/y.png")
            self.assertEqual(Image.open(io.BytesIO(jpg)).size, (720, 1280))

    def test_sem_url_ou_download_falho(self):
        with self.assertRaises(video_ia.VideoErro):
            video_ia.preparar_imagem_vertical("")
        with mock.patch.object(video_ia.requests, "get", side_effect=OSError("x")):
            with self.assertRaises(video_ia.VideoErro):
                video_ia.preparar_imagem_vertical("http://x")

    def test_arquivo_que_nao_e_imagem(self):
        r = mock.Mock(content=b"<html>nao sou imagem</html>")
        r.raise_for_status = lambda: None
        with mock.patch.object(video_ia.requests, "get", return_value=r):
            with self.assertRaises(video_ia.VideoErro):
                video_ia.preparar_imagem_vertical("http://x")


class TraduzirErroTest(unittest.TestCase):
    def test_mensagens(self):
        self.assertIn("conta PAGA", video_ia.traduzir_erro(Exception("403 PERMISSION_DENIED billing")))
        self.assertIn("Limite de uso", video_ia.traduzir_erro(Exception("429 RESOURCE_EXHAUSTED")))
        self.assertIn("seguranca", video_ia.traduzir_erro(Exception("blocked by safety")))


if __name__ == "__main__":
    unittest.main()
