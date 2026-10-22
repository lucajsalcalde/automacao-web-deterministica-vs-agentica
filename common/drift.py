"""
Injeção controlada de "drift" de UI, sem modificar o site original.

Como o ambiente não pertence ao pesquisador, o drift é aplicado no lado do
cliente: depois que o Playwright carrega a página, `apply_drift()` executa um
script no navegador que altera o DOM já renderizado, de forma parametrizada por
`tipo` e `level`, e reprodutível por `seed`.

TIPOS DE DRIFT
--------------
O drift tem duas dimensões independentes: QUANTO muda (`level`) e O QUE muda
(`tipo`). Os tipos são cumulativos e modelam classes distintas de mudança que
ocorrem em aplicações reais:

  cosmetico  — renomeia id e classes CSS.
               Equivale a uma refatoração de estilo ou troca de framework de CSS.
               Quebra seletores acoplados à implementação.

  estrutural — cosmético + insere irmãos de mesma tag, desloca visualmente e
               (onde é seguro) acrescenta contêineres intermediários.
               Equivale a uma reorganização de layout. Desloca os índices do
               caminho no DOM, quebrando localizadores por XPath, e degrada a
               pista posicional usada pelo self-healing.

  semantico  — estrutural + acrescenta um sufixo numérico ("Login" vira
               "Login_807") a placeholder, aria-label, title, alt, texto visível
               de elementos-folha e value de botões.
               Quebra a correspondência exata de rótulos, mas preserva o
               significado para um leitor humano ou um modelo de linguagem.
               NÃO altera `name` nem o value de campos: esses atributos são
               dados que a aplicação envia ao servidor, não sinais de
               identificação (ver a nota no bloco semântico do script).

POR QUE ISTO IMPORTA PARA O EXPERIMENTO
----------------------------------------
Uma versão anterior deste módulo aplicava apenas o tipo cosmético. Nessa
condição, os braços semântico e self-healing permaneciam praticamente imunes em
qualquer intensidade, porque os sinais de que dependem (texto, placeholder,
name, posição) eram preservados por construção. O experimento produziria três
curvas planas e uma em queda, sem qualquer ponto de cruzamento.

Com os três tipos, cada braço encontra a condição que efetivamente o desafia, e
a comparação passa a ter poder discriminativo.

NOTA: atributos `data-test` NÃO são alterados em nenhum tipo. Eles existem como
contrato explícito de automação e as equipes os mantêm estáveis justamente
através de refatorações; alterá-los não corresponderia à prática real.

PRESERVAÇÃO DA APARÊNCIA
------------------------
Renomear classes CSS sem mais nada faz o navegador deixar de aplicar os estilos:
a página se desmonta visualmente e elementos ficam sem dimensão, portanto não
clicáveis. Isso reprovaria todos os braços por um motivo alheio ao objeto de
estudo — inclusive o agêntico, que veria uma página quebrada.

Uma refatoração real renomeia as classes E atualiza a folha de estilo, de modo
que a página continua com a mesma aparência. Para reproduzir isso:

  1. O renomeamento é feito por TOKEN — o mesmo nome de classe recebe o mesmo
     novo nome em toda a página, como numa refatoração de verdade.
  2. Antes de renomear, o estilo COMPUTADO de cada elemento é fixado inline.
     A página passa a carregar sua própria aparência e não depende mais das
     regras CSS que deixarão de casar.
  3. Adicionalmente, tenta-se reescrever as regras das folhas de estilo com o
     mesmo mapa. Isso só funciona em folhas de mesma origem: o SauceDemo, por
     exemplo, serve o CSS de outro domínio e o navegador bloqueia a leitura das
     regras — daí o passo 2 ser o mecanismo principal, e não um complemento.

Verificado empiricamente no SauceDemo: com drift cosmético em intensidade
máxima, o link do carrinho manteve posição (x=1845, y=10) e tamanho (40x40)
idênticos aos originais, enquanto os seletores por id e por classe deixaram de
encontrá-lo. Sem esse cuidado, o elemento era deslocado para a origem da página
e ficava sem dimensão, tornando-se não clicável — o que reprovaria todos os
braços por um motivo alheio ao objeto de estudo.
"""

