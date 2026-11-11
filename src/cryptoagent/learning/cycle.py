"""Um ciclo de reavaliação: campeão x desafiantes, proteções e panoramas, tudo "ponto no tempo".

Passos (com `until_ms`, só o que se sabia até aquele instante entra em qualquer conta):
1. Monta o conjunto de dados dos momentos candidatos (mesmo simulador do backtest) e acrescenta as
   operações da SIMULAÇÃO que aconteceram depois do fim do histórico (dados novos que só a carteira
   virtual viu).
2. Avalia o campeão e cada desafiante no MESMO período fora da amostra, com custos:
   - desafiantes "regras": as hipóteses declaradas em `aprendizado.hipoteses`;
   - desafiante "modelo": regressão logística retreinada mês a mês (walk-forward);
   - as versões que já foram campeãs voltam a disputar.
3. Mede mudança de regime (drift) no mercado, nos momentos candidatos e na simulação x backtest.
4. Aplica os critérios de promoção; se mais de um desafiante passar, vence o de maior média por operação.
5. Grava o registro de versões, o panorama por cenário (com calibração) e o relatório HTML.

Nada aqui envia ordem nem lê a conta real.
"""

from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData, load_backtest_data
from cryptoagent.backtest.metrics import compute_metrics
from cryptoagent.backtest.provenance import history_files, parameters, params_hash, sha256_file
from cryptoagent.backtest.simulator import simulate
from cryptoagent.config import Config, apply_overlay
from cryptoagent.fmt import pct
from cryptoagent.learning import panorama as pano
from cryptoagent.learning import registry as regmod
from cryptoagent.learning.criteria import Verdict, judge
from cryptoagent.learning.drift import DriftReport, candidate_drift, market_drift, simulation_drift
from cryptoagent.model.dataset import build_dataset
from cryptoagent.model.features import FEATURES
from cryptoagent.model.walkforward import evaluate, evaluation_folds, final_model
from cryptoagent.timeutils import format_local, now_ms

DAY_MS = 86_400_000
MODEL_SOURCE = "modelo_logistico"
# Variáveis comparadas no drift dos momentos candidatos. Ficam de fora: indicadores de categoria (regime, hora) e
# as de NÍVEL da tendência longa do 4h (contexto_*). Estas mudam devagar e ficam semanas no mesmo patamar: 30 dias
# delas quase sempre diferem de 6 meses, então o PSI acusaria mudança em todo ciclo, sem dizer nada sobre o padrão.
DRIFT_FEATURES = [f for f in FEATURES if not f.startswith(("regime_", "hora_", "contexto_"))]


@dataclass
class Entry:
    """Um candidato avaliado neste ciclo."""

    version: regmod.Version
    metrics: dict
    verdict: Verdict | None = None  # None para o campeão (é a referência)
    note: str = ""


@dataclass
class CycleResult:
    """Resultado de um ciclo de reavaliação."""

    number: int
    trigger: str
    until_ms: int
    oos_start: int
    oos_end: int
    champion_before: regmod.Version
    champion_after: regmod.Version
    entries: list[Entry] = field(default_factory=list)
    drift: DriftReport = field(default_factory=DriftReport)
    sim_examples: int = 0
    dataset_size: int = 0
    report_path: Path | None = None

    @property
    def promoted(self) -> bool:
        """True se o campeão mudou neste ciclo."""
        return self.champion_after.id != self.champion_before.id


def data_hash(cfg: Config, symbols: list[str]) -> str:
    """Hash combinado dos arquivos de histórico usados."""
    import hashlib

    h = hashlib.sha256()
    for p in history_files(cfg, symbols):
        h.update(p.name.encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()


def simulation_rows(sim_db: Path | None, after_ms: int) -> tuple[pd.DataFrame, list[float]]:
    """Da simulação: (linhas de treino com decisão depois de `after_ms`, resultados % de todas as operações virtuais fechadas)."""
    if sim_db is None or not sim_db.is_file():
        return pd.DataFrame(), []
    conn = sqlite3.connect(f"file:{sim_db.as_posix()}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_trades)")}
        if "features_json" not in cols:
            return pd.DataFrame(), []
        rows = conn.execute(
            "SELECT decision_ms, exit_ms, pnl_pct, features_json, regime, scenario_json, confidence FROM paper_trades WHERE exit_ms IS NOT NULL AND variant = 'padrao'"
        ).fetchall()
    finally:
        conn.close()
    pnls = [float(r[2]) for r in rows]
    out = []
    for decision_ms, exit_ms, pnl, feats, regime, scen, conf in rows:
        if decision_ms <= after_ms or not feats:
            continue
        f = json.loads(feats)
        s = json.loads(scen) if scen else {}
        out.append(
            {
                "t_ms": decision_ms,
                **{k: f.get(k) for k in FEATURES},
                "pnl_pct": pnl,
                "motivo": "simulacao",
                "fim_ms": exit_ms,
                "rotulo": int(pnl > 0),
                "acao_regras": "COMPRAR",
                "confianca_regras": conf,
                "regime": regime,
                "hora_local": s.get("hora_local", 0),
            }
        )
    return pd.DataFrame(out), pnls


