# Plano de análise da coleta 2

Registrado antes da primeira execução da coleta 2. O commit que introduz este
arquivo, com seu hash e sua data, é o registro verificável de que hipóteses,
testes e exclusões foram fixados antes de os dados existirem.

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
   custo das não concluídas lido contra o teto de US$ 0,60.
10. Modelo econômico com probabilidade de quebra por tipo de mudança,
    premissas declaradas e sensibilidade (hora de R$ 100 a R$ 300; câmbio +-10%).

## 7. O que não será feito

- Escolher comparador, recorte ou teste depois de ver os dados.
- Reajustar limiar ou margem do self-healing.
- Recoletar execuções válidas por resultado indesejado.
