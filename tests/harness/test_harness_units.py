import json
from collections import Counter
from decimal import Decimal

import pytest

from plutosql.analysis.pareto import dominates, pareto_front
from plutosql.harness.store import ContextMismatch, MeasurementStore
from plutosql.harness.validate import Outcome, compare
from plutosql.harness.workload import Invocation


# ---------------------------------------------------------------- doğrulama

def outcome(rows, error=None, outs=None):
    return Outcome({"T": Counter(rows)}, outs or {}, error)


def test_identical_outcomes_have_no_differences():
    a = outcome({(1, "x"): 2, (2, None): 1})
    assert compare(a, outcome({(1, "x"): 2, (2, None): 1})) == []


def test_multiset_difference_is_bidirectional():
    exp = outcome({(1, "x"): 2, (2, None): 1})
    act = outcome({(1, "x"): 1, (3, "y"): 1})            # bir kopya eksik, (2,NULL) eksik, (3,'y') fazla
    (d,) = compare(exp, act)
    assert d.kind == "TABLE" and d.name == "T"
    assert "eksik=2" in d.detail and "fazla=1" in d.detail


def test_row_multiplicity_matters():
    assert compare(outcome({(1,): 1}), outcome({(1,): 2}))


def test_error_and_out_params_compared():
    diffs = compare(outcome({}, error=1403, outs={"P_ROWS": Decimal(5)}),
                    outcome({}, error=None, outs={"P_ROWS": Decimal(4)}))
    assert {d.kind for d in diffs} == {"ERROR", "OUT"}


# ---------------------------------------------------------------- depo

def test_store_roundtrip_and_context_guard(tmp_path):
    path = tmp_path / "m.jsonl"
    s = MeasurementStore(path, {"workload": "w", "n": 1})
    s.put({"digest": "abc", "f1": 1.0})
    assert MeasurementStore(path, {"n": 1, "workload": "w"}).get("abc")["f1"] == 1.0
    with pytest.raises(ContextMismatch):
        MeasurementStore(path, {"workload": "w", "n": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"context": {"n": 1, "workload": "w"}}


# ---------------------------------------------------------------- pareto

def test_dominance():
    assert dominates((1, 1), (1, 2))
    assert not dominates((1, 2), (1, 2))
    assert not dominates((1, 3), (2, 1))


def test_pareto_front():
    pts = [(5, 1), (1, 5), (3, 3), (4, 4), (3, 3), (6, 6)]
    assert pareto_front(pts, key=lambda p: p) == [(1, 5), (3, 3), (3, 3), (5, 1)]


def test_invocation_call_text():
    inv = Invocation("pluto_enrich", {"p_status": "NEW"}, {"p_rows": int})
    assert inv.call_text() == "BEGIN pluto_enrich(p_status => :p_status, p_rows => :p_rows); END;"


def test_store_retries_transient_permission_errors(tmp_path, monkeypatch):
    import pathlib

    s = MeasurementStore(tmp_path / "m.jsonl", {"c": 1})
    real_open, calls = pathlib.Path.open, {"n": 0}

    def flaky(self, *a, **k):
        if self.name == "m.jsonl" and calls["n"] < 2:
            calls["n"] += 1
            raise PermissionError("kilitli")
        return real_open(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", flaky)
    monkeypatch.setattr("plutosql.harness.store.time.sleep", lambda s: None)
    s.put({"digest": "x", "f1": 1})
    assert calls["n"] == 2
    monkeypatch.setattr(pathlib.Path, "open", real_open)
    assert MeasurementStore(tmp_path / "m.jsonl", {"c": 1}).get("x")["f1"] == 1
