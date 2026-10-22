"""
Self-healing de seletores por similaridade.

IDEIA
-----
Na automação determinística, quando o seletor `#user-name` deixa de existir a
execução falha. Aqui, em vez de falhar, o script tenta REENCONTRAR o elemento:

  1. Numa execução de referência (página limpa), grava-se uma "impressão
     digital" de cada elemento-alvo: tag, atributos, texto, classes e posição.
  2. Em produção, se o seletor original não encontrar nada, varre-se a página,
     pontua-se cada candidato por similaridade com a impressão digital e
     escolhe-se o melhor — desde que ultrapasse um limiar.

Isso posiciona o braço entre o determinístico (que quebra) e o agêntico (que
interpreta a página), sem custo de API.

POR QUE ISTO É UM PROBLEMA DE CIÊNCIA DE DADOS
----------------------------------------------
"Qual dos N candidatos é o elemento certo?" é uma tarefa de pontuação e decisão
com limiar. O limiar controla o compromisso clássico: baixo demais, o healer
agarra o elemento errado (falso positivo, potencialmente pior que falhar);
alto demais, ele desiste quando poderia ter acertado (falso negativo).

A calibração desse limiar é feita em `calibrar_limiar.py`, medindo precisão e
revocação contra uma verdade de referência — possível porque a injeção de drift
preserva o id original no atributo `data-drift-original-id`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

# Limiar padrão: definido empiricamente em calibrar_limiar.py. Ajuste por
# ambiente com a variável HEAL_THRESHOLD.
LIMIAR_PADRAO = float(os.getenv("HEAL_THRESHOLD", "0.62"))

# Margem mínima sobre o segundo colocado.
#
# Motivo: em teste, o campo de senha pontuou 0,691 contra a impressão digital do
# campo de usuário — acima do limiar. Enquanto o alvo verdadeiro está na página
# isso é inofensivo (vence com 0,985), mas se ele for REMOVIDO o healer agarraria
# o campo errado com confiança, preenchendo o usuário no lugar da senha. Esse
# tipo de falha é pior que falhar explicitamente, porque produz um falso sucesso.
#
# Exigir uma diferença mínima entre o primeiro e o segundo colocado transforma
# "achei algo parecido" em "achei algo inequivocamente mais parecido".
MARGEM_PADRAO = float(os.getenv("HEAL_MARGIN", "0.10"))

# Quanto esperar pelo seletor original antes de concluir que ele falhou.
#
# Motivo: a versão anterior decidia com `count()`, na hora. Os outros braços
# esperam o elemento (o Playwright faz isso em cada ação, até 10 s); este não.
# No SauceDemo, que troca de página sem recarregar, a conferência às vezes caía
# entre a mudança de endereço e o desenho da página nova: o botão ainda não
# existia, a cura era acionada sem degradação nenhuma e pontuava uma página
# incompleta. Na coleta 2 interrompida em 17/09/2026 isso deu uma falha na linha
# de base ('botao_checkout', pontuação 0,440) e curas do elemento errado. Com a
# espera, sob degradação a cura só começa depois dela, na página já desenhada.
ESPERA_ORIGINAL_MS = 3000

# Atributos que entram na similaridade. `data_test` fica de fora de propósito:
# ver a nota em `_sim_atributos`.
ATRIBUTOS_PONTUADOS = ("name", "placeholder", "type", "aria_label", "role", "value")

# Pesos dos componentes da similaridade. Somam 1.0.
PESOS = {
    "tag": 0.10,          # mesma tag HTML
    "atributos": 0.35,    # name, placeholder, type, aria-label, role, value
    "texto": 0.25,        # texto visível
    "classes": 0.10,      # similaridade textual das classes
    "posicao": 0.20,      # proximidade na tela (normalizada)
}

# Coleta candidatos da página. Limita a 500 elementos visíveis para não estourar
# o custo da varredura em páginas grandes.
_COLETAR_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const els = Array.from(document.querySelectorAll('body *')).filter(vis).slice(0, 500);
  const W = Math.max(window.innerWidth, 1), H = Math.max(window.innerHeight, 1);
  return els.map((el, i) => {
    const r = el.getBoundingClientRect();
    return {
      indice: i,
      tag: el.tagName.toLowerCase(),
      classes: (typeof el.className === 'string' ? el.className : ''),
      texto: (el.textContent || '').trim().slice(0, 120),
      atributos: {
        name: el.getAttribute('name') || '',
        placeholder: el.getAttribute('placeholder') || '',
        type: el.getAttribute('type') || '',
        aria_label: el.getAttribute('aria-label') || '',
        data_test: el.getAttribute('data-test') || '',
        role: el.getAttribute('role') || '',
        value: (el.tagName === 'INPUT' ? (el.getAttribute('value') || '') : ''),
      },
      x: (r.left + r.width / 2) / W,
      y: (r.top + r.height / 2) / H,
      // usados apenas pela calibração como verdade de referência:
      id_original: el.getAttribute('data-drift-original-id') || el.id || '',
      isca: el.getAttribute('data-drift-decoy') === 'true',
    };
  });
}
"""