from __future__ import annotations

TIPOS = ("cosmetico", "estrutural", "semantico")

_DRIFT_JS = r"""
([tipo, level, seed]) => {
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const rand = mulberry32(seed);
  const stats = { tipo: tipo, candidatos: 0, identidade: 0, ids: 0, classes: 0,
                  movidos: 0, deslocados: 0, envolvidos: 0, semanticos: 0,
                  textos: 0, iscas: 0, regras_css: 0, folhas_inacessiveis: 0, congelados: 0, referencias: 0,
                  cirurgia_bloqueada: 0 };
  if (level <= 0) return stats;

  const fazEstrutural = (tipo === 'estrutural' || tipo === 'semantico');
  const fazSemantico = (tipo === 'semantico');

  const todos = Array.from(document.querySelectorAll('body *'))
    .filter(el => el.id || (el.className && typeof el.className === 'string'));
  stats.candidatos = todos.length;

  // ---- MAPAS GLOBAIS DE RENOMEAÇÃO ----
  // O sorteio é por TOKEN, não por elemento: um mesmo nome de classe recebe o
  // mesmo novo nome em toda a página. Isso permite reescrever o CSS de forma
  // consistente ao final, preservando a aparência.
  // Só elementos INTERATIVOS têm o id renomeado. Ids de contêineres costumam
  // ser pontos de ancoragem do JavaScript da própria aplicação: verificado no
  // The Internet, renomear o id do contêiner faz getElementById('elements')
  // retornar nulo e a função addElement() para de inserir elementos — ou seja,
  // o drift quebraria a APLICAÇÃO, não os seletores. Uma refatoração real que
  // renomeasse esse id atualizaria o script junto. Restringir aos elementos
  // interativos mantém a fidelidade e não enfraquece o experimento, pois é
  // neles que a automação se ancora.
  const INTERATIVOS = new Set(['input', 'button', 'select', 'textarea', 'a', 'option']);

  // ---- DETECÇÃO DE SUBÁRVORE ADMINISTRADA POR FRAMEWORK ----
  //
  // Envolver um elemento o MOVE para um novo pai; inserir uma isca altera a
  // lista de filhos do pai. Frameworks que reconciliam o DOM guardam referências
  // aos nós e aos pais deles, e na atualização seguinte tentam remover um filho
  // de um pai que já não é o pai — `NotFoundError: removeChild` — o que derruba
  // a aplicação inteira.
  //
  // Medido no SauceDemo por ablação, com o sorteio mantido idêntico: com todas
  // as operações ativas, preencher o primeiro campo do checkout levava a página
  // de 71 para 4 elementos; desativando APENAS o envolvimento, 66 para 66.
  // Nenhuma operação isolada reproduz a falha, o que despistou três tentativas
  // de correção anteriores.
  //
  // O sintoma no dataset era traiçoeiro: o braço self-healing registrava
  // "melhor pontuação 0.000" ao procurar o campo seguinte — mensagem que parece
  // uma limitação do mecanismo de similaridade, quando na verdade não havia
  // mais página onde procurar.
  //
  // React e Vue marcam os nós que administram com propriedades próprias. É essa
  // marca, e não a presença de um <form>, que delimita onde a cirurgia é
  // segura: no SauceDemo os elementos afetados estavam no cabeçalho e no rodapé,
  // fora de qualquer formulário.
  //
  // ALCANCE DA RESTRIÇÃO: vale apenas para o ENVOLVIMENTO, que move o nó para
  // outro pai. A inserção de irmãos (iscas) não move nada e continua valendo em
  // qualquer página — é ela que desloca os índices do caminho no DOM e desafia
  // localização por XPath. Medido: SauceDemo tem 25 de 27 elementos
  // administrados pelo React; The Internet, 0 de 19.
  const administradoPorFramework = (el) => {
    if (!el) return false;
    for (const chave in el) {
      if (chave.startsWith('__reactFiber$') ||
          chave.startsWith('__reactProps$') ||
          chave.startsWith('__reactInternalInstance$') ||
          chave === '__vue__' || chave === '__vueParentComponent') {
        return true;
      }
    }
    return false;
  };
  // NÍVEIS ANINHADOS: o sorteio e o sufixo são SEMPRE consumidos.
  //
  // Antes, o segundo `rand()` (o do sufixo) só era chamado quando o primeiro
  // passava no teste. Isso fazia a sequência do gerador divergir entre níveis:
  // com a mesma semente, o nível 0,75 deixava de degradar cinco tokens que o
  // nível 0,50 degradava. A intensidade não era uma escala ordenada, e sim
  // quatro sorteios diferentes.
  //
  // O efeito apareceu no piloto: os braços semântico e self-healing falhavam no
  // nível 0,5 e voltavam a passar no 0,75, e a auditoria acusou monotonicidade
  // violada. Não era defeito do braço — era o próprio tratamento não sendo
  // monótono.
  //
  // Consumindo os dois sorteios sempre, o conjunto degradado no nível menor
  // passa a estar contido no do nível maior. A monotonicidade deixa de ser
  // esperança estatística e passa a ser propriedade estrutural, o que também dá
  // mais poder ao teste de tendência.
  const mapaClasses = {}, mapaIds = {};
  todos.forEach(el => {
    const sorteioId = rand(), sufixoId = Math.floor(rand() * 100000);
    if (el.id && !(el.id in mapaIds) && INTERATIVOS.has(el.tagName.toLowerCase())
        && sorteioId < level) {
      mapaIds[el.id] = el.id + '-' + sufixoId;
    }
    if (el.className && typeof el.className === 'string') {
      el.className.split(' ').filter(Boolean).forEach(c => {
        const sorteio = rand(), sufixo = Math.floor(rand() * 1000);
        if (!(c in mapaClasses) && sorteio < level) {
          mapaClasses[c] = c + '-' + sufixo;
        }
      });
    }
  });

  // ---- CONGELAMENTO DA APARÊNCIA ----
  // Renomear classes faz o navegador deixar de aplicar as regras CSS: elementos
  // perdem dimensão e deixam de ser clicáveis. Muitas folhas de estilo são
  // servidas de outra origem, e o navegador impede reescrevê-las — então a
  // solução robusta é fixar o estilo COMPUTADO de cada elemento inline, antes
  // de renomear. A página passa a carregar sua própria aparência, e a mudança
  // fica restrita à identificação, que é o objeto de estudo.
  //
  // Fixar apenas propriedades selecionadas não basta: a posição depende também
  // de propriedades de flex e grid dos ancestrais. Verificado empiricamente —
  // com lista curada o elemento mantinha o tamanho mas era deslocado para a
  // origem da página; com o estilo completo, posição e tamanho ficam idênticos.
  if (Object.keys(mapaClasses).length || Object.keys(mapaIds).length) {
    const paraCongelar = [document.body, ...document.querySelectorAll('body *')]
      .slice(0, 1500);
    paraCongelar.forEach(el => {
      const cs = getComputedStyle(el);
      const decl = [];
      for (let i = 0; i < cs.length; i++) {
        const prop = cs[i];
        const v = cs.getPropertyValue(prop);
        if (v) decl.push(prop + ':' + v);
      }
      el.setAttribute('style', decl.join(';'));
      stats.congelados++;
    });
  }

  todos.forEach(el => {
    // ---- 1. COSMÉTICO: identidade (id e classes) ----
    let mudouIdentidade = false;
    if (el.id && mapaIds[el.id]) {
      el.setAttribute('data-drift-original-id', el.id);
      el.id = mapaIds[el.id];
      stats.ids++;
      mudouIdentidade = true;
    }
    if (el.className && typeof el.className === 'string' && el.className.trim()) {
      const partes = el.className.split(' ').filter(Boolean);
      if (partes.some(c => mapaClasses[c])) {
        el.setAttribute('data-drift-original-class', el.className);
        el.className = partes.map(c => mapaClasses[c] || c).join(' ');
        stats.classes++;
        mudouIdentidade = true;
      }
    }
    if (mudouIdentidade) stats.identidade++;

    // Atualiza as REFERÊNCIAS ao id renomeado. Um <label for="x"> apontando
    // para um id que deixou de existir rompe a associação rótulo-campo, e
    // localizadores por rótulo param de funcionar — efeito que uma refatoração
    // real não produziria, porque o desenvolvedor atualizaria o `for` junto.
    if (el.id && el.getAttribute('data-drift-original-id')) {
      const antigo = el.getAttribute('data-drift-original-id');
      const novo = el.id;
      document.querySelectorAll(`label[for="${antigo}"]`).forEach(l => {
        l.setAttribute('for', novo);
        stats.referencias++;
      });
      for (const attr of ['aria-labelledby', 'aria-describedby', 'aria-controls',
                          'aria-owns', 'headers', 'list', 'form']) {
        document.querySelectorAll(`[${attr}~="${antigo}"]`).forEach(o => {
          o.setAttribute(attr,
            o.getAttribute(attr).split(/\s+/).map(t => t === antigo ? novo : t).join(' '));
          stats.referencias++;
        });
      }
    }

    // ---- 2. ESTRUTURAL: hierarquia e posição ----
    if (fazEstrutural) {
      // Elementos interativos são elevados no empilhamento. Sem isto, um
      // contêiner deslocado passa por cima de um botão ou link e o navegador
      // considera o alvo obstruído — verificado no SauceDemo, onde uma div de
      // cabeçalho cobria o link do carrinho, embora o link estivesse visível e
      // na posição correta. A falha seria por obstrução física, não por
      // incapacidade de localizar o elemento, e reprovaria todos os braços.
      if (INTERATIVOS.has(el.tagName.toLowerCase())) {
        if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
        el.style.zIndex = '20';
      }

      // Envolve o elemento num contêiner extra: aprofunda a árvore e quebra
      // seletores de caminho (pai > filho) sem alterar a aparência.
      //
      // NUNCA envolve elementos interativos nem elementos que contenham algum:
      // o contêiner usa `display: contents` para não afetar o layout, e o
      // Chromium REMOVE elementos com essa propriedade da árvore de
      // acessibilidade. Verificado no SauceDemo — o botão Checkout, envolvido
      // diretamente por um desses contêineres, continuava existindo no DOM, com
      // texto e dimensões corretos, mas deixava de ser encontrado por
      // localizadores baseados em papel acessível. O braço semântico falhava
      // sem que nada estivesse visualmente errado.
      //
      // Restringir o envolvimento a contêineres puramente estruturais preserva
      // a intenção (árvore mais profunda) sem alterar a exposição semântica dos
      // alvos da automação.
      const temInterativo = el.querySelector('input, button, select, textarea, a, option');
      const sorteioEnvolver = rand();
      if (!INTERATIVOS.has(el.tagName.toLowerCase()) && !temInterativo &&
          sorteioEnvolver < level * 0.5 && el.parentElement &&
          el.parentElement.tagName !== 'BODY') {
        if (administradoPorFramework(el.parentElement)) {
          stats.cirurgia_bloqueada++;
        } else {
        const wrapper = document.createElement('div');
        wrapper.setAttribute('data-drift-wrapper', 'true');
        // display:contents faz o contêiner não gerar caixa própria: a árvore
        // fica mais profunda (quebrando seletores de caminho, que é a intenção)
        // sem alterar o layout. Sem isto, o div extra vira item de flex/grid e
        // desloca o conteúdo, tornando elementos não clicáveis por um motivo
        // que não é o objeto de estudo.
        wrapper.style.display = 'contents';
        el.parentElement.insertBefore(wrapper, el);
        wrapper.appendChild(el);
        stats.envolvidos++;
        }
      }
      // Deslocamento visual: degrada a pista posicional usada pelo self-healing
      // e por abordagens visuais.
      //
      // Aplica-se APENAS a elementos interativos. Deslocar contêineres fazia
      // divs e spans passarem por cima de botões e links: no SauceDemo, uma div
      // de cabeçalho e depois um span de título cobriram o link do carrinho, que
      // permanecia visível e na posição correta, mas era recusado pelo navegador
      // como obstruído. Tentou-se resolver com z-index, sem sucesso — contextos
      // de empilhamento criados pelos ancestrais prevalecem sobre o valor do
      // elemento. Restringir o deslocamento aos próprios alvos elimina a
      // obstrução na origem e preserva a intenção: é a posição do elemento
      // procurado que deve mudar, não a moldura ao redor dele.
      const sorteioDeslocar = rand();
      const desvioX = (rand() * 80 - 40).toFixed(1);
      const desvioY = (rand() * 40 - 20).toFixed(1);
      if (INTERATIVOS.has(el.tagName.toLowerCase()) && sorteioDeslocar < level * 0.6) {
        el.style.position = 'relative';
        el.style.left = desvioX + 'px';
        el.style.top = desvioY + 'px';
        stats.deslocados++;
      }
      // A reordenação de irmãos foi REMOVIDA do drift estrutural.
      //
      // Motivo: ela não desafia nenhuma das quatro abordagens — nenhuma se
      // apoia na ordem do DOM como estratégia de localização — mas introduzia
      // dois efeitos indesejados. Primeiro, altera qual elemento o `.first` de
      // um localizador com múltiplas correspondências seleciona, o que é
      // variação sem relação com robustez. Segundo, move elementos para
      // posições de fluxo diferentes daquelas em que seu estilo computado foi
      // congelado, produzindo artefatos de layout que tornavam alvos
      // inalcançáveis por motivos físicos.
      //
      // O drift estrutural mantém seu significado com as duas operações que
      // restam: inserção de contêineres (aprofunda a árvore e quebra seletores
      // de caminho) e deslocamento dos elementos interativos (degrada a pista
      // posicional usada pelo self-healing).
    }

    // ---- 3. SEMÂNTICO: significado ----
    const sorteioSemantico = rand();
    const sufixoSemantico = '_' + Math.floor(rand() * 1000);
    if (fazSemantico && sorteioSemantico < level) {
      const sufixo = sufixoSemantico;
      let mexeu = false;
      // `name` ficou FORA a partir da coleta 2. Num formulário, o name é a
      // chave com que o navegador envia o campo ao servidor: renomeá-lo não
      // muda como o elemento é identificado na tela, muda o que a aplicação
      // recebe. Na coleta 1 isso quebrou o login do The Internet ("Your
      // username is invalid!") para todos os braços, inclusive o agêntico, e
      // as falhas foram lidas como falhas de localização. A degradação só pode
      // atingir sinais de identificação, nunca o comportamento da aplicação.
      // A lista de sorteios não mudou: os mesmos elementos continuam sorteados.
      for (const attr of ['placeholder', 'aria-label', 'title', 'alt']) {
        const v = el.getAttribute(attr);
        if (v) {
          el.setAttribute('data-drift-original-' + attr, v);
          el.setAttribute(attr, v + sufixo);
          mexeu = true;
        }
      }
      // altera o texto visível de rótulos e botões, preservando a estrutura:
      // só mexe em elementos-folha, para não destruir a página
      if (el.children.length === 0 && el.textContent && el.textContent.trim()) {
        const original = el.textContent;
        el.setAttribute('data-drift-original-text', original.trim().slice(0, 80));
        el.textContent = original.trim() + sufixo;
        stats.textos++;
        mexeu = true;
      }
      // botões <input type=submit> exibem o texto pelo atributo value. Só nos
      // inputs do tipo botão: num campo de texto o value é dado enviado ao
      // servidor, e alterá-lo mudaria o comportamento da aplicação.
      const TIPOS_BOTAO = ['submit', 'button', 'reset'];
      if (el.tagName === 'INPUT' && el.getAttribute('value') &&
          TIPOS_BOTAO.includes((el.getAttribute('type') || '').toLowerCase())) {
        const v = el.getAttribute('value');
        el.setAttribute('data-drift-original-value', v);
        el.setAttribute('value', v + sufixo);
        mexeu = true;
      }
      if (mexeu) stats.semanticos++;
    }

    // ---- ISCAS: cópias ocultas inseridas como IRMÃS ----
    //
    // Movidas para dentro do bloco ESTRUTURAL. Antes eram aplicadas em todos os
    // tipos, o que contrariava a definição — o cosmético deve alterar apenas a
    // identidade — e apagava a diferença entre as duas condições.
    //
    // A isca é criada com a MESMA TAG do elemento e inserida antes dele. Isso
    // desloca o índice do alvo no caminho do DOM: `input[1]` passa a ser
    // `input[2]`, e um XPath posicional passa a apontar para a cópia oculta.
    // Verificado no navegador: após a inserção, o XPath do campo de usuário do
    // SauceDemo resolvia para a isca, não para o campo real.
    //
    // Ao contrário do envolvimento, inserir um irmão NÃO move o alvo para outro
    // pai, e por isso é seguro em páginas administradas por framework — medido
    // por ablação: com iscas e sem contêineres, a página do checkout não
    // desmontou (66 elementos antes e depois). O bloqueio anterior alcançava as
    // duas operações e era largo demais.
    const sorteioIsca = rand();
    if (fazEstrutural && sorteioIsca < level * 0.2 && el.parentElement) {
      const isca = document.createElement(el.tagName);
      isca.className = el.className;
      isca.textContent = el.textContent;
      isca.setAttribute('data-drift-decoy', 'true');
      isca.style.display = 'none';
      el.parentElement.insertBefore(isca, el);
      stats.iscas++;
    }
  });

  // ---- REESCRITA DAS FOLHAS DE ESTILO ----
  // Aplica os mesmos mapas às regras CSS, para que a página continue com a
  // aparência original. Sem isto, os elementos perdem estilo e dimensão, e
  // deixam de ser clicáveis — reprovando os braços por um motivo que não é o
  // objeto de estudo.
  const escapar = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const trocas = [];
  for (const [antigo, novo] of Object.entries(mapaClasses)) {
    trocas.push([new RegExp('\\.' + escapar(antigo) + '(?![\\w-])', 'g'), '.' + novo]);
  }
  for (const [antigo, novo] of Object.entries(mapaIds)) {
    trocas.push([new RegExp('#' + escapar(antigo) + '(?![\\w-])', 'g'), '#' + novo]);
  }

  const reescreverRegras = (regras) => {
    for (const regra of regras) {
      try {
        if (regra.cssRules) { reescreverRegras(regra.cssRules); continue; }  // @media etc.
        if (!regra.selectorText) continue;
        let novoSeletor = regra.selectorText;
        for (const [re, sub] of trocas) novoSeletor = novoSeletor.replace(re, sub);
        if (novoSeletor !== regra.selectorText) {
          regra.selectorText = novoSeletor;
          stats.regras_css++;
        }
      } catch (e) { /* regra não reescrevível: segue */ }
    }
  };

  if (trocas.length) {
    for (const folha of Array.from(document.styleSheets)) {
      try {
        reescreverRegras(folha.cssRules);
      } catch (e) {
        // folha de outra origem: o navegador bloqueia a leitura das regras.
        stats.folhas_inacessiveis++;
      }
    }
  }

  return stats;
}
"""


