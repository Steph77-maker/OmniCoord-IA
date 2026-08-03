#!/bin/bash
# Se déplace automatiquement dans le dossier du script
cd "$(dirname "$0")"

# Lance l'application avec la bonne version de Python où Streamlit est installé
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m streamlit run app.py