def _rules_metrics(c: Config, data: BacktestData, costs: CostSchedule, window: tuple[int, int], until_ms: int) -> dict:
    bt = c.backtest
    r = simulate(
        data,
        c.estrategia,
        c.indicadores,
        costs,
        capital=bt.capital,
        loss_pct_day=bt.perda_max_dia_pct,
        divisor=c.risco.divisor_perda_diaria,
        spread_max_pct=c.custos.spread_max_pct,
        label="avaliação",
    )
    a, b = window
    trades = [t for t in r.trades if a <= t.entry_ms < b and t.exit_ms < until_ms]
    return compute_metrics(trades, a, b, bt.capital, c.fuso.local)


def run_cycle(  # noqa: C901 - roteiro em seções comentadas (avaliação, proteções, critérios) que usam o mesmo corte no tempo
    cfg: Config,
    costs: CostSchedule,
    *,
    until_ms: int | None = None,
    trigger: str = "manual",
    sim_db: Path | None = None,
    clock_ms: int | None = None,
    progress: Callable[[str], None] = print,
) -> CycleResult:
    """Roda um ciclo completo (ver a descrição do módulo) e grava registro, panorama e relatório."""
    a = cfg.aprendizado
    folder = cfg.path(a.pasta)
    now = clock_ms if clock_ms is not None else now_ms()
    until = until_ms if until_ms is not None else now
    pair = cfg.paper.par
    reg = regmod.load(folder)
    if not reg.versions:
        reg.add("regras", "regras (config atual)", None, now, state="campeao")
    for h in a.hipoteses:
        if reg.find("regras", h) is None:
            reg.add("regras", f"regras + hipótese {Path(h).stem}", h, now)
    if a.incluir_modelo and reg.find("modelo", MODEL_SOURCE) is None:
        reg.add("modelo", "regras + filtro do modelo (regressão logística)", MODEL_SOURCE, now)
    champion = reg.champion
    number = len(reg.cycles) + 1
    symbols = sorted({cfg.mercado.simbolo_analise, pair})
    dhash = data_hash(cfg, symbols)

    progress(f"Ciclo {number}: carregando o histórico e montando os momentos candidatos...")
    data = load_backtest_data(cfg, pair, season_cost_pct=costs.mean_roundtrip_pct)
    until = min(until, data.last_ms)  # depois do fim do histórico não há o que avaliar
    folds = evaluation_folds(cfg, data, until)
    if not folds:
        raise ValueError("histórico curto demais antes desta data para um período fora da amostra")
    window = (folds[0].test_start_ms, folds[-1].test_end_ms)
    dataset = build_dataset(cfg, data, costs)
    dataset = dataset[dataset["t_ms"] < until]
    sim_rows, sim_pnls = simulation_rows(sim_db, int(dataset["t_ms"].max()) if len(dataset) else 0)
    if len(sim_rows):
        sim_rows = sim_rows[sim_rows["fim_ms"] < until]
        dataset = pd.concat([dataset, sim_rows], ignore_index=True)

    # ---- avaliação de todos no mesmo período
    entries: list[Entry] = []
    model_report = None
    for v in reg.versions:
        progress(f"Avaliando v{v.id}: {v.name}...")
        if v.kind == "regras":
            c = apply_overlay(cfg, cfg.path(v.source)) if v.source else cfg
            d = data if v.source is None else load_backtest_data(c, pair, season_cost_pct=costs.mean_roundtrip_pct)
            m = _rules_metrics(c, d, costs, window, until)
            note = ""
        else:
            model_report = evaluate(cfg, data, costs, dataset, until_ms=until)
            m = dict(model_report.challenger)
            m["auc_fora_da_amostra"] = model_report.auc_oos
            files = sorted((folder / "modelos").glob("modelo-ciclo*.json"))
            note = (
                f"AUC fora da amostra {model_report.auc_oos:.3f}"
                if model_report.auc_oos is not None
                else "sem previsões fora da amostra"
            )
            if len(dataset) and dataset["rotulo"].nunique() == 2:
                fm = final_model(
                    cfg, dataset[dataset["fim_ms"] < until], {"par": pair, "ciclo": number, "ate_ms": until}
                )
                path = folder / "modelos" / f"modelo-ciclo{number:03d}.json"
                fm.save(path)
                m["arquivo_modelo"] = path.name
            elif files:
                m["arquivo_modelo"] = files[-1].name
        entries.append(Entry(v, m, note=note))

    champ_entry = next(e for e in entries if e.version.id == champion.id)

    # ---- proteções: mudança de regime
    p = cfg.estrategia
    ref_days = int(cfg.backtest.meses_treino * 30.44)
    drift = DriftReport(market_drift(data.frames[p.tf_setup], until, a.dias_recentes_drift, ref_days, a.psi_alerta))
    drift.items.append(candidate_drift(dataset, DRIFT_FEATURES, until, a.dias_recentes_drift, ref_days, a.psi_alerta))
    drift.items.append(simulation_drift(sim_pnls, champ_entry.metrics.get("expectativa_ic95_pct")))

    # ---- critérios
    oos_days = (window[1] - window[0]) / DAY_MS
    winners = []
    for e in entries:
        if e is champ_entry:
            continue
        e.verdict = judge(e.metrics, champ_entry.metrics, oos_days, a, drift=drift.drift)
        if e.verdict.promote:
            winners.append(e)
    best = max(winners, key=lambda e: e.metrics.get("expectativa_pct") or -1e9) if winners else None
    params = params_hash(parameters(cfg))
    for e in entries:
        if e is champ_entry:
            decision, reasons = ("campeão substituído" if best else "campeão (referência)"), []
        elif e is best:
            decision, reasons = "promovido", e.verdict.passed
        elif e.verdict and e.verdict.promote:
            decision, reasons = "aprovado, mas outro desafiante foi melhor", e.verdict.passed
        else:
            decision, reasons = "rejeitado", e.verdict.failed if e.verdict else []
        e.version.evaluations.append(
            regmod.Evaluation(
                number, now, until, window[0], window[1], e.metrics, params, dhash, decision, list(reasons)
            )
        )
        if e.version.state != "campeao" and e is not best:
            e.version.state = "rejeitado" if decision == "rejeitado" else "desafiante"
    if best is not None:
        reg.promote(best.version, now)
    result = CycleResult(
        number,
        trigger,
        until,
        window[0],
        window[1],
        champion,
        reg.champion,
        entries,
        drift,
        len(sim_rows),
        len(dataset),
    )
    reg.cycles.append(
        {
            "ciclo": number,
            "gatilho": trigger,
            "em_ms": now,
            "ate_ms": until,
            "fora_da_amostra": list(window),
            "campeao_antes": champion.id,
            "campeao_depois": reg.champion.id,
            "drift": drift.drift,
            "drift_resumo": drift.summary,
            "exemplos_da_simulacao": len(sim_rows),
            "momentos": len(dataset),
        }
    )
    regmod.save(folder, reg)

    # ---- panorama e relatório
    pan = pano.build(dataset, until_ms=until, model_calibration=model_report.calibration if model_report else None)
    pan["drift"] = {"alerta": drift.drift, "resumo": drift.summary, "itens": [i.__dict__ for i in drift.items]}
    pano.save(folder, pan)
    result.report_path = folder / "aprendizado.html"
    pano.render_html(
        pan, cycle_html(result, cfg), result.report_path, generated=format_local(now, cfg.fuso.local, "%d/%m/%Y %H:%M")
    )
    return result