def _validar(tipo: str, level: float) -> None:
    if tipo not in TIPOS:
        raise ValueError(f"tipo deve ser um de {TIPOS}, recebido {tipo!r}")
    if not (0.0 <= level <= 1.0):
        raise ValueError("level deve estar entre 0.0 e 1.0")


def apply_drift(page, level: float, seed: int = 42,
                tipo: str = "cosmetico") -> dict:
    """
    Aplica drift de UI na página já carregada (API SÍNCRONA do Playwright).

    tipo:  "cosmetico", "estrutural" ou "semantico" (cumulativos).
    level: intensidade — probabilidade de cada elemento ser afetado.
    seed:  torna a mutação reprodutível; varie por repetição para obter variância.

    Retorna estatísticas da mutação, para auditoria e para registro no dataset.
    """
    _validar(tipo, level)
    return page.evaluate(_DRIFT_JS, [tipo, level, seed])


async def apply_drift_async(page, level: float, seed: int = 42,
                            tipo: str = "cosmetico") -> dict:
    """Idêntica a `apply_drift`, para a API ASSÍNCRONA (usada no braço agêntico).

    As duas versões compartilham o mesmo `_DRIFT_JS`, garantindo que todos os
    braços do experimento enfrentem exatamente a mesma degradação.
    """
    _validar(tipo, level)
    return await page.evaluate(_DRIFT_JS, [tipo, level, seed])


