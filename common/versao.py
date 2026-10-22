"""
Impressão digital do instrumento.

Cada linha do results.csv registra de qual versão do código ela veio. A
impressão é um hash de todo arquivo que influencia o resultado de uma execução:
os módulos de common/, as tarefas dos dois sites, as impressões digitais do
self-healing e os caminhos do braço XPath.

POR QUE ISTO SUBSTITUI O data/versao_instrumento.json DA COLETA 1
------------------------------------------------------------------
Na coleta 1 o registro era um arquivo à parte, com hashes de alguns trechos, e
a auditoria comparava esse arquivo com o código no disco. Isso não dizia de qual
versão veio CADA linha: o degradador foi alterado às 16h41 (UTC) de 09/08,
depois da coleta dos braços determinísticos e antes da agêntica, e só o
histórico de datas permitia desconfiar. Com a impressão gravada na própria
linha, duas versões não se misturam sem que a auditoria acuse.

As quebras de linha são normalizadas antes do hash, para que o mesmo código dê a
mesma impressão no Windows e no Linux.
"""

from __future__ import annotations

import glob
import hashlib
import os

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PADROES = (
    "common/*.py",
    "sites/saucedemo/*.py",
    "sites/the_internet/*.py",
    "data/fingerprints/*.json",
    "data/xpaths/*",
)


def arquivos_do_instrumento() -> list[str]:
    encontrados = set()
    for padrao in PADROES:
        for caminho in glob.glob(os.path.join(RAIZ, padrao)):
            if os.path.isfile(caminho) and "__pycache__" not in caminho:
                encontrados.add(os.path.relpath(caminho, RAIZ).replace(os.sep, "/"))
    return sorted(encontrados)


def impressao_instrumento() -> str:
    h = hashlib.sha256()
    for rel in arquivos_do_instrumento():
        with open(os.path.join(RAIZ, rel), "rb") as f:
            conteudo = f.read().replace(b"\r\n", b"\n")
        h.update(rel.encode("utf-8") + b"\0" + conteudo + b"\0")
    return h.hexdigest()[:12]


if __name__ == "__main__":
    for rel in arquivos_do_instrumento():
        print(" ", rel)
    print(f"\nimpressão do instrumento: {impressao_instrumento()}")
