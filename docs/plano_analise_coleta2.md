# Plano de análise da coleta 2

Registrado antes da primeira execução da coleta 2. O commit que introduz este
arquivo, com seu hash e sua data, é o registro verificável de que hipóteses,
testes e exclusões foram fixados antes de os dados existirem.

Revisão de 16/09/2026, também anterior à coleta: versão do Playwright MCP
fixada (seção 2) e custo reportado em duas bases (seção 6, itens 9 e 10).

Segunda revisão de 16/09/2026, antes da primeira execução: a matriz de validação
acusou falha do braço XPath na linha de base do SauceDemo. O site mudou depois da
coleta preliminar: o cabeçalho passou a ser um elemento `header`, o que deslocou
os caminhos de todas as páginas após o login, e os campos de formulário e o link
do carrinho ganharam `aria-label`. Os caminhos XPath e as impressões digitais do
self-healing do SauceDemo foram recapturados da página íntegra; os do The Internet
foram conferidos e não mudaram. Nada mais foi alterado.

Terceira revisão de 17/09/2026, ainda antes da primeira execução: no teste do
agente, o The Internet respondeu HTTP 503 ("Application Error") por alguns
minutos e a tarefa sem degradação falhou. A página de erro carrega sem exceção;
por isso o runner não a reconhecia como falha de navegação, e uma execução nessas
condições seria gravada como falha. A coleta passa a rodar por
`coleta_com_guarda.py`: antes de gravar uma execução que falhou, o site é
consultado, e se o Chromium confirmar duas vezes, com 10 s de intervalo, resposta
5xx ou página que não carrega, a execução não é gravada e é refeita na retomada.
A regra não depende do braço nem do resultado da tarefa. O código de `common/` e
`sites/` não mudou (impressão `ec59440b286f`).

Quarta revisão de 17/09/2026: a coleta 2 começou e foi interrompida depois de 66
execuções, porque o self-healing falhou na linha de base do SauceDemo, o que não
ocorreu em nenhuma das 48 execuções de linha de base da coleta 1. A causa estava
em `SessaoHealing.localizar`: o seletor original era conferido sem espera, e no
SauceDemo, que troca de página sem recarregar, a conferência podia acontecer antes
de a página nova ser desenhada, acionando a cura sobre uma página incompleta. Os
demais braços esperam o elemento em cada ação. O self-healing passa a esperar o
seletor original por até 3 s antes de curar. As 66 execuções foram descartadas
(`data/coleta2_interrompida_20260917/`); as validações são refeitas e a coleta
recomeça do zero, com a impressão `6c6c5aba5b39`.

Quinta revisão de 17/09/2026: a cota da assinatura acabou no meio da coleta, e o
braço agêntico é o único que a consome. A coleta passa a ser feita em dois blocos:
primeiro os quatro braços sem API (960 execuções), depois as 240 agênticas. A
ordem sorteada das células e dos braços é a mesma nos dois blocos, porque o
sorteio não depende de quais braços estão ativos, e cada linha grava a impressão
do instrumento. Antes do bloco agêntico a matriz de validação é refeita, para
registrar que sites e instrumento se comportam como no primeiro bloco. A separação
temporal entre os blocos é limitação a declarar nos resultados: o que for coletado
em dias diferentes não está protegido contra mudanças das aplicações entre eles,
além do que a matriz de validação detecta.

