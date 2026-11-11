"""Dados das abas "Simulação" e "Operações reais" do painel (sem rede; só leitura dos bancos locais).

- Simulação: lido do banco SÓ da simulação, em modo somente leitura. Para cada carteira virtual:
  patrimônio, resultado em R$, posição aberta, curva de capital, operações e as mesmas métricas do
  backtest. Mais as versões da estratégia e a calibração do último ciclo de aprendizado.
- Operações reais: o diário manual (o que você registra à mão), com resultado realizado em R$ por dia
  calculado pelo mesmo livro FIFO da conta. Fica separado da simulação, em outro banco e outra aba.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from cryptoagent.account.ledger import LedgerTrade, OperationLedger
from cryptoagent.backtest.metrics import compute_metrics
from cryptoagent.paper.book import to_backtest_trades
from cryptoagent.timeutils import local_trading_day, now_ms


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def simulation_payload(db: Path, learning_folder: Path, tz: str, *, end_ms: int | None = None, max_trades: int = 40) -> dict:
    """Tudo da aba Simulação. `end_ms` (opcional) é o "agora" do período, para o modo demonstração."""
    out: dict = {"disponivel": False, "carteiras": [], "versoes": [], "calibracao": {}}
    conn = _ro(db)
    if conn is not None:
        try:
            states = conn.execute("SELECT * FROM paper_state ORDER BY variant").fetchall()
            for st in states:
                v = st["variant"]
                rows = conn.execute("SELECT * FROM paper_trades WHERE variant = ? ORDER BY entry_ms", (v,)).fetchall()
                closed = [r for r in rows if r["exit_ms"] is not None]
                opened = next((r for r in rows if r["exit_ms"] is None), None)
                initial = float(st["initial_capital"]) if "initial_capital" in st.keys() else float(st["equity"])
                end = end_ms or now_ms()
                metrics = compute_metrics(to_backtest_trades(closed), int(st["started_ms"]), max(end, int(st["started_ms"]) + 1), initial, tz)
                curve, eq = [[int(st["started_ms"]), initial]], initial
                for r in closed:
                    eq += float(r["pnl"])
                    curve.append([int(r["exit_ms"]), eq])
                out["carteiras"].append({
                    "variante": v, "capital_inicial": initial, "patrimonio": float(st["equity"]), "resultado_brl": float(st["equity"]) - initial,
                    "perda_max_dia": float(st["max_loss_day"]) if "max_loss_day" in st.keys() else None, "desde_ms": int(st["started_ms"]),
                    "metricas": metrics, "curva": curve,
                    "aberta": None if opened is None else {
                        "entrada_ms": opened["entry_ms"], "preco": opened["entry_price"], "qtd": opened["qty"], "stop": opened["stop"], "alvo1": opened["target1"],
                    },
                    "operacoes": [
                        {"id": r["id"], "entrada_ms": r["entry_ms"], "saida_ms": r["exit_ms"], "entrada": r["entry_price"], "saida": r["exit_price"],
                         "qtd": r["qty"], "valor": r["size"], "resultado_brl": r["pnl"], "resultado_pct": r["pnl_pct"], "taxas": r["fees"],
                         "motivo_saida": r["exit_reason"], "confianca": r["confidence"], "regime": r["regime"],
                         "motivo_entrada": r["entry_reason"] if "entry_reason" in r.keys() else None}
                        for r in reversed(closed[-max_trades:])
                    ],
                })
            out["disponivel"] = bool(states)
        except sqlite3.Error:
            out["erro"] = "não foi possível ler o banco da simulação"
        finally:
            conn.close()
    out.update(_learning(learning_folder))
    return out


def _learning(folder: Path) -> dict:
    from cryptoagent.learning import panorama, registry

    try:
        reg = registry.load(folder)
    except (OSError, ValueError):
        return {"versoes": [], "calibracao": {}}
    versions = []
    for v in reg.versions:
        ev = v.last
        versions.append({
            "id": v.id, "nome": v.name, "estado": v.state,
            "ciclo": ev.cycle if ev else None, "ate_ms": ev.until_ms if ev else None, "decisao": ev.decision if ev else None,
            "motivos": ev.reasons if ev else [], "metricas": ev.metrics if ev else {},
        })
    pan = panorama.load(folder) or {}
    return {
        "versoes": versions,
        "campeao": next((x["id"] for x in versions if x["estado"] == "campeao"), None),
        "calibracao": {"regras": pan.get("calibracao_regras", []), "modelo": pan.get("calibracao_modelo", []), "ate_ms": pan.get("ate_ms")},
        "drift": (pan.get("drift") or {}).get("resumo") if (pan.get("drift") or {}).get("alerta") else None,
    }


def journal_payload(conn: sqlite3.Connection, tz: str, day_start: str) -> dict:
    """Aba Operações reais: o diário manual e o resultado realizado (R$) por dia, pelo livro FIFO."""
    rows = conn.execute("SELECT * FROM manual_trades ORDER BY time_ms, id").fetchall()
    by_symbol: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    days: dict[tuple[str, str], dict] = {}
    items = []
    for symbol, trs in by_symbol.items():
        ledger = OperationLedger()
        for r in trs:
            att = ledger.apply(LedgerTrade(f"m{r['id']}", r["time_ms"], r["side"], r["qty"], r["price"], r["fee_quote"], source="manual"))
            day = local_trading_day(r["time_ms"], tz, day_start).isoformat()
            d = days.setdefault((day, symbol), {"dia": day, "par": symbol, "operacoes": 0, "realizado": 0.0, "taxas": 0.0})
            d["operacoes"] += 1
            d["realizado"] += att.realized_quote
            d["taxas"] += r["fee_quote"]
            items.append({"id": r["id"], "quando_ms": r["time_ms"], "par": symbol, "lado": "compra" if r["side"] == "buy" else "venda",
                          "qtd": r["qty"], "preco": r["price"], "taxa": r["fee_quote"], "nota": r["note"], "resultado": att.realized_quote if r["side"] == "sell" else None})
        if ledger.open_qty > 1e-12:
            items.append({"posicao_aberta": {"par": symbol, "qtd": ledger.open_qty, "custo_medio": ledger.avg_cost}})
    return {
        "operacoes": [i for i in reversed(items) if "id" in i],
        "posicoes": [i["posicao_aberta"] for i in items if "posicao_aberta" in i],
        "dias": sorted(days.values(), key=lambda d: (d["dia"], d["par"]), reverse=True),
        "moeda": {s: ("R$" if s.endswith("BRL") else s[-4:] if s.endswith("USDT") else "") for s in by_symbol},
    }
