@echo off
REM ============================================================================
REM Coleta 2 inteira, de uma vez: validacoes, coleta, auditoria, analises e git.
REM Pode fechar a janela e rodar de novo a qualquer momento: o script continua
REM de onde parou. Andamento:  executar_coleta2.bat --status
REM Detalhes no README.md e em executar_coleta2.py.
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Nao encontrei o ambiente virtual .venv nesta pasta.
    echo Crie com:  python -m venv .venv
    echo e depois:  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
".venv\Scripts\python.exe" executar_coleta2.py %*
echo.
pause
