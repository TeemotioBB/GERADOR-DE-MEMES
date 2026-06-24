#!/bin/bash
cd "$(dirname "$0")"
echo "Iniciando o Gerador de Posts..."
echo "Abra no navegador: http://localhost:5000"
echo "Para encerrar, aperte Ctrl+C ou feche esta janela."
python3 app.py
