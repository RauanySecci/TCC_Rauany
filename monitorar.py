"""
Monitor de recursos para acompanhar o benchmark.py e achar o gargalo depois.

Terminal 1 (monitor):   python monitorar.py                 # grava resultados/monitor.csv a cada 2 s
Terminal 2 (benchmark): python benchmark.py volume
Ao terminar o benchmark, Ctrl+C no monitor. Depois:

    python monitorar.py --grafico                           # gera resultados/monitor.png

O CSV tem uma linha por amostra, com:
  - cada container (CPU %, memória MiB, limite MiB, % do limite);
  - o PC (CPU %, RAM disponível, swap usado, swap in/out por segundo, leitura/escrita de disco);
  - pressão de memória/IO/CPU do kernel (PSI): quanto % do tempo algo ficou parado esperando.
O benchmark.py escreve em resultados/fases.csv o início de cada fase; o gráfico as usa como marcas.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

RAIZ = Path(__file__).resolve().parent
CONTAINERS = ["catalogo-postgres-db", "catalogo-mongo-db"]
MIB = 1024 * 1024


def unidade_para_mib(texto):
    texto = texto.strip()
    for sufixo, fator in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / MIB)):
        if texto.endswith(sufixo):
            return float(texto[: -len(sufixo)]) * fator
    return float("nan")


def ler_docker():
    """{container: (cpu%, mem MiB, limite MiB)} numa única chamada ao docker."""
    saida = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *CONTAINERS],
        capture_output=True, text=True,
    ).stdout
    dados = {}
    for linha in saida.splitlines():
        if not linha.strip():
            continue
        d = json.loads(linha)
        usada, limite = d["MemUsage"].split("/")
        dados[d["Name"]] = (float(d["CPUPerc"].rstrip("%")), unidade_para_mib(usada), unidade_para_mib(limite))
    return dados


def ler_psi(recurso):
    """Pressão do kernel (avg10, em %): tempo recente em que processos ficaram esperando."""
    try:
        some = full = 0.0
        for linha in Path(f"/proc/pressure/{recurso}").read_text().splitlines():
            tipo, *campos = linha.split()
            avg10 = float(dict(c.split("=") for c in campos)["avg10"])
            if tipo == "some":
                some = avg10
            else:
                full = avg10
        return some, full
    except OSError:
        return float("nan"), float("nan")


def colunas():
    cols = ["t", "epoch", "iso"]
    for c in CONTAINERS:
        cols += [f"{c}_cpu", f"{c}_mem_mib", f"{c}_limite_mib", f"{c}_mem_pct"]
    cols += ["host_cpu", "host_ram_disp_mib", "host_ram_usada_pct", "host_swap_usado_mib",
             "swap_in_mib_s", "swap_out_mib_s", "disco_leitura_mib_s", "disco_escrita_mib_s",
             "psi_mem_some", "psi_mem_full", "psi_io_some", "psi_io_full", "psi_cpu_some"]
    return cols


def monitorar(caminho, intervalo):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    cols = colunas()
    inicio = time.time()
    swap_ant, disco_ant, t_ant = psutil.swap_memory(), psutil.disk_io_counters(), inicio
    psutil.cpu_percent(None)
    print(f"Gravando em {caminho} a cada {intervalo}s. Ctrl+C para parar.")
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        try:
            while True:
                docker = ler_docker()  # leva ~1 s; o tempo real da amostra é o do relógio
                agora = time.time()
                dt = max(agora - t_ant, 1e-6)
                mem, swap, disco = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_io_counters()
                linha = [round(agora - inicio, 2), round(agora, 2), time.strftime("%H:%M:%S", time.localtime(agora))]
                for c in CONTAINERS:
                    cpu, usada, limite = docker.get(c, (float("nan"),) * 3)
                    linha += [cpu, round(usada, 1), round(limite, 1), round(100 * usada / limite, 1) if limite else float("nan")]
                mem_some, mem_full = ler_psi("memory")
                io_some, io_full = ler_psi("io")
                cpu_some, _ = ler_psi("cpu")
                linha += [
                    psutil.cpu_percent(None), round(mem.available / MIB, 1), mem.percent, round(swap.used / MIB, 1),
                    round((swap.sin - swap_ant.sin) / MIB / dt, 3), round((swap.sout - swap_ant.sout) / MIB / dt, 3),
                    round((disco.read_bytes - disco_ant.read_bytes) / MIB / dt, 2),
                    round((disco.write_bytes - disco_ant.write_bytes) / MIB / dt, 2),
                    mem_some, mem_full, io_some, io_full, cpu_some,
                ]
                w.writerow(linha)
                f.flush()
                swap_ant, disco_ant, t_ant = swap, disco, agora
                time.sleep(max(0, intervalo - (time.time() - agora)))
        except KeyboardInterrupt:
            print("\nMonitor encerrado.")


def grafico(csv_path, fases_path, png_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    df = pd.read_csv(csv_path)
    if df.empty:
        sys.exit("CSV vazio: rode o monitor por mais tempo.")
    t = df["t"] / 60
    fases = pd.read_csv(fases_path) if fases_path.exists() else pd.DataFrame(columns=["epoch", "fase"])
    inicio_epoch = df["epoch"].iloc[0] - df["t"].iloc[0]  # fases.csv usa epoch; converte para minutos desde o início

    fig, ax = plt.subplots(5, 1, figsize=(14, 15), sharex=True)
    for c in CONTAINERS:
        ax[0].plot(t, df[f"{c}_mem_mib"], label=c)
    ax[0].axhline(df[f"{CONTAINERS[0]}_limite_mib"].dropna().iloc[0], color="red", ls="--", lw=1, label="limite do container")
    ax[0].set_ylabel("Memória (MiB)"); ax[0].set_title("Memória dos containers (perto da linha vermelha = limite apertado)")
    for c in CONTAINERS:
        ax[1].plot(t, df[f"{c}_cpu"], label=c)
    ax[1].set_ylabel("CPU (%)"); ax[1].set_title("CPU dos containers (200% = limite de 2 CPUs)")
    ax[2].plot(t, df["host_ram_disp_mib"], color="tab:green", label="RAM disponível (PC)")
    ax2 = ax[2].twinx(); ax2.plot(t, df["host_swap_usado_mib"], color="tab:purple", label="Swap usado"); ax2.set_ylabel("Swap (MiB)")
    ax[2].set_ylabel("RAM disponível (MiB)"); ax[2].set_title("PC: RAM disponível caindo a ~0 + swap subindo = falta de RAM do PC")
    ax[3].plot(t, df["swap_in_mib_s"], label="swap in"); ax[3].plot(t, df["swap_out_mib_s"], label="swap out")
    ax[3].plot(t, df["disco_leitura_mib_s"], label="disco leitura"); ax[3].plot(t, df["disco_escrita_mib_s"], label="disco escrita")
    ax[3].set_ylabel("MiB/s"); ax[3].set_title("Swap e disco (swap in/out contínuo = o PC está paginando)")
    ax[4].plot(t, df["psi_mem_some"], label="memória (espera)"); ax[4].plot(t, df["psi_io_some"], label="disco (espera)")
    ax[4].plot(t, df["psi_cpu_some"], label="CPU (espera)")
    ax[4].set_ylabel("% do tempo esperando"); ax[4].set_title("Pressão do kernel (PSI): quem está segurando o sistema"); ax[4].set_xlabel("minutos")
    for a in ax:
        a.legend(loc="upper left", fontsize=8); a.grid(alpha=0.3)
        for _, r in fases.iterrows():
            x = (r["epoch"] - inicio_epoch) / 60
            if 0 <= x <= t.max():
                a.axvline(x, color="gray", ls=":", lw=1)
    for _, r in fases.iterrows():
        x = (r["epoch"] - inicio_epoch) / 60
        if 0 <= x <= t.max():
            ax[0].text(x, ax[0].get_ylim()[1], " " + str(r["fase"]), rotation=90, va="top", fontsize=7, color="gray")
    fig.tight_layout(); fig.savefig(png_path, dpi=110)
    print(f"Gráfico salvo em {png_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Monitor de recursos (containers + PC) para o benchmark.")
    p.add_argument("--saida", default="resultados", help="pasta do CSV (padrão: ./resultados)")
    p.add_argument("--intervalo", type=float, default=2.0, help="segundos entre amostras (padrão: 2)")
    p.add_argument("--grafico", action="store_true", help="gera o PNG a partir do CSV existente e sai")
    a = p.parse_args()
    pasta = Path(a.saida) if Path(a.saida).is_absolute() else RAIZ / a.saida
    if a.grafico:
        grafico(pasta / "monitor.csv", pasta / "fases.csv", pasta / "monitor.png")
    else:
        monitorar(pasta / "monitor.csv", a.intervalo)
