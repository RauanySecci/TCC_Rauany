"""
Gera os gráficos do TCC a partir dos JSON do analises.py (PostgreSQL JSONB vs MongoDB).

    python graficos.py                                   # lê ./resultados, grava ./resultados/graficos
    python graficos.py --entrada resultados --saida graficos
    python graficos.py --ref Médio                       # volume usado nos gráficos 2 e 5 (padrão: Grande)

Gráficos (cada um responde uma pergunta; cada imagem tem um único gráfico):
  1. grafico1_escala_<C>.png        O índice ajuda conforme o volume cresce? Um por consulta (volume, leituras)
  2. grafico2_ganho_indice.png      O índice ajuda?                                (volume, leituras)
  3. grafico3_seletividade_<C>.png  O índice ajuda conforme a seletividade? Um por consulta (C1, C4, C5, C7)
  4. grafico4_lote_<C>.png          Agrupar escritas compensa? Um por operação em lote (era o gráfico 5)
  5. grafico5_espaco_indice.png     Quanto espaço o índice ocupa?                  (volume)
  6. grafico6_carga_tempo.png       Quanto tempo leva carregar cada volume?        (com carga repetida: média das rodadas)
  7. grafico7_carga_cpu.png         Quanta CPU a carga usa?               (com carga repetida: média das rodadas)
  8. grafico8_carga_memoria.png     Quanta memória a carga usa?
  9. grafico9_carga_espaco.png      Quanto espaço os dados ocupam em disco?
"""
import argparse
import csv
import json
import statistics
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter, NullLocator

RAIZ = Path(__file__).resolve().parent

# ---- paleta (azul e laranja: slots 1 e 2 da paleta de referência, validados com o validate_palette.js) ----
SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
GRADE = "#e4e3de"
COR = {"PostgreSQL": "#2a78d6", "MongoDB": "#eb6834"}

VOLUMES = {"Pequeno": 5_000, "Médio": 100_000, "Grande": 1_000_000}
NOME_VOLUME = {"Pequeno": "5 mil", "Médio": "100 mil", "Grande": "1 milhão"}
LEITURAS = ["C1", "C4", "C5", "C6", "C7"]
ESCRITAS = ["C2", "C3", "C8"]
# C2, C3 e C8 medidas pelo id (com o índice da chave primária e varrendo a tabela): entram nos gráficos 1 e 2 junto das leituras
POR_ID = ["C2_id", "C3_id", "C8_id"]
COMPARAVEIS = LEITURAS + POR_ID
TITULO = {
    "C1": "C1 · categoria e status", "C2": "C2 · upsert", "C3": "C3 · soft delete",
    "C4": "C4 · junção (itens do restaurante)", "C5": "C5 · faixa de preço",
    "C6": "C6 · valor de atributo aninhado", "C7": "C7 · array aninhado", "C8": "C8 · atualização parcial",
    "C2_id": "C2 · upsert por id", "C3_id": "C3 · soft delete por id", "C8_id": "C8 · atualização parcial por id",
}
SUFIXO_FORCADO = "(índice forçado)"

linhas_csv = []  # (grafico, painel, serie, x, y, min, max)


def tipo_do_rotulo(rotulo):
    banco = "PostgreSQL" if rotulo.startswith("PostgreSQL") else "MongoDB"
    if SUFIXO_FORCADO in rotulo:
        return banco, "forcado"
    if "Sem índice" in rotulo:
        return banco, "sem"
    return banco, "com"


def estilo(banco, tipo):
    cor = COR[banco]
    if tipo == "sem":
        return dict(color=cor, ls=(0, (5, 3)), marker="o", markerfacecolor=SUPERFICIE, markeredgecolor=cor)
    if tipo == "forcado":
        return dict(color=cor, ls=(0, (1, 2)), marker="s", markerfacecolor=cor, markeredgecolor=cor)
    return dict(color=cor, ls="-", marker="o", markerfacecolor=cor, markeredgecolor=cor)


def nome_serie(banco, tipo):
    return f"{banco} · " + {"sem": "sem índice", "com": "com índice", "forcado": "índice forçado"}[tipo]


def fmt_num(x, _pos=None):
    if x >= 1000:
        return f"{x:,.0f}".replace(",", ".")
    if x >= 1:
        return f"{x:g}".replace(".", ",")
    return f"{x:.3g}".replace(".", ",")


def estilizar(ax, log_y=False, log_x=False):
    ax.set_facecolor(SUPERFICIE)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(GRADE)
    ax.grid(axis="y", color=GRADE, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=TINTA_2, labelsize=8.5, length=3)
    if log_y:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))  # 1, 2, 5, 10, 20, 50...: mais referências
        ax.yaxis.set_major_formatter(FuncFormatter(fmt_num))
        ax.yaxis.set_minor_formatter(NullFormatter())
    if log_x:
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.xaxis.set_major_formatter(FuncFormatter(fmt_num))
        ax.xaxis.set_minor_formatter(NullFormatter())


