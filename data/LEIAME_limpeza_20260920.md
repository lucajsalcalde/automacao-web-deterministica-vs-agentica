# Limpeza de 20/09/2026 — execuções duplicadas do braço agêntico

Duas instâncias do `executar_coleta2` ficaram abertas ao mesmo tempo durante o
bloco agêntico e cada uma rodou o seu runner. As duas leram a mesma lista de
execuções pendentes, e 89 condições do braço agêntico foram executadas duas
vezes (1.289 linhas no lugar de 1.200).

- `results_antes_limpeza.csv`: o arquivo como estava antes da remoção.
- `coleta2_duplicadas_20260920.csv`: as 89 linhas removidas.
- `results.csv`: 1.200 linhas, uma por condição.

Regra da remoção: de cada par ficou a ÚLTIMA gravada. É a que corresponde à
transcrição em `data/transcripts`, porque o arquivo é nomeado pela condição e a
segunda execução sobrescreveu a primeira. Nenhum par divergia em desfecho (as
178 execuções dos 89 pares terminaram em sucesso), então a regra não seleciona
resultado. A auditoria confere isso: toda linha removida tem a condição
correspondente presente no conjunto final.

Custo de API descartado com as duplicatas: US$ 15,60 pela estimativa do SDK.
