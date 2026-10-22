"""
Instrumentação comum usada por todas as tarefas (sites/*/task.py).

Contém:
- TaskResult: estrutura padronizada de uma execução (o que vira uma linha no CSV).
- StepCounter: contador de ações/passos executados pelo script.
- timed(): context manager que mede a duração da execução.
- Funções de oráculo: verificação do estado final REAL da página, independente
  do que o script "acha" que fez. É isso que garante que "sucesso" não é
  autodeclarado.
"""

from __future__ import annotations

import csv
import os
import time
import traceback
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


# Tempo máximo de espera por um elemento, em milissegundos.
#
# O padrão do Playwright é 30 s. Com o instrumento validado, um seletor quebrado
# nunca se resolve: a espera adicional não altera o resultado, apenas o custo.
# Uma execução bem-sucedida completa o fluxo inteiro em torno de 0,6 s, de modo
# que 10 s mantêm mais de dez vezes de folga sobre o tempo observado.
#
# O efeito no tempo total é grande: com cerca de 350 falhas esperadas em 720
# execuções, a coleta cai de aproximadamente 3,1 h para 1,1 h.
#
# Ajuste com a variável PW_TIMEOUT_MS. Use o MESMO valor em toda a coleta.
TIMEOUT_MS = int(os.getenv("PW_TIMEOUT_MS", "10000"))

# Tempo de espera para CARREGAR UMA PÁGINA — separado, e muito maior.
#
# Os 10 s acima valem para procurar elementos, e a lógica que os justifica é que
# um seletor quebrado nunca se resolve: esperar mais não muda o veredito. Uma
# NAVEGAÇÃO é o oposto — ela quase sempre se resolve, só que às vezes demora.
# Rede lenta, primeiro acesso, servidor acordando.
#
# Usar o mesmo valor para as duas coisas produzia falha espúria: numa validação,
# a primeira execução do SauceDemo excedeu 10 s no `goto` e foi registrada como
# reprovação do braço frágil SEM drift. No arquivo de resultados, uma falha
# dessas é indistinguível de uma falha causada pela degradação — exatamente o
# tipo de contaminação silenciosa que este projeto vem perseguindo.
#
# Carregar a página não é o que o experimento mede; é a preparação para medir.
NAV_TIMEOUT_MS = int(os.getenv("PW_NAV_TIMEOUT_MS", "30000"))

# Tentativas de navegação antes de desistir. Uma segunda chance elimina
# praticamente todo o ruído de rede, e não mascara defeito real: um site fora do
# ar falha nas duas.
NAV_TENTATIVAS = int(os.getenv("PW_NAV_TENTATIVAS", "2"))


