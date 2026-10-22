import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

plt.rcParams['font.family'] = ['Liberation Sans']  # métrica idêntica à Arial

dados = list(csv.DictReader(open('data/results.csv', encoding='utf-8')))
ARMS = [('deterministic', 'ID/CSS'), ('xpath', 'XPath'), ('semantic', 'Papel e rótulo'),
        ('healing', 'Self-healing'), ('agentic', 'Agêntica')]
TIPOS = [('cosmetico', 'Cosmética'), ('estrutural', 'Estrutural'), ('semantico', 'Semântica')]
NIVEIS = ['0.25', '0.5', '0.75', '1.0']

def taxa(a, t, n):
    g = [r for r in dados if r['approach'] == a and r['drift_type'] == t
         and abs(float(r['drift_level']) - float(n)) < 1e-9 and r['error_type'] != 'contornou']
    return 100 * sum(r['success'] == 'True' for r in g) / len(g), len(g)

M = [[taxa(a, t, n) for t, _ in TIPOS for n in NIVEIS] for a, _ in ARMS]

cmap = LinearSegmentedColormap.from_list('azul', ['#ffffff', '#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'])
fig, ax = plt.subplots(figsize=(6.3, 2.35), dpi=300)
gap = 0.35  # espaço entre os três grupos de tipo
for i, linha in enumerate(M):
    for j, (v, n) in enumerate(linha):
        x = j + (j // 4) * gap
        ax.add_patch(Rectangle((x, i), 0.94, 0.9, facecolor=cmap(v / 100) if v > 0 else '#f2f2f2', edgecolor='none'))
        txt = f'{v:.0f}' + ('*' if n < 16 else '')
        ax.text(x + 0.47, i + 0.45, txt, ha='center', va='center', fontsize=9.5,
                color='white' if v >= 55 else '#1a1a1a')

# destaque do recorte da hipótese principal: semântica 0,50 a 1,0, self-healing e agêntica
x0 = 9 + 2 * gap - 0.05
ax.add_patch(Rectangle((x0, 3 - 0.06), 2.94 + 0.10, 1.9 + 0.12, fill=False, edgecolor='#c0392b', linewidth=1.4))
ax.text(x0 + 1.52, 5.3, 'recorte da hipótese principal', ha='center', va='center', fontsize=8.5, color='#c0392b')

ax.set_xlim(-0.1, 12 + 2 * gap)
ax.set_ylim(5.55, -1.0)
ax.set_yticks([i + 0.45 for i in range(5)])
ax.set_yticklabels([n for _, n in ARMS], fontsize=9.5)
for lab in ax.get_yticklabels():
    if lab.get_text() == 'Self-healing':
        lab.set_fontstyle('italic')
xt = [j + (j // 4) * gap + 0.47 for j in range(12)]
ax.set_xticks([])
for x, l in zip(xt, ['0,25', '0,50', '0,75', '1,0'] * 3):
    ax.text(x, -0.22, l, ha='center', va='center', fontsize=8.5)
ax.tick_params(length=0)
for k, (_, nome) in enumerate(TIPOS):
    ax.text(k * (4 + gap) + 1.97, -0.75, f'Degradação {nome.lower()}', ha='center', va='center', fontsize=9.5, fontweight='bold')
for s in ax.spines.values():
    s.set_visible(False)
ax.text(-0.15, -0.22, 'intensidade', ha='right', va='center', fontsize=8.5, color='#444444')
fig.tight_layout(pad=0.3)
fig.savefig('data/analise/fig1_sucesso.png', dpi=300)
for a, l in zip(ARMS, M):
    print(a[1], [round(v) for v, _ in l])
