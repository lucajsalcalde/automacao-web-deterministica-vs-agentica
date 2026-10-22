"""
Executa a coleta 2 de ponta a ponta, na ordem descrita no README.md.

USO (Windows, na pasta do projeto)
----------------------------------
    executar_coleta2.bat              dois cliques no arquivo também funcionam
    executar_coleta2.bat --status     mostra o que já foi feito e sai
    executar_coleta2_sem_agente.bat   só os quatro braços sem API (960 execuções)
    login_claude.bat                  refaz o login do Claude usado pelo agente

O QUE FAZ, EM ORDEM
-------------------
  1. Confere o ambiente, a impressão digital do instrumento e o results.csv.
  2. Roda os autotestes das análises.
  3. Valida o instrumento: matriz de 32 combinações, validação funcional
     (192 de 192), conferência do limiar e teste do agente com o oráculo novo.
  4. Roda a coleta intercalada. Se o limite de uso da assinatura interromper,
     espera e retoma sozinho, do ponto em que parou. Se o login do Claude
     expirar, oferece o login na hora; sem ninguém para teclar Enter, tenta de
     novo algumas vezes (a renovação da sessão falha também quando a internet
     cai por um instante) e só então para. Com um site fora do ar, espera e
     retoma; a execução que falhou por isso não é gravada (coleta_com_guarda.py).
  5. Audita os dados.
  6. Roda analise.py, modelo_logistico.py, modelo_economico.py e figuras.py.
  7. Registra os dados no git (commit e tag coleta-2).

Para na primeira etapa que falhar e diz o que fazer. Rodar de novo retoma de
onde parou: etapas já concluídas com a mesma versão do código são puladas.
Tudo o que aparece na tela fica também em data/logs/.

EM DOIS BLOCOS (--sem-agente)
-----------------------------
Sem cota do Claude, roda as validações e só os quatro braços que não usam API
(960 execuções), na mesma ordem sorteada, e para. Depois, executar_coleta2.bat
sem opção refaz a matriz de validação (sem API), para confirmar que os sites não
mudaram entre os blocos, e roda só as 240 execuções agênticas que faltam.

O computador não entra em suspensão enquanto o script roda (Windows). A tela
pode apagar e a sessão pode ser bloqueada: o navegador roda sem janela. Um
clique na janela também não congela mais a coleta (ver selecao_no_console).
"""

from __future__ import annotations

import argparse
import codecs
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_LOGS = os.path.join(RAIZ, "data", "logs")
ARQ_ETAPAS = os.path.join(DIR_LOGS, "coleta2_etapas.json")
ARQ_TRAVA = os.path.join(DIR_LOGS, "coleta2.trava")
CSV_RESULTADOS = os.path.join(RAIZ, "data", "results.csv")
DIR_ANALISE = os.path.join(RAIZ, "data", "analise")

# Impressão digital do instrumento registrada na tag instrumento-coleta-2
# (common/versao.py). Se o código de common/, sites/, fingerprints ou xpaths
# mudar, a coleta não começa: os dados deixariam de corresponder ao registro.
IMPRESSAO_REGISTRADA = "6c6c5aba5b39"

TOTAL_ESPERADO = 1200
TOTAL_SEM_API = 960       # 4 braços x 2 sites x 3 tipos x 5 níveis x 8 repetições

# O runner (common.runner) com a guarda contra site fora do ar; ver o arquivo.
ARGS_COLETA = ["coleta_com_guarda.py", "--approach", "all", "--intercalado",
               "--semente-ordem", "2026", "--repeats", "8",
               "--drift-levels", "0,0.25,0.5,0.75,1.0",
               "--drift-types", "cosmetico,estrutural,semantico",
               "--coleta", "2", "--resume"]
# Mesma ordem sorteada: o runner consome os números aleatórios do mesmo jeito
# com ou sem o braço agêntico, então as células e a ordem dos braços coincidem.
ARGS_COLETA_SEM_API = ["sem-api" if a == "all" else a for a in ARGS_COLETA]

# Variáveis que alterariam o instrumento (limiar, tempos, tetos, modelo) ou
# mandariam o agente para a API cobrada por token. Ficam fora das etapas.
VARIAVEIS_REMOVIDAS = (
    "ANTHROPIC_API_KEY", "HEAL_THRESHOLD", "HEAL_MARGIN", "PW_TIMEOUT_MS",
    "PW_NAV_TIMEOUT_MS", "PW_NAV_TENTATIVAS", "AGENT_MAX_STEPS",
    "AGENT_BLOQUEAR_NAVEGACAO", "LLM_MODEL", "LLM_MAX_USD_POR_EXECUCAO",
    "LLM_INPUT_USD_PER_MTOK", "LLM_OUTPUT_USD_PER_MTOK", "LLM_PROMPT_CACHE",
)

# Mensagens do Claude Code quando a conta não está autenticada ou a sessão
# expirou. "/login" sozinho não serve de sinal: aparece na URL de uma tarefa.
SINAIS_LOGIN = (
    "failed to authenticate", "oauth session expired", "oauth token has expired",
    "could not be refreshed", "please run /login", "not logged in",
    "invalid api key", "authentication_error", "http 401",
)
TENTATIVAS_LOGIN = 3      # rodadas seguidas com login recusado antes de parar
ESPERA_LOGIN_MIN = 5      # minutos entre uma rodada e outra

