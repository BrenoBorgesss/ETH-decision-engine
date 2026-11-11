"""Panoramas: o que costuma acontecer em momentos parecidos com o de agora, e se a confiança é calibrada.

A base são os momentos candidatos do histórico (conjunto de dados do modelo): cada um com o resultado
de comprar ali, já com custos. Um "cenário" é a combinação de regime (15m), período do dia (horário
local) e faixa de volatilidade (percentil do ATR). Para cada cenário:
- probabilidade de acerto (resultado positivo) com intervalo de 95% (Wilson, bom para amostras pequenas);
- ganho médio quando acerta, perda média quando erra, e a média por operação com intervalo de 95%;
- quantos casos existem: com poucos casos o cenário é "inédito" e a confiança é reduzida.

Calibração: a confiança das regras (0 a 100) e a probabilidade do modelo acompanham a taxa real de acerto?
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from cryptoagent.backtest.metrics import mean_ci95
from cryptoagent.fmt import num, pct

PANORAMA_FILE = "panorama.json"
PERIODS = ((0, 6, "madrugada"), (6, 12, "manhã"), (12, 18, "tarde"), (18, 24, "noite"))
VOL_BANDS = ((0, 33.34, "volatilidade baixa"), (33.34, 66.67, "volatilidade média"), (66.67, 100.01, "volatilidade alta"))
CONF_BINS = (0, 40, 55, 70, 85, 101)


def period_of(hour: float) -> str:
    """Período do dia (madrugada, manhã, tarde, noite) de uma hora local."""
    h = float(hour) % 24
    return next(name for a, b, name in PERIODS if a <= h < b)


def vol_band(atr_pctl: float | None) -> str:
    """Faixa de volatilidade a partir do percentil do ATR (0 a 100)."""
    if atr_pctl is None or not math.isfinite(atr_pctl):
        return "volatilidade desconhecida"
    return next(name for a, b, name in VOL_BANDS if a <= atr_pctl < b)


def scenario_key(regime: str | None, hour: float, atr_pctl: float | None) -> str:
    """Nome do cenário: regime | período do dia | faixa de volatilidade."""
    return f"{regime or 'indefinido'} | {period_of(hour)} | {vol_band(atr_pctl)}"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de 95% de uma proporção (Wilson). (NaN, NaN) sem amostra."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def group_stats(df: pd.DataFrame) -> dict:
    """Estatísticas de um grupo de momentos: casos, acerto (com IC), ganho e perda médios, média por operação (com IC)."""
    n = len(df)
    wins = df[df["pnl_pct"] > 0]["pnl_pct"]
    losses = df[df["pnl_pct"] <= 0]["pnl_pct"]
    lo, hi = wilson(len(wins), n)
    elo, ehi = mean_ci95(df["pnl_pct"].to_numpy()) if n else (math.nan, math.nan)
    return {
        "casos": n,
        "acerto": len(wins) / n if n else math.nan,
        "acerto_ic95": [lo, hi],
        "ganho_medio_pct": float(wins.mean()) if len(wins) else math.nan,
        "perda_media_pct": float(losses.mean()) if len(losses) else math.nan,
        "media_pct": float(df["pnl_pct"].mean()) if n else math.nan,
        "media_ic95_pct": [elo, ehi],
    }


def build(dataset: pd.DataFrame, *, until_ms: int, model_calibration: list[dict] | None = None) -> dict:
    """Panorama completo a partir dos momentos cujo resultado já era conhecido em `until_ms`."""
    df = dataset[dataset["fim_ms"] < until_ms].copy()
    df["cenario"] = [scenario_key(r, h, a) for r, h, a in zip(df["regime"], df["hora_local"], df["setup_atr_pctl"])]
    scenarios = {k: group_stats(g) for k, g in df.groupby("cenario")}
    calib = []
    bins = pd.cut(df["confianca_regras"], CONF_BINS, right=False)
    for interval, g in df.groupby(bins, observed=True):
        lo, hi = wilson(int((g["pnl_pct"] > 0).sum()), len(g))
        calib.append({"faixa": f"{int(interval.left)}–{min(int(interval.right), 100)}", "casos": len(g), "confianca_media": float(g["confianca_regras"].mean()),
                      "acerto": float((g["pnl_pct"] > 0).mean()), "acerto_ic95": [lo, hi]})
    return {
        "ate_ms": until_ms,
        "momentos": len(df),
        "geral": group_stats(df) if len(df) else {},
        "cenarios": dict(sorted(scenarios.items(), key=lambda kv: -kv[1]["casos"])),
        "calibracao_regras": calib,
        "calibracao_modelo": model_calibration or [],
    }


def save(folder: Path, panorama: dict) -> Path:
    """Grava o panorama em JSON."""
    from cryptoagent.learning.registry import clean_json

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PANORAMA_FILE
    path.write_text(json.dumps(clean_json(panorama), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load(folder: Path) -> dict | None:
    """Lê o panorama salvo, ou None."""
    path = folder / PANORAMA_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


@dataclass(frozen=True)
class MomentView:
    """O panorama do momento atual: o cenário, as estatísticas dele e o fator de "cenário inédito"."""
    scenario: str
    stats: dict | None
    factor: float
    reason: str


def moment(panorama: dict | None, regime: str | None, hour: float, atr_pctl: float | None, *, min_cases: int, floor: float) -> MomentView:
    """Estatísticas do cenário de agora e o fator de confiança: 1 com casos suficientes, menor quanto menos casos houver."""
    key = scenario_key(regime, hour, atr_pctl)
    if panorama is None:
        return MomentView(key, None, 1.0, "")
    stats = panorama.get("cenarios", {}).get(key)
    n = int(stats["casos"]) if stats else 0
    if n >= min_cases:
        return MomentView(key, stats, 1.0, "")
    factor = floor + (1 - floor) * n / min_cases
    return MomentView(key, stats, factor, f"cenário pouco visto no histórico ({key}: {n} caso(s), mínimo {min_cases}): × {num(factor, 2)}")


# ------------------------------------------------------------------ relatório HTML

_CSS = """
:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#636c76; --line:#d8dee4; --ok:#1a7f37; --bad:#b42318; --accent:#2f6fde; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e6e8eb; --muted:#9aa4ae; --line:#2e333a; --ok:#4ac26b; --bad:#f47067; --accent:#6ea8fe; } }
body { background:var(--bg); color:var(--fg); font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; margin:0; padding:24px 16px; }
main { max-width:1100px; margin:0 auto; } h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:18px; margin:32px 0 8px; }
.muted { color:var(--muted); } .neg { color:var(--bad); } .pos { color:var(--ok); } .scroll { overflow-x:auto; }
table { border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { border-bottom:1px solid var(--line); padding:5px 10px; text-align:right; font-size:13px; white-space:nowrap; }
th:first-child, td:first-child { text-align:left; } svg { max-width:100%; height:auto; }
"""


def calibration_svg(points: list[tuple[float, float, float, float, int]], title: str) -> str:
    """Diagrama de calibração: x = confiança/probabilidade prevista (0–1), y = acerto real, com barras do IC de 95%.

    `points` = (previsto, acerto, ic_baixo, ic_alto, casos). A diagonal é a calibração perfeita.
    """
    w, h, pad = 420, 320, 44
    def sx(x: float) -> float:
        return pad + x * (w - 2 * pad)
    def sy(y: float) -> float:
        return h - pad - y * (h - 2 * pad)
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(title)}'>"]
    for v in (0, 0.25, 0.5, 0.75, 1):
        out.append(f"<line x1='{sx(0):.0f}' x2='{sx(1):.0f}' y1='{sy(v):.0f}' y2='{sy(v):.0f}' stroke='currentColor' stroke-opacity='.12'/>")
        out.append(f"<text x='{pad - 6}' y='{sy(v) + 4:.0f}' font-size='11' text-anchor='end' fill='currentColor' opacity='.6'>{v:.0%}</text>")
        out.append(f"<text x='{sx(v):.0f}' y='{h - pad + 16}' font-size='11' text-anchor='middle' fill='currentColor' opacity='.6'>{v:.0%}</text>")
    out.append(f"<line x1='{sx(0):.0f}' y1='{sy(0):.0f}' x2='{sx(1):.0f}' y2='{sy(1):.0f}' stroke='currentColor' stroke-opacity='.35' stroke-dasharray='4 4'/>")
    for x, y, lo, hi, n in points:
        if any(math.isnan(v) for v in (x, y)):
            continue
        if not (math.isnan(lo) or math.isnan(hi)):
            out.append(f"<line x1='{sx(x):.1f}' x2='{sx(x):.1f}' y1='{sy(lo):.1f}' y2='{sy(hi):.1f}' stroke='var(--accent)' stroke-width='2' stroke-opacity='.5'/>")
        out.append(f"<circle cx='{sx(x):.1f}' cy='{sy(y):.1f}' r='{3 + min(6, math.sqrt(n) / 3):.1f}' fill='var(--accent)'><title>{n} casos</title></circle>")
    out.append(f"<text x='{w / 2:.0f}' y='{h - 6}' font-size='12' text-anchor='middle' fill='currentColor'>previsto</text>")
    out.append(f"<text x='14' y='{h / 2:.0f}' font-size='12' text-anchor='middle' fill='currentColor' transform='rotate(-90 14 {h / 2:.0f})'>acerto real</text>")
    out.append("</svg>")
    return "".join(out)


def render_html(panorama: dict, cycle_html: str, path: Path, *, generated: str) -> None:
    """Página com o ciclo de reavaliação, os panoramas por cenário e os gráficos de calibração."""
    g = panorama.get("geral") or {}
    rows = []
    for key, s in panorama.get("cenarios", {}).items():
        cls = "neg" if (s["media_pct"] or 0) < 0 else "pos"
        lo, hi = s["acerto_ic95"]
        rows.append(
            f"<tr><td>{html.escape(key)}</td><td>{s['casos']}</td><td>{pct(s['acerto'] * 100, 0)} ({pct(lo * 100, 0)}–{pct(hi * 100, 0)})</td>"
            f"<td>{pct(s['ganho_medio_pct'], 2, sign=True)}</td><td>{pct(s['perda_media_pct'], 2, sign=True)}</td>"
            f"<td class='{cls}'>{pct(s['media_pct'], 3, sign=True)}</td><td>{pct(s['media_ic95_pct'][0], 3, sign=True)} a {pct(s['media_ic95_pct'][1], 3, sign=True)}</td></tr>"
        )
    rules_pts = [(c["confianca_media"] / 100, c["acerto"], *c["acerto_ic95"], c["casos"]) for c in panorama.get("calibracao_regras", [])]
    model_pts = []
    for c in panorama.get("calibracao_modelo", []):
        lo, hi = wilson(round(c["acerto_real"] * c["momentos"]), c["momentos"])
        model_pts.append((c["prob_media"], c["acerto_real"], lo, hi, c["momentos"]))
    page = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Aprendizado contínuo</title><style>{_CSS}</style></head><body><main>",
        "<h1>Aprendizado contínuo: campeão, desafiantes e panoramas</h1>",
        f"<p class='muted'>Gerado em {html.escape(generated)}. Tudo fora da amostra e já com custos. Resultados passados não garantem resultados futuros.</p>",
        cycle_html,
        "<h2>Panorama por cenário (momentos candidatos do histórico)</h2>",
        f"<p>Geral: {g.get('casos', 0)} momentos, acerto {pct((g.get('acerto') or 0) * 100, 1)}, média por operação {pct(g.get('media_pct'), 3, sign=True)}.</p>",
        "<div class='scroll'><table><tr><th>Cenário</th><th>Casos</th><th>Acerto (IC 95%)</th><th>Ganho médio</th><th>Perda média</th><th>Média por operação</th><th>IC 95% da média</th></tr>",
        *rows, "</table></div>",
        "<h2>Calibração</h2><p class='muted'>Na diagonal, a confiança acerta na mesma proporção que promete. Barras: intervalo de 95%.</p>",
        "<div style='display:flex;flex-wrap:wrap;gap:24px'>",
        f"<figure style='margin:0'>{calibration_svg(rules_pts, 'Confiança das regras')}<figcaption>Confiança das regras (0–100%)</figcaption></figure>",
        f"<figure style='margin:0'>{calibration_svg(model_pts, 'Probabilidade do modelo') if model_pts else '<p class=muted>Sem previsões do modelo fora da amostra.</p>'}"
        "<figcaption>Probabilidade do modelo (fora da amostra)</figcaption></figure></div>",
        "</main></body></html>",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(page), encoding="utf-8")


def as_lines(view: MomentView) -> list[str]:
    """Texto curto do panorama do momento, para o terminal."""
    s = view.stats
    if not s:
        return [f"Cenário de agora ({view.scenario}): sem casos no histórico."]
    lo, hi = s["acerto_ic95"]
    return [
        f"Em momentos parecidos ({view.scenario}; {s['casos']} casos no histórico): acerto {pct(s['acerto'] * 100, 0)} "
        f"(IC 95%: {pct(lo * 100, 0)} a {pct(hi * 100, 0)}), ganho médio {pct(s['ganho_medio_pct'], 2, sign=True)}, "
        f"perda média {pct(s['perda_media_pct'], 2, sign=True)}, média por operação {pct(s['media_pct'], 3, sign=True)} "
        f"(IC 95%: {pct(s['media_ic95_pct'][0], 3, sign=True)} a {pct(s['media_ic95_pct'][1], 3, sign=True)})."
    ]

