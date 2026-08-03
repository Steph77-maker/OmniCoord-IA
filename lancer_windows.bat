@echo off
echo ===================================================
echo   Lancement de l'Assistant Recrutement sur Windows  
echo ===================================================
echo.
echo Verification et installation des outils necessaires...
pip install -r requirements.txt
echo.
echo Demarrage de l'interface Streamlit...
streamlit run app.py
pause