@echo off
cd /d "%~dp0"
rem Streamlit 버전: http://localhost:8503 (miniPJT 8501, 웹 버전 8502와 겹치지 않게)
py -m streamlit run streamlit_app.py --server.port 8503
pause
