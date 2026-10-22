# Automação web determinística vs. agêntica

Código, dados e análises do artigo *Automação web determinística vs. agêntica:
uma avaliação empírica de confiabilidade, custo e robustez* — Pós-graduação em
Ciência de Dados, Centro Universitário Facens, 2026. Autor: Luca Jordão Salas
Alcalde.

Repositório: https://github.com/lucajsalcalde/automacao-web-deterministica-vs-agentica

O experimento compara cinco formas de localizar elementos numa página web sob
alterações controladas da página (degradação), em duas aplicações públicas de
teste. Cada linha de `data/results.csv` é uma execução completa da tarefa,
conferida pelo estado final da aplicação.

## Desenho

| Fator | Níveis | Qtd. |
|---|---|---|
| Abordagem | ID/CSS, XPath, papel e rótulo, *self-healing*, agêntica | 5 |
| Tipo de degradação | cosmética, estrutural, semântica (cumulativos) | 3 |
| Intensidade | 0 / 0,25 / 0,50 / 0,75 / 1,0 | 5 |
| Aplicação | SauceDemo, The Internet | 2 |
| Repetições (sementes 0 a 7) | | 8 |
| **Total** | | **1.200** |

Coleta definitiva: 17 a 20 de setembro de 2026. O plano de análise foi
registrado antes da primeira execução, em `docs/plano_analise_coleta2.md`
(versão `be6bd2c` do controle de versões local, reproduzida em
`docs/plano_analise_coleta2_be6bd2c.md`), com as revisões posteriores datadas no
próprio arquivo.

## Estrutura

```
artigo/                 artigo completo (.docx e .pdf)
common/                 instrumento
  drift.py              degradação da página (cosmética, estrutural, semântica)
  oracle.py             oráculo: confere o estado final da aplicação
  healing.py            self-healing: perfil dos elementos e escolha do substituto
  runner.py             executa as combinações abordagem x tipo x intensidade x semente
  agentic_base.py       ciclo do agente, limites de turnos e custo, detecção de contorno
  agent_sdk.py          agente via claude-agent-sdk (usado na coleta)
  agent.py              agente via API (alternativa, não usada na coleta)
  versao.py             versão do instrumento gravada em cada linha dos dados
sites/
  saucedemo/            uma tarefa por abordagem (task, task_xpath, task_semantic,
  the_internet/         task_healing, task_agentic)
  parabank/             fora do escopo; mantido porque o runner o importa
data/
  results.csv           as 1.200 execuções
  analise/              tabelas, saídas e figuras das análises
  transcripts/          ações registradas de cada execução agêntica
  fingerprints/         perfis de elemento do self-healing
  xpaths/               caminhos XPath capturados na página sem degradação
  calibracao_limiar.csv conferência do limiar e da margem do self-healing
  validacao_funcional.csv  192 combinações concluídas pelo programa de referência
  logs/                 registro da coleta
  coleta1/              coleta preliminar (agosto de 2026), descartada
  coleta2_interrompida_20260917/  66 execuções da primeira tentativa, descartadas
  results_antes_limpeza.csv, coleta2_duplicadas_20260920.csv,
  LEIAME_limpeza_20260920.md      remoção das 89 execuções agênticas duplicadas
docs/plano_analise_coleta2.md     plano de análise, com as revisões datadas no início
docs/plano_analise_coleta2_be6bd2c.md  o plano como estava na versão be6bd2c (16/09/2026)
docs/busca_literatura.md          protocolo, consultas e levantamento da busca (seção 3.1)
diagnosticos/           scripts auxiliares da coleta preliminar
```

Scripts na raiz:

| Script | Função |
|---|---|
| `executar_coleta2.py` | roda tudo em sequência: autotestes, validações, coleta, auditoria e análises |
| `coleta_com_guarda.py` | coleta; não grava execução que falhou com o site fora do ar |
| `check_selectors.py` | confere se os seletores das tarefas existem nas páginas |
| `capturar_fingerprints.py`, `capturar_xpaths.py` | capturam perfis e caminhos na página sem degradação |
| `calibrar_limiar.py` | confere limiar e margem do self-healing com sementes a partir de 1000 |
| `validar_instrumento.py` | roda as determinísticas sem degradação e com cada tipo e compara com o esperado |
| `validar_funcional.py` | confirma que a degradação não impede a tarefa (192 combinações) |
| `auditoria.py` | 20 verificações sobre `results.csv` |
| `analise.py` | hipótese principal (McNemar), taxas com intervalo de Wilson, tendência, comparações, tempo e custo |
| `modelo_logistico.py` | regressão logística com correção de Firth |
| `modelo_economico.py` | custo anual e ponto de equilíbrio (seção 3.9: custo médio por execução, sem parada; `--mediana` e `--com-perda` são variações) |
| `figura1_sucesso.py` | Figura 1 do artigo |
| `figuras.py` | figuras complementares |

