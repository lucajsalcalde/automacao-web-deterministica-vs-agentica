# Coleta 2 interrompida em 17/09/2026 — dados descartados

66 execuções (00:05 a 00:18 de 17/09/2026), impressão do instrumento
`ec59440b286f`. A coleta foi interrompida à mão ao se constatar defeito no braço
self-healing, e nada daqui entra nas análises.

O defeito: `SessaoHealing.localizar` conferia o seletor original com `count()`,
sem esperar a página. No SauceDemo, que troca de página sem recarregar, a
conferência às vezes caía antes de a página nova ser desenhada, e a cura era
acionada numa página incompleta. Efeitos observados aqui: falha na linha de base
(saucedemo, self-healing, semântico 0,0, rep. 5: 'botao_checkout', pontuação
0,440, 2 acionamentos sem degradação) e curas do elemento errado (cosmético 0,5).
Na coleta 1, as 48 execuções de linha de base do self-healing não acionaram a
cura nenhuma vez.

Correção: o seletor original passa a ser esperado por até 3 s antes da cura
(`ESPERA_ORIGINAL_MS` em common/healing.py). Nova impressão: `6c6c5aba5b39`.
A coleta 2 recomeça do zero, com as validações refeitas.
