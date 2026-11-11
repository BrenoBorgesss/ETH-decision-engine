"""Relatório do backtest: JSON (para o agente/dashboard) e HTML (para você ler). Mostra TUDO, inclusive o que deu errado."""

from __future__ import annotations

import html
import json
import math
from dataclasses import asdict
from pathlib import Path

from cryptoagent import DISCLAIMER
from cryptoagent.backtest.metrics import equity_curve
from cryptoagent.backtest.runner import PairReport, Variant
from cryptoagent.fmt import num, pct
from cryptoagent.timeutils import format_local, now_ms

COLS = [
    ("operacoes", "Operações", lambda v: f"{v:d}"),
    ("operacoes_por_dia", "Por dia", lambda v: num(v, 2)),
    ("retorno_total_pct", "Retorno", lambda v: pct(v, 2, sign=True)),
    ("drawdown_max_pct", "Drawdown máx.", lambda v: pct(v, 2)),
    ("taxa_acerto_pct", "Acerto", lambda v: pct(v, 1)),
    ("relacao_ganho_perda", "Ganho/perda médio", lambda v: num(v, 2)),
    ("fator_lucro", "Fator de lucro", lambda v: num(v, 2)),
    ("expectativa_pct", "Média por operação", lambda v: pct(v, 3, sign=True)),
    ("sharpe", "Sharpe", lambda v: num(v, 2)),
]


def _fmt(key: str, value) -> str:
    for k, _, f in COLS:
        if k == key:
            if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
                return "∞" if isinstance(value, float) and math.isinf(value) else "-"
            return f(value)
    return str(value)


def _clean(obj):
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def to_dict(r: PairReport) -> dict:
    v = lambda x: {"nome": x.name, "metricas": x.metrics, "nota": x.note} if x else None
    return _clean(
        {
            "par": r.pair,
            "periodo_fora_da_amostra": [r.oos_start, r.oos_end],
            "custos": {
                "taxa_por_lado_pct": r.costs.fee * 100,
                "origem_taxa": r.costs.fee_source,
                "spread_por_hora_pct": r.costs.spread_by_hour,
                "horas_com_spread_medido": r.costs.measured_hours,
                "slippage_mercado_pct": r.costs.slip_market_pct,
                "slippage_stop_pct": r.costs.slip_stop_pct,
                "custo_ida_volta_medio_pct": r.costs.mean_roundtrip_pct,
            },
            "padrao": v(r.default),
            "walkforward": v(r.walkforward),
            "escolhas_walkforward": r.wf_choices,
            "travas_distancia": [v(x) for x in r.distances],
            "ablacao": [v(x) for x in r.ablation],
            "nao_testaveis": r.untestable,
            "ingenua": v(r.naive),
            "comprar_e_segurar": r.buy_hold,
            "alvo_x_custo": r.target_coverage,
            "motivos_esperar": r.block_counts,
            "candidatos": r.candidates,
            "operacoes_padrao": [asdict(t) for t in r.default_result.trades],
        }
    )


def save_json(reports: list[PairReport], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({r.pair: to_dict(r) for r in reports}, ensure_ascii=False, indent=1), encoding="utf-8")


_CSS = """
:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#636c76; --line:#d8dee4; --ok:#1a7f37; --bad:#b42318; --accent:#2f6fde; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa4ae; --line:#2e333a; --ok:#4ac26b; --bad:#f47067; --accent:#6ea8fe; } }
body { background:var(--bg); color:var(--fg); font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; margin:0; padding:24px 16px; }
main { max-width:1100px; margin:0 auto; } h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:36px 0 8px; } h3 { font-size:15px; margin:22px 0 6px; }
.muted { color:var(--muted); } .neg { color:var(--bad); } .pos { color:var(--ok); }
.scroll { overflow-x:auto; } table { border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { border-bottom:1px solid var(--line); padding:5px 10px; text-align:right; font-size:13px; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
.box { border-left:3px solid var(--accent); padding:8px 14px; margin:14px 0; background:color-mix(in srgb, var(--accent) 6%, transparent); }
svg { max-width:100%; height:auto; }
"""


