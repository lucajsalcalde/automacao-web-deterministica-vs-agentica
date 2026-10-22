@echo off
REM ============================================================================
REM Refaz o login do Claude usado pelo agente da coleta (conta da assinatura).
REM Use quando o executar_coleta2.bat parar pedindo login.
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Nao encontrei o ambiente virtual .venv nesta pasta.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" executar_coleta2.py --login
echo.
pause
