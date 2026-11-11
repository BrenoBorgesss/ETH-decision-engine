"""Buffer de candles ao vivo: histórico recente (REST) + atualizações (WebSocket)."""

from __future__ import annotations

import pandas as pd

from cryptoagent.data.models import KLINE_COLUMNS, Gap, find_gaps


class CandleBuffer:
    """Candles recentes de um par e intervalo em memória, alimentados pela API REST e pelo WebSocket, limitados a `maxlen`."""
    def __init__(self, symbol: str, interval: str, maxlen: int = 1500):
        self.symbol = symbol
        self.interval = interval
        self.maxlen = maxlen
        self._rows: dict[int, dict] = {}
        self.last_update_ms: int | None = None
        self.last_source: str | None = None

    def load_frame(self, df: pd.DataFrame, received_ms: int, source: str = "binance-rest:/api/v3/klines") -> None:
        """Carrega candles vindos da REST. O candle ainda aberto (close_time no futuro) fica marcado como aberto."""
        for rec in df.to_dict("records"):
            rec["closed"] = int(rec["close_time"]) < received_ms
            self._rows[int(rec["open_time"])] = rec
        self._touch(received_ms, source)

    def apply_ws_kline(self, k: dict, received_ms: int) -> None:
        """Aplica o campo "k" de um evento kline do WebSocket."""
        row = {
            "open_time": int(k["t"]),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "close_time": int(k["T"]),
            "quote_volume": float(k["q"]),
            "trades": int(k["n"]),
            "taker_buy_base": float(k["V"]),
            "taker_buy_quote": float(k["Q"]),
            "closed": bool(k["x"]),
        }
        self._rows[row["open_time"]] = row
        self._touch(received_ms, "binance-ws:kline")

    def _touch(self, received_ms: int, source: str) -> None:
        self.last_update_ms = received_ms
        self.last_source = source
        if len(self._rows) > self.maxlen:
            for key in sorted(self._rows)[: len(self._rows) - self.maxlen]:
                del self._rows[key]

    def latest(self) -> dict | None:
        """Candle mais recente (pode estar aberto), ou None."""
        return self._rows[max(self._rows)] if self._rows else None

    def latest_closed(self) -> dict | None:
        """Candle fechado mais recente, ou None."""
        closed = [t for t, r in self._rows.items() if r["closed"]]
        return self._rows[max(closed)] if closed else None

    def to_frame(self, closed_only: bool = True) -> pd.DataFrame:
        """Candles em ordem de horário como DataFrame; por padrão, só os fechados."""
        rows = [self._rows[t] for t in sorted(self._rows) if self._rows[t]["closed"] or not closed_only]
        return pd.DataFrame(rows, columns=[*KLINE_COLUMNS, "closed"])

    def gaps(self) -> list[Gap]:
        """Buracos na sequência de candles fechados."""
        return find_gaps(self.to_frame(), self.interval)

    def __len__(self) -> int:
        return len(self._rows)
