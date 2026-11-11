"""Serviço ao vivo do painel: WebSocket -> candles -> motor de decisão -> alertas -> estado para a página.

- Candles e preços chegam pelo WebSocket. Se ele ficar mudo (ws_silencio_max_s) ou o buffer tiver
  buracos (reconexão), aquele timeframe é buscado pela API REST na análise (fallback) e o buffer é
  recarregado.
- Nova análise a cada fechamento de candle dos timeframes em `painel.recalcular_ao_fechar` e,
  opcionalmente, a cada `analise_extra_a_cada_s`.
- Chave com permissão além de leitura: o serviço PARA e o painel mostra o motivo.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import date

import pandas as pd
from rich.console import Console

from cryptoagent.account.permissions import ExcessivePermissions
from cryptoagent.alerts.notify import Alert, Alerter
from cryptoagent.config import Config, Secrets
from cryptoagent.data.market_store import CandleBuffer
from cryptoagent.data.rest import BinanceError, BinanceReadOnlyClient
from cryptoagent.data.websocket import BinanceStream
from cryptoagent.engine.journal import save_signal, set_association
from cryptoagent.engine.live import LiveResult, analyze_live
from cryptoagent.fmt import num
from cryptoagent.security.redact import redact
from cryptoagent.seasonality.analysis import load_profiles
from cryptoagent.session import daily
from cryptoagent.storage import agent_db
from cryptoagent.timeutils import format_local, now_ms, zone

log = logging.getLogger(__name__)


def _clean(obj):
    """Tudo que vai para a página passa pelo filtro de segredos e vira JSON simples."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, float):
        return None if obj != obj or obj in (float("inf"), float("-inf")) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


