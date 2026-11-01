"""Mede quantos forks ESTA máquina aguenta sem engasgar. Rode antes de planejar a tentativa.

Uso (na pasta do kit, com a venv ativa):
  python scripts/medir_forks.py                # testa 1, 2, 3, 4, 6 e 8 forks
  python scripts/medir_forks.py --max 4        # para em 4
  python scripts/medir_forks.py --leve         # só o eval (mais rápido; mede o pior caso de menos)

O que um "fork" faz aqui é o trabalho pesado de verdade de uma frente no relógio: a suíte de testes do kit
seguida do eval offline. Cada rodada sobe N cópias AO MESMO TEMPO e mede:
- o tempo de cada cópia comparado a uma cópia sozinha (lentidão: 1,0 = sem perda);
- a memória livre no pior momento.
Recomendação: o maior N com lentidão <= 1,5 e pelo menos 1,5 GB de memória livre. Acima disso, os números de
latência medidos no relógio deixam de valer (Getnet v3: p95 de 21 s com 8 forks disputando a CPU) e o
`check.py` de cada fork demora tanto que a integração atrasa.

O processo do Claude Code de cada fork (a sessão em si) também ocupa memória: informe com --ram-sessao (MB,
padrão 600) e o script desconta isso da memória livre ao recomendar.

Só biblioteca padrão; funciona em Windows, Linux e macOS. Grava o resultado em var/medir_forks.json.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "LLM_PROVIDER": "offline"}


def free_ram_mb() -> float | None:
    """Memória física livre agora, em MB (None se o sistema não disser)."""
    if os.name == "nt":
        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullAvailPhys / 2**20
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:  # macOS: páginas livres + inativas
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        page = 4096
        pages = sum(int(ln.split()[-1].rstrip(".")) for ln in out.splitlines()
                    if ln.startswith(("Pages free", "Pages inactive")))
        return pages * page / 2**20
    except Exception:
        return None


def total_ram_mb() -> float | None:
    if os.name == "nt":
        class MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("rest", ctypes.c_ulonglong * 6)]
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys / 2**20
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def one_fork(leve: bool) -> float:
    """O trabalho de uma frente: testes do kit (série, como no fork) + eval offline. Devolve segundos."""
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**ENV, "DB_PATH": str(Path(tmp) / "fork.db")}
        if not leve:
            subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "--no-header"],
                           cwd=ROOT, env=env, capture_output=True)
        subprocess.run([PY, "-m", "evals.run_evals", "--out", str(Path(tmp) / "r.json")],
                       cwd=ROOT, env=env, capture_output=True)
    return time.perf_counter() - t0


def round_of(n: int, leve: bool) -> dict:
    times: list[float] = []
    lowest = [free_ram_mb() or 0.0]
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            ram = free_ram_mb()
            if ram is not None:
                lowest[0] = min(lowest[0], ram)
            time.sleep(0.5)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    t0 = time.perf_counter()
    workers = [threading.Thread(target=lambda: times.append(one_fork(leve))) for _ in range(n)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    stop.set()
    watcher.join()
    return {"forks": n, "wall_s": round(time.perf_counter() - t0, 1), "mean_fork_s": round(sum(times) / n, 1),
            "max_fork_s": round(max(times), 1), "min_free_ram_mb": round(lowest[0])}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=8)
    ap.add_argument("--leve", action="store_true", help="só o eval offline em cada fork")
    ap.add_argument("--ram-sessao", type=int, default=600, help="MB por sessão do Claude Code aberta")
    args = ap.parse_args()

    levels = [n for n in (1, 2, 3, 4, 6, 8) if n <= args.max]
    cpus = os.cpu_count() or 1
    print(f"Máquina: {cpus} núcleos lógicos, {round((total_ram_mb() or 0) / 1024, 1)} GB de RAM, "
          f"{round((free_ram_mb() or 0) / 1024, 1)} GB livres agora. Feche o resto antes de medir.\n")
    rows = []
    for n in levels:
        print(f"rodando {n} fork(s) ao mesmo tempo...", flush=True)
        row = round_of(n, args.leve)
        base = rows[0]["mean_fork_s"] if rows else row["mean_fork_s"]
        row["lentidao"] = round(row["max_fork_s"] / base, 2)
        # memória que sobraria com N sessões do Claude Code abertas além do trabalho medido
        row["ram_livre_com_sessoes_mb"] = round(row["min_free_ram_mb"] - n * args.ram_sessao)
        rows.append(row)
        print(f"  pior fork {row['max_fork_s']} s · lentidão {row['lentidao']}× · "
              f"RAM livre no pior momento {row['min_free_ram_mb']} MB "
              f"(com {n} sessão(ões) do Claude: {row['ram_livre_com_sessoes_mb']} MB)")
        if row["lentidao"] > 3 or row["ram_livre_com_sessoes_mb"] < 0:
            print("  máquina saturada: parei aqui.")
            break

    ok = [r for r in rows if r["lentidao"] <= 1.5 and r["ram_livre_com_sessoes_mb"] >= 1536]
    best = max((r["forks"] for r in ok), default=1)
    print("\n| forks | pior fork (s) | lentidão | RAM livre c/ sessões (MB) |\n|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['forks']} | {r['max_fork_s']} | {r['lentidao']}× | {r['ram_livre_com_sessoes_mb']} |")
    print(f"\nRECOMENDADO NESTA MÁQUINA: no máximo {best} fork(s) rodando check ao mesmo tempo "
          "(lentidão <= 1,5× e >= 1,5 GB livres).")
    print("O resto das frentes vai para sessões na nuvem (docs/kit/PLAYBOOK.md).")
    out = ROOT / "var" / "medir_forks.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"cpus": cpus, "total_ram_mb": total_ram_mb(), "rows": rows,
                               "recommended_forks": best, "leve": args.leve,
                               "measured_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2), encoding="utf-8")
    print(f"Gravado em {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