def _row(v: Variant | None, highlight: bool = False) -> str:
    if v is None:
        return ""
    m = v.metrics
    cells = "".join(
        f"<td class='{'neg' if k in ('retorno_total_pct', 'expectativa_pct') and (m.get(k) or 0) < 0 else 'pos' if k in ('retorno_total_pct', 'expectativa_pct') and (m.get(k) or 0) > 0 else ''}'>{_fmt(k, m.get(k))}</td>"
        for k, _, _ in COLS
    )
    name = f"<b>{html.escape(v.name)}</b>" if highlight else html.escape(v.name)
    return f"<tr><td>{name}{'<br><span class=muted>' + html.escape(v.note) + '</span>' if v.note else ''}</td>{cells}</tr>"


def _table(rows: list[Variant | None], highlight_first: bool = False) -> str:
    head = "<tr><th>Variante</th>" + "".join(f"<th>{t}</th>" for _, t, _ in COLS) + "</tr>"
    body = "".join(_row(v, highlight_first and i == 0) for i, v in enumerate(rows))
    return f"<div class='scroll'><table>{head}{body}</table></div>"


def _equity_svg(r: PairReport, capital: float) -> str:
    trades = [t for t in r.default_result.trades if r.oos_start <= t.entry_ms < r.oos_end]
    eq = equity_curve(trades, capital)
    if eq.empty:
        return "<p class='muted'>Sem operações no período fora da amostra.</p>"
    xs = [r.oos_start] + eq["time_ms"].tolist()
    ys = [capital] + eq["equity"].tolist()
    w, h, pad = 900, 220, 30
    x0, x1 = r.oos_start, r.oos_end
    y0, y1 = min(ys + [capital]) * 0.995, max(ys + [capital]) * 1.005
    sx = lambda x: pad + (x - x0) / (x1 - x0) * (w - 2 * pad)
    sy = lambda y: h - pad - (y - y0) / (y1 - y0) * (h - 2 * pad)
    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    base = sy(capital)
    return (
        f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='Curva de patrimônio'>"
        f"<line x1='{pad}' x2='{w - pad}' y1='{base:.1f}' y2='{base:.1f}' stroke='currentColor' stroke-opacity='.25' stroke-dasharray='4 4'/>"
        f"<polyline fill='none' stroke='var(--accent)' stroke-width='2' points='{pts}'/>"
        f"<text x='{pad}' y='{h - 8}' font-size='11' fill='currentColor' opacity='.6'>{format_local(x0, 'America/Sao_Paulo', '%d/%m/%Y')}</text>"
        f"<text x='{w - pad}' y='{h - 8}' font-size='11' fill='currentColor' opacity='.6' text-anchor='end'>{format_local(x1, 'America/Sao_Paulo', '%d/%m/%Y')}</text>"
        f"<text x='{pad}' y='{base - 4:.1f}' font-size='11' fill='currentColor' opacity='.6'>capital inicial</text></svg>"
    )


