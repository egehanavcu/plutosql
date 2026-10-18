"""Canlı Oracle üzerinde uçtan uca doğrulama (veritabanı yoksa atlanır).

Referans boru hattı küçük ölçekle kurulur; her kanonik konfigürasyon için:
  sıfırla → üretilen bloğu çalıştır → hedef tabloların multiset'i (GROUP BY tüm kolonlar, COUNT(*))
  baseline ile aynı mı?
Bu, harness'taki tam doğrulama protokolünün hafif bir ön sürümüdür.
"""

from collections import Counter
from pathlib import Path

import pytest

oracledb = pytest.importorskip("oracledb")

from plutosql.db import pipeline
from plutosql.db.connection import connect
from plutosql.db.scripts import split_script
from plutosql.transformer import OracleCatalog, SearchSpace, find_sites, parse, rewrite

FIXTURE = Path(__file__).parents[1] / "transformer" / "fixtures" / "pipeline_baseline.sql"

pytestmark = pytest.mark.oracle


@pytest.fixture(scope="module")
def conn():
    try:
        c = connect()
    except (oracledb.Error, RuntimeError) as e:
        pytest.skip(f"Oracle erişilemiyor: {e}")
    pipeline.setup(c, pipeline.Scale(n_txn=3_000, n_accounts=80, n_days=10))
    yield c
    # Deney veritabanını varsayılan ölçekte bırak.
    pipeline.setup(c, pipeline.Scale())
    c.close()


def multiset(conn, table: str) -> Counter:
    with conn.cursor() as cur:
        cols = [r[0] for r in cur.execute(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :t ORDER BY column_id", t=table)]
        col_list = ", ".join(cols)
        rows = cur.execute(f"SELECT {col_list}, COUNT(*) FROM {table} GROUP BY {col_list}").fetchall()
    return Counter({tuple(r[:-1]): r[-1] for r in rows})


def run_block(conn, code: str) -> dict[str, Counter]:
    (block,) = split_script(code)
    pipeline.reset(conn)
    with conn.cursor() as cur:
        cur.execute("ALTER SESSION SET PLSQL_OPTIMIZE_LEVEL = 2")
        cur.execute(block)
    return {t: multiset(conn, t) for t in pipeline.TARGET_TABLES}


def test_reset_restores_initial_state(conn):
    pipeline.reset(conn)
    counts = pipeline.row_counts(conn)
    assert counts["STG_TXN"] == counts["STG_TXN_SEED"] == 3_000
    assert counts["TXN_ENRICHED"] == 0
    assert counts["SETTLEMENT"] == counts["SETTLEMENT_SEED"] > 0


def test_all_canonical_configs_match_baseline(conn):
    catalog = OracleCatalog(conn)
    analysis = find_sites(parse(FIXTURE.read_text(encoding="utf-8")), catalog)
    space = SearchSpace(tuple(analysis.sites))
    assert space.size == 50

    expected = run_block(conn, rewrite(analysis, space.default(), catalog).code)
    assert sum(expected["TXN_ENRICHED"].values()) == 3_000

    failures = []
    for x in space.canonical_configs():
        code = rewrite(analysis, x, catalog).code
        try:
            got = run_block(conn, code)
        except oracledb.Error as e:
            failures.append((x, f"çalışma hatası: {e}"))
            continue
        diff = [t for t in pipeline.TARGET_TABLES if got[t] != expected[t]]
        if diff:
            failures.append((x, f"multiset farkı: {diff}"))
    assert not failures, "\n".join(f"{x}: {msg}" for x, msg in failures)


@pytest.mark.parametrize("bad_currency", ["XXX", None], ids=["unknown", "null"])
def test_missing_lookup_key_raises_same_error_everywhere(conn, bad_currency):
    """Kur tablosunda karşılığı olmayan (ya da NULL) para birimi: orijinal SELECT INTO NO_DATA_FOUND
    fırlatır, blok ROLLBACK + RAISE yapar. Her konfigürasyon aynı hatayı vermeli ve tablolar
    sıfırlanmış durumda kalmalı."""
    catalog = OracleCatalog(conn)
    analysis = find_sites(parse(FIXTURE.read_text(encoding="utf-8")), catalog)
    space = SearchSpace(tuple(analysis.sites))

    pipeline.reset(conn)
    initial = {t: multiset(conn, t) for t in pipeline.TARGET_TABLES}

    failures = []
    for x in space.canonical_configs():
        (block,) = split_script(rewrite(analysis, x, catalog).code)
        pipeline.reset(conn)
        with conn.cursor() as cur:
            # Ortadaki bir satırı boz: hatadan önce bazı satırlar işlenmiş olur.
            cur.execute("UPDATE stg_txn SET currency = :c WHERE txn_id = 1500", c=bad_currency)
            conn.commit()
            initial_bad = {t: multiset(conn, t) for t in pipeline.TARGET_TABLES}
            try:
                cur.execute(block)
                failures.append((x, "hata bekleniyordu"))
                continue
            except oracledb.DatabaseError as e:
                code = e.args[0].code
            if code != 1403:
                failures.append((x, f"ORA-{code:05d} (beklenen ORA-01403)"))
            elif {t: multiset(conn, t) for t in pipeline.TARGET_TABLES} != initial_bad:
                failures.append((x, "geri alma sonrası tablolar farklı"))
    assert initial["TXN_ENRICHED"] == Counter()
    assert not failures, "\n".join(f"{x}: {msg}" for x, msg in failures)
