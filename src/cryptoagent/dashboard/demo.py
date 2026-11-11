"""Painel no modo demonstração: a mesma página e o mesmo motor, sobre o histórico do repositório.

Em vez do WebSocket e da conta real, o serviço roda o laço do paper trading (`paper/runner._cycle`) com o
cliente de replay (`paper/replay.py`): um relógio simulado que anda `velocidade` minutos por segundo,
candles da amostra e uma carteira VIRTUAL. Tudo o que aparece é rotulado como SIMULADO. Nada sai para a
internet e nenhuma chave é lida.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from rich.console import Console

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.config import Config, Secrets
from cryptoagent.dashboard.service import LiveService
from cryptoagent.paper.book import PaperBook, PaperCosts
from cryptoagent.paper.filters import load_saved
from cryptoagent.paper.replay import ReplayClient
from cryptoagent.paper.runner import Variant, _cycle
from cryptoagent.security.redact import redact
from cryptoagent.storage import sim_db

log = logging.getLogger(__name__)


class DemoService(LiveService):
    """Serviço do painel que repete o histórico em velocidade acelerada, com carteira virtual."""

    def __init__(
        self,
        cfg: Config,
        console: Console,
        *,
        start_ms: int,
        end_ms: int,
        costs: CostSchedule,
        minutes_per_second: float = 4.0,
        db_name: str = "painel-demo.sqlite",
    ) -> None:
        cfg = cfg.model_copy(deep=True)
        folder = cfg.path(cfg.paper.replay_pasta)
        cfg.sazonalidade.relatorio_pasta = str(folder / "sem_perfil_por_horario")  # perfil salvo usa dados posteriores
        cfg.aprendizado.pasta = str(folder / "sem_aprendizado")  # idem
        cfg.alertas.windows = cfg.alertas.som = False  # demonstração não dispara notificação nem som
        super().__init__(cfg, Secrets(), console)
        self.mode = "demo"
        self.signal_mode = "paper"
        db = folder / db_name
        for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            if p.exists():
                p.unlink()  # cada demonstração começa do zero
        self.sim_db_path = db
        self.signal_conn = sim_db.connect(db)
        self.replay = ReplayClient(cfg, start_ms=start_ms, costs=costs)
        self.client = self.replay
        self.end_ms = end_ms
        self.step_s = 1.0 / minutes_per_second
        self.costs = costs
        pc = cfg.paper
        book = PaperBook(
            self.signal_conn,
            "padrao",
            pc.par,
            capital=pc.capital,
            max_loss_brl=pc.perda_max_dia_brl,
            divisor=cfg.risco.divisor_perda_diaria,
            costs=PaperCosts(costs.fee, costs.slip_market_pct, costs.slip_stop_pct),
            tz=cfg.fuso.local,
            day_start=cfg.fuso.inicio_dia_local,
            now_ms=start_ms,
            filters=load_saved(cfg.path(pc.filtros_arquivo), pc.par),
        )
        self.variant = Variant("padrao", cfg, book)
        self.finished = False

    def now(self) -> int:
        """Relógio simulado."""
        return self.replay.local_now_ms()

    def ws_fresh(self) -> bool:
        """No demo não há WebSocket."""
        return False

    def virtual_wallet(self) -> dict:
        """Carteira virtual da demonstração: patrimônio, resultado do dia e posição aberta."""
        book = self.variant.book
        pos = book.open_trade()
        bid, ask, _ = self.tickers.get(self.cfg.paper.par, (None, None, None))
        mid = (bid + ask) / 2 if bid else None
        half = (ask - bid) / (ask + bid) if bid else 0.0
        return {
            "capital_inicial": book.initial_capital,
            "patrimonio": book.equity,
            "resultado_brl": book.equity - book.initial_capital,
            "resultado_dia_brl": book.day_pnl(self.now(), mid, half),
            "perda_max_dia": book.max_loss_day,
            "posicao": None
            if pos is None
            else {"qtd": pos["qty"], "entrada": pos["entry_price"], "stop": pos["stop"], "alvo1": pos["target1"]},
            "fim_do_periodo": self.finished,
        }

    async def run(self, extra_tasks: Iterable[asyncio.Task] = ()) -> None:
        """Avança o relógio minuto a minuto (na velocidade escolhida) e roda o mesmo laço do paper trading."""
        tasks = list(extra_tasks)
        client, pc, v = self.replay, self.cfg.paper, self.variant
        try:
            await client.sync_time()
            while not self.stop.is_set():
                now = client.server_now_ms()
                if now >= self.end_ms:
                    self.finished = True
                    await asyncio.sleep(0.5)  # período acabou: o painel fica parado no último estado
                    continue
                try:
                    bt = await client.get("/api/v3/ticker/bookTicker", {"symbol": pc.par})
                    bid, ask = float(bt.data["bidPrice"]), float(bt.data["askPrice"])  # type: ignore[index]
                    self.tickers[pc.par] = (bid, ask, now)
                    out = await _cycle(
                        v,
                        self.cfg,
                        self.secrets,
                        client,
                        self.signal_conn,
                        now,
                        bid,
                        ask,
                        (ask - bid) / (ask + bid),
                        lambda m: log.info("demo: %s", m),
                        client.advance,
                    )
                except Exception as exc:  # o painel mostra o erro, sem rastreamento de pilha
                    log.exception("Erro no ciclo do painel de demonstração")
                    self.error = redact(exc)
                    out = None
                if out is not None:
                    res, sid = out
                    self.error = None
                    d = res.decision
                    self.alerter.on_decision(self.prev_action, d, d.price * res.ratio)
                    self.prev_action, self.last, self.last_signal_id = d.action, res, sid
                    self.analyses += 1
                await asyncio.sleep(self.step_s)
                await client.advance(60)
        finally:
            self.stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.signal_conn.close()
            self.conn.close()
