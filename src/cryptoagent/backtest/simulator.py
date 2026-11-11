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
from cryptoagent.engine.decision import Decision, decide
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


@dataclass
class _Bar:
    """O que vale numa barra de decisão: posição `i` no 5m, instante, hora local, spread, custo e perda máxima do dia."""

    i: int
    t: int
    hour: int
    spread: float
    cost_pct: float
    max_loss: float


class _Market:
    """Leituras do histórico no instante de cada decisão, sempre só com candles já fechados.

    Tudo o que não depende do instante (arrays, pré-checagens do 15m, custo por hora) é calculado uma vez aqui.
    """

    def __init__(self, data: BacktestData, p: Estrategia, ind: Indicadores, costs: CostSchedule) -> None:
        self.data, self.p, self.ind, self.costs = data, p, ind, costs
        self.tf5, self.tf15 = p.tf_gatilho, p.tf_setup
        s5, s15 = data.frames[self.tf5], data.frames[self.tf15]
        self.n_bars = len(s5)
        self.ct = data.close_times
        self.n_rows = {getattr(p, role): n for role, n in ROWS.items()}
        self.n_rows[self.tf5] = max(p.stop_fundo_barras_5m, p.gatilho_janela_5m + 1, 6) + 2

        stretched = ((s5["bb_pctb"] <= 0) | (s5["low"] < s5["bb_lower"])).to_numpy()
        self.cum = np.concatenate([[0], np.cumsum(stretched)])  # cum[k] = esticamentos nas k primeiras barras
        self.days = s5["trading_day"].to_numpy()

        # Pré-checagens EXATAS no setup (15m): se der ESPERAR com certeza, nem chama decide().
        self.e15_of = np.searchsorted(self.ct[self.tf15], self.ct[self.tf5] + 1, side="left") - 1  # último 15m fechado
        self.regime15 = s15["regime"].to_numpy()
        self.no_stretch15 = (
            (s15["dist_vwap_atr"] >= -p.vwap_atr_inicio)
            & (s15["bb_pctb"] >= p.pctb_inicio)
            & (s15["rsi"] >= p.rsi_inicio)
        ).to_numpy()
        self.min_set_only = set(p.sinais_ativos) <= MIN_SET  # com sinais extras a nota pode ser > 0 sem esticamento
        self.sw_h, self.sw_hp = s15["swing_high"].to_numpy(dtype=float), s15["swing_high_pos"].to_numpy(dtype=float)
        self.sw_l, self.sw_lp = s15["swing_low"].to_numpy(dtype=float), s15["swing_low_pos"].to_numpy(dtype=float)
        self.atr15 = s15["atr"].to_numpy(dtype=float)

        self.columns = data.column_arrays()  # timeframe -> {coluna: array}: leitura de linha sem montar Series
        self.timing_close = self.columns[p.tf_timing]["close"]
        self.roundtrip_by_hour = [costs.roundtrip_pct(h) for h in range(24)]  # o custo só depende da hora

    def bar(self, i: int, t: int, max_loss: float) -> _Bar:
        """Dados da barra de decisão `i` (fecha em `t`)."""
        hour = int(self.data.decision_hours[i])
        return _Bar(i, t, hour, self.costs.spread_by_hour[hour], self.roundtrip_by_hour[hour], max_loss)

    def frames_at(self, t: int) -> tuple[dict[str, pd.DataFrame], dict[str, Callable], float]:
        """Candles fechados antes de `t`, os leitores rápidos (linha e últimos valores) e o último fechamento do timeframe de timing."""
        columns, n_rows = self.columns, self.n_rows
        out, ends = {}, {}
        for tf, f in self.data.frames.items():
            e = int(np.searchsorted(self.ct[tf], t, side="left"))
            out[tf] = f.iloc[max(0, e - n_rows.get(tf, 3)) : e]
            ends[tf] = e

        def rows(tf: str, back: int) -> ArrayRow:
            return ArrayRow(columns[tf], ends[tf] - back)

        def tails(tf: str, column: str, n: int) -> np.ndarray:
            e = ends[tf]
            return columns[tf][column][max(0, e - min(n, n_rows.get(tf, 3))) : e]  # mesma janela que `out[tf]` enxerga

        return out, {"rows": rows, "tails": tails}, float(self.timing_close[ends[self.p.tf_timing] - 1])

    def ratio_at(self, t: int) -> float:
        """Preço do par de operação ÷ preço do par de análise no último minuto fechado antes de `t`."""
        i = int(np.searchsorted(self.data.ratio_time, t - 60_000, side="right")) - 1
        return float(self.data.ratio[max(i, 0)])

    def levels_at(self, t: int) -> list[Level]:
        """Suportes/resistências do 15m e os níveis do dia anterior, como o motor ao vivo enxergaria em `t`."""
        e = int(np.searchsorted(self.ct[self.tf15], t, side="left")) - 1
        if e < 0 or not self.atr15[e] > 0:
            return []
        lv = support_resistance_arrays(
            self.sw_h,
            self.sw_hp,
            self.sw_l,
            self.sw_lp,
            at_pos=e,
            atr_value=float(self.atr15[e]),
            tolerance_atr=self.ind.sr_tolerancia_atr,
            lookback=self.ind.sr_janela_barras,
            max_levels=self.ind.sr_max_niveis,
        )
        ph, pl, pc = self.data.prev_day[e]
        if not np.isnan(ph):
            lv += [
                Level(ph, 1, e, "dia_anterior_maxima"),
                Level(pl, 1, e, "dia_anterior_minima"),
                Level(pc, 1, e, "dia_anterior_fechamento"),
            ]
        return lv

    def next_bar(self, t: int) -> int:
        """Índice do primeiro candle de 1m do par de operação que abre em `t` ou depois."""
        return int(np.searchsorted(self.data.t_open_time, t, side="left"))

    def hour_of(self, i_1m: int) -> int:
        """Hora local aproximada de um candle de 1m, pela decisão mais próxima (a tabela de spread é por hora)."""
        d = self.data
        j = int(np.searchsorted(self.ct[self.tf5], int(d.t_open_time[min(i_1m, len(d.t_open_time) - 1)]), side="left"))
        return int(d.decision_hours[min(j, len(d.decision_hours) - 1)])

    def season(self, t: int, season_on: bool) -> SeasonView:
        """Fator do horário em `t` (neutro se desligado ou sem tabela)."""
        return SeasonView(*(self.data.season.factor_at(t) if (season_on and self.data.season) else (1.0, [])))

    def no_recent_stretch(self, i: int) -> bool:
        """Sem esticamento recente no 5m: o gatilho anti-faca não pode disparar (equivale a ESPERAR)."""
        return self.cum[i] - self.cum[max(0, i - self.p.gatilho_janela_5m)] == 0

    def surely_wait(self, i: int, stats: SimStats, *, regime_shortcut: bool) -> bool:
        """Pré-checagens exatas no 15m: regime bloqueado ou nenhuma nota possível. Conta o motivo em `stats`."""
        e15 = int(self.e15_of[i])
        if e15 < 0:
            return False
        if regime_shortcut and self.regime15[e15] in self.p.bloquear_regimes:
            stats.blocks["regime bloqueado"] += 1
            return True  # decide() bloquearia pelo regime
        if self.min_set_only and self.no_stretch15[e15]:
            stats.blocks["sem esticamento"] += 1
            return True  # todas as notas seriam 0: decide() daria ESPERAR
        return False