# Textos do Claude Code quando desiste de uma chamada à API (sobrecarga, queda
# de conexão). É falha passageira de infraestrutura: espera e tenta de novo.
SINAIS_API = (
    "api error", "overloaded", "request timed out", "connection error",
    "http 500", "http 502", "http 503", "http 504", "http 529",
)

ETAPAS = [
    ("autotestes", "Autotestes das análises"),
    ("validar_instrumento", "Matriz de validação do instrumento"),
    ("validar_funcional", "Validação funcional (192 combinações)"),
    ("calibrar_limiar", "Conferência do limiar do self-healing"),
    ("teste_agentico", "Teste do agente com o oráculo novo"),
    ("coleta", "Coleta intercalada (1.200 execuções)"),
    ("auditoria", "Auditoria dos dados"),
    ("analises", "Análises, modelos e figuras"),
    ("registro", "Registro no git"),
]

ETAPA_SEM_API = ("coleta_sem_api", "Coleta dos braços sem API (960 execuções)")
ETAPA_REVALIDACAO = ("revalidacao_bloco_agentico",
                     "Matriz de validação de novo, antes do bloco agêntico")


class Parar(Exception):
    """Interrompe o script com uma mensagem para o usuário."""


# ------------------------------------------------------------------ saída

class Log:
    """Tela e arquivo ao mesmo tempo. Sem caminho, só a tela."""

    def __init__(self, caminho: str | None):
        self.caminho = caminho
        self._f = None
        if caminho:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            self._f = open(caminho, "a", encoding="utf-8")

    def escrever(self, texto: str, so_na_tela: bool = False) -> None:
        try:
            sys.stdout.write(texto)
        except UnicodeEncodeError:
            sys.stdout.write(texto.encode("ascii", "replace").decode("ascii"))
        sys.stdout.flush()
        if self._f and not so_na_tela:
            self._f.write(texto)
            self._f.flush()

    def linha(self, texto: str = "") -> None:
        self.escrever(texto + "\n")

    def tela(self, texto: str) -> None:
        """Fora do arquivo: os logs vão para o git junto com os dados."""
        self.escrever(texto + "\n", so_na_tela=True)

    def titulo(self, texto: str) -> None:
        agora = dt.datetime.now().strftime("%d/%m %H:%M")
        self.linha("\n" + "=" * 78)
        self.linha(f"{texto}   [{agora}]")
        self.linha("=" * 78)

    def fechar(self) -> None:
        if self._f:
            self._f.close()