class LiveService:
    def __init__(self, cfg: Config, secrets: Secrets, console: Console):
        self.cfg, self.secrets, self.console = cfg, secrets, console
        p = cfg.estrategia
        self.sym = cfg.mercado.simbolo_analise
        self.buffers = {tf: CandleBuffer(self.sym, tf, maxlen=cfg.painel.buffer_candles) for tf in p.timeframes}
        self.tickers: dict[str, tuple[float, float, int]] = {}
        streams = [f"{self.sym.lower()}@kline_{tf}" for tf in self.buffers] + [f"{s.lower()}@bookTicker" for s in cfg.mercado.simbolos_monitorados]
        self.stream = BinanceStream(cfg.binance.ws_url, streams, self.on_message, name="painel")
        self.trigger = asyncio.Event()
        self.stop = asyncio.Event()
        self.alerter = Alerter(cfg.alertas, console, cfg.fuso.local)
        self.conn = agent_db.connect(cfg.path(cfg.conta.banco))
        self.client: BinanceReadOnlyClient | None = None
        self.last: LiveResult | None = None
        self.last_signal_id: int | None = None
        self.prev_action: str | None = None
        self.fatal: str | None = None
        self.error: str | None = None
        self.needs_backfill: set[str] = set(self.buffers)
        self.started_ms = now_ms()
        self.analyses = 0

    # --------------------------------------------------------------- WebSocket

    def on_message(self, stream: str, data: dict, received: int) -> None:
        name = stream.split("@")[0].upper()
        if "@kline_" in stream:
            k = data["k"]
            buf = self.buffers.get(k["i"])
            if buf is not None:
                buf.apply_ws_kline(k, received)
                if k["x"] and k["i"] in self.cfg.painel.recalcular_ao_fechar:
                    self.trigger.set()
        elif stream.endswith("@bookTicker"):
            self.tickers[name] = (float(data["b"]), float(data["a"]), received)

    def ws_fresh(self) -> bool:
        last = self.stream.last_message_ms
        return self.stream.connected and last is not None and now_ms() - last <= self.cfg.painel.ws_silencio_max_s * 1000

    async def backfill(self, tfs: set[str]) -> None:
        assert self.client is not None
        for tf in sorted(tfs):
            df, _ = await self.client.klines(self.sym, tf, limit=self.cfg.binance.candles_por_consulta_padrao)
            self.buffers[tf].load_frame(df, self.client.server_now_ms())
        self.needs_backfill -= tfs

    def ws_candles(self) -> dict[str, pd.DataFrame] | None:
        """Candles do WebSocket por timeframe; timeframes com buraco ficam de fora (vão pela REST)."""
        if not self.ws_fresh():
            return None
        out = {}
        for tf, buf in self.buffers.items():
            if tf in self.needs_backfill or len(buf) < self.cfg.painel.min_candles_ws:
                continue
            if buf.gaps():
                self.needs_backfill.add(tf)
                continue
            out[tf] = buf.to_frame(closed_only=True)
        return out

    # ------------------------------------------------------------------ análise

    async def analyze(self) -> None:
        assert self.client is not None
        if self.needs_backfill:
            await self.backfill(set(self.needs_backfill))
        try:
            res = await analyze_live(self.cfg, self.secrets, self.client, self.conn, candles=self.ws_candles())
        except ExcessivePermissions as exc:
            self.fatal = str(exc)
            self.alerter.send(Alert(now_ms(), "RECUSADO: chave com permissões além de leitura", str(exc)[:240], True))
            self.stop.set()
            return
        except BinanceError as exc:
            self.error = redact(exc)
            self.alerter.on_health(False, [self.error])
            return
        self.error = None
        d = res.decision
        self.last_signal_id = save_signal(
            self.conn, d, ratio=res.ratio, mode="ao_vivo", data_ages=res.data_ages, latency_ms=res.latency_ms, market_extras=res.market_extras
        )
        self.alerter.on_decision(self.prev_action, d, d.price * res.ratio)
        self.alerter.on_health(res.health.ok, res.health.problems)
        if d.action != self.prev_action:
            self.console.print(
                f"{format_local(d.time_ms, self.cfg.fuso.local)} {d.action} {num(d.confidence, 0)}% | {d.trade_symbol} {num(d.price * res.ratio)} | sinal #{self.last_signal_id}"
            )
        self.prev_action = d.action
        self.last = res
        self.analyses += 1

    async def analysis_loop(self) -> None:
        extra = self.cfg.painel.analise_extra_a_cada_s or None
        while not self.stop.is_set():
            try:
                await self.analyze()
            except Exception:
                log.exception("Erro inesperado na análise")
                self.error = "erro inesperado na análise (detalhes no arquivo de log)"
            try:
                await asyncio.wait_for(self.trigger.wait(), timeout=extra)
            except TimeoutError:
                pass
            self.trigger.clear()
            await asyncio.sleep(1.5)  # dá tempo de os outros timeframes fecharem o mesmo candle

    async def run(self, extra_tasks=()) -> None:
        async with BinanceReadOnlyClient(self.cfg.binance, self.secrets) as client:
            self.client = client
            await client.sync_time()
            await self.backfill(set(self.buffers))
            tasks = [asyncio.create_task(self.stream.run(self.stop)), asyncio.create_task(self.analysis_loop()), *extra_tasks]
            try:
                await self.stop.wait()
            finally:
                self.stop.set()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.conn.close()

    def request_analysis(self) -> None:
        self.trigger.set()

    # -------------------------------------------------------- dados para a página

    def snapshot(self) -> dict:
        now = now_ms()
        cfg = self.cfg
        res = self.last
        out: dict = {
            "agora_ms": now,
            "fuso": cfg.fuso.local,
            "fatal": self.fatal,
            "erro": self.error,
            "aviso_legal": "Não é recomendação financeira e não há garantia de resultado.",
            "simbolo_analise": self.sym,
            "ws": {
                "conectado": self.stream.connected,
                "ultima_msg_s": (now - self.stream.last_message_ms) / 1000 if self.stream.last_message_ms else None,
                "reconexoes": self.stream.reconnects,
                "modo": "WebSocket (tempo real)" if self.ws_fresh() else "API REST (fallback)",
            },
            "precos": {s: {"bid": b, "ask": a, "idade_s": (now - t) / 1000} for s, (b, a, t) in self.tickers.items()},
            "alertas": [asdict(a) for a in list(self.alerter.recent)[:10]],
            "analises": self.analyses,
        }
        if res is None:
            return _clean(out)
        d = res.decision
        r = res.ratio
        lv = d.levels
        out["par"] = d.trade_symbol
        out["razao"] = r
        out["decisao"] = {
            "sinal_id": self.last_signal_id,
            "acao": d.action,
            "confianca": d.confidence,
            "regime": d.regime,
            "momento_ms": d.time_ms,
            "idade_s": (now - d.time_ms) / 1000,
            "preco": d.price * r,
            "parcelas": [asdict(p) for p in d.parts],
            "a_favor": d.reasons_for,
            "contra": d.reasons_against,
            "travas": d.blocks,
            "gatilho": d.trigger.detail if d.trigger else None,
            "invalidacao": d.invalidation,
            "posicao": d.position_note,
            "saida": d.exit_reason,
            "niveis": None if lv is None else {
                "entrada": lv.entry * r, "stop": lv.stop * r, "alvo1": lv.target1 * r, "alvo2": lv.target2 * r,
                "rr": lv.rr, "ganho_pct": lv.reward_net_pct, "risco_pct": lv.risk_net_pct, "tamanho_brl": lv.size_brl,
                "origem_alvo1": lv.target1_source, "perda_no_stop": lv.size_brl * lv.risk_net_pct / 100 if lv.size_brl else None,
            },
        }
        out["saude"] = {
            "ok": res.health.ok,
            "verificacoes": [asdict(c) for c in res.health.checks],
            "idades": res.data_ages,
            "latencia_ms": res.latency_ms,
        }
        out["custos"] = {"spread_pct": res.ctx.market.spread_pct if res.ctx else None, "spread_max_pct": cfg.custos.spread_max_pct,
                         "custo_ida_volta_pct": res.ctx.cost_pct if res.ctx else None}
        out["horario"] = res.hour_note
        acc = res.account
        if acc is not None:
            out["conta"] = {
                "modo": acc.mode,
                "verificado": acc.verified,
                "sessao": asdict(acc.session) if acc.session else None,
                "limites": asdict(acc.limits) if acc.limits else None,
                "dia": None if acc.day is None else {
                    "posicao": acc.day.open_qty, "custo_medio": acc.day.avg_cost_real, "realizado": acc.day.realized_quote,
                    "aberto": acc.day.unrealized_quote, "operacoes": acc.day.trades_today, "taxas": acc.day.fees_quote,
                },
                "avisos": acc.warnings,
                "travas": acc.reasons,
                "moeda": acc.quote_asset,
            }
        out["sinais"] = [dict(x) for x in self.conn.execute(
            "SELECT id, time_ms, trade_symbol, action, confidence, price, stop, target1 FROM signals WHERE mode = 'ao_vivo' ORDER BY id DESC LIMIT 15"
        )]
        out["compras"] = [dict(x) for x in self.conn.execute(
            "SELECT symbol, trade_id, time_ms, price, qty, signal_id, association FROM account_trades WHERE is_buyer = 1 ORDER BY time_ms DESC LIMIT 10"
        )]
        out["sinais_compra"] = [dict(x) for x in self.conn.execute(
            "SELECT id, time_ms, price FROM signals WHERE mode = 'ao_vivo' AND action = 'COMPRAR' ORDER BY id DESC LIMIT 10"
        )]
        out["paper"] = [dict(x) for x in self.conn.execute(
            """SELECT s.variant, s.equity, s.started_ms,
                      (SELECT COUNT(*) FROM paper_trades t WHERE t.variant = s.variant AND t.exit_ms IS NOT NULL) AS fechadas,
                      (SELECT COUNT(*) FROM paper_trades t WHERE t.variant = s.variant AND t.exit_ms IS NULL) AS abertas
               FROM paper_state s"""
        )]
        return _clean(out)

    def candles_payload(self, tf: str) -> dict:
        res = self.last
        if res is None or res.ctx is None or tf not in res.ctx.market.frames:
            return {"tf": tf, "candles": []}
        f = res.ctx.market.frames[tf].tail(self.cfg.painel.candles_grafico)
        z = zone(self.cfg.fuso.local)
        # o gráfico mostra o horário de Brasília: soma o deslocamento local de cada candle
        offsets = [int(pd.Timestamp(t, unit="ms", tz="UTC").tz_convert(z).utcoffset().total_seconds()) for t in f["open_time"]]
        cols = ["open", "high", "low", "close", "ema_20", "ema_50", "ema_200", "bb_upper", "bb_mid", "bb_lower", "vwap"]
        rows = [
            {"t": int(t // 1000) + off, **{c: (float(row[c]) if pd.notna(row[c]) else None) for c in cols if c in f.columns}}
            for t, off, (_, row) in zip(f["open_time"], offsets, f.iterrows())
        ]
        lv = res.decision.levels
        levels = {} if lv is None else {"entrada": lv.entry, "stop": lv.stop, "alvo1": lv.target1, "alvo2": lv.target2}
        return _clean({"tf": tf, "simbolo": self.sym, "candles": rows, "niveis": levels,
                       "suportes": [{"preco": x.price, "origem": x.source} for x in (res.ctx.market.levels if res.ctx else [])]})

    def season_payload(self) -> dict:
        profiles = load_profiles(self.cfg.path(self.cfg.sazonalidade.relatorio_pasta) / "sazonalidade.json")
        sym = self.last.decision.trade_symbol if self.last else (self.cfg.mercado.par_operacao or self.cfg.paper.par)
        prof = profiles.get(sym)
        if not prof:
            return {"simbolo": sym, "disponivel": False}
        return _clean({"simbolo": sym, "disponivel": True, "heatmap": prof["heatmap"], "horas": prof["hours"], "custo": prof["cost"]})

    def save_session(self, data: dict) -> dict:
        day = daily.today(self.cfg.fuso.local, self.cfg.fuso.inicio_dia_local)
        par = str(data.get("par") or self.cfg.mercado.par_operacao or self.cfg.mercado.pares_candidatos[0]).upper()
        saldo = "BRL" if par.endswith("BRL") else str(data.get("saldo") or "USDT").upper()
        s = daily.save_session(
            self.conn, day, capital_brl=float(data["capital"]), max_loss_brl=float(data["perda_max"]), par_operacao=par,
            saldo_em=saldo, candidates=self.cfg.mercado.pares_candidatos,
        )
        self.request_analysis()
        return _clean(asdict(s))

    def associate(self, data: dict) -> None:
        sig = data.get("sinal")
        set_association(self.conn, str(data["par"]).upper(), int(data["operacao"]), int(sig) if sig not in (None, "", "nenhum") else None)
