"""Modelo desafiador: conjunto de dados sem vazamento, treino reprodutível, arquivo salvo e avaliação walk-forward."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptoagent.backtest.costs import CostSchedule
from cryptoagent.backtest.data import load_backtest_data
from cryptoagent.config import DEMO_RELATIVE, load_config
from cryptoagent.model.dataset import build_dataset, simulate_outcome
from cryptoagent.model.features import FEATURES
from cryptoagent.model.logistic import LogisticModel, train
from cryptoagent.model.walkforward import evaluate

ROOT = Path(__file__).resolve().parents[1]
COSTS = CostSchedule("ETHBRL", 0.001, {h: 0.04 for h in range(24)}, [], 0.01, 0.05, "teste")


@pytest.fixture(scope="module")
def demo():
    cfg = load_config(ROOT / "config" / "config.yaml", ROOT / DEMO_RELATIVE)
    cfg.modelo.exemplos_min_treino = 20  # a amostra do repositório tem só um mês de treino
    data = load_backtest_data(cfg, "ETHBRL")
    return cfg, data, build_dataset(cfg, data, COSTS)


def test_conjunto_de_dados_tem_as_variaveis_e_rotulos_coerentes(demo):
    _, _, ds = demo
    assert len(ds) > 50 and list(ds.columns[1 : 1 + len(FEATURES)]) == list(FEATURES)
    assert ds["t_ms"].is_monotonic_increasing
    assert (ds["rotulo"] == (ds["pnl_pct"] > 0).astype(int)).all()
    assert (ds["fim_ms"] > ds["t_ms"]).all()  # o resultado só é conhecido depois da decisão
    assert set(ds["motivo"]) <= {"stop", "alvo1", "tempo"}
    assert ds[list(FEATURES)].notna().all().all()


def test_resultado_do_rotulo_usa_os_custos(demo):
    cfg, data, ds = demo
    row = ds.iloc[0]
    free = CostSchedule("ETHBRL", 0.0, {h: 0.0 for h in range(24)}, [], 0.0, 0.0, "x")
    # mesmo momento, com e sem custos: o resultado com custos é pior
    i = int(np.searchsorted(data.close_times["5m"], int(row["t_ms"]) - 1, side="left"))
    hour = int(data.decision_hours[i])
    stop, target = 0.97 * data.t_close[0], 1.03 * data.t_close[0]
    a = simulate_outcome(data, COSTS, int(row["t_ms"]), stop, target, 1.0, hour, 180)
    b = simulate_outcome(data, free, int(row["t_ms"]), stop, target, 1.0, hour, 180)
    assert a.reason == b.reason and a.pnl_pct < b.pnl_pct


def test_treino_e_reprodutivel_e_o_arquivo_salvo_preve_igual_ao_scikit_learn(demo, tmp_path):
    from sklearn.linear_model import LogisticRegression

    _, _, ds = demo
    m1 = train(ds, FEATURES, seed=42, regularization_c=0.5)
    m2 = train(ds, FEATURES, seed=42, regularization_c=0.5)
    assert m1.coef == m2.coef and m1.intercept == m2.intercept
    path = tmp_path / "m.json"
    m1.save(path)
    loaded = LogisticModel.load(path)
    assert loaded == m1
    X = ds[list(FEATURES)].to_numpy(dtype=float)
    ref = LogisticRegression(C=0.5, max_iter=2000, random_state=42).fit((X - np.array(m1.mean)) / np.array(m1.scale), ds["rotulo"])
    expected = ref.predict_proba((X - np.array(m1.mean)) / np.array(m1.scale))[:, 1]
    assert np.allclose(loaded.predict_proba(ds), expected, atol=1e-12)
    one = ds.iloc[3][list(FEATURES)].to_dict()
    assert loaded.predict_one(one) == pytest.approx(expected[3])
    m2.save(tmp_path / "m2.json")
    assert path.read_bytes() == (tmp_path / "m2.json").read_bytes()  # mesmo treino, arquivo idêntico byte a byte


def test_arquivo_de_modelo_desconhecido_e_recusado(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"versao": 99, "tipo": "regressao_logistica"}', encoding="utf-8")
    with pytest.raises(ValueError):
        LogisticModel.load(p)


def test_valor_ausente_vira_a_media_do_treino(demo):
    _, _, ds = demo
    m = train(ds, FEATURES, seed=42, regularization_c=0.5)
    x = {f: m.mean[i] for i, f in enumerate(FEATURES)}
    assert m.predict_one({}) == pytest.approx(m.predict_one(x))


def test_walk_forward_treina_so_com_resultados_conhecidos_antes_do_teste(demo, monkeypatch):
    cfg, data, ds = demo
    from cryptoagent.model import walkforward

    seen = []
    real_train = walkforward.train

    def spy(df, *a, **kw):
        seen.append(df)
        return real_train(df, *a, **kw)

    monkeypatch.setattr(walkforward, "train", spy)
    rep = evaluate(cfg, data, COSTS, ds)
    trained = [o for o in rep.folds if o.model is not None]
    assert trained and len(seen) == len(trained)
    for o, df in zip(trained, seen):
        assert (df["fim_ms"] < o.fold.test_start_ms).all()  # nenhum rótulo do futuro
        assert (df["t_ms"] >= o.fold.train_start_ms).all()
    assert rep.rules["operacoes"] >= 0 and "retorno_total_pct" in rep.challenger
    assert rep.buy_hold["retorno_total_pct"] is not None


def test_mes_com_treino_pequeno_fica_sem_modelo_e_sem_operacoes(demo):
    cfg, data, ds = demo
    strict = cfg.model_copy(deep=True)
    strict.modelo.exemplos_min_treino = 100_000
    rep = evaluate(strict, data, COSTS, ds)
    assert all(o.model is None and "sem modelo" in o.note for o in rep.folds)
    assert rep.challenger["operacoes"] == 0 and rep.auc_oos is None
