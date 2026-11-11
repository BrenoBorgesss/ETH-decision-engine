"""Status da conta: junta permissões, saldo, operações, sessão do dia e limites.

Qualquer falha aqui vira um MOTIVO de bloqueio (o sinal fica em ESPERAR), nunca um
valor presumido. A única exceção é a chave com permissões demais: aí o agente
levanta ExcessivePermissions e se recusa a rodar.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from cryptoagent.account.binance_account import (
    Balance,
    CommissionInfo,
    ensure_ledger_start,
    fallback_commission,
    fetch_balances,
    fetch_commission,
    load_account_trades,
    make_price_at,
    split_symbol,
    sync_trades,
)
from cryptoagent.account.journal import manual_ledger_trades
from cryptoagent.account.ledger import DayResult, LedgerTrade, MissingDayStartPrice, compute_day
from cryptoagent.account.limits import LimitStatus, evaluate_limits
from cryptoagent.account.permissions import PermissionCheck, check_permissions
from cryptoagent.config import Config, Secrets
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.security.redact import redact
from cryptoagent.session.daily import DailySession, get_session, today
from cryptoagent.timeutils import now_ms, trading_day_bounds

log = logging.getLogger(__name__)


@dataclass
class AccountStatus:
    """Foto da conta para a decisão: sessão do dia, saldos, taxas, resultado do dia, limites e os motivos de bloqueio e avisos."""
    mode: str
    verified: bool  # True = números conferidos pela Binance; False = diário manual
    reasons: list[str] = field(default_factory=list)  # motivos de bloqueio -> ESPERAR
    warnings: list[str] = field(default_factory=list)
    session: DailySession | None = None
    permissions: PermissionCheck | None = None
    balances: dict[str, Balance] | None = None
    balances_ms: int | None = None
    commission: CommissionInfo | None = None
    exit_fee_rate: float | None = None
    symbol: str | None = None
    quote_asset: str | None = None
    bid: float | None = None
    fx_usdtbrl: float | None = None
    day: DayResult | None = None
    pnl_brl: float | None = None
    available_brl: float | None = None
    effective_capital_brl: float | None = None
    limits: LimitStatus | None = None
    trades_synced: int = 0
    updated_ms: int = 0

    @property
    def blocked(self) -> bool:
        """True se há algum motivo de bloqueio (o sinal fica em ESPERAR)."""
        return bool(self.reasons)


class AccountMonitor:
    """Lê a conta (ou o diário manual) e monta o AccountStatus. Guarda em memória a última checagem de permissões."""
    def __init__(self, cfg: Config, secrets: Secrets, client: BinanceReadOnlyClient, conn: sqlite3.Connection) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.client = client
        self.conn = conn
        self._permissions: PermissionCheck | None = None
        self._permissions_ms = 0

    async def permissions(self, force: bool = False) -> PermissionCheck:
        """Checa (no máximo a cada N minutos) e levanta ExcessivePermissions se a chave puder mais que ler."""
        every_ms = self.cfg.conta.verificar_permissoes_a_cada_min * 60_000
        if force or self._permissions is None or now_ms() - self._permissions_ms > every_ms:
            self._permissions = await check_permissions(self.client)
            self._permissions_ms = now_ms()
        self._permissions.raise_if_unsafe()
        return self._permissions

    async def refresh(self) -> AccountStatus:
        """Monta o status agora: permissões, sessão do dia, preços, saldo, operações, resultado e limites. Falha de leitura vira motivo de bloqueio, nunca valor presumido."""
        cfg = self.cfg
        st = AccountStatus(mode=cfg.conta.modo, verified=cfg.conta.modo == "binance", updated_ms=now_ms())
        server_now = self.client.server_now_ms()
        use_binance = await self._check_key(st)
        session = get_session(self.conn, today(cfg.fuso.local, cfg.fuso.inicio_dia_local, server_now))
        st.session = session
        if session is None:
            st.reasons.append("Sessão do dia não respondida (capital e perda máxima de hoje). Rode: cryptoagent sessao iniciar")
            return st
        st.symbol = session.par_operacao
        _, st.quote_asset = split_symbol(st.symbol)
        day_start_ms, day_end_ms = trading_day_bounds(server_now, cfg.fuso.local, cfg.fuso.inicio_dia_local)
        ok, day_start_price = await self._market_prices(st, session, day_start_ms)
        if not ok:
            return st
        trades = await self._trades(st, use_binance, server_now, day_start_ms)
        if trades is None:
            return st
        if not self._day_result(st, trades, day_start_ms, day_end_ms, day_start_price):
            return st
        self._capital_and_limits(st, session)
        return st

    async def _check_key(self, st: AccountStatus) -> bool:
        """Confere chave e permissões. Devolve True se dá para ler a conta na Binance."""
        if self.cfg.conta.modo != "binance":
            st.warnings.append("Modo diário manual: resultado NÃO verificado pela corretora.")
            return False
        if not self.secrets.has_binance:
            st.reasons.append(
                "Chave da Binance ausente no .env: sem ler o saldo, o agente fica em ESPERAR. "
                "(Contingência: conta.modo: diario_manual no config.)"
            )
            return False
        try:
            st.permissions = await self.permissions()  # pode levantar ExcessivePermissions
        except BinanceError as exc:
            st.reasons.append(f"Não foi possível checar as permissões da chave: {redact(exc)}")
            return False
        ok = True
        if not st.permissions.can_read:
            st.reasons.append("A chave não tem permissão de leitura.")
            ok = False
        if not st.permissions.ip_restricted:
            st.warnings.append("Chave sem restrição de IP. Recomendado: restringir ao seu IP no site da Binance.")
        return ok

    async def _market_prices(self, st: AccountStatus, session: DailySession, day_start_ms: int) -> tuple[bool, float | None]:
        """Bid do par (para avaliar a posição), câmbio quando preciso e o preço do início do dia. (ok, preço das 00:00)."""
        try:
            bt = await self.client.get("/api/v3/ticker/bookTicker", {"symbol": st.symbol})
            st.bid = float(bt.data["bidPrice"])  # type: ignore[index]
            if st.quote_asset == "USDT" or session.saldo_em == "USDT":
                fx = await self.client.get("/api/v3/ticker/bookTicker", {"symbol": self.cfg.mercado.par_cambio})
                st.fx_usdtbrl = (float(fx.data["bidPrice"]) + float(fx.data["askPrice"])) / 2  # type: ignore[index]
            df, _ = await self.client.klines(st.symbol, "1m", start_ms=day_start_ms, limit=1)
        except BinanceError as exc:
            st.reasons.append(f"Falha ao ler preços de mercado: {redact(exc)}")
            return False, None
        return True, float(df["open"].iloc[0]) if not df.empty and int(df["open_time"].iloc[0]) == day_start_ms else None

    async def _trades(self, st: AccountStatus, use_binance: bool, server_now: int, day_start_ms: int) -> list[LedgerTrade] | None:
        """Saldo, taxas e operações (da Binance ou do diário manual). None se não deu para ler (com motivo de bloqueio)."""
        cfg, symbol = self.cfg, st.symbol
        assert symbol is not None
        if use_binance:
            try:
                balances = await fetch_balances(self.client)
                st.balances, st.balances_ms = balances.value, balances.received_ms
                age_s = (self.client.local_now_ms() - balances.received_ms) / 1000
                if age_s > cfg.conta.saldo_max_s:
                    st.reasons.append(f"Leitura do saldo velha demais ({age_s:.0f} s; limite {cfg.conta.saldo_max_s:g} s): ESPERAR.")
                    return None
                try:
                    st.commission = (await fetch_commission(self.client, symbol)).value
                except BinanceError as exc:
                    st.commission = fallback_commission(symbol, cfg.custos.taxa_padrao_maker, cfg.custos.taxa_padrao_taker)
                    st.warnings.append(f"Não foi possível ler as taxas da conta ({redact(exc)}); usando as do config.")
                start = ensure_ledger_start(self.conn, symbol, day_start_ms)
                st.trades_synced = await sync_trades(
                    self.client, self.conn, symbol, server_now_ms=server_now, start_ms=start,
                    price_at=make_price_at(self.client), fallback_fee_rate=cfg.custos.taxa_padrao_taker,
                )
                trades = load_account_trades(self.conn, symbol, start)
            except BinanceError as exc:
                st.reasons.append(f"Falha ao ler a conta na Binance: {redact(exc)}. Sem presumir valores: ESPERAR.")
                return None
        elif cfg.conta.modo == "diario_manual":
            trades = manual_ledger_trades(self.conn, symbol)
        else:
            return None  # modo binance sem chave/permissão: já há motivo de bloqueio
        has_bnb = bool(st.balances and st.balances.get("BNB") and st.balances["BNB"].total > 0)
        st.exit_fee_rate = st.commission.effective("taker", has_bnb=has_bnb) if st.commission else cfg.custos.taxa_padrao_taker
        return trades

    def _to_brl(self, st: AccountStatus) -> float | None:
        return st.fx_usdtbrl if st.quote_asset == "USDT" else 1.0

    def _day_result(self, st: AccountStatus, trades: list[LedgerTrade], day_start_ms: int, day_end_ms: int, day_start_price: float | None) -> bool:
        """Resultado do dia e posição da operação pelo histórico (FIFO, com o teto de capital_operacao). False se faltou dado."""
        cfg = self.cfg
        to_brl = self._to_brl(st)
        if st.quote_asset == "USDT" and not to_brl:
            st.reasons.append("Sem cotação USDTBRL: não dá para calcular o resultado em R$.")
            return False
        cap_base = cfg.capital_operacao.valor if cfg.capital_operacao.modo == "eth" else None
        cap_quote = cfg.capital_operacao.valor / to_brl if cfg.capital_operacao.modo == "brl" else None
        try:
            st.day = compute_day(
                trades, day_start_ms=day_start_ms, day_end_ms=day_end_ms, exit_price=st.bid, exit_fee_rate=st.exit_fee_rate,
                day_start_price=day_start_price, cap_base=cap_base, cap_quote=cap_quote,
            )
        except MissingDayStartPrice as exc:
            st.reasons.append(f"Falta dado para o resultado do dia: {exc}")
            return False
        st.pnl_brl = st.day.pnl_quote * to_brl
        return self._reconcile_with_balance(st)

    def _reconcile_with_balance(self, st: AccountStatus) -> bool:
        """Confere a posição do histórico com o saldo de ETH lido da conta. False (bloqueio) se o histórico diz que há mais ETH do que existe."""
        if st.balances is None or st.day is None or st.symbol is None:
            return True
        base, _ = split_symbol(st.symbol)
        held = st.balances[base].total if base in st.balances else 0.0
        tol = max(1e-8, st.day.open_qty * 1e-6)
        if st.day.open_qty > held + tol:
            st.reasons.append(
                f"A posição da operação pelo histórico ({st.day.open_qty:.8f} {base}) é maior que o saldo de {base} na conta ({held:.8f}): "
                "parte saiu por fora do histórico (venda em outro par, saque ou transferência). ESPERAR até conferir."
            )
            return False
        outside = held - st.day.open_qty
        if outside > tol:
            st.warnings.append(f"{outside:.8f} {base} na conta estão fora da operação e são ignorados (não contam como posição).")
        return True

    def _capital_and_limits(self, st: AccountStatus, session: DailySession) -> None:
        """Capital efetivo (o menor entre o informado e o disponível), limites do dia e avisos."""
        cfg = self.cfg
        assert st.day is not None and st.bid is not None and st.symbol is not None and st.pnl_brl is not None
        base, _ = split_symbol(st.symbol)
        to_brl = self._to_brl(st) or 1.0
        effective = session.capital_brl
        if st.balances is not None:
            cash = st.balances["BRL"].total if "BRL" in st.balances else 0.0
            if "USDT" in st.balances and st.fx_usdtbrl:
                cash += st.balances["USDT"].total * st.fx_usdtbrl
            st.available_brl = cash + st.day.open_qty * st.bid * to_brl  # só a posição DA OPERAÇÃO, não o ETH todo
            if st.available_brl <= 0:
                st.reasons.append("Sem saldo disponível em BRL/USDT nem posição de operação.")
                return
            if session.capital_brl > st.available_brl:
                st.warnings.append(
                    f"Capital informado (R$ {session.capital_brl:,.2f}) é maior que o disponível (R$ {st.available_brl:,.2f}); usando o disponível."
                )
                effective = st.available_brl
        if cfg.capital_operacao.modo == "brl" and effective > cfg.capital_operacao.valor:
            st.warnings.append(
                f"Só até R$ {cfg.capital_operacao.valor:,.2f} (capital_operacao do config) é tratado como posição da operação."
            )
        st.effective_capital_brl = effective

        st.limits = evaluate_limits(
            effective, session.max_loss_brl, st.pnl_brl, divisor=cfg.risco.divisor_perda_diaria, profit_extends=cfg.risco.lucro_amplia_limite
        )
        if st.limits.blocked:
            st.reasons.append("PERDA MÁXIMA DO DIA ATINGIDA: só ESPERAR até o próximo dia (00:00 de Brasília).")
        if session.max_loss_brl > effective * cfg.risco.aviso_perda_max_pct_capital / 100:
            st.warnings.append(
                f"A perda máxima de hoje (R$ {session.max_loss_brl:,.2f}) passa de {cfg.risco.aviso_perda_max_pct_capital:g}% do capital."
            )
        if st.day.ignored_buy_qty > 1e-9:
            st.warnings.append(f"{st.day.ignored_buy_qty:.6f} {base} comprados hoje passaram do teto da operação e foram ignorados.")
        if st.day.ignored_sell_qty > 1e-9:
            st.warnings.append(f"{st.day.ignored_sell_qty:.6f} {base} vendidos hoje eram de fora da operação e foram ignorados.")
        if st.day.fees_estimated:
            st.warnings.append("Algumas taxas não puderam ser convertidas e foram estimadas pelo config.")