Sexta revisão de 20/09/2026, já com os dados na mão: no bloco agêntico duas
instâncias do `executar_coleta2` ficaram abertas ao mesmo tempo — a primeira
esperando o limite de gasto liberar, a segunda aberta para retomar — e cada uma
rodou o seu runner. Como as duas leram a mesma lista de execuções pendentes, 89
condições do braço agêntico foram executadas duas vezes. De cada par ficou a
última gravada, que é a dona da transcrição em disco, já que o arquivo é nomeado
pela condição e a segunda execução sobrescreve a primeira; nenhum par divergia em
desfecho, de modo que a regra não escolhe resultado. O arquivo anterior à remoção
está em `data/results_antes_limpeza.csv` e as 89 linhas removidas em
`data/coleta2_duplicadas_20260920.csv` (US$ 15,60 de API descartados). No conjunto
final, 14 das 240 execuções agênticas rodaram enquanto outra execução agêntica
estava em andamento na mesma máquina; a duração mediana delas (35,5 s) não difere
da das demais (36,6 s) e todas concluíram, mas a coincidência fica declarada. O
`executar_coleta2` passou a recusar iniciar quando já existe outra execução na
mesma pasta.

## 1. Por que há uma coleta 2

A revisão do artigo de 15/09/2026 encontrou três defeitos no instrumento da
coleta 1 (09 a 13/08/2026), confirmados no código, nos dados e nas transcrições:

1. **A degradação semântica quebrava o login do The Internet.** O degradador
   acrescentava sufixo ao atributo `name` dos campos, que é a chave com que o
   formulário envia usuário e senha. O servidor recusava a senha correta. As 24
   falhas do self-healing no The Internet sob degradação semântica e as 9 falhas
   do agente tinham essa causa.
2. **O oráculo do agente era mais brando que o dos outros braços.** Não exigia
   login confirmado nem contagem intermediária, e aceitava o pico do carrinho.
   Uma execução em que o próprio agente relatou não ter conseguido fazer login
   foi registrada como sucesso.
3. **O atributo `data-test` tinha tratamento assimétrico.** Execuções agênticas
   que o usaram foram excluídas, mas ele pesava na impressão digital do
   self-healing em todos os alvos do SauceDemo.

A coleta 1 fica arquivada em `data/coleta1/` e na tag `coleta-1`.

## 2. Correções no instrumento

- `common/drift.py`: a degradação semântica não altera mais `name` nem o `value`
  de campos; `value` só muda em inputs do tipo botão. A sequência de sorteios não
  mudou.
- `validar_funcional.py` (novo): um braço de referência, que localiza pela
  identidade original, precisa concluir as duas tarefas em todas as 192
  combinações de aplicação, tipo, nível (0,25 a 1,0) e semente (0 a 7).
- `common/oracle.py`, `common/agentic_base.py` e as tarefas agênticas: o agente
  passa pela mesma conferência dos braços determinísticos (login confirmado,
  3 elementos adicionados e 1 removido no The Internet; exatamente 1 item no
  carrinho no resumo do pedido no SauceDemo).
- `common/healing.py`: `data_test` fora da pontuação; cada cura passa a ter
  veredito direto (correta, errada ou sem verdade de referência).
- `common/versao.py` e `common/runner.py`: cada linha grava a coleta e a
  impressão digital do código; a coleta é intercalada entre braços, em ordem
  sorteada com semente 2026.
- `common/agent.py` e `common/agent_sdk.py`: servidor Playwright MCP fixado na
  versão 0.0.81. Com `@latest`, a versão podia mudar entre uma retomada e outra.
- `calibrar_limiar.py`: sementes a partir de 1000, disjuntas das do experimento.
  O ponto de operação (limiar 0,62, margem 0,10) **não** será reajustado.

## 3. Desenho

Igual ao da coleta 1: 5 abordagens x 3 tipos cumulativos x 5 intensidades
(0; 0,25; 0,50; 0,75; 1,0) x 2 aplicações x 8 sementes = 1.200 execuções.
Todas as execuções são refeitas; nenhuma linha da coleta 1 entra na análise.

Condições para iniciar a coleta: `validar_instrumento.py` sem divergência,
`validar_funcional.py` com 192 de 192, e um teste agêntico com o novo oráculo
concluindo sem degradação nas duas aplicações.

## 4. Exclusões