## Onde conferir a seção 3.7 do artigo

| Afirmação | Onde conferir |
|---|---|
| Plano gravado antes da primeira execução (versão `be6bd2c` do controle de versões local, 16/09/2026) | `docs/plano_analise_coleta2_be6bd2c.md` reproduz o plano daquela versão; as revisões posteriores estão datadas em `docs/plano_analise_coleta2.md` |
| Recaptura dos caminhos XPath e dos perfis do SauceDemo | segunda revisão do plano; `data/xpaths/` e `data/fingerprints/` |
| Primeira tentativa descartada após 66 execuções e espera de 3 s do *self-healing* | quarta revisão do plano; `data/coleta2_interrompida_20260917/`; `ESPERA_ORIGINAL_MS` em `common/healing.py` |
| Determinísticas sem degradação e com cada tipo só falham onde se prevê, antes da coleta e de novo entre os blocos | `data/logs/` (matriz de validação, etapas 2 e 6 de `executar_coleta2.py`) |
| Programa de referência conclui as 192 combinações degradadas | `data/validacao_funcional.csv` |
| Coleta em dois blocos | quinta revisão do plano; `data/logs/`; coluna `timestamp` de `data/results.csv` |
| 89 condições agênticas executadas duas vezes | sexta revisão do plano; `data/LEIAME_limpeza_20260920.md`, `data/results_antes_limpeza.csv`, `data/coleta2_duplicadas_20260920.csv` |
| Vinte verificações automáticas sem erro | `auditoria.py`; `data/analise/saida_auditoria.txt` |

## Dados (`data/results.csv`)

| Coluna | Conteúdo |
|---|---|
| `site`, `task` | aplicação e tarefa |
| `approach` | `deterministic` (ID/CSS), `xpath`, `semantic` (papel e rótulo), `healing`, `agentic` |
| `drift_type`, `drift_level`, `repetition` | tipo, intensidade e semente da degradação |
| `success` | resultado conferido pelo oráculo |
| `duration_s`, `n_steps` | duração e passos (agente) |
| `error_type`, `error_message` | causa da falha; `contornou` marca o uso de atributo de teste pelo agente |
| `tokens_input`, `tokens_output`, `cost_usd` | consumo e custo estimado pela biblioteca do agente |
| `heal_*` | acionamentos, curas, desistências e acerto das curas do self-healing |
| `drift_*` | quantidade de elementos alterados em cada operação |
| `timestamp`, `coleta`, `versao_instrumento` | momento, coleta e versão do instrumento |

Todas as linhas têm `versao_instrumento = 6c6c5aba5b39`. Esse valor é calculado
por `common/versao.py` sobre `common/`, `sites/saucedemo/`, `sites/the_internet/`,
`data/fingerprints/` e `data/xpaths/`; qualquer alteração nesses arquivos muda o
valor, e a auditoria acusa a diferença.

## Como reproduzir

Requer Python 3.10 ou superior e Node.js (o servidor Playwright MCP 0.0.81 roda
via `npx`). A abordagem agêntica usa o modelo claude-sonnet-5 pela biblioteca
claude-agent-sdk e exige uma conta autenticada (`python executar_coleta2.py
--login`, ou `login_claude.bat` no Windows).

```bash
python -m venv .venv
.venv\Scripts\activate             # Windows
pip install -r requirements.txt
playwright install chromium
```

Coleta completa, com validações, auditoria e análises:

```bash
python executar_coleta2.py                # ou executar_coleta2.bat
python executar_coleta2.py --status       # andamento
python executar_coleta2.py --sem-agente   # só as 960 execuções determinísticas
```

A coleta pode ser interrompida e retomada: as execuções já gravadas com a mesma
versão do instrumento são puladas.

Só as análises, a partir dos dados entregues:

```bash
python analise.py --autoteste      # confere os testes estatísticos contra valores calculados à mão
python auditoria.py
python analise.py
python modelo_logistico.py
python modelo_economico.py
python figura1_sucesso.py
```

As saídas ficam em `data/analise/`.
