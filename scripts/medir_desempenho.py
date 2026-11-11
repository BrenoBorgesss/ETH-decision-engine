"""Mede o desempenho do projeto com execuções reais e grava os números num JSON.

Cada seção roda num processo separado, para o pico de memória de uma não contaminar a outra.
Os tempos são a MEDIANA de várias repetições (o número de repetições aparece no resultado).

Uso (na raiz do projeto):
    python scripts/medir_desempenho.py --saida medicao.json          # histórico completo (data/history)
    python scripts/medir_desempenho.py --demo --saida medicao.json   # amostra do repositório

Seções: inicializacao, leitura, validacao, indicadores, backtest, gravacao, ao_vivo.
A seção ao_vivo usa sempre a amostra do repositório e uma Binance simulada (tests/fake_binance.py):
mede o custo de processamento de uma análise e o efeito de uma latência de rede simulada.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ["inicializacao", "leitura", "validacao", "indicadores", "backtest", "gravacao", "ao_vivo"]


def peak_memory_mb() -> float:
    """Pico de memória física usado por este processo, em MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.PeakWorkingSetSize / 1e6
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def median_s(fn: Callable[[], object], repeats: int) -> float:
    """Mediana do tempo (s) de `repeats` chamadas de `fn`."""
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def load_cfg(demo: bool):  # noqa: ANN201
    """Config normal ou do modo demonstração."""
    from cryptoagent.config import DEMO_RELATIVE, load_config

    return load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE if demo else None)


def sec_inicializacao(demo: bool) -> dict:
    """Tempo para o programa começar: importar o pacote e responder a --version."""
    def run(args: list[str]) -> None:
        subprocess.run([sys.executable, *args], check=True, capture_output=True, cwd=ROOT)

    reps = 7
    return {
        "repeticoes": reps,
        "python_vazio_s": median_s(lambda: run(["-c", "pass"]), reps),
        "importar_cli_s": median_s(lambda: run(["-c", "import cryptoagent.cli"]), reps),
        "version_s": median_s(lambda: run(["-m", "cryptoagent", "--version"]), reps),
        "help_s": median_s(lambda: run(["-m", "cryptoagent", "--help"]), reps),
    }


def sec_leitura(demo: bool) -> dict:
    """Tempo para ler do disco cada série do par de análise e a de 1m do par de operação."""
    from cryptoagent.data.history import HistoryStore

    cfg = load_cfg(demo)
    store = HistoryStore(cfg.path(cfg.historico.pasta))
    sym = cfg.mercado.simbolo_analise
    out: dict = {"repeticoes": 5, "series": {}}
    for tf in cfg.estrategia.timeframes:
        rows = len(store.load(sym, tf))
        out["series"][f"{sym} {tf}"] = {"linhas": rows, "s": median_s(lambda tf=tf: store.load(sym, tf), 5)}
    out["pico_memoria_mb"] = peak_memory_mb()
    return out


def sec_validacao(demo: bool) -> dict:
    """Tempo da validação de qualidade na maior série (1m)."""
    from cryptoagent.data.history import HistoryStore
    from cryptoagent.data.validation import clean_klines

    cfg = load_cfg(demo)
    df = HistoryStore(cfg.path(cfg.historico.pasta)).load(cfg.mercado.simbolo_analise, "1m")
    return {"repeticoes": 5, "linhas": len(df), "s": median_s(lambda: clean_klines(df, "1m"), 5), "pico_memoria_mb": peak_memory_mb()}


def sec_indicadores(demo: bool) -> dict:
    """Tempo do cálculo de indicadores: no histórico inteiro e no tamanho usado ao vivo (1000 candles)."""
    from cryptoagent.data.history import HistoryStore
    from cryptoagent.indicators.compute import compute_indicators

    cfg = load_cfg(demo)
    store = HistoryStore(cfg.path(cfg.historico.pasta))
    sym = cfg.mercado.simbolo_analise
    kw = dict(tz_name=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local)
    out: dict = {"repeticoes_historico": 3, "repeticoes_ao_vivo": 30, "historico": {}, "ao_vivo_1000_candles": {}}
    live_total = 0.0
    for tf in cfg.estrategia.timeframes:
        df = store.load(sym, tf)
        out["historico"][tf] = {"linhas": len(df), "s": median_s(lambda df=df: compute_indicators(df, cfg.indicadores, cfg.regime, **kw), 3)}
        last = df.tail(1000).reset_index(drop=True)
        t = median_s(lambda last=last: compute_indicators(last, cfg.indicadores, cfg.regime, **kw), 30)
        out["ao_vivo_1000_candles"][tf] = t
        live_total += t
    out["ao_vivo_todos_os_timeframes_s"] = live_total
    out["pico_memoria_mb"] = peak_memory_mb()
    return out


