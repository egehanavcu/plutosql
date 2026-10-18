"""H2 kuplaj analizi — sentetik uzay (conftest) üzerinde bilinen yapılarla."""

import pytest

from plutosql.analysis.coupling import additivity


def with_objective(records, fn):
    out = []
    for r in records.values():
        f1 = fn(r["config"])
        out.append({**r, "f1": f1, "f2": 1.0,
                    "trials": [{"consistent_gets": f1, "delta_pga": 1.0}] * 3})
    return out


def test_additive_objective_has_no_interaction(space, records):
    # conftest'teki amaçlar toplamsal: R² = 1, hiçbir çift kazanç getirmez.
    rep = additivity(space, list(records.values()), "f1", "raw")
    assert rep.r2_additive == pytest.approx(1.0)
    assert max(rep.pair_gains.values()) == pytest.approx(0.0, abs=1e-9)
    assert rep.noise_share > 0                               # conftest denemeleri farklı (f1, f1+2)


def test_interaction_is_attributed_to_the_right_pair(space, records):
    def f(c):
        base = {"ROW": 100, "BULK_50": 60, "SET_BASED": 20}[c["s1"]] + {"ROW": 80, "BULK_50": 50, "SET_BASED": 30}[c["s3"]]
        return base + (500 if c["s1"] == "BULK_50" and c["s3"] == "BULK_50" else 0)   # yalnızca s1×s3 etkileşimi
    rep = additivity(space, with_objective(records, f), "f1", "raw")
    assert rep.interaction_share > 0.1
    best = max(rep.pair_gains, key=rep.pair_gains.get)
    assert best == ("s1", "s3")
    assert rep.pair_gains[("s1", "s3")] == pytest.approx(rep.interaction_share)
    assert rep.noise_share == 0.0


def test_multiplicative_effects_are_additive_on_log_scale(space, records):
    def f(c):
        return {"ROW": 10, "BULK_50": 4, "SET_BASED": 2}[c["s1"]] * {"ROW": 8, "BULK_50": 3, "SET_BASED": 1}[c["s3"]]
    raw = additivity(space, with_objective(records, f), "f1", "raw")
    log = additivity(space, with_objective(records, f), "f1", "log")
    assert raw.interaction_share > 0.01
    assert log.interaction_share == pytest.approx(0.0, abs=1e-9)