# Verdade de referência de UMA cura, avaliada DEPOIS da escolha.
#
# Na coleta 1 o artigo inferiu, por eliminação, que o healer "escolheu o
# elemento errado com confiança". Os registros não sustentavam a inferência: as
# falhas eram desistências e o login quebrado pelo degradador. Aqui a pergunta é
# respondida diretamente. O degradador preserva a identidade original de cada
# elemento (data-drift-original-id e data-drift-original-class); com ela,
# reavalia-se o seletor original como se a página estivesse íntegra e confere-se
# se o elemento escolhido está entre os que ele encontraria.
#
# Não participa da decisão do healer, que continua sem acesso a esses
# atributos: só mede o acerto.
_VERDADE_JS = r"""
([marca, seletor]) => {
  const escolhido = document.querySelector(`[data-heal-alvo="${marca}"]`);
  if (!escolhido) return 'indeterminado';
  const naoIsca = (el) => el.getAttribute('data-drift-decoy') !== 'true';
  let verdadeiros = [];
  let m = seletor.match(/^#([A-Za-z0-9_-]+)$/);
  if (m) {
    verdadeiros = Array.from(document.querySelectorAll('[id]'))
      .filter(el => (el.getAttribute('data-drift-original-id') || el.id) === m[1]);
  } else if ((m = seletor.match(/^\.([A-Za-z0-9_-]+)$/))) {
    verdadeiros = Array.from(document.querySelectorAll('[class]')).filter(el => {
      const original = el.getAttribute('data-drift-original-class') ||
        (typeof el.className === 'string' ? el.className : '');
      return original.split(/\s+/).includes(m[1]);
    });
  } else {
    try {
      verdadeiros = Array.from(document.querySelectorAll(seletor));
    } catch (e) {
      return 'indeterminado';
    }
  }
  verdadeiros = verdadeiros.filter(naoIsca);
  if (!verdadeiros.length) return 'indeterminado';
  return verdadeiros.includes(escolhido) ? 'correto' : 'errado';
}
"""

# Marca o elemento escolhido para que o Playwright possa agir sobre ele.
_MARCAR_JS = r"""
([indice, marca]) => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const els = Array.from(document.querySelectorAll('body *')).filter(vis).slice(0, 500);
  const el = els[indice];
  if (!el) return false;
  el.setAttribute('data-heal-alvo', marca);
  return true;
}
"""


@dataclass
class ImpressaoDigital:
    """Descrição de um elemento-alvo, capturada numa página sem drift."""

    nome: str                       # rótulo lógico, ex.: "campo_usuario"
    seletor_original: str           # ex.: "#user-name"
    id_elemento: str = ""           # id real do elemento na página íntegra
    tag: str = ""
    classes: str = ""
    texto: str = ""
    atributos: dict = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ImpressaoDigital":
        return ImpressaoDigital(**d)


def _sim_texto(a: str, b: str) -> float:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _sim_atributos(a: dict, b: dict) -> float:
    """
    Compara os atributos semânticos. Só conta os que existem na impressão
    digital: comparar campos vazios dos dois lados inflaria a pontuação de
    qualquer elemento sem atributos.
    """
    # `data_test` NÃO entra na pontuação a partir da coleta 2. O degradador
    # preserva esse atributo por ser contrato de teste, e as execuções agênticas
    # que o usaram foram excluídas por contornarem a interface degradada. Na
    # coleta 1, porém, ele pesava aqui para os 11 alvos do SauceDemo: o mesmo
    # atalho era proibido a um braço e concedido ao outro. Nenhum braço usa o
    # contrato de teste agora. O atributo continua capturado, só para registro.
    chaves = [k for k in ATRIBUTOS_PONTUADOS if (a or {}).get(k)]
    if not chaves:
        return 0.0
    return sum(_sim_texto(a.get(k, ""), (b or {}).get(k, "")) for k in chaves) / len(chaves)