class _Book:
    """Carteira da simulação: patrimônio, posição aberta, resultado do dia e operações fechadas."""

    def __init__(self, capital: float, market: _Market) -> None:
        self.m, self.costs, self.data = market, market.costs, market.data
        self.trades: list[Trade] = []
        self.equity = capital
        self.pos: _Pos | None = None
        self.day_key, self.day_start_equity, self.day_pnl = None, capital, 0.0

    def start_bar(self, day: object) -> None:
        """Zera o resultado do dia quando muda o dia de negociação."""
        if day != self.day_key:
            self.day_key, self.day_start_equity, self.day_pnl = day, self.equity, 0.0

    def open(self, k: int, fill: float, d: Decision, ratio: float) -> None:
        """Abre a posição no candle de 1m `k`, com stop e alvo traduzidos para o par de operação."""
        size = min(d.levels.size_brl, self.equity)
        lv = d.levels
        self.pos = _Pos(
            entry_ms=int(self.data.t_open_time[k]),
            entry_fill=fill,
            qty=size / fill,
            size=size,
            fee_in=size * self.costs.fee,
            stop_tr=lv.stop * ratio,
            t1_tr=lv.target1 * ratio,
            stop_an=lv.stop,
            t1_an=lv.target1,
            t2_an=lv.target2,
            avg_an=fill / ratio,
            equity_at_entry=self.equity,
            confidence=d.confidence,
            regime=d.regime,
            checked_until=int(self.data.t_open_time[k]),
        )

    def close(self, exit_ms: int, fill: float, reason: str) -> None:
        """Fecha a posição, registra a operação e atualiza o patrimônio e o resultado do dia."""
        pos = self.pos
        assert pos is not None
        pnl, fee_out = close_pnl(pos.qty, pos.size, pos.fee_in, fill, self.costs.fee)
        self.trades.append(
            Trade(
                pos.entry_ms,
                exit_ms,
                pos.entry_fill,
                fill,
                pos.qty,
                pos.size,
                pnl,
                pnl / pos.size * 100,
                pnl / pos.equity_at_entry,
                reason,
                pos.confidence,
                pos.regime,
                pos.stop_tr,
                pos.t1_tr,
                pos.fee_in + fee_out,
            )
        )
        self.equity += pnl
        self.day_pnl += pnl
        self.pos = None

    def check_oco(self, until: int) -> bool:
        """Stop/alvo executados na Binance entre a última verificação e `until`. True se fechou."""
        pos, d = self.pos, self.data
        assert pos is not None
        a = self.m.next_bar(pos.checked_until)
        b = int(np.searchsorted(d.t_open_time, until - 60_000, side="right"))  # candles de 1m já fechados
        if b <= a:
            return False
        hit = oco_check(
            d.t_open[a:b],
            d.t_high[a:b],
            d.t_low[a:b],
            stop=pos.stop_tr,
            target=pos.t1_tr,
            half_spread=self.costs.half_spread(self.m.hour_of(a)),
            slip_stop_pct=self.costs.slip_stop_pct,
        )
        if hit is None:
            return False
        self.close(int(d.t_open_time[a + hit.index]) + 60_000, hit.fill, hit.reason)
        return True

    def unrealized(self, t: int, hour: int) -> float:
        """Resultado se a posição fosse vendida agora (último 1m fechado), já com meio spread e taxa."""
        pos = self.pos
        assert pos is not None
        j = self.m.next_bar(t) - 1
        now_tr = float(self.data.t_close[max(j, 0)])
        return pos.qty * now_tr * (1 - self.costs.half_spread(hour)) * (1 - self.costs.fee) - pos.size - pos.fee_in


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
    observer: Callable[[int, DecisionContext, Decision], None] | None = None,
    entry_rule: Callable[[int, DecisionContext, Decision], bool] | None = None,
    open_positions: bool = True,
) -> SimResult:
    """Simula a estratégia candle a candle (ver a descrição do módulo).

    `prefilter=False` chama decide() em toda barra candidata (só para o teste de equivalência).
    `observer(t, ctx, decisão)` é chamado em cada barra avaliada sem posição (usado para montar o conjunto
    de dados do modelo). `entry_rule(t, ctx, decisão)` substitui a regra "compra se a decisão for COMPRAR"
    (usado pelo modelo desafiador). `open_positions=False` só observa, sem abrir operações.
    Com `entry_rule` ou `observer`, o atalho do regime bloqueado é desligado: o regime é uma trava da
    estratégia, e o desafiador pode decidir diferente.
    """
    m = _Market(data, p, ind, costs)
    book = _Book(capital, m)
    stats = SimStats()
    start = max(start_ms or 0, data.first_valid_ms)
    end = end_ms or data.last_ms
    regime_shortcut = entry_rule is None and observer is None

    i0 = int(np.searchsorted(m.ct[m.tf5], start - 1, side="left"))
    for i in range(i0, m.n_bars):
        t = int(m.ct[m.tf5][i]) + 1
        if t > end:
            break
        stats.decision_bars += 1
        book.start_bar(m.days[i])
        bar = m.bar(i, t, book.day_start_equity * loss_pct_day / 100)

        if book.pos is not None:
            _manage_position(m, book, stats, bar, spread_max_pct=spread_max_pct, season_on=season_on)
            continue

        if book.day_pnl <= -bar.max_loss:
            stats.limit_days.add(book.day_key)
            continue
        if prefilter and m.no_recent_stretch(i):
            continue
        stats.candidates += 1
        if prefilter and m.surely_wait(i, stats, regime_shortcut=regime_shortcut):
            continue

        ctx = _entry_context(m, book, bar, divisor=divisor, season_on=season_on)
        d = decide(ctx, p, spread_max_pct=spread_max_pct)
        _count(stats, d, bar.cost_pct)
        if observer is not None:
            observer(t, ctx, d)
        wants = entry_rule(t, ctx, d) if entry_rule is not None else d.action == "COMPRAR"
        if not open_positions or not wants or d.levels is None or not d.levels.size_brl:
            continue
        k = m.next_bar(t)
        if k >= len(data.t_open):
            break
        book.open(
            k,
            buy_fill(float(data.t_open[k]), costs.half_spread(bar.hour), costs.slip_market_pct),
            d,
            ctx.trade_price_ratio,
        )
        stats.buys += 1

    if book.pos is not None:  # fecha no último preço para não deixar operação aberta no relatório
        book.close(int(data.t_open_time[-1]) + 60_000, float(data.t_close[-1]), "fim_dos_dados")
    return SimResult(label, book.trades, stats, start, end, capital)


