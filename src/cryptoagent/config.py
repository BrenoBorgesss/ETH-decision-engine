"""Leitura e validação da configuração (config/config.yaml) e dos segredos (.env).

Segredos nunca ficam no YAML: vêm só de variáveis de ambiente e são guardados como
SecretStr, que não aparece em print, repr nem nos logs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from cryptoagent.timeutils import INTERVAL_MS, parse_hhmm

CONFIG_RELATIVE = Path("config") / "config.yaml"
# Únicas variáveis de ambiente tratadas como segredo. Só este módulo as lê.
SECRET_ENV_VARS = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


def _check_interval(value: str) -> str:
    if value not in INTERVAL_MS:
        raise ValueError(f"intervalo inválido: {value!r} (use um de {', '.join(INTERVAL_MS)})")
    return value


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")  # campo digitado errado = erro claro


class Mercado(_Section):
    simbolo_analise: str = "ETHUSDT"
    pares_candidatos: list[str] = Field(default_factory=lambda: ["ETHUSDT", "ETHBRL"])
    par_operacao: str | None = None
    par_cambio: str = "USDTBRL"
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m", "15m", "1h", "4h", "1d"])

    @field_validator("simbolo_analise", "par_cambio", "par_operacao")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("pares_candidatos")
    @classmethod
    def _upper_list(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]

    @field_validator("timeframes")
    @classmethod
    def _timeframes(cls, v: list[str]) -> list[str]:
        return [_check_interval(tf) for tf in v]

    @model_validator(mode="after")
    def _par_operacao_valido(self) -> Mercado:
        if self.par_operacao is not None and self.par_operacao not in self.pares_candidatos:
            raise ValueError(f"par_operacao {self.par_operacao} não está em pares_candidatos {self.pares_candidatos}")
        return self

    @property
    def simbolos_monitorados(self) -> list[str]:
        """Análise + pares candidatos + câmbio, sem repetição."""
        return list(dict.fromkeys([self.simbolo_analise, *self.pares_candidatos, self.par_cambio]))


class Fuso(_Section):
    local: str = "America/Sao_Paulo"
    inicio_dia_local: str = "00:00"

    @field_validator("local")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError(f"fuso desconhecido: {v!r} (no Windows, instale o pacote tzdata)") from None
        return v

    @field_validator("inicio_dia_local")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        parse_hhmm(v)
        return v


class Binance(_Section):
    rest_url: str = "https://api.binance.com"
    futures_url: str = "https://fapi.binance.com"
    ws_url: str = "wss://stream.binance.com:9443"
    dados_historicos_url: str = "https://data.binance.vision"
    timeout_s: float = Field(10, gt=0)
    max_tentativas: int = Field(4, ge=1)
    limite_peso_por_minuto: int = Field(6000, gt=0)
    pausar_acima_de_pct_peso: float = Field(80, gt=0, le=100)
    recv_window_ms: int = Field(5000, gt=0, le=60000)
    # Quantos candles pedir por timeframe na análise ao vivo (a Binance aceita no máximo 1000).
    candles_por_consulta: dict[str, int] = Field(
        default_factory=lambda: {"1m": 500, "5m": 1000, "15m": 1000, "1h": 1000, "4h": 1000, "1d": 500}
    )
    candles_por_consulta_padrao: int = Field(1000, ge=50, le=1000)

    @field_validator("candles_por_consulta")
    @classmethod
    def _candles(cls, v: dict[str, int]) -> dict[str, int]:
        for tf, n in v.items():
            _check_interval(tf)
            if not 50 <= n <= 1000:
                raise ValueError(f"candles_por_consulta[{tf}] precisa estar entre 50 e 1000 (recebido {n})")
        return v


class SaudeDados(_Section):
    preco_max_s: float = Field(5, gt=0)
    order_book_max_s: float = Field(5, gt=0)
    candle_tolerancia_s: float = Field(30, ge=0)
    derivativos_max_s: float = Field(120, gt=0)
    desvio_relogio_max_ms: float = Field(1000, gt=0)
    divergencia_ws_rest_max_pct: float = Field(0.15, gt=0)


class Historico(_Section):
    pasta: str = "data/history"
    meses: int = Field(13, ge=1, le=120)
    downloads_simultaneos: int = Field(4, ge=1, le=16)
    timeout_download_s: float = Field(60, gt=0)
    tentativas_download: int = Field(3, ge=1, le=10)
    espera_entre_tentativas_s: float = Field(1.5, ge=0)
    intervalos: dict[str, list[str]] = Field(
        default_factory=lambda: {"ETHUSDT": ["1m", "5m", "15m", "1h", "4h", "1d"]}
    )

    @field_validator("intervalos")
    @classmethod
    def _intervalos(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        return {sym.upper(): [_check_interval(i) for i in ivs] for sym, ivs in v.items()}


class Custos(_Section):
    banco: str = "data/costs.sqlite"
    amostra_a_cada_s: float = Field(5, ge=1)
    snapshot_completo_a_cada_s: float = Field(300, ge=10)
    tamanhos_ordem_brl: list[float] = Field(default_factory=lambda: [500, 1000, 2500, 5000])
    taxa_padrao_maker: float = Field(0.001, ge=0, lt=0.05)
    taxa_padrao_taker: float = Field(0.001, ge=0, lt=0.05)
    spread_max_pct: float = Field(0.15, gt=0)
    dias_minimos_para_conclusao: int = Field(7, ge=1)

    @field_validator("tamanhos_ordem_brl")
    @classmethod
    def _positivos(cls, v: list[float]) -> list[float]:
        if not v or any(x <= 0 for x in v):
            raise ValueError("tamanhos_ordem_brl precisa ter valores positivos")
        return sorted(v)


class CapitalOperacao(_Section):
    modo: Literal["brl", "eth"] = "brl"
    valor: float = Field(1000, gt=0)


class Conta(_Section):
    modo: Literal["binance", "diario_manual"] = "binance"
    banco: str = "data/agent.sqlite"
    verificar_permissoes_a_cada_min: float = Field(60, ge=1)
    saldo_max_s: float = Field(120, gt=0)


class Risco(_Section):
    divisor_perda_diaria: float = Field(3, ge=1)
    lucro_amplia_limite: bool = False
    aviso_perda_max_pct_capital: float = Field(10, gt=0, le=100)


class Bollinger(_Section):
    periodo: int = Field(20, ge=2)
    desvios: float = Field(2.0, gt=0)


class Indicadores(_Section):
    ema: list[int] = Field(default_factory=lambda: [20, 50, 200])
    inclinacao_ema_barras: int = Field(5, ge=1)
    rsi: int = Field(14, ge=2)
    macd: tuple[int, int, int] = (12, 26, 9)
    stoch_rsi: tuple[int, int, int, int] = (14, 14, 3, 3)
    atr: int = Field(14, ge=1)
    bollinger: Bollinger = Field(default_factory=Bollinger)
    adx: int = Field(14, ge=2)
    volume_relativo: int = Field(20, ge=2)
    fluxo_taker_barras: int = Field(6, ge=2)
    pivo_barras: int = Field(3, ge=1)
    divergencia_max_barras: int = Field(60, ge=5)
    pavio_min_proporcao: float = Field(0.5, gt=0, lt=1)
    sr_tolerancia_atr: float = Field(0.5, gt=0)
    sr_janela_barras: int = Field(300, ge=20)
    sr_max_niveis: int = Field(6, ge=1)
    janela_percentil: int = Field(500, ge=50)

    @model_validator(mode="after")
    def _macd_ordem(self) -> Indicadores:
        if self.macd[0] >= self.macd[1]:
            raise ValueError("macd: a média rápida precisa ser menor que a lenta")
        return self

    @property
    def warmup_bars(self) -> int:
        """Candles mínimos para os indicadores estarem estáveis (EMA longa converge em ~3× o período)."""
        return max(3 * max(self.ema), self.janela_percentil, 2 * self.adx + 10)


class Regime(_Section):
    adx_tendencia: float = Field(25, gt=0, lt=100)
    adx_lateral: float = Field(20, gt=0, lt=100)
    atr_percentil_alta_vol: float = Field(90, gt=50, le=100)

    @model_validator(mode="after")
    def _ordem(self) -> Regime:
        if self.adx_lateral > self.adx_tendencia:
            raise ValueError("regime: adx_lateral precisa ser <= adx_tendencia")
        return self


class Sazonalidade(_Section):
    timeframe: str = "15m"
    meses_treino: int = Field(6, ge=1)
    meses_teste: int = Field(1, ge=1)
    custo_folga: float = Field(1.5, ge=1)
    pct_min_compensa: float = Field(50, gt=0, le=100)
    pct_max_nao_compensa: float = Field(30, ge=0, lt=100)
    desvio_min_volatilidade: float = Field(0.10, gt=0, lt=1)
    consistencia_min_pct: float = Field(75, gt=50, le=100)
    percentil_baixo: float = Field(20, gt=0, lt=50)
    percentil_alto: float = Field(80, gt=50, lt=100)
    dias_comparacao: int = Field(90, ge=14)
    mult_compensa: float = Field(1.0, gt=0, le=1)
    mult_neutro: float = Field(0.85, gt=0, le=1)
    mult_nao_compensa: float = Field(0.6, gt=0, le=1)
    mult_parado_agora: float = Field(0.8, gt=0, le=1)
    spread_padrao_pct: dict[str, float] = Field(default_factory=lambda: {"ETHUSDT": 0.001, "ETHBRL": 0.04})
    relatorio_pasta: str = "data/reports"

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        return _check_interval(v)

    @model_validator(mode="after")
    def _limites(self) -> Sazonalidade:
        if self.pct_max_nao_compensa >= self.pct_min_compensa:
            raise ValueError("sazonalidade: pct_max_nao_compensa precisa ser menor que pct_min_compensa")
        return self


SIGNAL_NAMES = ("vwap", "bollinger", "rsi", "stoch_rsi", "divergencia", "pavio", "fluxo_taker", "suporte", "order_book")


class Estrategia(_Section):
    tf_contexto: str = "4h"
    tf_confirmacao: str = "1h"
    tf_setup: str = "15m"
    tf_gatilho: str = "5m"
    tf_timing: str = "1m"
    sinais_ativos: list[str] = Field(default_factory=lambda: ["vwap", "bollinger", "rsi"])
    pesos: dict[str, float] = Field(
        default_factory=lambda: {
            "vwap": 1.0, "bollinger": 1.0, "rsi": 1.0, "stoch_rsi": 0.5, "divergencia": 0.7,
            "pavio": 0.5, "fluxo_taker": 0.7, "suporte": 0.7, "order_book": 0.5,
        }
    )
    vwap_atr_inicio: float = 1.0
    vwap_atr_maximo: float = 3.0
    pctb_inicio: float = 0.2
    pctb_maximo: float = -0.2
    rsi_inicio: float = 40
    rsi_maximo: float = 20
    bloquear_regimes: list[str] = Field(default_factory=lambda: ["alta_volatilidade", "indefinido"])
    confianca_minima: dict[str, float] = Field(
        default_factory=lambda: {"lateral": 55, "transicao": 60, "tendencia_alta": 50, "tendencia_baixa": 70}
    )
    gatilho_janela_5m: int = Field(3, ge=1)
    gatilho_fundo_mais_alto_1m: bool = True
    adx_queda_forte_1h: float = Field(30, gt=0)
    adx_vies_baixa_1h: float = Field(20, gt=0)
    confirmacao_ambos_favoraveis: float = Field(1.0, gt=0, le=1)
    confirmacao_um_neutro: float = Field(0.85, gt=0, le=1)
    confirmacao_um_contra: float = Field(0.65, gt=0, le=1)
    stop_fundo_barras_5m: int = Field(12, ge=2)
    stop_atr_5m: float = Field(0.5, ge=0)
    stop_min_atr_5m: float = Field(0.5, gt=0)
    stop_posicao_sem_sinal_atr_5m: float = Field(2.0, gt=0)
    rr_minimo: float = Field(1.0, gt=0)
    distancia_alvo_min_custo: float = Field(2.5, ge=0)
    tempo_max_min: float = Field(180, gt=0)
    vender_pctb: float = 1.0
    vender_rsi_5m: float = 70
    vender_vwap_atr: float = 2.0
    qualidade_min_dados: float = Field(0.9, gt=0, le=1)
    penalidade_por_aviso_dados: float = Field(0.05, ge=0, le=1)

    @field_validator("tf_contexto", "tf_confirmacao", "tf_setup", "tf_gatilho", "tf_timing")
    @classmethod
    def _tf(cls, v: str) -> str:
        return _check_interval(v)

    @field_validator("sinais_ativos")
    @classmethod
    def _sinais(cls, v: list[str]) -> list[str]:
        unknown = [s for s in v if s not in SIGNAL_NAMES]
        if unknown:
            raise ValueError(f"sinais desconhecidos: {unknown} (use {', '.join(SIGNAL_NAMES)})")
        if not v:
            raise ValueError("sinais_ativos não pode ficar vazio")
        return v

    @model_validator(mode="after")
    def _coerente(self) -> Estrategia:
        missing = [s for s in self.sinais_ativos if s not in self.pesos]
        if missing:
            raise ValueError(f"faltam pesos para: {missing}")
        if not (self.vwap_atr_maximo > self.vwap_atr_inicio and self.pctb_inicio > self.pctb_maximo and self.rsi_inicio > self.rsi_maximo):
            raise ValueError("estrategia: as rampas precisam ir do início ao máximo na direção certa")
        return self

    @property
    def timeframes(self) -> list[str]:
        return list(dict.fromkeys([self.tf_contexto, self.tf_confirmacao, self.tf_setup, self.tf_gatilho, self.tf_timing]))


class Backtest(_Section):
    pares: list[str] = Field(default_factory=lambda: ["ETHUSDT", "ETHBRL"])
    capital: float = Field(1000, gt=0)
    perda_max_dia_pct: float = Field(3, gt=0, lt=100)
    slippage_mercado_pct: float = Field(0.01, ge=0, lt=5)
    slippage_stop_pct: float = Field(0.05, ge=0, lt=5)
    amostras_min_spread_hora: int = Field(50, ge=1)
    meses_treino: int = Field(6, ge=1)
    meses_teste: int = Field(1, ge=1)
    operacoes_min_treino: int = Field(20, ge=1)
    grade: dict[str, list[float]] = Field(
        default_factory=lambda: {"distancia_alvo_min_custo": [1.5, 2.5], "stop_atr_5m": [0.5, 1.0], "tempo_max_min": [120, 240]}
    )
    distancias_alternativas: list[float] = Field(default_factory=lambda: [1.5, 1.0])
    relatorio_pasta: str = "data/reports"

    @field_validator("pares")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.upper() for s in v]

    @field_validator("grade")
    @classmethod
    def _grade(cls, v: dict[str, list[float]]) -> dict[str, list[float]]:
        allowed = set(Estrategia.model_fields)
        unknown = [k for k in v if k not in allowed]
        if unknown:
            raise ValueError(f"grade: parâmetros desconhecidos da estratégia: {unknown}")
        if any(not vals for vals in v.values()):
            raise ValueError("grade: cada parâmetro precisa de pelo menos um valor")
        return v


class Paper(_Section):
    par: str = "ETHBRL"
    capital: float = Field(1000, gt=0)
    perda_max_dia_pct: float = Field(3, gt=0, lt=100)
    variantes: list[str] = Field(default_factory=lambda: ["padrao"])
    atraso_s: float = Field(5, ge=0, le=60)
    horizonte_avaliacao_h: float = Field(12, gt=0)
    operacoes_min_conclusao: int = Field(30, ge=1)
    # Variante de ESTUDO: compra virtualmente mesmo quando só travas de estratégia seguraram
    # (as de segurança continuam valendo). Serve para medir quanto cada trava ajuda ou atrapalha.
    estudo: bool = False

    @field_validator("par")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


class Painel(_Section):
    porta: int = Field(8765, ge=1024, le=65535)
    abrir_navegador: bool = True
    recalcular_ao_fechar: list[str] = Field(default_factory=lambda: ["5m"])
    analise_extra_a_cada_s: float = Field(60, ge=0)
    ws_silencio_max_s: float = Field(30, gt=0)
    candles_grafico: int = Field(300, ge=50, le=1000)
    buffer_candles: int = Field(1100, ge=300, le=5000)
    min_candles_ws: int = Field(300, ge=50)

    @model_validator(mode="after")
    def _buffer_coerente(self) -> Painel:
        if self.min_candles_ws > self.buffer_candles:
            raise ValueError("painel: min_candles_ws precisa ser <= buffer_candles")
        return self

    @field_validator("recalcular_ao_fechar")
    @classmethod
    def _tfs(cls, v: list[str]) -> list[str]:
        return [_check_interval(tf) for tf in v]


class Alertas(_Section):
    terminal: bool = True
    som: bool = True
    windows: bool = True


class Logs(_Section):
    pasta: str = "logs"
    nivel: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    formato: Literal["texto", "json"] = "texto"
    tamanho_max_mb: float = Field(5, gt=0, le=1000)
    arquivos_antigos: int = Field(5, ge=0, le=100)


class Config(_Section):
    mercado: Mercado = Field(default_factory=Mercado)
    fuso: Fuso = Field(default_factory=Fuso)
    binance: Binance = Field(default_factory=Binance)
    saude_dados: SaudeDados = Field(default_factory=SaudeDados)
    historico: Historico = Field(default_factory=Historico)
    custos: Custos = Field(default_factory=Custos)
    capital_operacao: CapitalOperacao = Field(default_factory=CapitalOperacao)
    conta: Conta = Field(default_factory=Conta)
    risco: Risco = Field(default_factory=Risco)
    indicadores: Indicadores = Field(default_factory=Indicadores)
    regime: Regime = Field(default_factory=Regime)
    sazonalidade: Sazonalidade = Field(default_factory=Sazonalidade)
    estrategia: Estrategia = Field(default_factory=Estrategia)
    backtest: Backtest = Field(default_factory=Backtest)
    paper: Paper = Field(default_factory=Paper)
    painel: Painel = Field(default_factory=Painel)
    alertas: Alertas = Field(default_factory=Alertas)
    logs: Logs = Field(default_factory=Logs)

    _root: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def root(self) -> Path:
        return self._root

    def path(self, relative: str | Path) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self._root / p


class Secrets(BaseModel):
    model_config = ConfigDict(frozen=True)

    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None

    @property
    def has_binance(self) -> bool:
        return self.binance_api_key is not None and self.binance_api_secret is not None

    def raw_values(self) -> list[str]:
        """Valores reais, usados SÓ para registrar no filtro de logs."""
        return [s.get_secret_value() for s in (self.binance_api_key, self.binance_api_secret) if s]


def find_root(start: Path | None = None) -> Path:
    """Pasta do projeto: CRYPTOAGENT_HOME, ou a primeira pasta (subindo) que tenha config/config.yaml."""
    env = os.environ.get("CRYPTOAGENT_HOME")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_RELATIVE).is_file():
            return folder
    return here


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None = None, overlay: Path | None = None) -> Config:
    """Lê o config. `overlay` (opcional) é um YAML com só os campos que mudam (ex.: uma hipótese de teste)."""
    path = (path or find_root() / CONFIG_RELATIVE).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if overlay is not None:
        if not overlay.is_file():
            raise FileNotFoundError(f"Arquivo de sobreposição não encontrado: {overlay}")
        data = _deep_merge(data, yaml.safe_load(overlay.read_text(encoding="utf-8")) or {})
    cfg = Config.model_validate(data)
    cfg._root = path.parent.parent  # config/config.yaml -> pasta do projeto
    return cfg


def load_secrets(root: Path) -> Secrets:
    """Lê as chaves do `.env` (ou das variáveis de ambiente). Sem chave, devolve Secrets vazio."""
    load_dotenv(root / ".env", override=False)

    def read(name: str) -> SecretStr | None:
        value = os.environ.get(name, "").strip()
        return SecretStr(value) if value else None

    return Secrets(
        binance_api_key=read("BINANCE_API_KEY"),
        binance_api_secret=read("BINANCE_API_SECRET"),
    )
