"""Junta perfil + validação de um par e salva o resultado (JSON para o agente, HTML para você ver)."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cryptoagent.config import Config
from cryptoagent.seasonality.cost_model import CostModel
from cryptoagent.seasonality.profiles import WEEKDAYS_PT, add_buckets, bucket_stats, classify
from cryptoagent.seasonality.validation import ValidationReport, validate
from cryptoagent.timeutils import format_local, now_ms

DAY_MS = 86_400_000


@dataclass
class SymbolAnalysis:
    symbol: str
    timeframe: str
    cost: CostModel
    n_candles: int
    first_ms: int
    last_ms: int
    window_start_ms: int  # perfil atual = últimos meses_treino meses
    hours: pd.DataFrame  # por hora, com classe e se o padrão foi validado
    heat: pd.DataFrame  # por (dia da semana, hora)
    weekdays: pd.DataFrame
    validation: ValidationReport
    folga: float

    def hour_row(self, hour: int) -> pd.Series:
        return self.hours.loc[hour]


def analyze_symbol(df: pd.DataFrame, symbol: str, cfg: Config, cost: CostModel) -> SymbolAnalysis:
    s = cfg.sazonalidade
    df_b = add_buckets(df, cfg.fuso.local, cfg.indicadores.atr)
    report = validate(df_b, cost.breakeven_pct, s)
    last_ms = int(df_b["open_time"].iloc[-1])
    window_start = last_ms - int(s.meses_treino * 30.44 * DAY_MS)
    recent = df_b[df_b["open_time"] >= window_start]

    hours = bucket_stats(recent, ["hour"], cost.breakeven_pct, s.custo_folga)
    hours["class"] = classify(hours, s.pct_min_compensa, s.pct_max_nao_compensa)
    kept = {(p.key, p.claim) for p in report.kept("hora")}
    hours["validated"] = [(f"{int(h):02d}h", c) in kept for h, c in hours["class"].items()]
    heat = bucket_stats(recent, ["weekday", "hour"], cost.breakeven_pct, s.custo_folga)
    weekdays = bucket_stats(recent, ["weekday"], cost.breakeven_pct, s.custo_folga)
    return SymbolAnalysis(
        symbol, s.timeframe, cost, len(df_b), int(df_b["open_time"].iloc[0]), last_ms, window_start, hours, heat, weekdays, report, s.custo_folga
    )


# ----------------------------------------------------------------------- JSON


def to_dict(a: SymbolAnalysis) -> dict:
    v = a.validation
    grid = lambda col: [[float(a.heat[col].get((d, h), float("nan"))) for h in range(24)] for d in range(7)]
    return {
        "symbol": a.symbol,
        "timeframe": a.timeframe,
        "generated_ms": now_ms(),
        "data": {"candles": a.n_candles, "first_ms": a.first_ms, "last_ms": a.last_ms, "window_start_ms": a.window_start_ms},
        "cost": {
            "fee_pct_side": a.cost.fee_pct_side,
            "spread_pct": a.cost.spread_pct,
            "breakeven_pct": a.cost.breakeven_pct,
            "spread_source": a.cost.spread_source,
        },
        "hours": [
            {
                "hour": int(h),
                "range_median": float(r["range_median"]),
                "pct_above_cost": float(r["pct_above_cost"]),
                "quote_vol_mean": float(r["quote_vol_mean"]),
                "atr_pct_mean": float(r["atr_pct_mean"]),
                "class": r["class"],
                "validated": bool(r["validated"]),
            }
            for h, r in a.hours.iterrows()
        ],
        "heatmap": {"weekdays": WEEKDAYS_PT, "range_median": grid("range_median"), "pct_above_cost": grid("pct_above_cost")},
        "validation": {
            "folds": len(v.spearman),
            "spearman": v.spearman,
            "profile_repeats": v.profile_repeats,
            "patterns": [
                {"kind": p.kind, "key": p.key, "claim": p.claim, "claims": p.claims, "held": p.held, "hold_rate": p.hold_rate, "kept": p.kept}
                for p in v.patterns
            ],
        },
    }


def save_json(analyses: list[SymbolAnalysis], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({a.symbol: to_dict(a) for a in analyses}, ensure_ascii=False, indent=1), encoding="utf-8")


def load_profiles(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# ----------------------------------------------------------------------- HTML

_CSS = """
:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#636c76; --line:#d8dee4; --ok:#1a7f37; --bad:#b42318; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa4ae; --line:#2e333a; --ok:#4ac26b; --bad:#f47067; } }
body { background:var(--bg); color:var(--fg); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; margin:0; padding:24px 16px; }
main { max-width:1100px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; } h3 { font-size:15px; margin:20px 0 6px; }
.muted { color:var(--muted); } .ok { color:var(--ok); } .bad { color:var(--bad); }
.scroll { overflow-x:auto; }
table.heat { border-collapse:collapse; font-variant-numeric:tabular-nums; }
table.heat th, table.heat td { border:1px solid var(--line); padding:3px 5px; text-align:center; font-size:12px; min-width:34px; }
table.heat td { color:#111; }
table.list { border-collapse:collapse; font-variant-numeric:tabular-nums; }
table.list th, table.list td { border-bottom:1px solid var(--line); padding:4px 10px; text-align:left; font-size:13px; }
.note { border-left:3px solid var(--line); padding:6px 12px; margin:12px 0; }
"""


def _cell(value: float, lo: float, hi: float) -> str:
    if pd.isna(value):
        return '<td class="muted">–</td>'
    t = 0.0 if hi <= lo else min(1.0, max(0.0, (value - lo) / (hi - lo)))
    # escala sequencial de um só tom (claro = pouco, escuro = muito)
    light = 94 - 52 * t
    return f'<td style="background:hsl(210 70% {light:.0f}%);color:{"#fff" if light < 55 else "#111"}">{value:.0f}</td>'


def _heat_table(grid: list[list[float]], title: str, fmt_scale: float = 1.0) -> str:
    flat = [x for row in grid for x in row if not pd.isna(x)]
    lo, hi = (min(flat), max(flat)) if flat else (0, 1)
    head = "<tr><th>hora</th>" + "".join(f"<th>{d}</th>" for d in WEEKDAYS_PT) + "</tr>"
    rows = []
    for h in range(24):
        cells = "".join(_cell(grid[d][h] * fmt_scale, lo * fmt_scale, hi * fmt_scale) for d in range(7))
        rows.append(f"<tr><th>{h:02d}h</th>{cells}</tr>")
    return f"<h3>{html.escape(title)}</h3><div class='scroll'><table class='heat'>{head}{''.join(rows)}</table></div>"


def render_html(analyses: list[SymbolAnalysis], path: Path, tz: str) -> None:
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Análise por horário</title><style>{_CSS}</style></head><body><main>",
        "<h1>Análise por horário (Brasília)</h1>",
        f"<p class='muted'>Gerado em {format_local(now_ms(), tz, '%d/%m/%Y %H:%M')}. Filtro e contexto: nunca é motivo sozinho para comprar ou vender. "
        "Não é recomendação financeira e não há garantia de resultado.</p>",
    ]
    for a in analyses:
        d = to_dict(a)
        v = a.validation
        c = a.cost
        parts.append(f"<h2>{a.symbol} — candles de {a.timeframe}</h2>")
        parts.append(
            f"<p>Custo de ida e volta (ponto de equilíbrio): <b>{c.breakeven_pct:.3f}%</b> "
            f"(taxa {c.fee_pct_side:.3f}% por lado + spread {c.spread_pct:.4f}% — {html.escape(c.spread_source)}). "
            f"“Compensa” = amplitude do candle acima de {a.cost.breakeven_pct * a.folga:.3f}% (custo × {a.folga:g}). "
            "A amplitude é o maior movimento possível no candle; ninguém captura ele inteiro.</p>"
        )
        parts.append(
            f"<p class='muted'>Perfil: {format_local(a.window_start_ms, tz, '%d/%m/%Y')} a {format_local(a.last_ms, tz, '%d/%m/%Y')} "
            f"| {a.n_candles:,} candles no histórico total.</p>"
        )
        parts.append(_heat_table(d["heatmap"]["pct_above_cost"], "% dos candles que passam do custo com folga (mais escuro = mais espaço para as taxas)"))
        parts.append(_heat_table(d["heatmap"]["range_median"], "Amplitude mediana do candle, em centésimos de % (ex.: 35 = 0,35%)", fmt_scale=100))
        sp = ", ".join(f"{x:.2f}" for x in v.spearman)
        verdict = "<span class='ok'>se repete</span>" if v.profile_repeats else "<span class='bad'>NÃO se repete o suficiente</span>"
        parts.append(
            f"<div class='note'>Formato do dia fora da amostra: {verdict} "
            f"(correlação de Spearman treino x teste em {len(v.spearman)} meses: {sp}).</div>"
        )
        for kind, label in (("hora", "Horas"), ("dia_semana", "Dias da semana"), ("hora_dia", "Hora × dia da semana")):
            kept, disc = v.kept(kind), v.discarded(kind)
            parts.append(f"<h3>{label}: {len(kept)} padrões mantidos, {len(disc)} descartados</h3>")
            rows = "".join(
                f"<tr><td>{html.escape(p.key)}</td><td>{p.claim.replace('_', ' ')}</td><td>{p.held}/{p.claims}</td>"
                f"<td>{p.hold_rate:.0f}%</td><td class='{'ok' if p.kept else 'bad'}'>{'mantido' if p.kept else 'descartado'}</td></tr>"
                for p in sorted(v.patterns, key=lambda p: (not p.kept, -p.hold_rate))
                if p.kind == kind
            )
            if kind == "hora_dia" and len(disc) + len(kept) > 40:
                rows = "".join(
                    f"<tr><td>{html.escape(p.key)}</td><td>{p.claim.replace('_', ' ')}</td><td>{p.held}/{p.claims}</td><td>{p.hold_rate:.0f}%</td><td class='ok'>mantido</td></tr>"
                    for p in kept
                ) + f"<tr><td colspan='5' class='muted'>+ {len(disc)} descartados (lista completa no JSON)</td></tr>"
            parts.append(f"<div class='scroll'><table class='list'><tr><th>Grupo</th><th>Afirmação</th><th>Confirmou</th><th>Taxa</th><th>Resultado</th></tr>{rows}</table></div>")
    parts.append("</main></body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts), encoding="utf-8")