def cycle_html(r: CycleResult, cfg: Config) -> str:
    """Trecho HTML do ciclo: campeão, cada desafiante com o veredito e os critérios, e as proteções."""
    tz = cfg.fuso.local
    head = (
        f"<h2>Ciclo {r.number} ({html.escape(r.trigger)}): dados até {format_local(r.until_ms, tz, '%d/%m/%Y %H:%M')}</h2>"
        f"<p>Fora da amostra: {format_local(r.oos_start, tz, '%d/%m/%Y')} a {format_local(r.oos_end, tz, '%d/%m/%Y')}. "
        f"{'<b>Campeão substituído</b>: ' if r.promoted else 'Campeão mantido: '}v{r.champion_after.id} {html.escape(r.champion_after.name)}.</p>"
    )
    rows = []
    for e in r.entries:
        m = e.metrics
        verdict = e.version.last.decision if e.version.last else ""
        why = "; ".join(e.version.last.reasons) if e.version.last else ""
        rows.append(
            f"<tr><td>v{e.version.id} {html.escape(e.version.name)}</td><td>{m.get('operacoes', 0)}</td><td>{pct(m.get('retorno_total_pct'), 2, sign=True)}</td>"
            f"<td>{pct(m.get('drawdown_max_pct'), 2)}</td><td>{pct(m.get('expectativa_pct'), 3, sign=True)}</td>"
            f"<td>{html.escape(verdict)}</td><td style='text-align:left;white-space:normal'>{html.escape(why)}{' | ' + html.escape(e.note) if e.note else ''}</td></tr>"
        )
    drift = "".join(
        f"<li>{'⚠ ' if i.alert else ''}{html.escape(i.name)}: {html.escape(i.detail)}</li>" for i in r.drift.items
    )
    return (
        head
        + "<div class='scroll'><table><tr><th>Versão</th><th>Operações</th><th>Retorno</th><th>Drawdown</th><th>Média por operação</th>"
        "<th>Decisão</th><th>Critérios</th></tr>" + "".join(rows) + "</table></div>"
        f"<h2>Proteções: mudança de regime</h2><ul>{drift}</ul>"
        f"<p class='muted'>{'Mudança de regime detectada: nenhuma promoção neste ciclo e a confiança ao vivo é reduzida.' if r.drift.drift else 'Sem mudança de regime acima do limite.'}</p>"
    )
