# Portão de senha: protege TODAS as páginas do site.
import base64
import hmac
import os

from django.http import HttpResponse

class SenhaAcessoMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Lê a senha definida no Render (variável de ambiente)
        senha_correta = os.environ.get("SENHA_ACESSO", "").strip()

        # Se nenhuma senha foi definida ainda, o site fica aberto
        # (isso evita você ficar trancado de fora durante os testes)
        if not senha_correta:
            return self.get_response(request)

        autorizado = False
        cabecalho = request.META.get("HTTP_AUTHORIZATION", "")

        if cabecalho.startswith("Basic "):
            try:
                decodificado = base64.b64decode(cabecalho[6:]).decode("utf-8")
                _, senha_digitada = decodificado.split(":", 1)
                autorizado = hmac.compare_digest(senha_digitada, senha_correta)
            except Exception:
                autorizado = False

        if not autorizado:
            resposta = HttpResponse("Acesso restrito.", status=401)
            resposta["WWW-Authenticate"] = 'Basic realm="Sharefy - acesso restrito"'
            return resposta

        return self.get_response(request)