def rodar(log: Log, args: list, env: dict, salvar_em: str | None = None) -> tuple:
    """Roda `python <args>` na pasta do projeto, mostrando e gravando a saída."""
    log.linha("\n$ python " + " ".join(args))
    proc = subprocess.Popen([sys.executable, *args], cwd=RAIZ, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    decodificador = codecs.getincrementaldecoder("utf-8")(errors="replace")
    partes = []
    extra = open(salvar_em, "w", encoding="utf-8") if salvar_em else None
    try:
        while True:
            bloco = proc.stdout.read1(4096)
            if not bloco:
                break
            texto = decodificador.decode(bloco)
            partes.append(texto)
            log.escrever(texto)
            if extra:
                extra.write(texto)
        resto = decodificador.decode(b"", final=True)
        if resto:
            partes.append(resto)
            log.escrever(resto)
            if extra:
                extra.write(resto)
        codigo = proc.wait()
    except KeyboardInterrupt:
        # O Ctrl+C chega também ao processo filho, que fecha sozinho.
        try:
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    finally:
        if extra:
            extra.close()
    return codigo, "".join(partes)


def esperar(log: Log, minutos: float, motivo: str) -> None:
    volta = dt.datetime.now() + dt.timedelta(minutes=minutos)
    log.linha(f"\n{motivo}. Nova tentativa às {volta:%H:%M} (em {minutos:g} min). "
              "Pode deixar o computador ligado e sair.")
    time.sleep(minutos * 60)


# ------------------------------------------------------------------ estado

def ler_etapas() -> dict:
    try:
        with open(ARQ_ETAPAS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def marcar(estado: dict, etapa: str, detalhe: str = "") -> None:
    estado.setdefault("impressao", IMPRESSAO_REGISTRADA)
    estado[etapa] = {"quando": dt.datetime.now().isoformat(timespec="seconds"),
                     "detalhe": detalhe}
    os.makedirs(DIR_LOGS, exist_ok=True)
    with open(ARQ_ETAPAS, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def linhas_da_coleta() -> list:
    if not os.path.isfile(CSV_RESULTADOS):
        return []
    with open(CSV_RESULTADOS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_TRAVA = None


def travar() -> bool:
    """
    Garante uma execução por vez nesta pasta. Devolve False se já há outra.

    Em 20/09/2026 duas janelas ficaram abertas ao mesmo tempo: a primeira
    esperando o limite de gasto liberar, e outra aberta para retomar. Cada uma
    rodou o seu runner, as duas pegaram as mesmas condições pendentes e 89
    execuções agênticas saíram em dobro — cerca de US$ 15 de API desperdiçados e
    uma limpeza a mais no conjunto. A trava é do sistema operacional: se o
    script morrer, ela cai sozinha.
    """
    global _TRAVA
    os.makedirs(DIR_LOGS, exist_ok=True)
    try:
        arquivo = open(ARQ_TRAVA, "a+")
        if os.name == "nt":
            import msvcrt
            arquivo.seek(0)
            msvcrt.locking(arquivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(arquivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            arquivo.close()
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception:  # noqa: BLE001 - sem trava disponível, segue sem ela
        return True
    _TRAVA = arquivo            # a referência mantém a trava viva até o fim
    return True


def contar_execucoes(sem_agente: bool = False) -> int:
    linhas = linhas_da_coleta()
    if sem_agente:
        return sum(1 for l in linhas if l.get("approach") != "agentic")
    return len(linhas)


def impedir_suspensao(ligar: bool) -> None:
    """Mantém o Windows acordado durante a coleta (sem efeito em outros sistemas)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        estado = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(estado)
    except Exception:  # noqa: BLE001
        pass


_MODO_CONSOLE_ORIGINAL = None
_SELECAO_DESLIGADA = False


def selecao_no_console(permitir: bool) -> None:
    """
    Liga ou desliga a seleção com o mouse nesta janela (Windows).

    No console clássico, um clique na janela inicia uma seleção e congela a
    escrita na tela até alguém teclar Esc. O script para de ler a saída da
    coleta, e a coleta para junto, sem aviso. Durante as etapas a seleção fica
    desligada; ao terminar, volta ao que era, para dar para copiar a saída.
    """
    global _MODO_CONSOLE_ORIGINAL, _SELECAO_DESLIGADA
    _SELECAO_DESLIGADA = not permitir
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")        # instância própria: tipos não vazam
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.GetStdHandle.argtypes = (wintypes.DWORD,)
        k32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        entrada = k32.GetStdHandle(0xFFFFFFF6)          # STD_INPUT_HANDLE, (DWORD)-10
        modo = wintypes.DWORD()
        if not k32.GetConsoleMode(entrada, ctypes.byref(modo)):
            return
        if _MODO_CONSOLE_ORIGINAL is None:
            _MODO_CONSOLE_ORIGINAL = modo.value
        EDICAO_RAPIDA, FLAGS_ESTENDIDAS = 0x0040, 0x0080
        if permitir:
            novo = _MODO_CONSOLE_ORIGINAL | FLAGS_ESTENDIDAS
        else:
            novo = (modo.value & ~EDICAO_RAPIDA) | FLAGS_ESTENDIDAS
        k32.SetConsoleMode(entrada, novo)
    except Exception:  # noqa: BLE001
        pass


def tem_console() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def tecla_enter(segundos: float) -> bool:
    """Espera um Enter por até `segundos`. Sem console para ler, só espera."""
    fim = time.time() + segundos

    def resto() -> float:
        return max(0.0, fim - time.time())

    if not tem_console():
        time.sleep(resto())
        return False
    if os.name == "nt":
        try:
            import msvcrt
            while msvcrt.kbhit():          # descarta o que foi teclado antes da pergunta
                msvcrt.getwch()
            while resto() > 0:
                if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                    return True
                time.sleep(0.2)
            return False
        except Exception:  # noqa: BLE001
            time.sleep(resto())
            return False
    import select
    pronto, _, _ = select.select([sys.stdin], [], [], resto())
    if pronto and sys.stdin.readline():
        return True
    time.sleep(resto())
    return False


# ------------------------------------------------------------------ login do Claude

def erro_de_login(texto: str) -> bool:
    texto = (texto or "").lower()
    return any(sinal in texto for sinal in SINAIS_LOGIN)


def erro_de_api(texto: str) -> bool:
    texto = (texto or "").lower()
    return any(sinal in texto for sinal in SINAIS_API)


def sites_fora_do_ar(log: Log, env: dict) -> list:
    """
    Confere os dois sites com a mesma regra da guarda da coleta. Se a própria
    conferência quebrar, não bloqueia nada: a guarda continua valendo na coleta.
    """
    _, saida = rodar(log, ["coleta_com_guarda.py", "--conferir-site",
                           "saucedemo,the_internet"], env)
    return re.findall(r"^(saucedemo|the_internet): FORA DO AR", saida, re.M)


def esperar_site(log: Log, fora: list, vez: int) -> None:
    if vez > 12:
        raise Parar(f"O site {', '.join(fora)} continua fora do ar depois de uma hora. Rode "
                    "o script de novo mais tarde; ele continua de onde parou.")
    esperar(log, 5, f"Site fora do ar ({', '.join(fora)})")


def esperar_api(log: Log, vez: int) -> None:
    if vez > 12:
        raise Parar("A API do Claude continua falhando depois de uma hora. Rode o script de "
                    "novo mais tarde; ele continua de onde parou.")
    esperar(log, 5, "A API do Claude falhou (sobrecarga ou queda de conexão)")


def ambiente_limpo() -> tuple:
    """Ambiente das etapas, sem as variáveis que alterariam o instrumento."""
    env = dict(os.environ)
    removidas = [v for v in VARIAVEIS_REMOVIDAS if v in env]
    for v in removidas:
        env.pop(v)
    env["AGENT_BACKEND"] = "sdk"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Sem isto a saída do runner, que passa por um pipe, chega em blocos: a tela
    # ficava parada por minutos com a coleta andando.
    env["PYTHONUNBUFFERED"] = "1"
    return env, removidas


def cli_do_claude() -> str | None:
    """O Claude Code que acompanha o claude-agent-sdk: é ele que o agente usa."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    nome = "claude.exe" if os.name == "nt" else "claude"
    embutido = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", nome)
    return embutido if os.path.isfile(embutido) else shutil.which("claude")


def status_do_login(cli: str, env: dict) -> dict | None:
    """
    Lê `claude auth status`. Diz se há credencial salva, não se ela ainda vale:
    uma sessão que expirou continua aparecendo como conectada. Quem descobre
    isso é o teste do agente.
    """
    try:
        r = subprocess.run([cli, "auth", "status"], env=env, cwd=os.path.expanduser("~"),
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return None
    texto = (r.stdout or b"").decode("utf-8", "replace")
    dados = {}
    inicio, fim = texto.find("{"), texto.rfind("}")
    if 0 <= inicio < fim:
        try:
            dados = json.loads(texto[inicio:fim + 1])
        except ValueError:
            dados = {}
    if not isinstance(dados, dict):
        dados = {}
    dados.setdefault("loggedIn", r.returncode == 0)
    return dados


def descrever_login(log: Log, status: dict) -> None:
    plano = status.get("subscriptionType")
    metodo = status.get("authMethod")
    log.linha("Login do Claude: conectado" + (f" (assinatura {plano})" if plano else "") + ".")
    if status.get("email"):
        log.tela(f"Conta: {status['email']}")
    if metodo and metodo not in ("claude.ai", "oauth_token"):
        log.linha(f"ATENÇÃO: o login atual não é o da assinatura (método: {metodo}). "
                  "O agente pode ser cobrado por token. Rode login_claude.bat e entre na "
                  "conta que tem a assinatura.")


def fazer_login(log: Log) -> None:
    """Abre o login do Claude Code no navegador e confere se deu certo."""
    cli = cli_do_claude()
    if not cli:
        raise Parar("Não encontrei o Claude Code que acompanha o claude-agent-sdk.\n"
                    "Rode: .venv\\Scripts\\pip install -r requirements.txt")
    env, _ = ambiente_limpo()
    log.titulo("Login do Claude (a conta da assinatura, usada pelo agente)")
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.linha("ATENÇÃO: a variável CLAUDE_CODE_OAUTH_TOKEN está definida e vale mais "
                  "que este login. Se o erro continuar, apague essa variável do Windows.")
    log.linha("O navegador vai abrir. Entre na conta do Claude que tem a assinatura e "
              "autorize.\nSe a página mostrar um código, cole aqui (Ctrl+V) e tecle Enter.\n")
    estava_desligada = _SELECAO_DESLIGADA
    selecao_no_console(True)           # para colar o código com o mouse, se precisar
    try:
        codigo = subprocess.call([cli, "auth", "login", "--claudeai"], env=env,
                                 cwd=os.path.expanduser("~"))
    except OSError as e:
        raise Parar(f"Não consegui abrir o login do Claude: {e}")
    finally:
        if estava_desligada:
            selecao_no_console(False)
    status = status_do_login(cli, env) or {}
    if codigo != 0 or not status.get("loggedIn"):
        raise Parar("O login do Claude não foi concluído. Rode login_claude.bat de novo; "
                    "se falhar outra vez, confira a mensagem que apareceu.")
    log.linha("")
    descrever_login(log, status)


def verificar_login(log: Log) -> None:
    """Antes das etapas com o agente: há login salvo? Sem ele, nada funciona."""
    cli = cli_do_claude()
    if not cli:
        log.linha("Login do Claude: não encontrei o Claude Code do claude-agent-sdk para "
                  "conferir.")
        return
    env, _ = ambiente_limpo()
    status = status_do_login(cli, env)
    if status is None:
        log.linha("Login do Claude: não consegui conferir agora; o teste do agente vai dizer.")
        return
    if status.get("loggedIn"):
        descrever_login(log, status)
        return
    log.linha("Login do Claude: nenhum login salvo, e o agente não funciona sem ele.")
    if not tem_console():
        raise Parar("Falta o login do Claude usado pelo agente.\n"
                    "Dê dois cliques em login_claude.bat e entre na conta da assinatura.")
    fazer_login(log)


def tratar_erro_de_login(log: Log, rodada: int) -> None:
    """
    O Claude recusou o agente por login. Com alguém diante da tela, faz o login
    na hora. Sem ninguém, espera e tenta de novo: a renovação da sessão também
    falha quando a internet cai por um instante, e aí basta tentar outra vez.
    """
    log.linha("\nO Claude recusou o acesso do agente: o login expirou ou a renovação da "
              "sessão falhou.")
    if rodada > TENTATIVAS_LOGIN:
        raise Parar("O login do Claude usado pelo agente expirou e não voltou sozinho.\n"
                    "Dê dois cliques em login_claude.bat e entre na conta da assinatura.")
    if not tem_console():
        esperar(log, ESPERA_LOGIN_MIN, f"Tentativa {rodada} de {TENTATIVAS_LOGIN}")
        return
    volta = dt.datetime.now() + dt.timedelta(minutes=ESPERA_LOGIN_MIN)
    log.linha(f"Tecle Enter para entrar na conta agora (abre o navegador). Sem resposta, "
              f"tento de novo sozinho às {volta:%H:%M} (tentativa {rodada} de "
              f"{TENTATIVAS_LOGIN}).")
    if tecla_enter(ESPERA_LOGIN_MIN * 60):
        fazer_login(log)


def avisar_linhas_com_erro_de_login(log: Log) -> None:
    """
    Uma sessão que expira no MEIO de uma execução, depois de o agente já ter
    agido, chega ao CSV como `exception`. Não é resultado do experimento. A
    auditoria aponta essas linhas no fim; aqui só se avisa na hora.
    """
    gravadas = [l for l in linhas_da_coleta()
                if l.get("approach") == "agentic" and erro_de_login(l.get("error_message", ""))]
    if gravadas:
        log.linha(f"\nAtenção: {len(gravadas)} execução(ões) do agente foram gravadas com erro "
                  "de login no meio da tarefa. Não são resultado do experimento: a auditoria "
                  "vai apontá-las no fim, para serem refeitas.")


# ------------------------------------------------------------------ etapas

def conferir_ambiente(log: Log) -> dict:
    log.titulo("0. Conferência do ambiente")
    if not os.path.isdir(os.path.join(RAIZ, "common")):
        raise Parar("Rode este script de dentro da pasta do projeto.")

    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        log.linha("ATENÇÃO: o Python em uso não é o do ambiente virtual (.venv). "
                  "Use executar_coleta2.bat.")

    faltando = []
    for modulo in ("playwright", "claude_agent_sdk", "matplotlib"):
        try:
            __import__(modulo)
        except ImportError:
            faltando.append(modulo)
    if faltando:
        raise Parar("Faltam pacotes no ambiente virtual: " + ", ".join(faltando) +
                    ".\nRode: .venv\\Scripts\\pip install -r requirements.txt")
    if not shutil.which("npx"):
        raise Parar("O comando npx não foi encontrado. O agente precisa do Node.js "
                    "instalado para rodar o Playwright MCP.")

    sys.path.insert(0, RAIZ)
    from common.versao import impressao_instrumento
    impressao = impressao_instrumento()
    log.linha(f"Impressão do instrumento: {impressao} (registrada: {IMPRESSAO_REGISTRADA})")
    if impressao != IMPRESSAO_REGISTRADA:
        raise Parar(
            "O código do instrumento (common/, sites/, fingerprints ou xpaths) não é o "
            "registrado na tag instrumento-coleta-2.\nConfira com:  git status  e  "
            "git diff instrumento-coleta-2 -- common sites data\nNão comece a coleta "
            "com o código alterado.")

    linhas = linhas_da_coleta()
    alheias = [l for l in linhas if str(l.get("coleta", "")) != "2"
               or l.get("versao_instrumento", "") != IMPRESSAO_REGISTRADA]
    if alheias:
        raise Parar(
            f"data/results.csv tem {len(alheias)} linha(s) de outra coleta ou de outra "
            "versão do código.\nMova o arquivo para outra pasta antes de continuar; a "
            "coleta 2 precisa começar com o arquivo vazio ou só com linhas dela.")
    log.linha(f"Execuções já gravadas na coleta 2: {len(linhas)} de {TOTAL_ESPERADO}")

    env, removidas = ambiente_limpo()
    if removidas:
        log.linha("Variáveis ignoradas nesta execução, para não alterar o instrumento "
                  "nem usar a API cobrada: " + ", ".join(removidas))
    return env


def etapa_autotestes(log: Log, env: dict) -> str:
    esperado = {
        "analise.py": "Todos os testes passaram.",
        "modelo_logistico.py": "TODOS OS TESTES PASSARAM.",
        "modelo_economico.py": "TODOS OS TESTES PASSARAM.",
    }
    for script, frase in esperado.items():
        codigo, saida = rodar(log, [script, "--autoteste"], env)
        if codigo != 0 or frase not in saida:
            raise Parar(f"O autoteste de {script} falhou. Confira a saída acima.")
    return "3 de 3"


def etapa_validar_instrumento(log: Log, env: dict) -> str:
    codigo, saida = rodar(log, ["validar_instrumento.py"], env)
    if codigo != 0 or "Nenhuma divergência" not in saida:
        raise Parar("A matriz de validação encontrou divergência. Não comece a coleta; "
                    "confira o bloco de divergências acima.")
    return "sem divergência"


def etapa_validar_funcional(log: Log, env: dict) -> str:
    codigo, saida = rodar(log, ["validar_funcional.py"], env)
    if codigo != 0:
        raise Parar("A validação funcional falhou: a degradação alterou o funcionamento "
                    "de alguma página. Não comece a coleta; confira a saída acima.")
    m = re.search(r"(\d+) execuções de referência", saida)
    return f"{m.group(1) if m else '?'} de referência, nenhuma falha"


def etapa_calibrar_limiar(log: Log, env: dict) -> str:
    codigo, saida = rodar(log, ["calibrar_limiar.py"], env)
    if codigo != 0:
        raise Parar("A conferência do limiar terminou com erro. Confira a saída acima.")
    m = re.search(r"^\s*0\.10\s+0\.62\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)", saida, re.M)
    log.linha("\nLembrete: esta etapa só registra a precisão. O limiar (0,62) e a margem "
              "(0,10) NÃO são alterados, mesmo que a tabela recomende outro valor.")
    if m:
        return (f"limiar 0,62 e margem 0,10: VP {m.group(1)}, FP {m.group(2)}, "
                f"FN {m.group(3)}, precisão {m.group(4)}")
    return "concluída (ver data/calibracao_limiar.csv)"


def etapa_teste_agentico(log: Log, env: dict, args) -> str:
    esperas = logins = esperas_api = esperas_site = repeticoes = 0
    while True:
        fora = sites_fora_do_ar(log, env)
        if fora:
            esperas_site += 1
            esperar_site(log, fora, esperas_site)
            continue
        codigo, saida = rodar(log, ["validar_instrumento.py", "--com-agentico",
                                    "--bracos", "agentico", "--tipos", "nenhum,semantico"], env)
        if "limite de uso" in saida and "INTERROMPIDO" in saida:
            esperas += 1
            if esperas > args.max_esperas:
                raise Parar("O limite de uso não liberou depois de muitas tentativas.")
            esperar(log, args.espera_limite, "Limite de uso da assinatura atingido")
            continue
        if erro_de_login(saida):
            logins += 1
            tratar_erro_de_login(log, logins)
            continue
        if "INTERROMPIDO" in saida and erro_de_api(saida):
            esperas_api += 1
            esperar_api(log, esperas_api)
            continue
        if codigo != 0 or "INTERROMPIDO" in saida:
            raise Parar("O teste do agente foi interrompido. Confira a saída acima.")
        linhas = re.findall(r"^(saucedemo|the_internet)\s+agêntico\s+(nenhum|semantico)\s+"
                            r"(sucesso|falha)", saida, re.M)
        base = {site: res for site, tipo, res in linhas if tipo == "nenhum"}
        if set(base) != {"saucedemo", "the_internet"}:
            raise Parar("Não encontrei o resultado do agente sem degradação nas duas "
                        "aplicações. Confira a saída acima.")
        if any(res != "sucesso" for res in base.values()):
            fora = sites_fora_do_ar(log, env)
            if fora:
                esperas_site += 1
                log.linha("\nA tarefa sem degradação falhou com o site fora do ar. Não conta: "
                          "o teste será refeito quando o site voltar.")
                esperar_site(log, fora, esperas_site)
                continue
            if repeticoes == 0:
                repeticoes += 1
                log.linha("\nA tarefa sem degradação falhou com os sites no ar. Repetindo o "
                          "teste uma vez, para descartar falha passageira (queda rápida do "
                          "site, instabilidade da API).")
                continue
            raise Parar("Sem degradação, o agente precisa concluir as duas tarefas, e "
                        "pelo menos uma falhou. Isso indica problema no oráculo novo ou "
                        "na configuração do agente. Não comece a coleta; confira a "
                        "saída acima.")
        semantico = {site: res for site, tipo, res in linhas if tipo == "semantico"}
        return ("sem degradação: sucesso nas duas; semântica 1,0: " +
                ", ".join(f"{s} {r}" for s, r in sorted(semantico.items())))


def etapa_coleta(log: Log, env: dict, args, sem_agente: bool = False) -> str:
    argumentos = ARGS_COLETA_SEM_API if sem_agente else ARGS_COLETA
    total = TOTAL_SEM_API if sem_agente else TOTAL_ESPERADO
    esperas_limite = esperas_rede = esperas_api = erros = logins = 0
    while True:
        fora = sites_fora_do_ar(log, env)
        if fora:
            esperas_rede += 1
            esperar_site(log, fora, esperas_rede)
            continue
        antes = contar_execucoes(sem_agente)
        log.linha(f"\nExecuções gravadas até agora: {antes} de {total}")
        codigo, saida = rodar(log, argumentos, env)
        depois = contar_execucoes(sem_agente)
        if depois > antes:
            # As contagens medem falha PERSISTENTE ("depois de uma hora"); se a
            # coleta andou, a falha anterior já passou.
            esperas_limite = esperas_rede = esperas_api = logins = 0
        if codigo != 0:
            erros += 1
            if erros > 2:
                raise Parar("A coleta terminou com erro inesperado três vezes seguidas. "
                            "Confira o final do log.")
            esperar(log, 2, "A coleta terminou com erro inesperado")
            continue
        if re.search(r"^Concluído\.", saida, re.M):
            if depois < total:
                raise Parar(f"O runner terminou com {depois} de {total} execuções. "
                            "Confira o final do log.")
            return f"{depois} execuções"
        m = re.search(r"^Motivo: (.*)$", saida, re.M)
        motivo = m.group(1).strip() if m else "motivo não informado"
        if depois > antes:
            erros = 0
        if "limite de uso" in motivo:
            esperas_limite += 1
            if esperas_limite > args.max_esperas:
                raise Parar("O limite de uso não liberou depois de muitas tentativas. "
                            "Rode o script de novo mais tarde; ele continua de onde parou.")
            esperar(log, args.espera_limite, "Limite de uso da assinatura atingido")
        elif m and erro_de_login(saida[m.start():]):
            # Só o trecho a partir de "Motivo:": as linhas de execuções anteriores
            # não dizem por que o runner parou agora.
            logins += 1
            avisar_linhas_com_erro_de_login(log)
            tratar_erro_de_login(log, logins)
        elif "acessar o site" in motivo:
            esperas_rede += 1
            if esperas_rede > 12:
                raise Parar("Os sites continuam inacessíveis depois de uma hora. Confira a "
                            "internet e rode o script de novo; ele continua de onde parou.")
            esperar(log, 5, "Site inacessível ou internet fora")
        elif m and erro_de_api(saida[m.start():]):
            esperas_api += 1
            esperar_api(log, esperas_api)
        elif "pelo usuário" in motivo:
            raise Parar("Coleta pausada. Rode executar_coleta2.bat de novo para continuar "
                        "de onde parou.")
        else:
            raise Parar(f"A coleta parou: {motivo}\nConfira o final do log.")


def etapa_auditoria(log: Log, env: dict) -> str:
    os.makedirs(DIR_ANALISE, exist_ok=True)
    codigo, saida = rodar(log, ["auditoria.py"], env,
                          salvar_em=os.path.join(DIR_ANALISE, "saida_auditoria.txt"))
    m = re.search(r"VEREDITO: (\d+) erro", saida)
    if codigo != 0 or m or "VEREDITO:" not in saida:
        raise Parar("A auditoria encontrou erro nos dados. Não rode as análises ainda; "
                    "confira a saída acima (também em data/analise/saida_auditoria.txt).")
    alertas = re.search(r"(\d+) alerta", saida)
    return f"sem erro, {alertas.group(1) if alertas else 0} alerta(s)"


def etapa_analises(log: Log, env: dict) -> str:
    os.makedirs(DIR_ANALISE, exist_ok=True)
    for script in ("analise.py", "modelo_logistico.py", "modelo_economico.py", "figuras.py"):
        nome = os.path.splitext(script)[0]
        codigo, _ = rodar(log, [script], env,
                          salvar_em=os.path.join(DIR_ANALISE, f"saida_{nome}.txt"))
        if codigo != 0:
            raise Parar(f"{script} terminou com erro. Confira a saída acima.")
    return "saídas em data/analise/"


def etapa_registro(log: Log) -> str:
    git = shutil.which("git")
    if not git:
        log.linha("git não encontrado no PATH. Faça o commit à mão: "
                  "git add data  e  git commit -m \"Coleta 2 concluída\"")
        return "sem git"
    subprocess.run([git, "add", "--", "data"], cwd=RAIZ, check=False)
    r = subprocess.run([git, "commit", "-m", "Coleta 2 concluída"], cwd=RAIZ,
                       capture_output=True, text=True, check=False)
    log.linha((r.stdout or "") + (r.stderr or ""))
    subprocess.run([git, "tag", "-f", "coleta-2"], cwd=RAIZ, check=False)
    h = subprocess.run([git, "rev-parse", "--short", "HEAD"], cwd=RAIZ,
                       capture_output=True, text=True, check=False).stdout.strip()
    return f"commit {h}, tag coleta-2"


# ------------------------------------------------------------------ principal

def mostrar_status() -> None:
    estado = ler_etapas()
    print(f"Execuções gravadas: {contar_execucoes()} de {TOTAL_ESPERADO} "
          f"(sem API: {contar_execucoes(True)} de {TOTAL_SEM_API})\n")
    extras = [e for e in (ETAPA_SEM_API, ETAPA_REVALIDACAO) if e[0] in estado]
    for chave, nome in ETAPAS[:5] + extras + ETAPAS[5:]:
        feito = estado.get(chave)
        marca = f"feito em {feito['quando']}  {feito['detalhe']}" if feito else "pendente"
        print(f"  {nome:<45} {marca}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="Executa a coleta 2 de ponta a ponta")
    ap.add_argument("--status", action="store_true", help="mostra o andamento e sai")
    ap.add_argument("--refazer-validacoes", action="store_true",
                    help="refaz autotestes e validações já concluídos")
    ap.add_argument("--espera-limite", type=float, default=30,
                    help="minutos de espera quando o limite de uso interrompe (padrão: 30)")
    ap.add_argument("--max-esperas", type=int, default=48,
                    help="quantas esperas pelo limite de uso antes de desistir (padrão: 48)")
    ap.add_argument("--login", action="store_true",
                    help="refaz o login do Claude usado pelo agente e sai")
    ap.add_argument("--sem-agente", action="store_true",
                    help="valida e roda só os quatro braços sem API (960 execuções), "
                         "sem gastar cota do Claude; o agêntico fica para depois")
    args = ap.parse_args()

    if args.status:
        mostrar_status()
        return

    if args.login:
        tela = Log(None)
        try:
            fazer_login(tela)
            tela.linha("\nPronto. Agora dê dois cliques em executar_coleta2.bat.")
            sys.exit(0)
        except Parar as e:
            tela.linha("\n" + str(e))
            sys.exit(1)
        except KeyboardInterrupt:
            tela.linha("\nLogin cancelado.")
            sys.exit(2)

    if not travar():
        print("\nJá existe uma execução em andamento nesta pasta, em outra janela.\n"
              "Duas ao mesmo tempo pegam as mesmas execuções pendentes e gravam tudo em\n"
              "dobro. Use a janela que já está aberta; se ela estiver esperando o limite\n"
              "de uso liberar, é só deixar: ela retoma sozinha.\n"
              "Para ver o andamento: executar_coleta2.bat --status")
        sys.exit(3)

    carimbo = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = Log(os.path.join(DIR_LOGS, f"coleta2_{carimbo}.log"))
    impedir_suspensao(True)
    selecao_no_console(False)
    inicio = time.time()
    estado = ler_etapas()
    if estado.get("impressao") != IMPRESSAO_REGISTRADA:
        estado = {}
    if args.refazer_validacoes:
        for chave in ("autotestes", "validar_instrumento", "validar_funcional",
                      "calibrar_limiar", "teste_agentico"):
            estado.pop(chave, None)
    codigo_saida = 0

    try:
        env = conferir_ambiente(log)
        if args.sem_agente:
            log.linha("Modo sem agente: nenhuma chamada ao Claude nesta execução.")
            etapas = ETAPAS[:4] + [ETAPA_SEM_API]
        else:
            etapas = list(ETAPAS)
            if ETAPA_SEM_API[0] in estado:
                # Bloco agêntico depois do bloco sem API: confere de novo, sem API,
                # que os sites e o instrumento se comportam como na primeira parte.
                etapas.insert(5, ETAPA_REVALIDACAO)
            if "teste_agentico" not in estado or "coleta" not in estado:
                verificar_login(log)
        funcoes = {
            "autotestes": lambda: etapa_autotestes(log, env),
            "validar_instrumento": lambda: etapa_validar_instrumento(log, env),
            "validar_funcional": lambda: etapa_validar_funcional(log, env),
            "calibrar_limiar": lambda: etapa_calibrar_limiar(log, env),
            "teste_agentico": lambda: etapa_teste_agentico(log, env, args),
            "coleta": lambda: etapa_coleta(log, env, args),
            "coleta_sem_api": lambda: etapa_coleta(log, env, args, sem_agente=True),
            "revalidacao_bloco_agentico": lambda: etapa_validar_instrumento(log, env),
            "auditoria": lambda: etapa_auditoria(log, env),
            "analises": lambda: etapa_analises(log, env),
            "registro": lambda: etapa_registro(log),
        }
        for numero, (chave, nome) in enumerate(etapas, start=1):
            if chave in estado:
                log.linha(f"\n{numero}. {nome}: já feito em {estado[chave]['quando']}, pulando.")
                continue
            log.titulo(f"{numero}. {nome}")
            detalhe = funcoes[chave]()
            marcar(estado, chave, detalhe)
            log.linha(f"\n-> {nome}: OK ({detalhe})")

        horas = (time.time() - inicio) / 3600
        log.titulo("BLOCO SEM API CONCLUÍDO" if args.sem_agente else "COLETA 2 CONCLUÍDA")
        for chave, nome in etapas:
            log.linha(f"  {nome:<45} {estado[chave]['detalhe']}")
        log.linha(f"\nTempo desta execução: {horas:.1f} h. Log completo: {log.caminho}")
        if args.sem_agente:
            log.linha("Próximo passo: quando a cota do Claude voltar, dê dois cliques em "
                      "executar_coleta2.bat.\nEle refaz a matriz de validação (sem API) e "
                      "roda só as 240 execuções agênticas que faltam.")
        else:
            log.linha("Próximo passo: me avise que terminou, para eu preencher o artigo.")
    except Parar as e:
        log.titulo("PAROU")
        log.linha(str(e))
        log.linha(f"\nLog completo: {log.caminho}")
        bat = "executar_coleta2_sem_agente.bat" if args.sem_agente else "executar_coleta2.bat"
        log.linha(f"Depois de resolver, rode {bat} de novo: ele continua de onde parou.")
        codigo_saida = 1
    except KeyboardInterrupt:
        log.titulo("PAUSADO")
        bat = "executar_coleta2_sem_agente.bat" if args.sem_agente else "executar_coleta2.bat"
        log.linha(f"Você interrompeu. Rode {bat} de novo para continuar de onde parou.")
        codigo_saida = 2
    finally:
        impedir_suspensao(False)
        selecao_no_console(True)
        log.fechar()
    sys.exit(codigo_saida)


if __name__ == "__main__":
    main()
