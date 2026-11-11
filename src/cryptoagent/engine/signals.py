"""Sinais de compra por reversão à média. Cada um dá uma nota de 0 a 1, com o motivo em texto.

0 = nenhuma evidência; 1 = evidência máxima. A nota final é a média ponderada (pesos no config).

Conjunto mínimo (ligado): vwap, bollinger, rsi.
Extras (desligados até a ablação mostrar valor): stoch_rsi, divergencia, pavio, fluxo_taker,
suporte, order_book (só ao vivo: não existe histórico do livro).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from cryptoagent.config import Estrategia
from cryptoagent.engine.context import DecisionContext, nan_min
from cryptoagent.fmt import num
from cryptoagent.indicators.structure import nearest_levels


@dataclass(frozen=True)
class SignalScore:
    """Nota de um sinal (0 a 1), seu peso, a explicação e se o dado estava disponível."""

    name: str
    score: float  # 0 a 1
    weight: float
    detail: str  # explicação em português
    available: bool = True  # False = dado não disponível (ex.: order book no backtest)

    @property
    def contribution(self) -> float:
        """Nota × peso."""
        return self.score * self.weight


def ramp(value: float, start: float, full: float) -> float:
    """0 em `start`, 1 em `full`, linear no meio (funciona nas duas direções)."""
    if value is None or np.isnan(value):
        return 0.0
    if start == full:
        return 1.0 if value >= full else 0.0
    return float(min(1.0, max(0.0, (value - start) / (full - start))))


def _last(ctx: DecisionContext, tf: str) -> Any:  # noqa: ANN401 - Series do pandas ou ArrayRow
    return ctx.market.last(tf)


def sig_vwap(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pela distância do preço abaixo do VWAP do dia, em ATR."""
    r = _last(ctx, p.tf_setup)
    d = float(r["dist_vwap_atr"])
    s = ramp(-d, p.vwap_atr_inicio, p.vwap_atr_maximo)
    side = "abaixo" if d < 0 else "acima"
    return SignalScore("vwap", s, p.pesos["vwap"], f"preço {num(abs(d), 2)} ATR {side} do VWAP do dia ({p.tf_setup})")


