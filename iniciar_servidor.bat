@echo off
title Servidor Minerador de Produtos - meu_sharefy
cd /d "C:\Users\danie\meu_sharefy"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
)

python manage.py runserver 127.0.0.1:8000 --noreload