def titulo_painel(ax, texto):
    ax.set_title(texto, loc="left", fontsize=10, color=TINTA, fontweight="bold", pad=8)


def moldura(titulo, subtitulo=None, nota=None, tamanho=(8.6, 5.2)):
    """Figura só com o título. `subtitulo` e `nota` são ignorados (as figuras não levam textos adicionais; a explicação
    fica na legenda do TCC)."""
    fig = plt.figure(figsize=tamanho, facecolor=SUPERFICIE)
    fig.text(0.012, 0.985, titulo, ha="left", va="top", fontsize=14, color=TINTA, fontweight="bold")
    return fig


def figura_simples(titulo, tamanho):
    fig = moldura(titulo, None, None, tamanho)
    return fig, fig.add_subplot(111)


def itens_variacao():
    """Legenda do que a barra e o fio significam nos gráficos com média (1 e 4)."""
    return [Line2D([], [], color=TINTA_2, marker="o", markerfacecolor=TINTA_2, ls="", ms=6, label="ponto: média"),
            Patch(facecolor=to_rgba(TINTA_2, 0.45), edgecolor="none", label="barra: Q1 a Q3"),
            Line2D([], [], color=TINTA_2, lw=1.2, label="fio: mínimo a máximo")]


def itens_legenda(forcado=False, so_bancos=False):
    h = [Line2D([], [], color=COR["PostgreSQL"], lw=6, label="PostgreSQL"),
         Line2D([], [], color=COR["MongoDB"], lw=6, label="MongoDB")]
    if so_bancos:
        return h
    h += [Line2D([], [], color=TINTA_2, ls="-", marker="o", markerfacecolor=TINTA_2, lw=2, ms=6, label="com índice"),
          Line2D([], [], color=TINTA_2, ls=(0, (2.5, 1.5)), marker="o", markerfacecolor=SUPERFICIE, lw=2, ms=6,
                 label="sem índice")]  # traço mais curto que o da linha, para caber no símbolo da legenda
    if forcado:
        h.append(Line2D([], [], color=TINTA_2, ls=(0, (1, 2)), marker="s", markerfacecolor=TINTA_2, lw=2, ms=5,
                        label="índice forçado"))
    return h


def estilo_barra(banco, tipo):
    cor = COR[banco]
    if tipo == "sem":  # mesma cor, mais clara: "sem índice" (a cor continua identificando o banco)
        return dict(facecolor=to_rgba(cor, 0.35), edgecolor=cor, linewidth=1.2)
    if tipo == "forcado":
        return dict(facecolor=SUPERFICIE, edgecolor=cor, hatch="////", linewidth=1.2)
    if tipo == "ignorado":  # índice criado, mas o planejador não o usou (Seq Scan): pontilhado
        return dict(facecolor=SUPERFICIE, edgecolor=cor, hatch="....", linewidth=1.2)
    return dict(facecolor=cor, edgecolor=SUPERFICIE, linewidth=1.5)


def legenda_barras(ignorado=False):
    itens = [("PostgreSQL", "sem", "PostgreSQL · sem índice"), ("PostgreSQL", "com", "PostgreSQL · com índice")]
    if ignorado:
        itens.append(("PostgreSQL", "ignorado", "PostgreSQL · índice ignorado (Seq Scan)"))
    itens += [("MongoDB", "sem", "MongoDB · sem índice"), ("MongoDB", "com", "MongoDB · com índice")]
    return [Patch(label=r, **estilo_barra(b, tp)) for b, tp, r in itens]


def fmt_tempo(ms):
    return f"{ms / 1000:.1f} s".replace(".", ",") if ms >= 1000 else f"{ms:.0f} ms"


