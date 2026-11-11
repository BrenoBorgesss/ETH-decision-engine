"""Comandos `indicadores ver` e `indicadores validar`."""

from __future__ import annotations

import argparse

from rich.table import Table

from cryptoagent.cli._shared import console
from cryptoagent.config import Config, Secrets
from cryptoagent.data import history
from cryptoagent.data.rest import BinanceReadOnlyClient
from cryptoagent.engine.decision import REGIME_PT as DECISION_REGIME_PT
from cryptoagent.timeutils import format_local

# -------------------------------------------------------------- indicadores

# Mesmos nomes do motor de decisão; aqui o "indefinido" explica o motivo (indicadores aquecendo).
REGIME_PT = {**DECISION_REGIME_PT, "indefinido": "indefinido (aquecendo)"}


async def cmd_indicators_show(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `indicadores ver`: mostra os indicadores do último candle fechado e os suportes e resistências. Devolve o código de saída do processo."""
    from cryptoagent.indicators.compute import compute_indicators
    from cryptoagent.indicators.structure import nearest_levels, previous_day_levels, support_resistance

    sym = (args.simbolo or cfg.mercado.simbolo_analise).upper()
    p, tz = cfg.indicadores, cfg.fuso.local
    async with BinanceReadOnlyClient(cfg.binance, secrets) as client:
        await client.sync_time()
        df, _ = await client.klines(sym, args.tf, limit=1000)
        server_now = client.server_now_ms()
    df = df[df["close_time"] < server_now].reset_index(drop=True)  # só candles fechados
    if len(df) < p.warmup_bars:
        console.print(f"[yellow]Só {len(df)} candles; os indicadores precisam de {p.warmup_bars} para estabilizar.[/]")
    ind = compute_indicators(df, p, cfg.regime, tz_name=tz, day_start=cfg.fuso.inicio_dia_local)
    last = ind.iloc[-1]
    pos = len(ind) - 1

    t = Table(title=f"{sym} {args.tf} | candle fechado de {format_local(int(last['open_time']), tz)} (Brasília)")
    t.add_column("Indicador")
    t.add_column("Valor")
    t.add_column("Leitura simples")
    c = last["close"]
    t.add_row("Fechamento", f"{c:,.2f}", "")
    for n in p.ema:
        e = last[f"ema_{n}"]
        t.add_row(f"EMA {n}", f"{e:,.2f}", f"preço {'acima' if c > e else 'abaixo'} | inclinação {last[f'ema_{n}_slope']:+.2f}% em {p.inclinacao_ema_barras} candles")
    t.add_row("VWAP do dia", f"{last['vwap']:,.2f}", f"preço a {last['dist_vwap_atr']:+.2f} ATR do VWAP ({'esticado para baixo' if last['dist_vwap_atr'] <= -2 else 'esticado para cima' if last['dist_vwap_atr'] >= 2 else 'perto da média'})")
    t.add_row("Bollinger %B", f"{last['bb_pctb']:.2f}", "≤ 0 = na/abaixo da banda inferior; ≥ 1 = na/acima da superior")
    t.add_row("RSI", f"{last['rsi']:.1f}", "< 30 sobrevendido; > 70 sobrecomprado")
    t.add_row("Stoch RSI %K / %D", f"{last['stochrsi_k']:.1f} / {last['stochrsi_d']:.1f}", "< 20 baixo; > 80 alto")
    t.add_row("MACD / sinal / hist.", f"{last['macd']:.2f} / {last['macd_signal']:.2f} / {last['macd_hist']:+.2f}", "histograma > 0 = momentum de alta")
    t.add_row("ATR", f"{last['atr']:,.2f} ({last['atr_pct']:.2f}%)", f"volatilidade no percentil {last['atr_pctl']:.0f} das últimas {p.janela_percentil} barras")
    t.add_row("ADX (+DI / −DI)", f"{last['adx']:.1f} ({last['plus_di']:.1f} / {last['minus_di']:.1f})", "> 25 tendência forte; < 20 sem tendência")
    t.add_row("Volume relativo", f"{last['rvol']:.2f}×", f"volume ÷ média dos {p.volume_relativo} candles anteriores")
    t.add_row("Venda agressiva", f"{last['taker_sell_ratio'] * 100:.0f}% do volume", f"volume vendido = {last['sell_vol_vs_peak'] * 100:.0f}% do pico recente")
    t.add_row("Estrutura", str(last["structure"]), "topos/fundos confirmados")
    recent = ind.iloc[-p.pivo_barras * 4 :]
    t.add_row("Divergência RSI recente", "altista" if recent["bull_div"].any() else "baixista" if recent["bear_div"].any() else "nenhuma", "")
    t.add_row("Pavio de rejeição", "altista" if last["bull_rejection"] else "baixista" if last["bear_rejection"] else "não", "")
    t.add_row("[bold]Regime[/]", f"[bold]{REGIME_PT[last['regime']]}[/]", "")
    console.print(t)

    levels = support_resistance(
        ind, ind, at_pos=pos, atr_value=float(last["atr"]), tolerance_atr=p.sr_tolerancia_atr, lookback=p.sr_janela_barras, max_levels=p.sr_max_niveis
    ) + previous_day_levels(ind, pos, tz, cfg.fuso.inicio_dia_local, days=ind["trading_day"])
    sup, res = nearest_levels(levels, c)
    lv = Table(title="Suportes e resistências (conhecidos até agora)")
    for col in ("Nível", "Preço", "Distância", "Toques / origem"):
        lv.add_column(col)
    for level in sorted(levels, key=lambda x: x.price, reverse=True):
        mark = " ← resistência mais próxima" if level is res else " ← suporte mais próximo" if level is sup else ""
        lv.add_row(("resistência" if level.price > c else "suporte") + mark, f"{level.price:,.2f}", f"{(level.price / c - 1) * 100:+.2f}%", f"{level.touches} | {level.source}")
    console.print(lv)
    console.print("[dim]Indicadores são insumos; a decisão COMPRAR/VENDER/ESPERAR vem de: cryptoagent analisar.[/]")
    return 0


async def cmd_indicators_validate(cfg: Config, secrets: Secrets, args: argparse.Namespace) -> int:
    """Comando `indicadores validar`: confere os indicadores em todo o histórico baixado. Devolve o código de saída do processo."""
    import time as _t

    from cryptoagent.indicators.compute import compute_indicators

    store = history.HistoryStore(cfg.path(cfg.historico.pasta))
    sym = (args.simbolo or cfg.mercado.simbolo_analise).upper()
    p = cfg.indicadores
    t = Table(title=f"Validação dos indicadores no histórico real de {sym}")
    for col in ("TF", "Candles", "Tempo", "NaN após aquecimento", "RSI mín–máx", "Dentro das bandas", "VWAP reinicia certo", "Regimes (%)"):
        t.add_column(col)
    for tf in cfg.historico.intervalos.get(sym, []):
        df = store.load(sym, tf)
        if len(df) <= p.warmup_bars:
            continue
        t0 = _t.perf_counter()
        ind = compute_indicators(df, p, cfg.regime, tz_name=cfg.fuso.local, day_start=cfg.fuso.inicio_dia_local)
        elapsed = _t.perf_counter() - t0
        tail = ind.iloc[p.warmup_bars :]
        cols = ["ema_200", "rsi", "macd", "stochrsi_k", "atr", "bb_pctb", "adx", "vwap", "rvol", "atr_pctl"]
        nans = int(tail[cols].isna().sum().sum())
        inside = ((tail["close"] >= tail["bb_lower"]) & (tail["close"] <= tail["bb_upper"])).mean() * 100
        # no 1º candle de cada dia (Brasília) o VWAP tem de ser igual ao preço típico daquele candle
        first = ind[ind["trading_day"] != ind["trading_day"].shift(1)].iloc[1:]
        typical = (first["high"] + first["low"] + first["close"]) / 3
        vwap_ok = f"{int(((first['vwap'] - typical).abs() < 1e-6 * typical).sum())}/{len(first)} dias"
        regimes = tail["regime"].value_counts(normalize=True) * 100
        short = {"tendencia_alta": "tend. alta", "tendencia_baixa": "tend. baixa", "lateral": "lateral", "alta_volatilidade": "alta vol.", "transicao": "transição"}
        reg_txt = ", ".join(f"{short.get(k, k)} {v:.0f}" for k, v in regimes.items())
        t.add_row(tf, f"{len(df):,}", f"{elapsed:.1f} s", str(nans), f"{tail['rsi'].min():.1f}–{tail['rsi'].max():.1f}", f"{inside:.1f}%", vwap_ok, reg_txt)
    console.print(t)
    console.print(
        "[dim]Esperado: 0 NaN depois do aquecimento; RSI entre 0 e 100; ~88–95% dos fechamentos dentro das bandas de 2 desvios "
        "(cripto tem caudas gordas); VWAP reiniciando em todos os dias. NaN e dias de VWAP que sobrarem costumam vir de períodos "
        "sem negociação (volume zero), comuns no 1m de pares pouco líquidos como ETHBRL.[/]"
    )
    return 0
