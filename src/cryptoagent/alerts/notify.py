"""Alertas quando a decisão muda: terminal (com bipe), notificação do Windows e painel.

Quando alertar (regras explícitas):
- a ação mudou (ESPERAR -> COMPRAR, COMPRAR -> ESPERAR, qualquer -> VENDER...);
- VENDER por stop é URGENTE;
- perda máxima do dia atingida (uma vez por dia);
- dados com problema por mais de 1 minuto (uma vez, até voltarem ao normal).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field

from rich.console import Console

from cryptoagent.config import Alertas
from cryptoagent.engine.decision import Decision
from cryptoagent.fmt import num
from cryptoagent.timeutils import format_local, now_ms

log = logging.getLogger(__name__)

# Script fixo: título e texto entram por variável de ambiente (nunca concatenados no comando).
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:CA_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:CA_BODY)) > $null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


@dataclass(frozen=True)
class Alert:
    time_ms: int
    title: str
    body: str
    urgent: bool = False


def alert_for_change(prev_action: str | None, d: Decision, trade_price: float) -> Alert | None:
    """Alerta se a ação mudou. Na primeira análise só alerta se já for COMPRAR ou VENDER."""
    if prev_action == d.action or (prev_action is None and d.action == "ESPERAR"):
        return None
    urgent = d.action == "VENDER" and d.exit_reason == "stop"
    detail = (d.reasons_for[0] if d.action != "ESPERAR" and d.reasons_for else (d.blocks[0] if d.blocks else "")).strip()
    title = f"{d.action} {d.trade_symbol} a {num(trade_price)} ({num(d.confidence, 0)}%)" + (" — URGENTE" if urgent else "")
    body = (f"Antes: {prev_action}. " if prev_action else "") + detail[:180]
    return Alert(d.time_ms, title, body, urgent)


@dataclass
class Alerter:
    cfg: Alertas
    console: Console
    tz: str = "America/Sao_Paulo"
    recent: deque = field(default_factory=lambda: deque(maxlen=30))
    _limit_day: str | None = None
    _data_bad_since: int | None = None
    _data_alerted: bool = False

    def send(self, alert: Alert) -> None:
        self.recent.appendleft(alert)
        log.info("Alerta: %s | %s", alert.title, alert.body)
        if self.cfg.terminal:
            style = "bold white on red" if alert.urgent else "bold black on cyan"
            bell = "\a" if self.cfg.som else ""
            self.console.print(f"{bell}[{style}] ALERTA [/] {format_local(alert.time_ms, self.tz)} {alert.title} — {alert.body}")
        if self.cfg.windows and sys.platform == "win32":
            self._toast(alert)

    def _toast(self, alert: Alert) -> None:
        try:
            env = {**os.environ, "CA_TITLE": alert.title[:120], "CA_BODY": alert.body[:240]}
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:  # notificação é conveniência: falhar aqui não pode derrubar o agente
            log.warning("Notificação do Windows falhou: %s", exc)

    def on_decision(self, prev_action: str | None, d: Decision, trade_price: float) -> None:
        a = alert_for_change(prev_action, d, trade_price)
        if a:
            self.send(a)
        limit = next((b for b in d.blocks if "PERDA MÁXIMA" in b), None)
        day = format_local(d.time_ms, self.tz, "%Y-%m-%d")
        if limit and self._limit_day != day:
            self._limit_day = day
            self.send(Alert(d.time_ms, "Perda máxima do dia atingida", "Só ESPERAR até 00:00 (Brasília). Confira a ordem de stop de posições abertas.", True))

    def on_health(self, ok: bool, problems: list[str]) -> None:
        now = now_ms()
        if ok:
            if self._data_alerted:
                self.send(Alert(now, "Dados normalizados", "Os dados voltaram a ficar frescos e consistentes."))
            self._data_bad_since, self._data_alerted = None, False
            return
        self._data_bad_since = self._data_bad_since or now
        if not self._data_alerted and now - self._data_bad_since >= 60_000:
            self._data_alerted = True
            self.send(Alert(now, "Problema nos dados: ESPERAR", "; ".join(problems)[:240], True))