def sig_bollinger(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pelo %B das Bandas de Bollinger (quanto mais abaixo da banda inferior, maior)."""
    r = _last(ctx, p.tf_setup)
    b = float(r["bb_pctb"])
    s = ramp(b, p.pctb_inicio, p.pctb_maximo)
    where = "abaixo da banda inferior" if b < 0 else "perto da banda inferior" if b < 0.2 else "longe da banda inferior"
    return SignalScore("bollinger", s, p.pesos["bollinger"], f"%B {num(b, 2)}: {where} ({p.tf_setup})")


def sig_rsi(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pelo RSI (quanto mais baixo, maior)."""
    r = _last(ctx, p.tf_setup)
    v = float(r["rsi"])
    s = ramp(v, p.rsi_inicio, p.rsi_maximo)
    return SignalScore(
        "rsi",
        s,
        p.pesos["rsi"],
        f"RSI {num(v, 1)} ({'sobrevendido' if v < 30 else 'baixo' if v < 40 else 'neutro/alto'}, {p.tf_setup})",
    )


def sig_stoch_rsi(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota 1 se o Stoch RSI cruzou para cima abaixo de 20 no timeframe do gatilho; senão 0."""
    g = ctx.market.frames[p.tf_gatilho].iloc[-2:]
    if len(g) < 2:
        return SignalScore("stoch_rsi", 0.0, p.pesos["stoch_rsi"], "sem candles suficientes", False)
    k0, k1, d0, d1 = g["stochrsi_k"].iloc[0], g["stochrsi_k"].iloc[1], g["stochrsi_d"].iloc[0], g["stochrsi_d"].iloc[1]
    cross = k0 <= d0 and k1 > d1 and min(k0, d0) < 20
    return SignalScore(
        "stoch_rsi",
        1.0 if cross else 0.0,
        p.pesos["stoch_rsi"],
        f"Stoch RSI {'cruzou para cima abaixo de 20' if cross else 'sem cruzamento de alta na zona baixa'} ({p.tf_gatilho})",
    )


def sig_divergencia(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota 1 se houve divergência altista do RSI nos últimos 8 candles do setup; senão 0."""
    recent = ctx.market.frames[p.tf_setup].iloc[-8:]
    found = bool(recent["bull_div"].any())
    return SignalScore(
        "divergencia",
        1.0 if found else 0.0,
        p.pesos["divergencia"],
        f"divergência altista do RSI {'recente' if found else 'ausente'} ({p.tf_setup})",
    )


def sig_pavio(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota 1 se houve pavio de rejeição embaixo nos últimos 2 candles do gatilho; senão 0."""
    recent = ctx.market.frames[p.tf_gatilho].iloc[-2:]
    found = bool(recent["bull_rejection"].any())
    return SignalScore(
        "pavio",
        1.0 if found else 0.0,
        p.pesos["pavio"],
        f"pavio de rejeição embaixo {'nos últimos 2 candles' if found else 'ausente'} ({p.tf_gatilho})",
    )


def sig_fluxo_taker(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pela queda da venda agressiva em relação ao pico recente, só quando o pico foi dominado por vendas."""
    g = ctx.market.frames[p.tf_gatilho].iloc[-6:]
    peak_ratio = float(g["taker_sell_ratio"].max())
    now = float(_last(ctx, p.tf_gatilho)["sell_vol_vs_peak"])
    s = ramp(now, 0.8, 0.3) if peak_ratio >= 0.55 else 0.0
    return SignalScore(
        "fluxo_taker",
        s,
        p.pesos["fluxo_taker"],
        f"venda agressiva agora = {now * 100:.0f}% do pico recente (pico com {peak_ratio * 100:.0f}% do volume vendido)",
    )


def sig_suporte(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pela proximidade do suporte mais próximo abaixo do preço, em ATR."""
    r = _last(ctx, p.tf_setup)
    sup, _ = nearest_levels(ctx.market.levels, ctx.market.price * 1.0000001)
    if sup is None or not r["atr"] > 0:
        return SignalScore("suporte", 0.0, p.pesos["suporte"], "sem suporte conhecido por perto")
    dist_atr = (ctx.market.price - sup.price) / float(r["atr"])
    s = ramp(dist_atr, 1.0, 0.0)
    return SignalScore(
        "suporte",
        s,
        p.pesos["suporte"],
        f"suporte {sup.price:,.2f} a {dist_atr:.2f} ATR ({sup.source}, {sup.touches} toque(s))",
    )


def sig_order_book(ctx: DecisionContext, p: Estrategia) -> SignalScore:
    """Nota pelo desequilíbrio do livro a favor dos compradores. Indisponível no backtest (não há histórico do livro)."""
    imb = ctx.market.book_imbalance
    if imb is None:
        return SignalScore(
            "order_book",
            0.0,
            p.pesos["order_book"],
            "order book indisponível (no backtest não existe histórico)",
            False,
        )
    s = ramp(imb, 0.0, 0.4)
    return SignalScore(
        "order_book",
        s,
        p.pesos["order_book"],
        f"desequilíbrio do livro {imb:+.2f} ({'compradores dominam' if imb > 0 else 'vendedores dominam'})",
    )


SIGNALS: dict[str, Callable[[DecisionContext, Estrategia], SignalScore]] = {
    "vwap": sig_vwap,
    "bollinger": sig_bollinger,
    "rsi": sig_rsi,
    "stoch_rsi": sig_stoch_rsi,
    "divergencia": sig_divergencia,
    "pavio": sig_pavio,
    "fluxo_taker": sig_fluxo_taker,
    "suporte": sig_suporte,
    "order_book": sig_order_book,
}


def score_signals(ctx: DecisionContext, p: Estrategia) -> tuple[float, list[SignalScore]]:
    """Nota combinada (0 a 1) = Σ nota × peso ÷ Σ pesos dos sinais DISPONÍVEIS."""
    scores = [SIGNALS[name](ctx, p) for name in p.sinais_ativos]
    usable = [s for s in scores if s.available]
    total_w = sum(s.weight for s in usable)
    combined = sum(s.contribution for s in usable) / total_w if total_w > 0 else 0.0
    return combined, scores


@dataclass(frozen=True)
class TriggerResult:
    """Resultado do gatilho anti-faca caindo: se disparou e a explicação."""

    ok: bool
    detail: str


def anti_knife_trigger(ctx: DecisionContext, p: Estrategia) -> TriggerResult:
    """Não compra no primeiro toque: exige sinal de que a queda parou.

    5m: algum dos últimos N candles esticou (fechou na/abaixo da banda inferior, ou a mínima furou a
    banda), e o ÚLTIMO candle fechou de volta dentro da banda e acima do fechamento anterior.
    Alternativa (opcional): fundo mais alto no 1m (mínima dos 3 últimos acima da mínima dos 10 anteriores).
    """
    g = ctx.market.frames[p.tf_gatilho]
    if len(g) < p.gatilho_janela_5m + 1:
        return TriggerResult(False, "candles de 5m insuficientes para o gatilho")
    last, prev = ctx.market.last(p.tf_gatilho), ctx.market.last(p.tf_gatilho, 2)
    n = p.gatilho_janela_5m  # os N candles antes do último
    pctb, low, lower = (ctx.market.tail(p.tf_gatilho, c, n + 1)[:-1] for c in ("bb_pctb", "low", "bb_lower"))
    stretched = bool(((pctb <= 0) | (low < lower)).any())
    back_inside = bool(last["bb_pctb"] > 0 and last["close"] > prev["close"])
    if stretched and back_inside:
        return TriggerResult(
            True, f"{p.tf_gatilho}: esticou abaixo da banda e o último candle fechou de volta dentro, subindo"
        )
    if p.gatilho_fundo_mais_alto_1m and p.tf_timing in ctx.market.frames:
        lows = ctx.market.tail(p.tf_timing, "low", 13) if stretched else ()
        if len(lows) >= 13:
            recent_low = nan_min(lows[-3:])
            prior_low = nan_min(lows[-13:-3])
            if recent_low > prior_low:
                return TriggerResult(
                    True, f"{p.tf_timing}: fundo mais alto ({recent_low:,.2f} > {prior_low:,.2f}) depois do esticamento"
                )
    if not stretched:
        return TriggerResult(
            False,
            f"gatilho: não houve esticamento abaixo da banda nos últimos {p.gatilho_janela_5m} candles de {p.tf_gatilho}",
        )
    return TriggerResult(
        False,
        f"gatilho: ainda sem sinal de que a queda parou (último {p.tf_gatilho} não fechou de volta dentro da banda subindo)",
    )