def render_html(reports: list[PairReport], path: Path, *, capital: float, tz: str, train_months: int, default_distance: float) -> None:
    out = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Backtest da estratégia</title><style>{_CSS}</style></head><body><main>",
        "<h1>Backtest: reversão à média intraday (spot)</h1>",
        f"<p class='muted'>Gerado em {format_local(now_ms(), tz, '%d/%m/%Y %H:%M')}. Todos os números já descontam taxas, spread e slippage. "
        f"Resultados passados não garantem resultados futuros. {html.escape(DISCLAIMER)}</p>",
    ]
    for r in reports:
        c = r.costs
        out.append(f"<h2>{r.pair}</h2>")
        out.append(
            f"<p>Período fora da amostra: <b>{format_local(r.oos_start, tz, '%d/%m/%Y')} a {format_local(r.oos_end, tz, '%d/%m/%Y')}</b> "
            f"({len(r.folds)} meses de teste, cada um precedido de {train_months} meses de treino). "
            f"Custo médio de ida e volta: <b>{pct(c.mean_roundtrip_pct, 3)}</b> (taxa {pct(c.fee * 100, 3)}/lado {html.escape(c.fee_source)}; "
            f"spread medido ao vivo em {len(c.measured_hours)} de 24 horas, o resto pelo padrão do config; slippage {pct(c.slip_market_pct, 2)} a mercado, {pct(c.slip_stop_pct, 2)} no stop).</p>"
        )
        out.append("<h3>Resultado principal (fora da amostra)</h3>")
        out.append(_table([r.default, r.walkforward, r.naive], highlight_first=True))
        bh = r.buy_hold or {}
        out.append(f"<p>Comprar e segurar {r.pair} no mesmo período: retorno <b>{pct(bh.get('retorno_total_pct'), 2, sign=True)}</b>, drawdown {pct(bh.get('drawdown_max_pct'), 2)}.</p>")
        out.append("<h3>Curva de patrimônio (parâmetros padrão, fora da amostra)</h3>" + _equity_svg(r, capital))
        if r.default.metrics.get("saidas"):
            out.append("<p class='muted'>Saídas: " + ", ".join(f"{html.escape(k)} {v}" for k, v in r.default.metrics["saidas"].items()) + "</p>")

        out.append("<h3>Trava de distância mínima até o alvo (operações por dia)</h3>")
        out.append(_table([Variant(f"trava padrão ({num(default_distance, 1)}× o custo)", r.default.metrics), *r.distances]))

        if r.target_coverage:
            tc = r.target_coverage
            out.append(
                f"<div class='box'>Dos {num(tc['sinais_tecnicos'], 0)} momentos com esticamento + gatilho (sem queda forte), o alvo 1 cobria o custo de ida e volta em "
                f"<b>{pct(tc['pct_alvo_cobre_custo'], 0)}</b> deles e cobria {num(default_distance, 1)}× o custo em <b>{pct(tc['pct_alvo_cobre_2_5x'], 0)}</b>. "
                f"Mediana: alvo a {num(tc['mediana_multiplos'], 2)}× o custo.</div>"
            )
        if r.wf_choices:
            out.append("<h3>Walk-forward: o que foi escolhido em cada mês (só com dados do treino)</h3><div class='scroll'><table><tr><th>Mês de teste</th><th>Parâmetros escolhidos</th><th>Ops. treino</th><th>Média treino</th><th>Ops. teste</th><th>Média teste</th><th>Retorno teste</th></tr>")
            for ch in r.wf_choices:
                esc = ch["escolha"] if isinstance(ch["escolha"], str) else ", ".join(f"{k}={num(v, 2)}" for k, v in ch["escolha"].items())
                out.append(
                    f"<tr><td>{format_local(ch['teste_inicio'], tz, '%m/%Y')}</td><td>{html.escape(esc)}</td><td>{ch.get('treino_ops', 0)}</td>"
                    f"<td>{pct(ch.get('treino_expectativa_pct'), 3, sign=True)}</td><td>{ch.get('teste_ops', '-')}</td>"
                    f"<td>{pct(ch.get('teste_expectativa_pct'), 3, sign=True)}</td><td>{pct(ch.get('teste_retorno_pct'), 2, sign=True)}</td></tr>"
                )
            out.append("</table></div>")
        if r.ablation:
            out.append("<h3>Ablação: o que cada sinal extra acrescenta (fora da amostra)</h3>" + _table(r.ablation, highlight_first=True))
            for k, why in r.untestable.items():
                out.append(f"<p class='muted'>{html.escape(k)}: {html.escape(why)}.</p>")
        if r.block_counts:
            out.append("<h3>Por que a estratégia esperou (motivos nas barras candidatas; uma barra pode ter vários)</h3><div class='scroll'><table><tr><th>Motivo</th><th>Vezes</th></tr>")
            out += [f"<tr><td>{html.escape(k)}</td><td>{v:,}</td></tr>" for k, v in r.block_counts.items()]
            out.append("</table></div>")
    out.append("</main></body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(out), encoding="utf-8")
