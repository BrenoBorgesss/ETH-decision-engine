"""Regras de tamanho de ordem da Binance (filtros do par), aplicadas às ordens VIRTUAIS.

Uma ordem virtual que a corretora recusaria também é recusada aqui:
- LOT_SIZE: quantidade mínima e em múltiplos de `stepSize` (o que sobra é arredondado para BAIXO);
- MARKET_LOT_SIZE: quantidade máxima numa ordem a mercado;
- NOTIONAL (ou MIN_NOTIONAL, nome antigo): valor mínimo da ordem, na moeda de cotação (R$ no ETHBRL).

Os filtros vêm de `/api/v3/exchangeInfo` (dado público, somente leitura). Sem internet (replay e modo
demo), vêm de um arquivo salvo dessa mesma consulta (`exemplos/filtros_binance.json`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class SymbolFilters:
    """Limites de ordem de um par."""
    symbol: str
    min_qty: float
    step: float
    max_market_qty: float | None
    min_notional: float
    tick: float

    @classmethod
    def from_exchange_info(cls, data: dict, symbol: str) -> SymbolFilters:
        """Lê os filtros do par na resposta de /api/v3/exchangeInfo. Levanta ValueError se o par não estiver nela."""
        info = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
        if info is None:
            raise ValueError(f"o par {symbol} não está na resposta de exchangeInfo")
        f = {x["filterType"]: x for x in info["filters"]}
        lot = f.get("LOT_SIZE", {})
        market = f.get("MARKET_LOT_SIZE", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        max_market = float(market.get("maxQty", 0) or 0)
        return cls(
            symbol=symbol,
            min_qty=float(lot.get("minQty", 0) or 0),
            step=float(lot.get("stepSize", 0) or 0),
            max_market_qty=max_market if max_market > 0 else None,
            min_notional=float(notional.get("minNotional", 0) or 0),
            tick=float(f.get("PRICE_FILTER", {}).get("tickSize", 0) or 0),
        )

    def round_qty(self, qty: float) -> float:
        """Quantidade arredondada para BAIXO no múltiplo de `step` (sem erro de ponto flutuante)."""
        if self.step <= 0:
            return qty
        step = Decimal(str(self.step))
        return float((Decimal(str(qty)) // step) * step)

    def check(self, qty: float, price: float) -> tuple[float, str | None]:
        """(quantidade aceita, motivo da recusa). Recusa se, depois de arredondar, ficar abaixo do mínimo de quantidade ou de valor."""
        q = self.round_qty(qty)
        if self.max_market_qty is not None and q > self.max_market_qty:
            q = self.round_qty(self.max_market_qty)
        if q <= 0 or q < self.min_qty - 1e-12:
            return 0.0, f"quantidade {q:.8f} abaixo do mínimo do par ({self.min_qty:g})"
        if q * price < self.min_notional - 1e-9:
            return 0.0, f"ordem de {q * price:.2f} abaixo do valor mínimo do par ({self.min_notional:g})"
        return q, None


def load_saved(path: Path, symbol: str) -> SymbolFilters | None:
    """Filtros salvos em arquivo (para uso sem internet), ou None se o arquivo ou o par não existirem."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    item = data.get("pares", {}).get(symbol)
    return SymbolFilters(**item) if item else None


def to_saved(filters: list[SymbolFilters], source_note: str) -> dict:
    """Forma do arquivo salvo: nota de origem e um item por par."""
    return {"origem": source_note, "pares": {f.symbol: asdict(f) for f in filters}}

