# Busca da literatura (seção 3.1 do artigo)

## Protocolo

| Item | Definição |
|---|---|
| Data da busca | 21 de agosto de 2026 |
| Bases | arXiv, ACM Digital Library, IEEE Xplore, SpringerLink, Wiley Online Library e Google Scholar |
| Período | 2014 a 2026 |
| Idiomas | inglês e português |
| Inclusão | trabalhos que avaliem empiricamente a robustez de automação web, comparem formas de localização de elementos ou avaliem agentes de modelos de linguagem em páginas web |
| Exclusão | material de fornecedor sem método declarado, textos de blog e trabalhos sem avaliação empírica |
| Triagem | leitura de títulos e resumos |

Consultas:

1. `("web agent" OR "LLM agent" OR "agentic automation") AND (robustness OR reliability)`
2. `("web test" OR "test automation") AND (locator OR selector) AND (fragility OR breakage OR repair)`
3. `("self-healing") AND ("test automation" OR "web testing")`
4. `(RPA OR "robotic process automation") AND ("LLM agent" OR "agentic") AND compar*`

Em 7 de setembro de 2026, as mesmas consultas, em português, foram feitas na BDTD, na
SciELO e no Portal de Periódicos da CAPES, sem trabalho nacional sobre o tema.

## Levantamento

Doze trabalhos vieram da busca e dois da consulta às referências deles (marcados com *).

### Usados no artigo (7)

- HAMMOUDI, M.; ROTHERMEL, G.; TONELLA, P. Why do record/replay tests of web applications break? ICST, 2016. DOI: 10.1109/ICST.2016.16. *
- LEOTTA, M.; CLERISSI, D.; RICCA, F.; TONELLA, P. Visual vs. DOM-based web locators: an empirical study. ICWE, 2014. DOI: 10.1007/978-3-319-08245-5_19.
- LEOTTA, M.; STOCCO, A.; RICCA, F.; TONELLA, P. Robula+: an algorithm for generating robust XPath locators for web testing. Journal of Software: Evolution and Process, 2016. DOI: 10.1002/smr.1771.
- STOCCO, A.; YANDRAPALLY, R.; MESBAH, A. Visual web test repair. ESEC/FSE, 2018. DOI: 10.1145/3236024.3236063.
- QI, X.; QIAN, X.; LI, Y. Semantic test repair for web applications. ESEC/FSE, 2023. DOI: 10.1145/3611643.3616324.
- XUE, T. et al. An illusion of progress? Assessing the current state of web agents. COLM, 2025.
- PRŮCHA, P.; MATOUŠKOVÁ, M.; STRNAD, J. Encontrado como preprint (arXiv:2509.04198, 2025); o artigo cita a versão publicada em revista: Cogent Business & Management, v. 13, n. 1, 2026. DOI: 10.1080/23311975.2026.2724760.

### Fora do artigo: sem relação direta com a pergunta (4)

- HAMMOUDI, M.; ROTHERMEL, G.; STOCCO, A. WATERFALL: an incremental approach for repairing record-replay tests of web applications. FSE, 2016. DOI: 10.1145/2950290.2950294. *
- LEOTTA, M.; STOCCO, A.; RICCA, F.; TONELLA, P. Using multi-locators to increase the robustness of web test cases. ICST, 2015. DOI: 10.1109/ICST.2015.7102611.
- PAN, Y. et al. WebCanvas: benchmarking web agents in online environments. arXiv:2406.12373, 2024.
- LEE, H. Practical limits of autonomous test repair: a multi-agent case study with LLM-driven discovery and self-correction. arXiv:2605.01471, 2026.

### Fora do artigo: preprints, sem revisão por pares (3)

- BAI, H. et al. StressWeb: a diagnostic benchmark for web agent robustness under realistic interaction variability. arXiv:2604.16385, 2026.
- GUPTA, A. ReliabilityBench: evaluating LLM agent reliability under production-like stress conditions. arXiv:2601.06112, 2026.
- JOSEPH, R. N. Beyond LLM-based test automation: a zero-cost self-healing approach using DOM accessibility tree extraction. arXiv:2603.20358, 2026.

## Citados fora do protocolo

Van der Aalst, Bichler e Heinzl (2018), que definem RPA; Nass et al. (2023), Similo; Leotta
et al. (2013) e Kapoor et al. (2025), base do modelo de custo; Kara, Faisal e Nath (2026),
WAREX, no lugar dos preprints de testes de robustez de agentes. As estatísticas (Agresti e
Min, 2005; Firth, 1993) e as documentações técnicas (Playwright e Anthropic) também não
vêm da busca.