async def instalar_drift_persistente(context, level: float, seed: int = 42,
                                     tipo: str = "cosmetico") -> None:
    """
    Faz o drift valer em TODAS as páginas visitadas, e não só na primeira.

    O PROBLEMA QUE ISTO RESOLVE
    ---------------------------
    O drift altera o DOM já renderizado. Uma navegação substitui o documento e
    leva junto toda alteração feita por script. Os braços determinísticos lidam
    com isso reaplicando o drift depois de cada `goto` — no SauceDemo a função
    `degradar()` é chamada cinco vezes, uma por página da tarefa.

    O braço agêntico não tinha como fazer isso: quem navega é o agente, e o
    experimento não intervém entre uma página e outra. O drift era aplicado uma
    vez, na página inicial, e desaparecia no primeiro login. Resultado: os
    braços determinísticos enfrentavam a interface degradada em todas as
    páginas, e o agêntico apenas na primeira — comparação inválida, com a
    aparência de um braço extraordinariamente robusto.

    POR QUE UM SCRIPT DE INICIALIZAÇÃO, E NÃO UM OUVINTE DE `load`
    --------------------------------------------------------------
    Reagir ao evento `load` do lado do Python deixaria uma janela entre a página
    ficar pronta e o drift ser aplicado. O agente poderia capturar a página
    íntegra nesse intervalo, e o vazamento seria intermitente e silencioso.
    Um script de inicialização roda DENTRO da página, antes de qualquer código
    do site, e aplica o drift assim que o DOM está montado — não há instante
    observável em que a página esteja íntegra.

    DOIS OBSTÁCULOS, DESCOBERTOS UM DE CADA VEZ
    -------------------------------------------
    1. Aplicar em `DOMContentLoaded` não basta. Funcionou no site servido pronto
       pelo servidor, mas quase não teve efeito no SauceDemo: ele monta a
       interface no navegador, e nesse instante o documento está praticamente
       vazio. A degradação caía de 28 marcadores para 1 — uma interface quase
       íntegra se registrando como degradada. Daí esperar o DOM ficar QUIETO:
       um observador de mutações reinicia um cronômetro a cada alteração, e o
       drift só é aplicado após um intervalo sem mudanças.

    2. Nem todo avanço de página troca o documento. No SauceDemo o login leva a
       inventory.html por roteamento no cliente: a URL muda, a interface inteira
       é substituída, mas o documento é o mesmo — então o script de
       inicialização NÃO roda de novo. Com uma guarda booleana por documento, a
       segunda página ficava sem drift nenhum. Por isso a guarda é a URL: o
       drift é reaplicado sempre que o endereço muda, exatamente como a função
       `degradar()` dos braços determinísticos é chamada após cada navegação.

    Reaplicar por URL reproduz o comportamento dos braços determinísticos, o que
    é o requisito que importa: os quatro braços precisam enfrentar a mesma
    degradação nas mesmas páginas.
    """
    import json

    _validar(tipo, level)
    parametros = json.dumps([tipo, level, seed])
    script = """
    (() => {
      const aplicarDrift = %s;
      const params = %s;
      const QUIETO_MS = 250;   // silêncio necessário para considerar montado
      const TETO_MS = 3000;    // limite para páginas que nunca param de mudar

      let cronometro = null;
      let pendenteDesde = null;

      // Reaplicar quando o endereço mudar. Cobre tanto a troca de documento
      // quanto o roteamento no cliente, em que o documento permanece o mesmo.
      const precisaAplicar = () => window.__driftUrl !== location.href;

      const executar = () => {
        if (!precisaAplicar()) return;
        window.__driftUrl = location.href;
        pendenteDesde = null;
        try {
          const stats = aplicarDrift(params);
          window.__driftStats = stats;
          window.__driftAplicado = true;
          // ACUMULA em sessionStorage: o drift é reaplicado a cada página, e o
          // que interessa registrar é o total da execução. Sem isto, as colunas
          // de estatística ficavam zeradas no braço agêntico — o único sem
          // registro de quanta degradação enfrentou, justamente o ponto cego
          // que essas colunas existem para fechar.
          const CHAVE = '__drift_total';
          const total = JSON.parse(sessionStorage.getItem(CHAVE) || '{}');
          for (const campo of ['ids', 'classes', 'textos', 'envolvidos', 'iscas']) {
            total[campo] = (total[campo] || 0) + (stats[campo] || 0);
          }
          sessionStorage.setItem(CHAVE, JSON.stringify(total));
        } catch (e) {
          window.__driftErro = String(e);
        }
      };

      const agendar = () => {
        if (cronometro) clearTimeout(cronometro);
        cronometro = setTimeout(executar, QUIETO_MS);
      };

      const iniciar = () => {
        agendar();
        try {
          // O observador segue ativo o tempo todo: depois da primeira
          // aplicação suas chamadas são inócuas (precisaAplicar devolve
          // falso), mas ele é o que detecta a remontagem da interface quando
          // a aplicação troca de rota sem recarregar.
          new MutationObserver(agendar).observe(
            document.documentElement, {childList: true, subtree: true});
        } catch (e) { /* documento sem raiz ainda: o cronômetro basta */ }

        // Rede de segurança para páginas que nunca ficam quietas: se o
        // endereço mudou e o drift continua pendente além do teto, aplica
        // assim mesmo. Sem isto, uma página com animação contínua adiaria a
        // degradação indefinidamente e rodaria íntegra.
        setInterval(() => {
          if (!precisaAplicar()) { pendenteDesde = null; return; }
          if (pendenteDesde === null) pendenteDesde = Date.now();
          else if (Date.now() - pendenteDesde > TETO_MS) executar();
        }, 250);
      };

      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', iniciar);
      } else {
        iniciar();
      }
    })();
    """ % (_DRIFT_JS, parametros)
    await context.add_init_script(script)


