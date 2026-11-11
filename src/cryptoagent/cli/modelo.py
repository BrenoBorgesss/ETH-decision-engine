"""Comando `modelo treinar`: monta o conjunto de dados, avalia o desafiador em walk-forward e salva o modelo."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.cli.backtest import resolve_costs
from cryptoagent.config import Config, Secrets
from cryptoagent.fmt import num, pct
from cryptoagent.timeutils import format_local, now_ms


def _clean(obj: object) -> object:
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _report_dict(rep: object, model_file: str) -> dict:
    """Relatório do desafiador em tipos de JSON."""
    return _clean({
        "par": rep.pair,
        "limiar_probabilidade": rep.threshold,
        "momentos_no_conjunto": rep.n_dataset,
        "periodo_fora_da_amostra": [rep.oos_start, rep.oos_end],
        "meses": [
            {"teste_inicio": o.fold.test_start_ms, "exemplos_treino": o.n_train, "momentos_teste": o.n_test,
             "acerto_no_treino": o.base_rate_train, "auc": o.auc, "nota": o.note}
            for o in rep.folds
        ],
        "auc_fora_da_amostra": rep.auc_oos,
        "brier_fora_da_amostra": rep.brier_oos,
        "brier_referencia": rep.brier_baseline_oos,
        "acerto_fora_da_amostra": rep.base_rate_oos,
        "calibracao": rep.calibration,
        "momentos_fora_da_amostra": rep.signals_oos,
        "regras": rep.rules,
        "desafiador": rep.challenger,
        "comprar_e_segurar": rep.buy_hold,
        "coeficientes_ultimo_mes": [[k, v] for k, v in rep.coefficients],
        "modelo_salvo": model_file,
    })


async def cmd_model_train(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `modelo treinar`: avalia o desafiador contra as regras fora da amostra e salva o modelo. Devolve o código de saída."""
    from cryptoagent.backtest import provenance
    from cryptoagent.backtest.data import load_backtest_data
    from cryptoagent.backtest.report import _fmt
    from cryptoagent.model.dataset import build_dataset
    from cryptoagent.model.walkforward import evaluate, final_model

    pair = (args.par or cfg.backtest.pares[-1]).upper()
    frozen_path = Path(args.custos_arquivo) if args.custos_arquivo else None
    costs = (await resolve_costs(cfg, secrets, [pair], frozen_path))[0][pair]
    tz = cfg.fuso.local
    console.print(f"[bold]{pair}[/]: carregando histórico e montando o conjunto de dados (mesmo simulador do backtest)...")
    data = load_backtest_data(cfg, pair, season_cost_pct=costs.mean_roundtrip_pct)
    dataset = build_dataset(cfg, data, costs)
    if dataset.empty:
        console.print("[yellow]Nenhum momento candidato no histórico: nada para treinar.[/]")
        return 1
    console.print(
        f"{len(dataset)} momentos candidatos; {pct(dataset['rotulo'].mean() * 100, 1)} terminaram no lucro depois dos custos; "
        f"resultado médio {pct(dataset['pnl_pct'].mean(), 3, sign=True)}."
    )
    console.print("[dim]Treinando mês a mês (só com o passado) e comparando com as regras...[/]")
    rep = evaluate(cfg, data, costs, dataset)

    t = Table(title=f"Desafiador x regras: fora da amostra ({format_local(rep.oos_start, tz, '%d/%m/%Y')} a {format_local(rep.oos_end, tz, '%d/%m/%Y')}), já com custos")
    t.add_column("Mês de teste")
    for col in ("Exemplos de treino", "Momentos no teste", "Acerto no treino", "AUC", "Observação"):
        t.add_column(col)
    for o in rep.folds:
        t.add_row(format_local(o.fold.test_start_ms, "UTC", "%m/%Y"), str(o.n_train), str(o.n_test),
                  pct(o.base_rate_train * 100, 1) if o.base_rate_train is not None else "-", num(o.auc, 2) if o.auc is not None else "-", o.note or "-")
    console.print(t)
    if rep.auc_oos is not None:
        console.print(
            f"Qualidade da previsão fora da amostra: AUC [bold]{num(rep.auc_oos, 3)}[/] (0,5 = sorteio) | Brier {num(rep.brier_oos, 4)} "
            f"(referência: prever sempre o acerto do treino = {num(rep.brier_baseline_oos, 4)}; menor é melhor)."
        )
        cal = Table(title="Calibração: probabilidade prevista x o que aconteceu")
        for col in ("Faixa prevista", "Momentos", "Prob. média", "Acerto real", "Resultado médio"):
            cal.add_column(col)
        for c in rep.calibration:
            cal.add_row(c["faixa"], str(c["momentos"]), pct(c["prob_media"] * 100, 1), pct(c["acerto_real"] * 100, 1), pct(c["resultado_medio_pct"], 3, sign=True))
        console.print(cal)

    res = Table(title="Operando no simulador (uma posição por vez, perda máxima do dia, mesmos custos)")
    res.add_column("Estratégia")
    keys = ("operacoes", "retorno_total_pct", "drawdown_max_pct", "taxa_acerto_pct", "expectativa_pct", "sharpe")
    for k in ("Operações", "Retorno", "Drawdown máx.", "Acerto", "Média por operação", "Sharpe", "IC 95% da média"):
        res.add_column(k)
    for name, m in (("regras (campeão atual)", rep.rules), (f"desafiador (prob. >= {num(rep.threshold, 2)})", rep.challenger)):
        lo, hi = m.get("expectativa_ic95_pct", [None, None])
        ic = f"{pct(lo, 3, sign=True)} a {pct(hi, 3, sign=True)}" if lo is not None and not math.isnan(lo) else "-"
        res.add_row(name, *(_fmt(k, m.get(k)) for k in keys), ic)
    console.print(res)
    bh = rep.buy_hold
    console.print(f"Comprar e segurar no mesmo período: {pct(bh.get('retorno_total_pct'), 2, sign=True)} (drawdown {pct(bh.get('drawdown_max_pct'), 2)}).")

    # ---- modelo final (todo o histórico) + relatórios + procedência
    folder = cfg.path(cfg.backtest.relatorio_pasta)
    stamp = format_local(now_ms(), "UTC", "%Y%m%d-%H%M%S")
    model_path = cfg.path(cfg.modelo.pasta) / pair / f"modelo-{stamp}.json"
    info = {"par": pair, "momentos": len(dataset), "auc_fora_da_amostra": rep.auc_oos, "relatorio": "modelo.json"}
    model = final_model(cfg, dataset, info)
    model.save(model_path)
    (folder).mkdir(parents=True, exist_ok=True)
    report_path = folder / "modelo.json"
    report_path.write_text(json.dumps(_report_dict(rep, model_path.name), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    record = provenance.build(
        cfg, symbols=sorted({cfg.mercado.simbolo_analise, pair}), costs={pair: costs.to_dict()}, frozen_costs=frozen_path,
        outputs=[report_path, model_path], kind="modelo treinar",
    )
    provenance.save(record, folder / "modelo.meta.json")
    console.print(f"\nRelatório: {report_path}\nModelo treinado com todo o histórico: {model_path}\nProcedência: {folder / 'modelo.meta.json'}")
    console.print(
        "[dim]O desafiador só substitui as regras se for melhor fora da amostra, depois dos custos e sem piorar o drawdown "
        "(critério da etapa de aprendizado contínuo). Até lá, ele é só estudo.[/]"
    )
    return 0
