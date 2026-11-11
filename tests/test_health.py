from cryptoagent.data.health import Check, HealthReport, check_age, check_candle, check_clock, check_divergence

NOW = 1_800_000_000_000


def test_idade():
    assert check_age("preço", NOW - 2_000, NOW, 5).ok
    stale = check_age("preço", NOW - 9_000, NOW, 5)
    assert not stale.ok and "velho" in stale.detail
    missing = check_age("preço", None, NOW, 5)
    assert not missing.ok and "ausente" in missing.detail


def test_candle_1m_atrasado():
    assert check_candle("1m", NOW - 50_000, "1m", NOW, 30).ok
    assert not check_candle("1m", NOW - 91_000, "1m", NOW, 30).ok
    assert not check_candle("1m", None, "1m", NOW, 30).ok


def test_candle_4h():
    assert check_candle("4h", NOW - 3 * 3_600_000, "4h", NOW, 30).ok
    assert not check_candle("4h", NOW - 5 * 3_600_000, "4h", NOW, 30).ok


def test_relogio_e_divergencia():
    assert check_clock(300, 1000).ok
    assert not check_clock(-1500, 1000).ok
    assert "adiantado 31.4 s" in check_clock(-31_400, 1000).detail  # Binance atrás do PC = PC adiantado
    assert "atrasado 2.0 s" in check_clock(2_000, 1000).detail
    assert check_divergence("x", 100, 100.1, 0.15).ok
    assert not check_divergence("x", 100, 100.3, 0.15).ok
    assert not check_divergence("x", None, 100, 0.15).ok


def test_relatorio_so_bloqueia_por_problemas_criticos():
    report = HealthReport([Check("a", True, "ok"), Check("derivativos", False, "fora do ar", critical=False)])
    assert report.ok and report.warnings and not report.problems
    report.add(Check("preço", False, "dado velho"))
    assert not report.ok and report.problems == ["preço: dado velho"]