def fmt_linhas(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".replace(".", ",") + " mi"
    if n >= 10_000:
        return f"{n / 1000:.0f} mil"
    if n >= 1000:
        return f"{n / 1000:.1f}".replace(".", ",") + " mil"
    return str(n)


NOME_PARAM = {"medio": "médio"}


def mediana(r):
    return None if r is None else r["mediana"]


def salvar(fig, destino):
    fig.savefig(destino, dpi=170, facecolor=SUPERFICIE)
    plt.close(fig)
    print("gerado:", destino)


def carregar(caminho):
    if not caminho.exists():
        print(f"AVISO: {caminho.name} não encontrado; os gráficos que dependem dele serão pulados.")
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


# 1. escala com o volume (um por consulta), em boxplot
def itens_boxplot():
    """Legenda de como ler cada boxplot (gráfico 1)."""
    return [Patch(facecolor=to_rgba(TINTA_2, 0.45), edgecolor=TINTA_2, linewidth=1.2, label="caixa: Q1 a Q3"),
            Line2D([], [], color=TINTA, lw=2, label="traço: média"),
            Line2D([], [], color=TINTA_2, lw=1.2, label="fio: mínimo a máximo")]


# cores de sempre por banco (sem índice = mais claro, com índice = cheio); só o índice forçado ganha cor própria
COR_FORCADO = "#cc79a7"


def estilo_caixa(banco, tipo):
    if tipo == "forcado":
        return dict(facecolor=COR_FORCADO, edgecolor=COR_FORCADO, linewidth=1)
    cor = COR[banco]
    if tipo == "sem":
        return dict(facecolor=to_rgba(cor, 0.35), edgecolor=cor, linewidth=1.2)
    return dict(facecolor=cor, edgecolor=cor, linewidth=1)


def grafico_escala(vol, saida):
    """Boxplot dos tempos brutos das repetições em cada volume: caixa = Q1 a Q3 (numpy.percentile), traço = média,
    fio = mínimo a máximo. Sem linhas ligando os volumes."""
    presets = [p for p in VOLUMES if p in vol]
    if len(presets) < 2:
        print("AVISO: gráfico 1 pulado (precisa de ao menos 2 volumes).")
        return
    xs = [VOLUMES[p] for p in presets]
    for c in COMPARAVEIS:
        rotulos = list(dict.fromkeys(r for p in presets for r in vol[p].get(c, {})))
        if not rotulos:
            continue
        fig, ax = figura_simples(f"{TITULO[c]}: tempo por volume de itens", (8.6, 5.0))
        estilizar(ax, log_y=True, log_x=True)
        tem_forcado = False
        y_min, y_max = float("inf"), 0.0
        for k, rotulo in enumerate(rotulos):
            banco, tipo = tipo_do_rotulo(rotulo)
            tem_forcado |= tipo == "forcado"
            centro = 1 + 0.2 * (k - (len(rotulos) - 1) / 2)  # afasta as séries na escala log
            meia = 1.08  # meia-largura da caixa (fator multiplicativo)
            for p in presets:
                r = vol[p].get(c, {}).get(rotulo)
                if not r:
                    continue
                t = r["tempos_brutos"]
                q1, q3 = np.percentile(t, [25, 75])
                med = sum(t) / len(t)
                mn, mx = min(t), max(t)
                xc = VOLUMES[p] * centro
                x0, x1 = xc / meia, xc * meia
                ax.vlines(xc, mn, mx, color=to_rgba(estilo_caixa(banco, tipo)["edgecolor"], 0.45 if tipo == "sem" else 1), lw=1.6, zorder=2)
                ax.add_patch(plt.Rectangle((x0, q1), x1 - x0, q3 - q1, zorder=3, **estilo_caixa(banco, tipo)))
                ax.hlines(med, x0, x1, color=TINTA, lw=2, zorder=4)
                y_min, y_max = min(y_min, mn), max(y_max, mx)
                linhas_csv.append(("1_escala_volume_media", c, nome_serie(banco, tipo), VOLUMES[p], med, mn, mx))
                linhas_csv.append(("1_escala_volume_Q1_Q3", c, nome_serie(banco, tipo), VOLUMES[p], q1, q3, None))
        ax.set_xticks(xs)
        ax.set_xticklabels([NOME_VOLUME[p] for p in presets])
        ax.xaxis.set_minor_locator(NullLocator())  # só os 3 volumes medidos, sem marcas intermediárias
        ax.set_xlim(min(xs) / 1.9, max(xs) * 1.9)
        ax.set_ylim(y_min / 1.3, y_max * 2.6)
        ax.set_xlabel("itens no banco", fontsize=9, color=TINTA_2)
        ax.set_ylabel("tempo (ms)", fontsize=9, color=TINTA_2)
        tipos = dict.fromkeys(tipo_do_rotulo(r) for r in rotulos)
        legenda_series = [Patch(label=nome_serie(b, tp), **estilo_caixa(b, tp)) for b, tp in tipos]
        ax.legend(handles=legenda_series + itens_boxplot(), loc="upper left", frameon=False, fontsize=8.5,
                  labelcolor=TINTA, ncol=2)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        salvar(fig, saida / f"grafico1_escala_{c}.png")


# 2. ganho do índice
def grafico_ganho(vol, ref, saida, estatistica="mediana", sufixo=""):
    """Ganho = tempo sem índice ÷ tempo com índice; estatistica: \"mediana\" ou \"media\" das repetições."""
    usar_media = estatistica == "media"
    dados = vol[ref]
    consultas = sorted((c for c in COMPARAVEIS if c in dados), key=lambda c: int(c[1]))  # C1, C2, C3, ...
    fig, ax = figura_simples(f"Ganho do índice por consulta (volume de {NOME_VOLUME[ref]} de itens)", (12, 5.0))
    estilizar(ax, log_y=True)
    largura = 0.24
    razoes = []
    tem_forcado = False
    for i, c in enumerate(consultas):
        resultados = {tipo_do_rotulo(r): v for r, v in dados[c].items() if v}
        medianas = {k: (v["media"] if usar_media else v["mediana"]) for k, v in resultados.items()}
        barras = []
        for banco in ("PostgreSQL", "MongoDB"):
            sem, com = medianas.get((banco, "sem")), medianas.get((banco, "com"))
            forc = medianas.get((banco, "forcado"))
            if sem and forc:  # o banco ignorou o índice: só o índice forçado mede o efeito dele (uma barra por banco)
                barras.append((banco, "forcado", sem / forc))
            elif sem and com:
                barras.append((banco, "com", sem / com))
        deslocamento = -largura * (len(barras) - 1) / 2
        for k, (banco, tipo, r) in enumerate(barras):
            x = i + deslocamento + k * largura
            ax.bar(x, r - 1, bottom=1, width=largura * 0.9, color=COR[banco], edgecolor=SUPERFICIE, linewidth=1.5,
                   hatch="////" if tipo == "forcado" else None)
            rotulo = (f"{r:.1f}".replace(".", ",") + "×") if r < 10 else f"{r:.0f}×"
            ax.text(x, r * (1.08 if r >= 1 else 0.93), rotulo, ha="center", va="bottom" if r >= 1 else "top",
                    fontsize=8.5, color=TINTA)
            razoes.append(r)
            tem_forcado |= tipo == "forcado"
            linhas_csv.append(("2_ganho_indice", c, nome_serie(banco, tipo), ref, r, None, None))
    ax.axhline(1, color=TINTA_2, lw=1)
    if razoes:
        ax.set_ylim(min(razoes + [1]) / 1.7, max(razoes + [1]) * 2.0)
    ticks = [t for t in (0.1, 0.25, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000) if ax.get_ylim()[0] <= t <= ax.get_ylim()[1]]
    ax.set_yticks(ticks)
    ax.set_yticklabels([(f"{int(t)}" if t >= 1 else fmt_num(t)) + "×" for t in ticks])
    ax.set_xticks(range(len(consultas)))
    ax.set_xticklabels([TITULO[c].split(" · ")[0] + "\n" + textwrap.fill(TITULO[c].split(" · ")[1], 15)
                        for c in consultas], fontsize=8.5, color=TINTA)
    ax.set_ylabel("ganho do índice (escala logarítmica)", fontsize=9, color=TINTA_2)
    legenda = itens_legenda(so_bancos=True)
    if tem_forcado:
        legenda.append(Patch(facecolor=SUPERFICIE, edgecolor=TINTA_2, hatch="////", label="índice forçado"))
    ax.legend(handles=legenda, loc="upper left", frameon=False, fontsize=9, labelcolor=TINTA)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    salvar(fig, saida / f"grafico2_ganho_indice{sufixo}.png")


# 3. seletividade (um gráfico por consulta)
ORDEM_BARRAS = [("PostgreSQL", "sem"), ("PostgreSQL", "com"), ("PostgreSQL", "forcado"), ("MongoDB", "sem"),
                ("MongoDB", "com")]
def grafico_seletividade(sel, saida, estatistica="mediana", variacao=False):
    """variacao=True (só com a média): sobre cada barra, traço grosso = Q1 a Q3 e fio = mínimo a máximo."""
    usar_media = estatistica == "media"
    consultas = [c for c in ("C1", "C4", "C5", "C7") if c in sel]
    if not consultas:
        print("AVISO: gráfico 3 pulado (sem dados de seletividade).")
        return
    for c in consultas:
        tempos, linhas, ignorados, dist = {}, {}, set(), {}
        for rotulo, pontos in sel[c].items():
            chave = tipo_do_rotulo(rotulo)
            if chave[1] == "forcado":  # aqui só se comparam os tempos entre seletividades: o índice forçado não entra
                continue
            for param, v in pontos.items():
                if v and not param.endswith(" deletados"):  # C1 com status = DELETED ficou fora dos gráficos
                    tempos.setdefault(chave, {})[param] = v["media"] if usar_media else v["mediana"]
                    t_brutos = v["tempos_brutos"]
                    dist.setdefault(chave, {})[param] = (*np.percentile(t_brutos, [25, 75]), min(t_brutos), max(t_brutos))
                    linhas[param] = v["n_resultados"]
                    if chave[1] == "com" and v.get("indice") == "nenhum":  # índice criado, mas não usado pelo planejador
                        ignorados.add((chave[0], param))
        params = sorted(linhas, key=linhas.get)  # do mais ao menos seletivo
        series = [k for k in ORDEM_BARRAS if k in tempos]
        fig, ax = figura_simples(f"{TITULO[c]}: seletividade (volume de 1 milhão de itens)",
                                 (max(9.4, 3.0 + 1.9 * len(params)), 5.0))
        estilizar(ax)
        largura = 0.84 / max(len(series), 1)
        maximo = 0
        for j, (banco, tipo) in enumerate(series):
            for i, param in enumerate(params):
                y = tempos[(banco, tipo)].get(param)
                if y is None:
                    continue
                x = i - 0.42 + largura * (j + 0.5)
                estilo_da_barra = "ignorado" if (banco, param) in ignorados and tipo == "com" else tipo
                ax.bar(x, y, width=largura * 0.9, **estilo_barra(banco, estilo_da_barra))
                topo = y
                if variacao:
                    q1, q3, mn, mx = dist[(banco, tipo)][param]
                    ax.vlines(x, mn, mx, color=TINTA, lw=1.1, zorder=4)
                    ax.vlines(x, q1, q3, color=TINTA, lw=4, alpha=0.55, zorder=4)
                    topo = max(y, mx)
                ax.text(x, topo, " " + fmt_tempo(y), ha="center", va="bottom", fontsize=7.5, color=TINTA, rotation=90)
                maximo = max(maximo, topo)
                linhas_csv.append(("3_seletividade_media" if usar_media else "3_seletividade", c, nome_serie(banco, tipo), linhas[param], y, None, param))
        ax.set_ylim(0, maximo * 1.6 if maximo else 1)  # folga para a legenda não cobrir o rótulo da barra mais alta
        ax.set_xticks(range(len(params)))
        ax.set_xticklabels([f"{NOME_PARAM.get(p, p)}\n{fmt_linhas(linhas[p])} linhas" for p in params],
                           fontsize=8.5, color=TINTA)
        ax.set_ylabel("tempo (ms)", fontsize=8.5, color=TINTA_2)
        ax.legend(handles=legenda_barras(bool(ignorados)), loc="upper left", frameon=False, fontsize=8, labelcolor=TINTA,
                  ncol=2)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        salvar(fig, saida / f"grafico3_seletividade_{c}.png")


# 4. lote (um por operação)
ARQUIVO_LOTE = {"C2 - Inserção": "C2_insercao", "C2 - Atualização": "C2_atualizacao", "C8": "C8", "C3": "C3"}


def grafico_lote(lote, saida, estatistica="mediana"):
    """estatistica="media": ponto = média; barra grossa = Q1 a Q3; fio fino = mínimo a máximo (tudo ÷ itens do lote)."""
    media = estatistica == "media"
    paineis = [("C2 - Inserção", "C2 · upsert de itens novos"), ("C2 - Atualização", "C2 · upsert de itens existentes"),
               ("C8", TITULO["C8"]), ("C3", TITULO["C3"])]
    paineis = [p for p in paineis if p[0] in lote]
    if not paineis:
        print("AVISO: gráfico 4 (lote) pulado (sem dados de lote).")
        return
    for chave, nome in paineis:
        fig, ax = figura_simples(f"{nome}: custo por item por tamanho do lote", (7.6, 5.0))
        estilizar(ax, log_y=True, log_x=True)
        y_min, y_max = float("inf"), 0.0
        for k, (banco, tamanhos) in enumerate(lote[chave].items()):
            if media:
                pts = []
                for n, v in tamanhos.items():
                    if v:
                        t = [x / int(n) for x in v["tempos_brutos"]]
                        q1, q3 = np.percentile(t, [25, 75])
                        pts.append((int(n), sum(t) / len(t), float(q1), float(q3), min(t), max(t)))
                pts.sort()
                if not pts:
                    continue
                desloc = 1.0 + 0.10 * (k - (len(lote[chave]) - 1) / 2)  # afasta os dois bancos na escala log
                xs_p = [p[0] * desloc for p in pts]
                ax.plot(xs_p, [p[1] for p in pts], lw=2, ms=6, **estilo(banco, "com"))
                for xp, (n, m, q1, q3, mn, mx) in zip(xs_p, pts):
                    ax.vlines(xp, mn, mx, color=COR[banco], lw=1.2, zorder=2)
                    ax.vlines(xp, q1, q3, color=COR[banco], lw=5, alpha=0.45, zorder=2)
                    linhas_csv.append(("4_lote_media", chave, banco, n, m, mn, mx))
                    linhas_csv.append(("4_lote_Q1_Q3", chave, banco, n, q1, q3, None))
                y_min = min(y_min, *[p[4] for p in pts])
                y_max = max(y_max, *[p[5] for p in pts])
                continue
            pts = sorted((int(n), v["mediana"] / int(n)) for n, v in tamanhos.items() if v)
            if not pts:
                continue
            ax.plot(*zip(*pts), lw=2, ms=6, **estilo(banco, "com"))
            for n, y in pts:
                linhas_csv.append(("4_lote", chave, banco, n, y, None, None))
        tamanhos_lote = sorted({int(n) for t in lote[chave].values() for n, v in t.items() if v})  # um tique por tamanho de lote testado
        ax.set_xticks(tamanhos_lote)
        # tiques muito próximos na escala log (50.000 e 100.000) se sobrepõem: o segundo desce uma linha
        rotulos, desceu = [], False
        for k, n in enumerate(tamanhos_lote):
            desce = k > 0 and n / tamanhos_lote[k - 1] < 3 and not desceu
            rotulos.append(("\n" if desce else "") + fmt_num(n))
            desceu = desce
        ax.set_xticklabels(rotulos)
        ax.set_xlabel("itens por lote", fontsize=9, color=TINTA_2)
        if media and y_max:
            ax.set_ylim(y_min / 1.3, y_max * 1.5)
            ax.set_xlim(min(tamanhos_lote) / 1.8, max(tamanhos_lote) * 1.8)
        ax.set_ylabel("ms por item", fontsize=9, color=TINTA_2)
        ax.legend(handles=itens_legenda(so_bancos=True) + (itens_variacao() if media else []), loc="upper right",
                  frameon=False, fontsize=9, labelcolor=TINTA)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        salvar(fig, saida / f"grafico4_lote_{ARQUIVO_LOTE[chave]}.png")


# 5. espaço do índice
def grafico_espaco(vol, ref, saida):
    dados = vol[ref]
    fig, ax = figura_simples(f"Espaço ocupado pelos índices (volume de {NOME_VOLUME[ref]} de itens)", (11, 5.2))
    estilizar(ax)
    largura = 0.34
    maximo = 0
    for i, c in enumerate(LEITURAS):
        for k, banco in enumerate(("PostgreSQL", "MongoDB")):
            com = [v for r, v in dados.get(c, {}).items() if tipo_do_rotulo(r) == (banco, "com") and v]
            if not com:
                continue
            mb = com[0]["espaco_indice_mb"]
            x = i + (k - 0.5) * largura
            ax.bar(x, mb, width=largura * 0.92, color=COR[banco], edgecolor=SUPERFICIE, linewidth=1.5)
            ax.text(x, mb, (f"{mb:.1f}".replace(".", ",") if mb < 10 else f"{mb:.0f}") + " MB", ha="center",
                    va="bottom", fontsize=8.5, color=TINTA)
            maximo = max(maximo, mb)
            linhas_csv.append(("5_espaco_indice", c, banco, ref, mb, None, None))
    ax.set_ylim(0, maximo * 1.15 if maximo else 1)
    ax.set_xticks(range(len(LEITURAS)))
    ax.set_xticklabels([TITULO[c].replace(" · ", "\n") for c in LEITURAS], fontsize=9, color=TINTA)
    ax.set_ylabel("espaço do índice (MB)", fontsize=9, color=TINTA_2)
    ax.legend(handles=itens_legenda(so_bancos=True), loc="upper left", frameon=False, fontsize=9, labelcolor=TINTA)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    salvar(fig, saida / "grafico5_espaco_indice.png")


# 6-9. carga inicial dos dados
def _barras_carga(carga, series, titulo, subtitulo, nota, ylabel, arquivo, formatar, log_y=False, tamanho=(8.6, 5.2)):
    """series: [(rótulo, banco, estilo da barra, valor(c) -> número|None, máximo(c) -> número|None)]."""
    presets = [p for p in VOLUMES if p in carga]
    fig = moldura(titulo, subtitulo, nota, tamanho)
    ax = fig.add_subplot(111)
    estilizar(ax, log_y=log_y)
    largura = 0.8 / len(series)
    topo = 0
    for k, (rotulo, banco, tipo, valor, maximo) in enumerate(series):
        for i, p in enumerate(presets):
            v = valor(carga[p][banco] if banco in carga[p] else None)
            x = i + (k - (len(series) - 1) / 2) * largura
            if v is None:
                ax.text(x, 0 if not log_y else ax.get_ylim()[0], "sem\namostra", ha="center", va="bottom",
                        fontsize=7.5, color=TINTA_2)
                continue
            ax.bar(x, v, width=largura * 0.92, **estilo_barra(banco, tipo))
            m = maximo(carga[p][banco]) if maximo else None
            if m is not None and m > v:
                ax.errorbar([x], [v], yerr=[[0], [m - v]], fmt="none", ecolor=TINTA, elinewidth=1, capsize=3)
            ax.text(x, max(v, m or v), " " + formatar(v) + ("" if m is None else f"\nmáx {formatar(m)}"),
                    ha="center", va="bottom", fontsize=8, color=TINTA)
            topo = max(topo, v, m or 0)
            linhas_csv.append((arquivo, rotulo, banco, VOLUMES[p], v, None, m))
    ax.set_xticks(range(len(presets)))
    ax.set_xticklabels([f"{NOME_VOLUME[p]}\nde itens" for p in presets])
    ax.set_ylabel(ylabel, fontsize=9, color=TINTA_2)
    if log_y:
        ax.set_ylim(top=topo * 3)
    else:
        ax.set_ylim(0, topo * 1.25 if topo else 1)
    ax.legend(handles=[Patch(label=r, **estilo_barra(b, tp)) for r, b, tp, *_ in series], loc="upper left",
              frameon=False, fontsize=9, labelcolor=TINTA)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    salvar(fig, saida_global / f"{arquivo}.png")


def _valor(chave, escala=1.0):
    return lambda c: None if c is None or c.get(chave) is None else c[chave] * escala


def grafico_carga(carga, saida, pular_cpu_memoria=False):
    """pular_cpu_memoria=True: os gráficos 6, 7 e 8 (tempo, CPU e memória) saem de grafico_carga_rodadas, com a média
    das rodadas repetidas."""
    global saida_global
    saida_global = saida
    if not carga:
        return
    nota_amostra = ("Amostras tiradas só enquanto havia envio ao banco; volumes pequenos têm poucas amostras "
                    "(a carga dura menos de 1 s).")
    pg, mg = "PostgreSQL", "MongoDB"
    if not pular_cpu_memoria:
        _barras_carga(carga, [("PostgreSQL", pg, "com", _valor("tempo_envio_ms", 1e-3), None),
                              ("MongoDB", mg, "com", _valor("tempo_envio_ms", 1e-3), None)],
                      "Tempo de carga por volume",
                      "Tempo gasto enviando os itens ao banco, em lotes de 10 mil, com commit a cada lote nos dois bancos "
                      "(escala logarítmica)",
                      "Não inclui a conversão dos dados no Python.", "tempo (s)", "grafico6_carga_tempo",
                      lambda v: f"{v:.1f} s".replace(".", ",") if v >= 1 else f"{v * 1000:.0f} ms", log_y=True)
    if not pular_cpu_memoria:
        _barras_carga(carga, [("PostgreSQL", pg, "com", _valor("cpu_media_percent"), _valor("cpu_max_percent")),
                              ("MongoDB", mg, "com", _valor("cpu_media_percent"), _valor("cpu_max_percent"))],
                      "CPU durante a carga por volume",
                      "CPU do container durante o envio: média (barra) e pico (traço). 100% equivale a um núcleo",
                      nota_amostra, "CPU do container (%)", "grafico7_carga_cpu", lambda v: f"{v:.0f}%")
        _barras_carga(carga, [("PostgreSQL", pg, "com", _valor("memoria_media_mb"), _valor("memoria_max_mb")),
                              ("MongoDB", mg, "com", _valor("memoria_media_mb"), _valor("memoria_max_mb"))],
                      "Memória durante a carga por volume",
                      "Memória do container durante o envio: média (barra) e pico (traço)",
                      nota_amostra, "memória do container (MB)", "grafico8_carga_memoria", lambda v: f"{v:.0f} MB")
    _barras_carga(carga, [("PostgreSQL", pg, "com", _valor("espaco_dados_mb"), None),
                          ("MongoDB · em disco (comprimido)", mg, "com", _valor("espaco_dados_mb"), None),
                          ("MongoDB · sem compressão", mg, "sem", _valor("tamanho_logico_mb"), None)],
                  "Espaço em disco dos dados por volume",
                  "Tabela de itens do PostgreSQL contra a coleção do MongoDB, sem índices (escala logarítmica)",
                  "O MongoDB comprime os dados em disco; o PostgreSQL não comprime a tabela, por isso a comparação "
                  "justa é com o tamanho sem compressão.",
                  "espaço (MB)", "grafico9_carga_espaco",
                  lambda v: f"{v:.1f} MB".replace(".", ",") if v < 10 else f"{v:.0f} MB", log_y=True,
                  tamanho=(9.6, 5.4))


def _valores_rodadas(rodadas, preset, banco, metrica):
    # rodadas sem amostra do monitor (comum em 5 mil itens, onde a carga dura menos de 1 s) trazem CPU e memória vazios: ignoradas
    return [r[banco][metrica] for r in rodadas.get(preset, []) if r.get(banco, {}).get(metrica) is not None]


def grafico_carga_rodadas(rodadas, saida):
    """Gráficos 6, 7 e 8 (tempo, CPU e memória) a partir da carga repetida (resultados_carga_rodadas.json):
    barra = média das rodadas; fio = menor a maior valor entre as rodadas."""
    presets = [p for p in VOLUMES if p in rodadas]
    bancos = ["PostgreSQL", "MongoDB"]
    n = max(len(v) for v in rodadas.values())
    nota = (f"Barra: média de {n} rodadas da carga; fio: menor a maior valor entre as rodadas. Cada rodada recria os "
            "bancos do zero. Em 5 mil itens há só 1 a 3 amostras do monitor.")

    def barras(metrica, titulo, subtitulo, ylabel, arquivo, formatar, escala=1.0, log_y=False):
        fig = moldura(titulo, subtitulo, nota, (8.6, 5.2))
        ax = fig.add_subplot(111)
        estilizar(ax, log_y=log_y)
        largura = 0.8 / len(bancos)
        topo, piso = 0.0, float("inf")
        for k, banco in enumerate(bancos):
            for i, p in enumerate(presets):
                v = [x * escala for x in _valores_rodadas(rodadas, p, banco, metrica)]
                if not v:
                    continue
                media, mn, mx = statistics.mean(v), min(v), max(v)
                x = i + (k - (len(bancos) - 1) / 2) * largura
                ax.bar(x, media, width=largura * 0.92, **estilo_barra(banco, "com"))
                ax.errorbar([x], [media], yerr=[[media - mn], [mx - media]], fmt="none", ecolor=TINTA, elinewidth=1.2,
                            capsize=4, zorder=4)
                ax.text(x, mx, formatar(media) + "\n(média)", ha="center", va="bottom", fontsize=8.5, color=TINTA,
                        linespacing=1.15)
                topo = max(topo, mx)
                piso = min(piso, mn)
                linhas_csv.append((arquivo, metrica, banco, VOLUMES[p], media, mn, mx))
        ax.set_xticks(range(len(presets)))
        ax.set_xticklabels([f"{NOME_VOLUME[p]}\nde itens" for p in presets])
        ax.set_ylabel(ylabel, fontsize=9, color=TINTA_2)
        if log_y:
            ax.set_ylim(piso / 1.8, topo * 3)
        else:
            ax.set_ylim(0, topo * 1.4 if topo else 1)
        ax.legend(handles=[Patch(label=b, **estilo_barra(b, "com")) for b in bancos]
                  + [Line2D([], [], color=TINTA, lw=1.2, label="fio: mínimo a máximo entre as rodadas")],
                  loc="upper left", frameon=False, fontsize=9, labelcolor=TINTA)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        salvar(fig, saida / f"{arquivo}.png")

    barras("tempo_envio_ms", "Tempo de carga por volume",
           "Tempo gasto enviando os itens ao banco, em lotes de 10 mil, com commit a cada lote nos dois bancos (escala logarítmica)",
           "tempo (s)", "grafico6_carga_tempo",
           lambda v: f"{v:.1f} s".replace(".", ",") if v >= 1 else f"{v * 1000:.0f} ms", escala=1e-3, log_y=True)
    barras("cpu_media_percent", "CPU durante a carga por volume",
           "CPU média do container durante o envio (100% = um núcleo)", "CPU do container (%)",
           "grafico7_carga_cpu", lambda v: f"{v:.0f}%")
    barras("memoria_media_mb", "Memória durante a carga por volume",
           "Memória média do container durante o envio", "memória do container (MB)",
           "grafico8_carga_memoria", lambda v: f"{v:.0f} MB")


saida_global = None


def main():
    p = argparse.ArgumentParser(description="Gráficos do TCC a partir dos JSON do analises.py.")
    p.add_argument("--entrada", default="resultados", help="pasta com os JSON (padrão: ./resultados)")
    p.add_argument("--saida", default=None, help="pasta dos PNG (padrão: <entrada>/graficos)")
    p.add_argument("--ref", default=None, choices=list(VOLUMES),
                   help="volume usado nos gráficos 2 e 5 (padrão: Grande)")
    a = p.parse_args()

    entrada = Path(a.entrada) if Path(a.entrada).is_absolute() else RAIZ / a.entrada
    saida = (Path(a.saida) if Path(a.saida).is_absolute() else RAIZ / a.saida) if a.saida else entrada / "graficos"
    saida.mkdir(parents=True, exist_ok=True)

    vol = carregar(entrada / "resultados_volume.json")
    sel = carregar(entrada / "resultados_seletividade.json")
    lote = carregar(entrada / "resultados_lote.json")
    carga = carregar(entrada / "resultados_carga.json")
    rodadas_carga = (json.loads((entrada / "resultados_carga_rodadas.json").read_text(encoding="utf-8"))
                     if (entrada / "resultados_carga_rodadas.json").exists() else None)
    if not (vol or sel or lote or carga):
        sys.exit(f"Nenhum JSON encontrado em {entrada}. Rode o analises.py antes.")

    if vol:
        disponiveis = [pr for pr in VOLUMES if pr in vol]
        ref = a.ref if a.ref in vol else ("Grande" if "Grande" in vol else disponiveis[-1])
        print(f"Volume de referência dos gráficos 2 e 5: {ref}")
        grafico_escala(vol, saida)
        grafico_ganho(vol, ref, saida, estatistica="media")
        grafico_espaco(vol, ref, saida)
    if sel:
        grafico_seletividade(sel, saida, estatistica="media")
    if lote:
        grafico_lote(lote, saida, estatistica="media")
    if carga:
        grafico_carga(carga, saida, pular_cpu_memoria=bool(rodadas_carga))
    if rodadas_carga:  # carga repetida: tempo, CPU e memória (6, 7 e 8) com a média das rodadas e o fio de mínimo a máximo
        grafico_carga_rodadas(rodadas_carga, saida)

    with open(saida / "dados_graficos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grafico", "painel", "serie", "x", "y", "min", "max_ou_parametro"])
        w.writerows(linhas_csv)
    print("tabela dos dados plotados:", saida / "dados_graficos.csv")


if __name__ == "__main__":
    main()
