"""Harness'ın canlı Oracle üzerindeki davranışı (veritabanı yoksa atlanır)."""

from pathlib import Path

import pytest

oracledb = pytest.importorskip("oracledb")

from plutosql.db import pipeline
from plutosql.db.connection import connect, load_config
from plutosql.harness.evaluator import BudgetExhausted, Evaluator
from plutosql.harness.protocol import Protocol
from plutosql.harness.store import MeasurementStore
from plutosql.harness.workload import Invocation, Workload, pipeline_workload
from plutosql.transformer import OracleCatalog

pytestmark = pytest.mark.oracle

FAST = Protocol(warmups=1, repeats=1)


@pytest.fixture(scope="module")
def admin():
    try:
        c = connect()
    except (oracledb.Error, RuntimeError) as e:
        pytest.skip(f"Oracle erişilemiyor: {e}")
    pipeline.setup(c, pipeline.Scale(n_txn=2_000, n_accounts=60, n_days=10))
    yield c
    with c.cursor() as cur:
        cur.execute("DROP PROCEDURE IF EXISTS pluto_enrich")
    pipeline.setup(c, pipeline.Scale())
    c.close()


def evaluator(admin, workload=None, **kw):
    return Evaluator(workload or pipeline_workload(), admin, load_config(), OracleCatalog(admin), FAST, **kw)


def test_budget_counts_unique_code_only(admin):
    ev = evaluator(admin, budget=2)
    a = ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "ROW"})
    b = ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"})     # aynı kanonik kod
    assert a.digest == b.digest and ev.budget_used == 1
    assert a.valid and a.f1 > 0
    ev.evaluate({"s1": "ROW", "s2": "LOOKUP_SQL", "s3": "ROW"})
    assert ev.budget_used == 2
    ev.evaluate({"s1": "ROW", "s2": "LOOKUP_SQL", "s3": "ROW"})                 # tekrar: bütçe yemez
    with pytest.raises(BudgetExhausted):
        ev.evaluate({"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"})
    assert len(ev.history) == 4


def test_store_reuse_across_runs(admin, tmp_path):
    store = MeasurementStore(tmp_path / "m.jsonl", {"t": 1})
    x = {"s1": "BULK_50", "s2": "LOOKUP_CACHE", "s3": "BULK_50"}
    first = evaluator(admin, store=store).evaluate(x)
    second = evaluator(admin, store=MeasurementStore(tmp_path / "m.jsonl", {"t": 1}), budget=1).evaluate(x)
    assert first.measured_now and not second.measured_now
    assert (first.f1, first.f2, first.valid) == (second.f1, second.f2, second.valid)


def test_semantic_break_is_detected(admin):
    """Referans sonucundan farklı bir sonuç geçersiz sayılmalı (doğrulamanın kendisinin testi)."""
    ev = evaluator(admin)
    ref = ev.reference_outcome()
    ref.tables["TXN_ENRICHED"][("sahte",)] += 1                  # referansı boz
    e = ev.evaluate({"s1": "BULK_500", "s2": "LOOKUP_SQL", "s3": "ROW"})
    assert not e.valid
    assert any("TXN_ENRICHED" in d and "eksik=1" in d for d in e.differences)


def test_procedure_workload_with_out_params(admin):
    source = (Path(__file__).parent / "fixtures" / "proc_enrich.sql").read_text(encoding="utf-8")
    wl = Workload(
        name="proc_enrich",
        source=source,
        target_tables=("TXN_ENRICHED",),
        reset=pipeline.reset,
        invocation=Invocation("pluto_enrich", {"p_status": "NEW"}, {"p_rows": int}),
    )
    ev = evaluator(admin, wl)
    assert ev.space.size == 5 * 2
    ref = ev.reference_outcome()
    assert ref.out_params == {"p_rows": 2_000} and ref.error is None
    for x in ({"s1": "BULK_500", "s2": "LOOKUP_CACHE"}, {"s1": "SET_BASED", "s2": "LOOKUP_SQL"}):
        e = ev.evaluate(x)
        assert e.valid, e.differences
    with admin.cursor() as cur:
        level = cur.execute("SELECT plsql_optimize_level FROM user_plsql_object_settings "
                            "WHERE name = 'PLUTO_ENRICH'").fetchone()[0]
    assert level == 2