def sec_backtest(demo: bool) -> dict:
    """Tempo do backtest rápido do ETHBRL, com custos do config (sem conta), por fase."""
    from cryptoagent.backtest.costs import build_schedule
    from cryptoagent.backtest.data import load_backtest_data
    from cryptoagent.backtest.runner import run_pair

    cfg = load_cfg(demo)
    bt = cfg.backtest
    pair = "ETHBRL"
    costs = build_schedule(
        pair, cfg.custos.taxa_padrao_taker, cfg.sazonalidade.spread_padrao_pct.get(pair, 0.05), None, cfg.fuso.local,
        min_samples=bt.amostras_min_spread_hora, slip_market=bt.slippage_mercado_pct, slip_stop=bt.slippage_stop_pct, fee_source="do config",
    )
    t0 = time.perf_counter()
    data = load_backtest_data(cfg, pair, season_cost_pct=costs.mean_roundtrip_pct)
    t1 = time.perf_counter()
    mem_load = peak_memory_mb()
    rep = run_pair(cfg, data, costs, quick=True, progress=lambda msg: None)
    t2 = time.perf_counter()
    return {
        "repeticoes": 1,
        "carregar_e_preparar_s": t1 - t0,
        "simular_s": t2 - t1,
        "total_s": t2 - t0,
        "candles_de_decisao": int(len(data.frames[data.decision_tf])),
        "operacoes_fora_da_amostra": rep.default.metrics.get("operacoes"),
        "retorno_total_pct": rep.default.metrics.get("retorno_total_pct"),
        "pico_memoria_apos_carregar_mb": mem_load,
        "pico_memoria_mb": peak_memory_mb(),
    }


def sec_gravacao(demo: bool) -> dict:
    """Tempo para gravar a série de 1m inteira (o que cada atualização incremental faz hoje)."""
    from cryptoagent.data.history import HistoryStore

    cfg = load_cfg(demo)
    df = HistoryStore(cfg.path(cfg.historico.pasta)).load(cfg.mercado.simbolo_analise, "1m")
    with tempfile.TemporaryDirectory() as tmp:
        store = HistoryStore(Path(tmp))
        t = median_s(lambda: store.save("X", "1m", df), 3)
        size = store.path("X", "1m").stat().st_size
    return {"repeticoes": 3, "linhas": len(df), "s": t, "arquivo_mb": size / 1e6}


def sec_ao_vivo(demo: bool) -> dict:
    """Tempo de uma análise ao vivo completa (carteira virtual), com Binance simulada e candles da amostra."""
    import asyncio
    import sqlite3
    from datetime import UTC, datetime

    sys.path.insert(0, str(ROOT / "tests"))
    from fake_binance import FakeBinance, FakeClock

    from cryptoagent.config import Secrets
    from cryptoagent.data.rest import BinanceReadOnlyClient
    from cryptoagent.engine import live
    from cryptoagent.timeutils import datetime_to_ms

    cfg = load_cfg(True)
    start = datetime_to_ms(datetime(2026, 9, 1, 15, 15, 7, tzinfo=UTC))
    paper = live.PaperView("ETHBRL", fee=0.001, capital=1000.0, risk_per_trade=10.0)

    async def no_sleep(_s: float) -> None:
        return None

    async def session(latency_s: float, n: int) -> tuple[list[float], dict]:
        """`n` análises seguidas na mesma sessão, uma a cada 5 minutos simulados (como o painel faz)."""
        clock = FakeClock(start)
        live.now_ms = clock
        server = FakeBinance(clock, latency_s=latency_s)
        times = []
        async with BinanceReadOnlyClient(cfg.binance, Secrets(), transport=server.transport(), sleep=no_sleep, clock=clock) as client:
            for _ in range(n):
                t0 = time.perf_counter()
                await live.analyze_live(cfg, Secrets(), client, sqlite3.connect(":memory:"), paper=paper)
                times.append(time.perf_counter() - t0)
                clock.t += 300_000
        return times, dict(server.calls)

    cpu, calls = asyncio.run(session(0.0, 13))
    net, _ = asyncio.run(session(0.05, 7))
    return {
        "analises_seguidas": 12,
        "consultas_por_analise": {path: round(n / 13, 2) for path, n in sorted(calls.items())},
        "sem_latencia_primeira_s": cpu[0],
        "sem_latencia_mediana_das_seguintes_s": statistics.median(cpu[1:]),
        "latencia_simulada_por_consulta_s": 0.05,
        "com_latencia_primeira_s": net[0],
        "com_latencia_mediana_das_seguintes_s": statistics.median(net[1:]),
        "pico_memoria_mb": peak_memory_mb(),
    }


def main() -> int:
    """Roda as seções pedidas (cada uma em seu processo) e grava o JSON. Devolve o código de saída."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", action="store_true", help="mede na amostra do repositório em vez do histórico completo")
    parser.add_argument("--saida", type=Path, default=None, help="arquivo JSON de saída")
    parser.add_argument("--secao", choices=SECTIONS, default=None, help="(uso interno) roda só uma seção e imprime o JSON")
    args = parser.parse_args()
    if args.secao:
        print(json.dumps(globals()[f"sec_{args.secao}"](args.demo)))
        return 0
    result: dict = {"dados": "amostra do repositório (demo)" if args.demo else "histórico completo", "python": sys.version.split()[0]}
    for name in SECTIONS:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--secao", name, *(["--demo"] if args.demo else [])]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        if proc.returncode != 0:
            print(f"seção {name} falhou:\n{proc.stderr[-2000:]}")
            return 1
        result[name] = json.loads(proc.stdout.strip().splitlines()[-1])
        print(f"{name}: ok")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.saida:
        args.saida.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