- Execução agêntica que usar atributo de teste (`data-test`, `data-testid`,
  `data-qa`) ou ferramenta de código arbitrário sob degradação: registrada como
  `contornou` e fora das taxas (análise principal).
- Sensibilidade: as mesmas execuções contadas como falha.
- Erros de infraestrutura (limite de uso, falha de navegação) não são gravados;
  a execução é refeita.
- Execução duplicada pela abertura de duas instâncias ao mesmo tempo: fica uma por
  condição, a última gravada, e as demais vão para
  `data/coleta2_duplicadas_20260920.csv`.
- Site fora do ar: execução que falha com o site respondendo 5xx ou sem carregar,
  confirmado duas vezes pelo Chromium logo após a falha, não é gravada e é refeita
  (mesma regra para todos os braços). O total descartado é relatado no texto
  (`data/logs/guarda_site.csv`, conferido pela auditoria).

## 5. Hipótese primária

**H1.** Sob degradação semântica nas intensidades 0,50, 0,75 e 1,0, nas duas
aplicações, a abordagem agêntica conclui a tarefa com frequência diferente da do
self-healing.

- Comparador fixo: self-healing (o braço determinístico mais robusto na coleta 1).
- Unidade: par (aplicação, intensidade, semente); 48 pares.
- Teste: McNemar exato, bicaudal, alfa = 0,05.
- Efeitos: diferença pareada com IC 95% de Agresti e Min; razão de chances
  condicional com IC exato.
- Leitura: H1 é sustentada se p < 0,05 e a diferença favorecer a agêntica. Se
  p >= 0,05, o artigo reporta que a diferença não foi detectada, com o intervalo.
- Referência de poder: com c = 0 pares favoráveis ao self-healing, bastam 6
  pares favoráveis à agêntica para p < 0,05; com c = 1, 8.

## 6. Análises secundárias

1. H1 por aplicação (McNemar exato), sem correção, rotuladas como secundárias.
2. Taxa de sucesso por abordagem, tipo e intensidade, com IC de Wilson.
3. Tendência ao longo da intensidade por abordagem e tipo (Cochran-Armitage),
   com Holm sobre os testes calculáveis.
4. Comparações entre abordagens por condição (McNemar exato, Holm): exploratórias.
5. Divergência entre aplicações por abordagem (Fisher, Holm), com IC de Newcombe.
6. Regressão logística de Firth na degradação semântica (intensidades 0,25 a
   1,0; papel e rótulo, self-healing e agêntica): termos de abordagem
   (referência self-healing), intensidade centrada em passos de nível por
   abordagem e aplicação; IC por verossimilhança penalizada perfilada.
7. Self-healing: acionamentos, curas, desistências e veredito de cada cura.
8. Tempo: medianas só das execuções bem-sucedidas; Mann-Whitney.
9. Custo agêntico das execuções concluídas por intensidade (Kruskal-Wallis);
   custo das não concluídas lido contra o teto de US$ 0,60. O custo aparece em
   duas bases, lado a lado: a estimativa do SDK, sobre a qual incide o teto, e o
   recálculo pela contagem de tokens com o preço público do modelo (US$ 2 e
   US$ 10 por milhão de tokens de entrada e de saída; escrita de cache a 1,25x
   e a 2x; leitura a 0,1x). Na coleta 1 as duas bases deram US$ 36,54 e
   US$ 20,33 a 24,11.
10. Modelo econômico com probabilidade de quebra por tipo de mudança,
    premissas declaradas e sensibilidade (hora de R$ 100 a R$ 300; câmbio +-10%).
    Custo agêntico unitário pelo preço público recalculado dos tokens (escrita de
    cache a 2x); a estimativa do SDK entra como sensibilidade (`--base-custo sdk`).

## 7. O que não será feito

- Escolher comparador, recorte ou teste depois de ver os dados.
- Reajustar limiar ou margem do self-healing.
- Recoletar execuções válidas por resultado indesejado.