def _sim_posicao(fp_x: float, fp_y: float, cx: float, cy: float) -> float:
    """1.0 no mesmo ponto, caindo com a distância euclidiana normalizada."""
    d = ((fp_x - cx) ** 2 + (fp_y - cy) ** 2) ** 0.5
    return max(0.0, 1.0 - d / 1.4142)  # 1.4142 = diagonal do espaço normalizado


def pontuar(fp: ImpressaoDigital, candidato: dict) -> float:
    """
    Similaridade entre 0 e 1 da impressão digital com um candidato.

    Componentes SEM INFORMAÇÃO são descartados, e os pesos redistribuídos entre
    os restantes. Isso corrige um viés medido na prática: quando a impressão
    digital e o candidato têm ambos texto vazio — o caso de qualquer campo de
    formulário — a similaridade textual valia 1,0 e concedia crédito máximo por
    uma informação inexistente. O efeito era inflar a nota de todos os campos e
    comprimir a distância entre eles: o campo "First Name" pontuava 0,985 e o
    "Last Name" 0,900, margem de apenas 0,084, insuficiente para uma decisão
    segura. Descartado o componente vazio, a distância volta a refletir o que
    de fato distingue os elementos.
    """
    componentes = {
        "tag": (1.0 if fp.tag == candidato.get("tag") else 0.0, True),
        "atributos": (
            _sim_atributos(fp.atributos, candidato.get("atributos")),
            any((fp.atributos or {}).get(k) for k in ATRIBUTOS_PONTUADOS),
        ),
        "texto": (
            _sim_texto(fp.texto, candidato.get("texto", "")),
            bool((fp.texto or "").strip()),
        ),
        "classes": (
            _sim_texto(fp.classes, candidato.get("classes", "")),
            bool((fp.classes or "").strip()),
        ),
        "posicao": (
            _sim_posicao(fp.x, fp.y, candidato.get("x", 0.0), candidato.get("y", 0.0)),
            True,
        ),
    }

    ativos = {k: v for k, (v, informativo) in componentes.items() if informativo}
    peso_total = sum(PESOS[k] for k in ativos) or 1.0
    return round(sum(PESOS[k] * v for k, v in ativos.items()) / peso_total, 6)


def coletar_candidatos(page) -> list:
    return page.evaluate(_COLETAR_JS)


def compativel(fp: ImpressaoDigital, candidato: dict) -> bool:
    """
    Filtro rígido aplicado antes da pontuação.

    A similaridade é contínua e, sozinha, admite trocas que jamais são corretas:
    um campo de texto "curado" para um campo de senha, ou um input para uma div.
    Esses casos produziriam falso sucesso (o script preenche algo, o fluxo segue,
    e o erro só aparece no resultado final). Por isso tag e tipo são tratados
    como restrições, não como pontos.
    """
    if fp.tag and fp.tag != candidato.get("tag"):
        return False
    tipo_fp = (fp.atributos or {}).get("type", "")
    tipo_cand = (candidato.get("atributos") or {}).get("type", "")
    if tipo_fp and tipo_fp != tipo_cand:
        return False
    return True


def melhor_candidato(fp: ImpressaoDigital, candidatos: list) -> tuple:
    """
    Devolve (candidato, pontuação, margem) do melhor candidato.

    `margem` é a diferença para o segundo colocado — quanto maior, mais
    inequívoca é a escolha. Sem candidatos, devolve (None, 0.0, 0.0).
    """
    elegiveis = [c for c in candidatos if compativel(fp, c)]
    pontuados = sorted(((pontuar(fp, c), c) for c in elegiveis),
                       key=lambda t: t[0], reverse=True)
    if not pontuados:
        return None, 0.0, 0.0
    melhor_p, melhor = pontuados[0]
    segundo_p = pontuados[1][0] if len(pontuados) > 1 else 0.0
    return melhor, melhor_p, round(melhor_p - segundo_p, 6)


def capturar(page, nome: str, seletor: str) -> ImpressaoDigital:
    """Captura a impressão digital de um elemento numa página SEM drift."""
    dados = page.evaluate(
        """(seletor) => {
            const el = document.querySelector(seletor);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            const W = Math.max(window.innerWidth, 1), H = Math.max(window.innerHeight, 1);
            return {
              id_elemento: el.id || '',
              tag: el.tagName.toLowerCase(),
              classes: (typeof el.className === 'string' ? el.className : ''),
              texto: (el.textContent || '').trim().slice(0, 120),
              atributos: {
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || '',
                type: el.getAttribute('type') || '',
                aria_label: el.getAttribute('aria-label') || '',
                data_test: el.getAttribute('data-test') || '',
                role: el.getAttribute('role') || '',
                value: (el.tagName === 'INPUT' ? (el.getAttribute('value') || '') : ''),
              },
              x: (r.left + r.width / 2) / W,
              y: (r.top + r.height / 2) / H,
            };
        }""",
        seletor,
    )
    if not dados:
        raise RuntimeError(f"elemento '{seletor}' não encontrado para capturar ({nome})")
    return ImpressaoDigital(nome=nome, seletor_original=seletor, **dados)


