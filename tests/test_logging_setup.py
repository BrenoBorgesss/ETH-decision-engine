"""Logs: formato texto e JSON (estruturado), rotação vinda do config e segredos sempre mascarados."""

import json
import logging
from logging.handlers import RotatingFileHandler

import pytest
from pydantic import SecretStr, ValidationError

from cryptoagent.config import Config, Logs, Secrets
from cryptoagent.logging_setup import LOG_FILE, JsonRedactingFormatter, build_formatter, setup_logging
from cryptoagent.security.redact import RedactingFormatter

SECRET = "SEGREDO_DE_LOG_0123456789"


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            h.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def _setup(tmp_path, **logs) -> Config:
    cfg = Config(logs=Logs(pasta=str(tmp_path / "logs"), **logs))
    setup_logging(cfg, Secrets(binance_api_key=SecretStr(SECRET), binance_api_secret=SecretStr(SECRET + "_B")))
    return cfg


def _file_handler() -> RotatingFileHandler:
    return next(h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler))


def _read_log(tmp_path) -> str:
    _file_handler().flush()
    return (tmp_path / "logs" / LOG_FILE).read_text(encoding="utf-8")


def test_formato_padrao_e_texto(tmp_path, restore_root_logger):
    _setup(tmp_path)
    logging.getLogger("cryptoagent.teste").info("mensagem simples")
    line = _read_log(tmp_path).strip()
    assert line.endswith("INFO cryptoagent.teste: mensagem simples")
    assert line[:4].isdigit() and "T" in line.split(" ")[0] and line.split(" ")[0].endswith("Z")  # horário UTC ISO
    assert isinstance(_file_handler().formatter, RedactingFormatter)


def test_formato_json_gera_um_objeto_por_linha_com_campos_extras(tmp_path, restore_root_logger):
    _setup(tmp_path, formato="json")
    log = logging.getLogger("cryptoagent.teste")
    log.info("análise concluída em %d ms", 42, extra={"par": "ETHBRL", "latencia_ms": 42})
    log.warning("segunda linha")
    rows = [json.loads(line) for line in _read_log(tmp_path).splitlines()]
    assert len(rows) == 2
    first = rows[0]
    assert first["nivel"] == "INFO" and first["logger"] == "cryptoagent.teste"
    assert first["msg"] == "análise concluída em 42 ms"
    assert first["par"] == "ETHBRL" and first["latencia_ms"] == 42
    assert first["ts"].endswith("Z") and "erro" not in first
    assert rows[1]["nivel"] == "WARNING"


def test_json_mascara_segredos_na_mensagem_no_traceback_e_nos_extras(tmp_path, restore_root_logger):
    _setup(tmp_path, formato="json")
    log = logging.getLogger("cryptoagent.teste")
    try:
        raise RuntimeError(f"falhou usando {SECRET}")
    except RuntimeError:
        log.exception("erro com %s", SECRET, extra={"url": f"https://x/?signature=abc123&k={SECRET}"})
    text = _read_log(tmp_path)
    assert SECRET not in text and "abc123" not in text
    row = json.loads(text.strip())
    assert "RuntimeError" in row["erro"] and "***" in row["msg"] and "***" in row["url"]


def test_rotacao_vem_do_config(tmp_path, restore_root_logger):
    _setup(tmp_path, tamanho_max_mb=2, arquivos_antigos=7)
    handler = _file_handler()
    assert handler.maxBytes == 2_000_000 and handler.backupCount == 7


def test_build_formatter_escolhe_a_classe():
    assert isinstance(build_formatter("json"), JsonRedactingFormatter)
    assert isinstance(build_formatter("texto"), RedactingFormatter)


def test_formato_desconhecido_e_erro_de_configuracao():
    with pytest.raises(ValidationError):
        Logs(formato="xml")
