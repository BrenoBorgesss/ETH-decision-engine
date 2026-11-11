"""Números no padrão brasileiro: 1.234,56."""

from __future__ import annotations

import math


def num(value: float | None, decimals: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def brl(value: float | None, decimals: int = 2, sign: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    prefix = ("+" if value > 0 else "−" if value < 0 else "") if sign else ("−" if value < 0 else "")
    return f"{prefix}R$ {num(abs(value), decimals)}"


def pct(value: float | None, decimals: int = 2, sign: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return (f"{value:+.{decimals}f}" if sign else f"{value:.{decimals}f}").replace(".", ",") + "%"