def navegar(page, url: str, wait_until: str = "domcontentloaded") -> None:
    """
    Carrega uma página com tolerância a lentidão de rede. Ver NAV_TIMEOUT_MS.

    Se todas as tentativas falharem, a exceção traz um prefixo explícito para que
    a falha seja reconhecível no arquivo de resultados como problema de acesso ao
    site, e não como consequência do drift.
    """
    ultimo = None
    for tentativa in range(1, NAV_TENTATIVAS + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
            return
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise RuntimeError(
        f"[FALHA DE NAVEGACAO] não foi possível carregar {url} em "
        f"{NAV_TENTATIVAS} tentativas de {NAV_TIMEOUT_MS} ms. Isto é acesso ao "
        f"site, não efeito do drift. Causa: {type(ultimo).__name__}: "
        f"{str(ultimo).splitlines()[0][:160]}")


async def navegar_async(page, url: str, wait_until: str = "domcontentloaded") -> None:
    """Idêntica a `navegar`, para a API assíncrona usada no braço agêntico."""
    ultimo = None
    for _ in range(NAV_TENTATIVAS):
        try:
            await page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
            return
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise RuntimeError(
        f"[FALHA DE NAVEGACAO] não foi possível carregar {url} em "
        f"{NAV_TENTATIVAS} tentativas de {NAV_TIMEOUT_MS} ms. Isto é acesso ao "
        f"site, não efeito do drift. Causa: {type(ultimo).__name__}: "
        f"{str(ultimo).splitlines()[0][:160]}")


def novo_contexto(browser):
    """Cria um contexto de navegação com o tempo de espera padronizado."""
    context = browser.new_context()
    context.set_default_timeout(TIMEOUT_MS)
    return context


@dataclass
class TaskResult:
    site: str
    task: str
    approach: str            # deterministic | semantic | healing | agentic
    drift_level: float       # intensidade: 0.0, 0.25, 0.5 ...
    repetition: int
    success: bool
    duration_s: float
    n_steps: int
    drift_type: str = "cosmetico"   # cosmetico | estrutural | semantico
    error_type: str = ""     # "", "timeout", "selector_not_found", "assertion_failed", "exception"
    error_message: str = ""
    tokens_input: int = 0    # só relevante para approach="agentic"
    tokens_output: int = 0
    cost_usd: float = 0.0
    # só relevantes para approach="healing": quantas vezes o seletor original
    # falhou, quantas o healer recuperou e quantas ele desistiu.
    heal_acionamentos: int = 0
    heal_curados: int = 0
    heal_desistencias: int = 0
    # Verdade de referência das curas (coleta 2): o elemento escolhido era o
    # alvo verdadeiro? Medido pela identidade que o degradador preserva, só
    # depois da escolha (ver _VERDADE_JS em common/healing.py).
    heal_corretos: int = 0
    heal_errados: int = 0
    heal_indeterminados: int = 0
    # QUANTO de drift foi de fato aplicado, somado por todas as páginas da
    # execução. Antes o arquivo registrava só o tipo e o nível PEDIDOS, e não
    # havia como conferir depois o que o script realmente mutou.
    #
    # A ausência dessas colunas custou caro: o drift estrutural passou uma
    # coleta inteira sem produzir efeito algum, e só foi descoberto ao comparar
    # execução por execução com a condição cosmética. Com estes números o
    # problema apareceria na primeira auditoria — bastaria ver `drift_iscas`
    # igual a zero onde deveria ser positivo.
    drift_ids: int = 0           # ids renomeados
    drift_classes: int = 0       # elementos com classe renomeada
    drift_textos: int = 0        # textos de folha alterados (só semântico)
    drift_envolvidos: int = 0    # contêineres inseridos (só estrutural)
    drift_iscas: int = 0         # irmãos-cópia inseridos (só estrutural)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Rastreabilidade: de qual coleta e de qual versão do código veio a linha.
    # A versão é a impressão digital calculada por common/versao.py; linhas de
    # versões diferentes não se misturam na análise (a auditoria confere).
    coleta: int = 2
    versao_instrumento: str = ""

    def to_row(self) -> dict:
        return asdict(self)


class AcumuladorDrift:
    """
    Soma as estatísticas do drift ao longo de uma execução.

    O drift é reaplicado a cada página, então uma execução do SauceDemo produz
    cinco conjuntos de estatísticas. O que interessa registrar é o total: quanto
    de degradação aquela execução enfrentou do começo ao fim.

    Uso:
        acc = AcumuladorDrift()
        acc.somar(apply_drift(page, nivel, seed=s, tipo=t))
        ...
        result.aplicar(acc)
    """

    CAMPOS = ("ids", "classes", "textos", "envolvidos", "iscas")

    def __init__(self) -> None:
        self.totais = dict.fromkeys(self.CAMPOS, 0)

    def somar(self, stats) -> None:
        if not isinstance(stats, dict):
            return
        for campo in self.CAMPOS:
            self.totais[campo] += int(stats.get(campo, 0) or 0)

    def aplicar(self, resultado: "TaskResult") -> None:
        for campo, valor in self.totais.items():
            setattr(resultado, f"drift_{campo}", valor)

    def __repr__(self) -> str:
        return f"AcumuladorDrift({self.totais})"


class StepCounter:
    """
    Conta as ações (cliques, preenchimentos, navegações) que o script executa e
    guarda o rótulo da última concluída.

    O rótulo é essencial para diagnóstico: sem ele, uma falha aparece apenas
    como "Locator.click: Timeout", sem indicar QUAL clique do fluxo falhou, o
    que obriga a investigar passo a passo. Com ele, a mensagem aponta
    diretamente o ponto de parada.
    """

    def __init__(self) -> None:
        self.count = 0
        self.ultimo = "inicio"

    def step(self, label: str = "") -> None:
        self.count += 1
        if label:
            self.ultimo = label


@contextmanager
def timed():
    """Uso: with timed() as t: ...  depois t.duration tem os segundos decorridos."""

    class _Timer:
        duration: float = 0.0

    t = _Timer()
    start = time.perf_counter()
    try:
        yield t
    finally:
        t.duration = time.perf_counter() - start


def run_task_safely(site: str, task: str, approach: str, drift_level: float,
                     repetition: int, fn, drift_type: str = "cosmetico") -> TaskResult:
    """
    Executa `fn(counter) -> None` (a sequência de passos da tarefa) e transforma
    qualquer exceção em um TaskResult de falha, em vez de derrubar o runner inteiro.
    `fn` deve levantar AssertionError quando o oráculo falhar (ver funções expect_*
    abaixo) e incrementar `counter.step()` a cada ação.
    """
    counter = StepCounter()
    error_type, error_message = "", ""
    success = False

    with timed() as t:
        try:
            fn(counter)
            success = True
        except AssertionError as e:
            error_type = "assertion_failed"
            error_message = f"[apos '{counter.ultimo}'] {e}"
        except TimeoutError as e:
            error_type = "timeout"
            error_message = f"[apos '{counter.ultimo}'] {e}"
        except Exception as e:  # noqa: BLE001 - queremos capturar tudo e registrar
            # Playwright levanta seu próprio TimeoutError (playwright._impl._errors.TimeoutError);
            # cai aqui porque não importamos o tipo específico para não acoplar o oráculo à lib.
            name = type(e).__name__
            error_type = "timeout" if "Timeout" in name else "exception"
            error_message = f"[apos '{counter.ultimo}'] {name}: {e}"
            error_message += "\n" + traceback.format_exc(limit=3)

    return TaskResult(
        site=site,
        task=task,
        approach=approach,
        drift_level=drift_level,
        drift_type=drift_type,
        repetition=repetition,
        success=success,
        duration_s=round(t.duration, 3),
        n_steps=counter.count,
        error_type=error_type,
        error_message=error_message[:500],  # evita CSV gigante em caso de stack trace longo
    )


# ---- Oráculo: verificação do estado final real, independente do agente ----

def expect_url_contains(page, substring: str) -> None:
    if substring not in page.url:
        raise AssertionError(f"URL esperada conter '{substring}', mas é '{page.url}'")


def expect_text_visible(page, text: str, timeout: int = 8000) -> None:
    locator = page.get_by_text(text, exact=False)
    try:
        locator.first.wait_for(state="visible", timeout=timeout)
    except Exception as e:  # re-lança como AssertionError para o run_task_safely categorizar certo
        raise AssertionError(f"Texto esperado '{text}' não apareceu na tela: {e}") from e


def carrinho_saucedemo(page) -> int:
    """
    Quantos itens estão no carrinho, lidos do ESTADO DA APLICAÇÃO.

    POR QUE NÃO CONTAR ELEMENTOS NA TELA
    ------------------------------------
    Verificar o carrinho por seletor de DOM cria dois problemas.

    O primeiro é de simetria. Só o braço frágil fazia essa verificação, e por
    `.cart_item` — uma classe que o drift renomeia. Ele então falhava na
    CONFERÊNCIA mesmo tendo executado a ação corretamente, enquanto os demais
    braços não conferiam nada. A comparação media coisas diferentes.

    O segundo é mais grave. Verificado no navegador: o SauceDemo conclui o
    checkout com o carrinho VAZIO e exibe "Thank you for your order!". Como o
    oráculo final procura exatamente esse texto, uma execução em que o clique de
    "adicionar ao carrinho" falhou silenciosamente — por ter acertado o elemento
    errado, por exemplo — seria registrada como SUCESSO. É o falso sucesso que
    todo o restante do instrumento existe para impedir.

    O estado da aplicação resolve os dois: `cart-contents` no armazenamento local
    é a fonte de verdade do próprio site, não depende de nome de classe nem de
    posição, e o drift não o alcança. A mesma verificação vale para os quatro
    braços sem favorecer nenhuma estratégia de localização.
    """
    conteudo = page.evaluate("() => localStorage.getItem('cart-contents')")
    if not conteudo:
        return 0
    import json
    try:
        return len(json.loads(conteudo))
    except (ValueError, TypeError):
        return 0


def expect_carrinho(page, esperado: int = 1) -> None:
    """Confere o carrinho pelo estado da aplicação. Ver `carrinho_saucedemo`."""
    n = carrinho_saucedemo(page)
    if n != esperado:
        raise AssertionError(
            f"Carrinho deveria conter {esperado} item(ns), mas o estado da "
            f"aplicação registra {n}. A ação de adicionar ao carrinho não "
            f"surtiu efeito — sem esta conferência, o fluxo seguiria e a "
            f"página de confirmação apareceria mesmo assim.")


# Script instalado no braço agêntico para registrar o PICO de itens no carrinho.
#
# Ao contrário dos braços determinísticos, ali não há como conferir o carrinho no
# meio do caminho: o experimento entrega o controle ao agente e só reassume no
# final, quando o SauceDemo já esvaziou o carrinho. Sem registro, o oráculo não
# teria como distinguir uma compra real de um checkout vazio — e o site conclui
# os dois, exibindo a mesma mensagem de confirmação.
#
# O script observa as escritas do próprio site no armazenamento e guarda o maior
# valor visto. Fica em sessionStorage para sobreviver às trocas de página.
RASTREAR_CARRINHO_JS = """
(() => {
  const CHAVE = '__pico_carrinho';
  const RESUMO = '__carrinho_no_resumo';
  const tamanho = (valor) => {
    try { return JSON.parse(valor || '[]').length; } catch (e) { return null; }
  };
  const registrarPico = (valor) => {
    const n = tamanho(valor);
    if (n === null) return;
    const atual = parseInt(sessionStorage.getItem(CHAVE) || '0', 10);
    if (n > atual) sessionStorage.setItem(CHAVE, String(n));
  };
  try {
    registrarPico(localStorage.getItem('cart-contents'));
    const original = localStorage.setItem.bind(localStorage);
    localStorage.setItem = function (chave, valor) {
      if (chave === 'cart-contents') registrarPico(valor);
      return original(chave, valor);
    };
  } catch (e) { /* documento sem armazenamento acessível */ }

  // Coleta 2: tamanho do carrinho quando a página de RESUMO do pedido
  // (checkout-step-two) aparece, que é o que será comprado. Os braços
  // determinísticos exigem exatamente 1 item antes de finalizar; o pico da
  // coleta 1 aceitava comprar dois itens ou adicionar e remover o único.
  // O SauceDemo troca de rota sem recarregar o documento, por isso a URL é
  // conferida periodicamente, e o registro ocorre uma vez a cada chegada.
  let rotaAnterior = '';
  const conferirResumo = () => {
    try {
      const rota = location.pathname;
      if (rota === rotaAnterior) return;
      rotaAnterior = rota;
      if (rota.endsWith('checkout-step-two.html')) {
        const n = tamanho(localStorage.getItem('cart-contents'));
        if (n !== null) sessionStorage.setItem(RESUMO, String(n));
      }
    } catch (e) { /* ignora */ }
  };
  conferirResumo();
  setInterval(conferirResumo, 150);
})();
"""


async def pico_do_carrinho(page) -> int:
    """Maior número de itens que o carrinho chegou a ter. Ver RASTREAR_CARRINHO_JS."""
    valor = await page.evaluate(
        "() => sessionStorage.getItem('__pico_carrinho')")
    try:
        return int(valor or 0)
    except (ValueError, TypeError):
        return 0


# Seletor dos elementos criados em tempo de execução no The Internet.
#
# Os botões "Delete" nascem DEPOIS de o drift ter sido aplicado à página, e por
# isso costumam conservar a classe original. Mas o drift persistente reaplica a
# degradação a cada mudança de endereço: se a navegação sair da página e voltar
# — comportamento observado no braço agêntico —, os elementos já existentes
# passam a ser renomeados, e uma conferência presa a `.added-manually` acusaria
# ausência de elementos que estão lá.
#
# Aceitar também o atributo que preserva a classe original torna a conferência
# indiferente à renomeação. Isso não enfraquece o experimento: contar o
# resultado da tarefa é papel do oráculo, não parte da estratégia de localização
# que está sendo medida.
ELEMENTOS_ADICIONADOS = ".added-manually, [data-drift-original-class~='added-manually']"


def expect_elementos_adicionados(page, esperado: int) -> None:
    """
    Confere quantos elementos criados em tempo de execução existem na página.

    SEPARAÇÃO DELIBERADA ENTRE AGIR E CONFERIR
    ------------------------------------------
    Cada braço LOCALIZA os elementos pela sua própria estratégia — é isso que o
    experimento mede. Mas CONTAR quantos existem é conferência, e precisa ser
    idêntica para todos, senão um braço pode falhar na verificação tendo
    executado a ação corretamente.

    O problema era real: o braço semântico contava por papel acessível
    (`get_by_role("button", name="Delete")`). Sob drift semântico o texto do
    botão ganha sufixo, o nome acessível muda, a contagem devolve zero — e a
    execução era reprovada mesmo tendo clicado nos elementos certos. O braço
    frágil, por sua vez, contava por classe, que o drift renomeia.

    Cada um falhava na conferência por um motivo diferente do que o estudo quer
    medir, e os três eram avaliados por critérios distintos.
    """
    n = page.locator(ELEMENTOS_ADICIONADOS).count()
    if n != esperado:
        raise AssertionError(
            f"Esperava {esperado} elemento(s) na lista, encontrou {n}")


# ---- Conferência do braço agêntico igual à dos determinísticos (coleta 2) ----
#
# Na coleta 1 o oráculo agêntico do The Internet só olhava o estado final da
# página Add/Remove Elements, que é pública. Um agente que não conseguiu fazer
# login e seguiu adiante era contado como sucesso, enquanto os quatro braços
# determinísticos precisavam confirmar o login e a contagem intermediária. Os
# dois scripts abaixo registram, dentro da página, o que os determinísticos
# conferem passo a passo; o oráculo lê os registros no fim.

RASTREAR_LOGIN_JS = """
(() => {
  const conferir = () => {
    try {
      if (location.pathname.startsWith('/secure') && document.body &&
          (document.body.innerText || '').includes('You logged into a secure area')) {
        sessionStorage.setItem('__login_confirmado', '1');
      }
    } catch (e) { /* ignora */ }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', conferir);
  } else {
    conferir();
  }
})();
"""

RASTREAR_ELEMENTOS_JS = """
(() => {
  if (!location.pathname.includes('add_remove_elements')) return;
  const SELETOR = ".added-manually, [data-drift-original-class~='added-manually']";
  const ehIsca = (no) => no.getAttribute && no.getAttribute('data-drift-decoy') === 'true';
  const contar = (no) => {
    if (!no || no.nodeType !== 1 || ehIsca(no)) return 0;
    let n = (no.matches && no.matches(SELETOR)) ? 1 : 0;
    if (no.querySelectorAll) n += no.querySelectorAll(SELETOR).length;
    return n;
  };
  // Por documento: se o agente recarregar a página, a contagem recomeça junto
  // com a lista de elementos, como aconteceria com qualquer usuário.
  window.__contagemElementos = { adicionados: 0, removidos: 0 };
  const iniciar = () => {
    new MutationObserver((registros) => {
      for (const r of registros) {
        r.addedNodes.forEach(no => { window.__contagemElementos.adicionados += contar(no); });
        r.removedNodes.forEach(no => { window.__contagemElementos.removidos += contar(no); });
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
"""


async def login_confirmado(page) -> bool:
    """A página segura do The Internet, com a mensagem de sucesso, foi exibida nesta aba?"""
    valor = await page.evaluate("() => sessionStorage.getItem('__login_confirmado')")
    return valor == "1"


async def contagem_elementos(page) -> dict:
    """Elementos adicionados e removidos na página Add/Remove atual. Ver RASTREAR_ELEMENTOS_JS."""
    valor = await page.evaluate("() => window.__contagemElementos || null")
    return valor or {"adicionados": None, "removidos": None}


async def carrinho_no_resumo(page):
    """Itens no carrinho quando o resumo do pedido apareceu. Ver RASTREAR_CARRINHO_JS."""
    valor = await page.evaluate("() => sessionStorage.getItem('__carrinho_no_resumo')")
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def expect_count(locator, expected: int) -> None:
    actual = locator.count()
    if actual != expected:
        raise AssertionError(f"Esperava {expected} elementos, encontrou {actual}")


def append_result_csv(result: TaskResult, csv_path: str) -> None:
    """
    Acrescenta uma execução ao CSV, validando o cabeçalho antes de escrever.

    A validação existe por causa de uma falha real: quando novas colunas foram
    acrescentadas ao TaskResult, o arquivo pré-existente manteve o cabeçalho
    antigo, enquanto as novas linhas passaram a ser gravadas com mais campos e
    em outra ordem. O csv.DictWriter não compara o cabeçalho do arquivo com os
    campos informados — ele simplesmente escreve os valores na ordem recebida.
    O resultado foram colunas deslocadas: o tipo de drift caiu na coluna de tipo
    de erro, e assim por diante, sem qualquer aviso.

    Um dataset silenciosamente desalinhado é pior que um erro: a análise roda,
    produz números, e as conclusões saem erradas. Por isso a incompatibilidade
    agora interrompe a coleta imediatamente.
    """
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    row = result.to_row()
    campos = list(row.keys())

    if os.path.isfile(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            cabecalho = next(csv.reader(f), None)
        if cabecalho is not None and cabecalho != campos:
            faltando = [c for c in campos if c not in cabecalho]
            sobrando = [c for c in cabecalho if c not in campos]
            raise RuntimeError(
                f"O cabeçalho de {csv_path} não corresponde às colunas atuais.\n"
                f"  colunas ausentes no arquivo: {faltando or 'nenhuma'}\n"
                f"  colunas a mais no arquivo:   {sobrando or 'nenhuma'}\n"
                "O arquivo foi gerado por uma versão anterior do experimento e\n"
                "misturá-lo com os dados novos produziria colunas deslocadas.\n"
                "Arquive-o ou apague-o e reinicie a coleta:\n"
                f"  del {csv_path}"
            )

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow(row)
