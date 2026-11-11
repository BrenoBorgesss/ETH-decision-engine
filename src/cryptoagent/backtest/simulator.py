"""Simulação candle a candle com o MESMO decide() do modo ao vivo.

Como cada coisa é simulada:
- Decisão: ao fechar cada candle de 5m (só candles fechados antes do instante).
- Entrada (COMPRAR): ordem a mercado na abertura do candle de 1m seguinte do par de OPERAÇÃO,
  pagando meio spread + slippage + taxa.
- Proteção: OCO na Binance com stop e alvo 1, verificada candle a candle no 1m. Se o stop e o alvo
  caem no mesmo candle de 1m, assume o PIOR caso (stop). Stop executa a mercado, com slippage maior;
  se o preço abrir abaixo do stop (gap), executa na abertura.
- Outras saídas (esticado para cima, invalidação, tempo): a mercado no 1m seguinte.
- Perda máxima do dia (Brasília) e risco por operação: as mesmas regras da sessão do dia.
- Otimização exata: sem posição e sem esticamento recente no 5m, o gatilho anti-faca não pode
  disparar, então essas barras são puladas (o resultado é idêntico ao de chamar decide() nelas).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import BacktestData
from cryptoagent.backtest.execution import buy_fill, close_pnl, oco_check, sell_fill
from cryptoagent.config import Estrategia, Indicadores
from cryptoagent.engine.context import ArrayRow, DecisionContext, MarketView, PositionView, RiskView, SeasonView
from cryptoagent.engine.decision import decide
from cryptoagent.indicators.structure import Level, support_resistance_arrays

ROWS = {"tf_contexto": 3, "tf_confirmacao": 3, "tf_setup": 10, "tf_timing": 15}

BLOCK_CATEGORIES = [  # a ordem importa: a primeira palavra-chave encontrada define a categoria
    ("perda máxima", "PERDA MÁXIMA"),
    ("spread alto", "spread de"),
    ("confiança abaixo do mínimo", "abaixo do mínimo de"),
    ("regime bloqueado", "sinais de compra bloqueados"),
    ("filtro de tendência", "filtro de tendência"),
    ("sem esticamento", "não está esticado"),
    ("sem gatilho anti-faca", "gatilho"),
    ("acima das médias", "acima das médias"),
    ("alvo perto demais p/ taxas", "alvo 1 a só"),
    ("risco/retorno baixo", "risco/retorno"),
]
MIN_SET = {"vwap", "bollinger", "rsi"}


@dataclass
class Trade:
    """Uma operação simulada fechada, com preços, tamanho, resultado líquido e motivo de saída."""
    entry_ms: int
    exit_ms: int
    entry_price: float
    exit_price: float
    qty: float
    size: float  # valor aplicado (moeda do par)
    pnl: float  # resultado líquido (moeda do par)
    pnl_pct: float  # resultado ÷ valor aplicado × 100
    ret_equity: float  # resultado ÷ patrimônio na entrada
    exit_reason: str
    confidence: float
    regime: str
    stop: float
    target1: float
    fees: float


@dataclass
class SimStats:
    """Contadores de uma simulação: barras de decisão, candidatos, compras e motivos de ESPERAR."""
    decision_bars: int = 0
    candidates: int = 0
    buys: int = 0
    blocks: Counter = field(default_factory=Counter)
    # distância até o alvo 1 em múltiplos do custo, para sinais técnicos (esticamento + gatilho, sem filtro de tendência)
    target_cost_multiples: list[float] = field(default_factory=list)
    limit_days: set = field(default_factory=set)


@dataclass
class SimResult:
    """Resultado de uma simulação: operações, estatísticas, período e capital inicial."""
    label: str
    trades: list[Trade]
    stats: SimStats
    start_ms: int
    end_ms: int
    capital: float


@dataclass
class _Pos:
    entry_ms: int
    entry_fill: float
    qty: float
    size: float
    fee_in: float
    stop_tr: float  # no par de operação
    t1_tr: float
    stop_an: float  # no par de análise (para o motor)
    t1_an: float
    t2_an: float
    avg_an: float
    equity_at_entry: float
    confidence: float
    regime: str
    checked_until: int


def _categorize(block: str) -> str:
    for name, key in BLOCK_CATEGORIES:
        if key in block:
            return name
    return "dados/outros"


def simulate(
    data: BacktestData,
    p: Estrategia,
    ind: Indicadores,
    costs: CostSchedule,
    *,
    capital: float,
    loss_pct_day: float,
    divisor: float,
    spread_max_pct: float,
    start_ms: int | None = None,
    end_ms: int | None = None,
    season_on: bool = True,
    label: str = "",
    prefilter: bool = True,
) -> SimResult:
    """`prefilter=False` chama decide() em toda barra candidata (só para o teste de equivalência)."""
    tf5, tf15 = p.tf_gatilho, p.tf_setup
    s5, s15 = data.frames[tf5], data.frames[tf15]
    ct = data.close_times
    start = max(start_ms or 0, data.first_valid_ms)
    end = end_ms or data.last_ms
    n_rows = {getattr(p, role): n for role, n in ROWS.items()}
    n_rows[tf5] = max(p.stop_fundo_barras_5m, p.gatilho_janela_5m + 1, 6) + 2

    stretched = ((s5["bb_pctb"] <= 0) | (s5["low"] < s5["bb_lower"])).to_numpy()
    cum = np.concatenate([[0], np.cumsum(stretched)])  # cum[k] = esticamentos nas k primeiras barras
    days = s5["trading_day"].to_numpy()

    # Pré-checagens EXATAS no setup (15m): se der ESPERAR com certeza, nem chama decide().
    e15_of = np.searchsorted(ct[tf15], ct[tf5] + 1, side="left") - 1  # último 15m fechado em cada decisão
    regime15 = s15["regime"].to_numpy()
    no_stretch15 = (
        (s15["dist_vwap_atr"] >= -p.vwap_atr_inicio) & (s15["bb_pctb"] >= p.pctb_inicio) & (s15["rsi"] >= p.rsi_inicio)
    ).to_numpy()
    min_set_only = set(p.sinais_ativos) <= MIN_SET  # com sinais extras a nota pode ser > 0 sem esticamento
    sw_h, sw_hp = s15["swing_high"].to_numpy(dtype=float), s15["swing_high_pos"].to_numpy(dtype=float)
    sw_l, sw_lp = s15["swing_low"].to_numpy(dtype=float), s15["swing_low_pos"].to_numpy(dtype=float)
    atr15 = s15["atr"].to_numpy(dtype=float)

    columns = data.column_arrays()  # timeframe -> {coluna: array}: leitura de linha sem montar Series
    timing_close = columns[p.tf_timing]["close"]

    roundtrip_by_hour = [costs.roundtrip_pct(h) for h in range(24)]  # o custo só depende da hora: calcula uma vez

    def frames_at(t: int) -> tuple[dict[str, pd.DataFrame], dict[str, Callable], float]:
        """Candles fechados antes de `t`, os leitores rápidos (linha e últimos valores) e o último fechamento do timeframe de timing."""
        out, ends = {}, {}
        for tf, f in data.frames.items():
            e = int(np.searchsorted(ct[tf], t, side="left"))
            out[tf] = f.iloc[max(0, e - n_rows.get(tf, 3)) : e]
            ends[tf] = e

        def rows(tf: str, back: int) -> ArrayRow:
            return ArrayRow(columns[tf], ends[tf] - back)

        def tails(tf: str, column: str, n: int) -> np.ndarray:
            e = ends[tf]
            return columns[tf][column][max(0, e - min(n, n_rows.get(tf, 3))) : e]  # mesma janela que `out[tf]` enxerga

        return out, {"rows": rows, "tails": tails}, float(timing_close[ends[p.tf_timing] - 1])

    def ratio_at(t: int) -> float:
        i = int(np.searchsorted(data.ratio_time, t - 60_000, side="right")) - 1
        return float(data.ratio[max(i, 0)])

    def levels_at(t: int) -> list[Level]:
        e = int(np.searchsorted(ct[tf15], t, side="left")) - 1
        if e < 0 or not atr15[e] > 0:
            return []
        lv = support_resistance_arrays(
            sw_h, sw_hp, sw_l, sw_lp, at_pos=e, atr_value=float(atr15[e]), tolerance_atr=ind.sr_tolerancia_atr,
            lookback=ind.sr_janela_barras, max_levels=ind.sr_max_niveis,
        )
        ph, pl, pc = data.prev_day[e]
        if not np.isnan(ph):
            lv += [Level(ph, 1, e, "dia_anterior_maxima"), Level(pl, 1, e, "dia_anterior_minima"), Level(pc, 1, e, "dia_anterior_fechamento")]
        return lv

    def next_bar(t: int) -> int:
        return int(np.searchsorted(data.t_open_time, t, side="left"))

    trades: list[Trade] = []
    stats = SimStats()
    equity = capital
    pos: _Pos | None = None
    day_key, day_start_equity, day_pnl = None, capital, 0.0

    def close_position(exit_ms: int, fill: float, reason: str) -> None:
        nonlocal pos, equity, day_pnl
        assert pos is not None
        pnl, fee_out = close_pnl(pos.qty, pos.size, pos.fee_in, fill, costs.fee)
        trades.append(
            Trade(pos.entry_ms, exit_ms, pos.entry_fill, fill, pos.qty, pos.size, pnl, pnl / pos.size * 100, pnl / pos.equity_at_entry,
                  reason, pos.confidence, pos.regime, pos.stop_tr, pos.t1_tr, pos.fee_in + fee_out)
        )
        equity += pnl
        day_pnl += pnl
        pos = None

    def check_oco(until: int) -> bool:
        """Stop/alvo executados na Binance entre a última verificação e `until`. True se fechou."""
        assert pos is not None
        a = next_bar(pos.checked_until)
        b = int(np.searchsorted(data.t_open_time, until - 60_000, side="right"))  # candles de 1m já fechados
        if b <= a:
            return False
        hit = oco_check(
            data.t_open[a:b], data.t_high[a:b], data.t_low[a:b], stop=pos.stop_tr, target=pos.t1_tr,
            half_spread=costs.half_spread(_hour_of(a)), slip_stop_pct=costs.slip_stop_pct,
        )
        if hit is None:
            return False
        close_position(int(data.t_open_time[a + hit.index]) + 60_000, hit.fill, hit.reason)
        return True

    def _hour_of(i_1m: int) -> int:
        # hora local aproximada pela decisão mais próxima (a tabela de spread é por hora)
        j = int(np.searchsorted(ct[tf5], int(data.t_open_time[min(i_1m, len(data.t_open_time) - 1)]), side="left"))
        return int(data.decision_hours[min(j, len(data.decision_hours) - 1)])

    i0 = int(np.searchsorted(ct[tf5], start - 1, side="left"))
    for i in range(i0, len(s5)):
        t = int(ct[tf5][i]) + 1
        if t > end:
            break
        stats.decision_bars += 1
        if days[i] != day_key:
            day_key, day_start_equity, day_pnl = days[i], equity, 0.0
        max_loss = day_start_equity * loss_pct_day / 100
        hour = int(data.decision_hours[i])
        spread = costs.spread_by_hour[hour]
        cost_pct = roundtrip_by_hour[hour]

        if pos is not None:
            if check_oco(t):
                continue
            pos.checked_until = t
            j = next_bar(t) - 1
            now_tr = float(data.t_close[max(j, 0)])
            unreal = pos.qty * now_tr * (1 - costs.half_spread(hour)) * (1 - costs.fee) - pos.size - pos.fee_in
            limit = "PERDA MÁXIMA DO DIA ATINGIDA" if day_pnl + unreal <= -max_loss else None
            if limit:
                stats.limit_days.add(day_key)
            frames, readers, price = frames_at(t)
            ratio = ratio_at(t)
            ctx = DecisionContext(
                data.analysis, data.trade, ratio,
                MarketView(t, price, frames, [], spread, **readers),
                RiskView([], None, equity, daily_limit_reason=limit),
                cost_pct,
                PositionView(pos.qty, pos.avg_an, pos.entry_ms, pos.stop_an, pos.t1_an, pos.t2_an, "sinal do backtest"),
                SeasonView(*(data.season.factor_at(t) if (season_on and data.season) else (1.0, []))),
                live=False,
            )
            d = decide(ctx, p, spread_max_pct=spread_max_pct)
            if d.action == "VENDER":
                k = next_bar(t)
                if k < len(data.t_open):
                    fill = sell_fill(float(data.t_open[k]), costs.half_spread(hour), costs.slip_market_pct)
                    close_position(int(data.t_open_time[k]), fill, d.exit_reason or "vender")
            continue

        if day_pnl <= -max_loss:
            stats.limit_days.add(day_key)
            continue
        if prefilter and cum[i] - cum[max(0, i - p.gatilho_janela_5m)] == 0:
            continue  # sem esticamento recente: o gatilho não pode disparar (equivalente a ESPERAR)

        stats.candidates += 1
        e15 = int(e15_of[i])
        if prefilter and e15 >= 0 and regime15[e15] in p.bloquear_regimes:
            stats.blocks["regime bloqueado"] += 1
            continue  # decide() bloquearia pelo regime
        if prefilter and e15 >= 0 and min_set_only and no_stretch15[e15]:
            stats.blocks["sem esticamento"] += 1
            continue  # todas as notas seriam 0: decide() daria ESPERAR
        frames, readers, price = frames_at(t)
        ratio = ratio_at(t)
        risk_trade = max_loss / divisor
        ctx = DecisionContext(
            data.analysis, data.trade, ratio,
            MarketView(t, price, frames, levels_at(t), spread, **readers),
            RiskView([], risk_trade, equity),
            cost_pct,
            None,
            SeasonView(*(data.season.factor_at(t) if (season_on and data.season) else (1.0, []))),
            live=False,
        )
        d = decide(ctx, p, spread_max_pct=spread_max_pct)
        for b in d.blocks:
            stats.blocks[_categorize(b)] += 1
        technical = d.levels is not None and d.trigger is not None and d.trigger.ok and d.parts and d.parts[0].value > 0
        if technical and cost_pct > 0 and not any("filtro de tendência" in b for b in d.blocks):
            stats.target_cost_multiples.append(d.levels.target_distance_pct / cost_pct)
        if d.action != "COMPRAR" or d.levels is None or not d.levels.size_brl:
            continue
        k = next_bar(t)
        if k >= len(data.t_open):
            break
        fill = buy_fill(float(data.t_open[k]), costs.half_spread(hour), costs.slip_market_pct)
        size = min(d.levels.size_brl, equity)
        lv = d.levels
        pos = _Pos(
            entry_ms=int(data.t_open_time[k]), entry_fill=fill, qty=size / fill, size=size, fee_in=size * costs.fee,
            stop_tr=lv.stop * ratio, t1_tr=lv.target1 * ratio, stop_an=lv.stop, t1_an=lv.target1, t2_an=lv.target2,
            avg_an=fill / ratio, equity_at_entry=equity, confidence=d.confidence, regime=d.regime, checked_until=int(data.t_open_time[k]),
        )
        stats.buys += 1

    if pos is not None:  # fecha no último preço para não deixar operação aberta no relatório
        close_position(int(data.t_open_time[-1]) + 60_000, float(data.t_close[-1]), "fim_dos_dados")
    return SimResult(label, trades, stats, start, end, capital)
