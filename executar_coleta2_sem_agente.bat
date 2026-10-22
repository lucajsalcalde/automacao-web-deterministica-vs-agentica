@echo off
REM ============================================================================
REM Coleta 2 em dois blocos: este roda as validacoes e os QUATRO bracos que nao
REM usam a cota do Claude (960 execucoes). As 240 agenticas ficam para depois,
REM com o executar_coleta2.bat. Ver README.md.
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Nao encontrei o ambiente virtual .venv nesta pasta.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" executar_coleta2.py --sem-agente %*
echo.
pause