class SessaoHealing:
    """
    Envolve a página e resolve localizadores com self-healing.

    Uso:
        s = SessaoHealing(page, impressoes)
        s.localizar("campo_usuario").fill("...")

    Estatísticas de cada execução ficam em `s.estatisticas`, permitindo
    reportar com que frequência o healing foi acionado e com que sucesso.
    """

    def __init__(self, page, impressoes: dict, limiar: float = LIMIAR_PADRAO,
                 margem: float = MARGEM_PADRAO):
        self.page = page
        self.impressoes = impressoes
        self.limiar = limiar
        self.margem = margem
        self.estatisticas = {"acionamentos": 0, "curados": 0, "desistencias": 0,
                             "desistencias_por_margem": 0,
                             # verdade de referência de cada cura (ver _VERDADE_JS)
                             "corretos": 0, "errados": 0, "indeterminados": 0}
        self._contador_marca = 0

    def _original_presente(self, alvo) -> bool:
        """O seletor original encontra o elemento em até ESPERA_ORIGINAL_MS?"""
        if alvo.count() > 0:
            return True
        try:
            alvo.first.wait_for(state="attached", timeout=ESPERA_ORIGINAL_MS)
            return True
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ != "TimeoutError":   # TimeoutError do Playwright
                raise
            return False

    def localizar(self, nome: str):
        fp = self.impressoes.get(nome)
        if fp is None:
            raise KeyError(f"sem impressão digital para '{nome}' — rode capturar_fingerprints.py")

        alvo = self.page.locator(fp.seletor_original)
        if self._original_presente(alvo):
            return alvo.first

        # seletor original falhou: tenta curar
        self.estatisticas["acionamentos"] += 1
        candidatos = coletar_candidatos(self.page)
        candidato, pontuacao, margem = melhor_candidato(fp, candidatos)

        if candidato is None or pontuacao < self.limiar:
            self.estatisticas["desistencias"] += 1
            raise AssertionError(
                f"healing falhou para '{nome}': melhor pontuação {pontuacao:.3f} "
                f"abaixo do limiar {self.limiar:.3f}"
            )

        if margem < self.margem:
            # há dois candidatos parecidos demais; escolher seria arriscar um
            # falso sucesso. Desistir explicitamente é preferível.
            self.estatisticas["desistencias"] += 1
            self.estatisticas["desistencias_por_margem"] += 1
            raise AssertionError(
                f"healing ambíguo para '{nome}': pontuação {pontuacao:.3f}, mas a "
                f"margem sobre o segundo colocado ({margem:.3f}) é menor que "
                f"{self.margem:.3f}"
            )

        self._contador_marca += 1
        marca = f"heal{self._contador_marca}"
        if not self.page.evaluate(_MARCAR_JS, [candidato["indice"], marca]):
            self.estatisticas["desistencias"] += 1
            raise AssertionError(f"healing não conseguiu marcar o elemento de '{nome}'")

        self.estatisticas["curados"] += 1
        try:
            veredito = self.page.evaluate(_VERDADE_JS, [marca, fp.seletor_original])
        except Exception:  # noqa: BLE001 - medir não pode derrubar a execução
            veredito = "indeterminado"
        chave = {"correto": "corretos", "errado": "errados"}.get(veredito, "indeterminados")
        self.estatisticas[chave] += 1
        return self.page.locator(f'[data-heal-alvo="{marca}"]').first


def salvar_impressoes(impressoes: dict, caminho: str) -> None:
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({k: v.to_dict() for k, v in impressoes.items()}, f,
                  ensure_ascii=False, indent=1)


def carregar_impressoes(caminho: str) -> dict:
    if not os.path.isfile(caminho):
        raise FileNotFoundError(
            f"{caminho} não existe. Rode antes:  python capturar_fingerprints.py"
        )
    with open(caminho, encoding="utf-8") as f:
        return {k: ImpressaoDigital.from_dict(v) for k, v in json.load(f).items()}
