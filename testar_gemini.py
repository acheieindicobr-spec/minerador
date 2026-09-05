"""
Teste com os modelos NOVOS recomendados pela API Gemini.
Rodar: python testar_gemini.py
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()
chave = os.getenv("GEMINI_API_KEY", "")

if not chave:
    print("ERRO: GEMINI_API_KEY esta vazia no .env")
else:
    for modelo in ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview", "gemini-2.5-flash"]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={chave}"
        payload = {"contents": [{"parts": [{"text": "Responda apenas: FUNCIONOU"}]}]}
        try:
            r = requests.post(url, json=payload, timeout=30)
            print(f"\n=== Modelo: {modelo} -> HTTP {r.status_code} ===")
            print(r.text[:300])
        except Exception as e:
            print(f"\n=== Modelo: {modelo} -> ERRO: {e} ===")