def _manage_position(
    m: _Market, book: _Book, stats: SimStats, bar: _Bar, *, spread_max_pct: float, season_on: bool
) -> None:
    """Com posição: confere o OCO na Binance e, se ainda aberta, pergunta ao motor se é hora de VENDER."""
    pos, data, costs, t = book.pos, m.data, m.costs, bar.t
    assert pos is not None
    if book.check_oco(t):
        return
    pos.checked_until = t
    unreal = book.unrealized(t, bar.hour)
    limit = "PERDA MÁXIMA DO DIA ATINGIDA" if book.day_pnl + unreal <= -bar.max_loss else None
    if limit:
        stats.limit_days.add(book.day_key)
    frames, readers, price = m.frames_at(t)
    ctx = DecisionContext(
        data.analysis,
        data.trade,
        m.ratio_at(t),
        MarketView(t, price, frames, [], bar.spread, **readers),
        RiskView([], None, book.equity, daily_limit_reason=limit),
        bar.cost_pct,
        PositionView(pos.qty, pos.avg_an, pos.entry_ms, pos.stop_an, pos.t1_an, pos.t2_an, "sinal do backtest"),
        m.season(t, season_on),
        live=False,
    )
    d = decide(ctx, m.p, spread_max_pct=spread_max_pct)
    if d.action == "VENDER":
        k = m.next_bar(t)
        if k < len(data.t_open):
            fill = sell_fill(float(data.t_open[k]), costs.half_spread(bar.hour), costs.slip_market_pct)
            book.close(int(data.t_open_time[k]), fill, d.exit_reason or "vender")


def _entry_context(m: _Market, book: _Book, bar: _Bar, *, divisor: float, season_on: bool) -> DecisionContext:
    """Contexto de decisão sem posição: risco por operação = perda máxima do dia ÷ divisor."""
    t = bar.t
    frames, readers, price = m.frames_at(t)
    return DecisionContext(
        m.data.analysis,
        m.data.trade,
        m.ratio_at(t),
        MarketView(t, price, frames, m.levels_at(t), bar.spread, **readers),
        RiskView([], bar.max_loss / divisor, book.equity),
        bar.cost_pct,
        None,
        m.season(t, season_on),
        live=False,
    )


def _count(stats: SimStats, d: Decision, cost_pct: float) -> None:
    """Conta os motivos de ESPERAR e, para sinais técnicos, a distância até o alvo em múltiplos do custo."""
    for b in d.blocks:
        stats.blocks[_categorize(b)] += 1
    technical = d.levels is not None and d.trigger is not None and d.trigger.ok and d.parts and d.parts[0].value > 0
    if technical and cost_pct > 0 and not any("filtro de tendência" in b for b in d.blocks):
        stats.target_cost_multiples.append(d.levels.target_distance_pct / cost_pct)