async def totais_drift(page) -> dict:
    """
    Soma de tudo que o drift mutou ao longo da execução, lida da própria página.

    Contrapartida do `AcumuladorDrift` dos braços síncronos: lá o Python recebe
    as estatísticas de cada chamada; aqui o drift roda dentro do navegador e
    acumula em sessionStorage, que sobrevive às trocas de página.
    """
    import json
    bruto = await page.evaluate(
        "() => sessionStorage.getItem('__drift_total')")
    try:
        return json.loads(bruto) if bruto else {}
    except (ValueError, TypeError):
        return {}


async def esperar_drift(page, timeout_ms: int = 8000) -> dict:
    """
    Espera o drift da página ATUAL terminar e devolve suas estatísticas.

    A condição de espera é `__driftUrl === location.href`, e não uma bandeira
    booleana. A diferença importa no roteamento em que o documento não é
    recarregado: uma bandeira continuaria verdadeira da página anterior e a
    espera devolveria imediatamente, dando por aplicada uma degradação que ainda
    não ocorrera.

    Confirmar a aplicação evita o pior desfecho possível para o experimento —
    uma execução sem drift nenhum registrada como se tivesse drift.
    """
    await page.wait_for_function(
        "() => window.__driftUrl === location.href", timeout=timeout_ms)
    return await page.evaluate(
        "() => ({stats: window.__driftStats || null, erro: window.__driftErro || null})")
