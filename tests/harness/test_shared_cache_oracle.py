"""Ortak önbelleğin canlı Oracle'da anlamı koruduğunun doğrulanması (veritabanı yoksa atlanır)."""

import pytest

oracledb = pytest.importorskip("oracledb")

from plutosql.db.connection import connect, load_config
from plutosql.harness.evaluator import Evaluator
from plutosql.harness.protocol import Protocol
from plutosql.harness.workload import workload_from_package
from plutosql.transformer import OracleCatalog
from plutosql.workloads.generator import GeneratedSpec, RefSpec, StageSpec, generate

pytestmark = pytest.mark.oracle

TINY = GeneratedSpec(
    name="tst_shared", description="ortak önbellek doğrulaması", n_src=300, n_groups=20,
    refs=(RefSpec(rows=100, spread=0.5, key_type="VARCHAR2"),),
    stages=(StageSpec("INSERT", (0,)), StageSpec("INSERT", (0,), selectivity=40)),
)
# Not: 3 aşamalı / upsert'li 765 konfigürasyonluk sürüm bir kez tamamen doğrulandı (2026-10-18, ~8 dk);
# düzenli test için ortak önbelleği sınayan en küçük yapı kullanılır.


@pytest.fixture(scope="module")
def setup():
    try:
        conn = connect()
    except (oracledb.Error, RuntimeError) as e:
        pytest.skip(f"Oracle erişilemiyor: {e}")
    pkg = generate(TINY)
    pkg.setup(conn)
    yield conn, pkg
    pkg.drop(conn)
    conn.close()


def test_all_configs_with_shared_cache_are_equivalent(setup):
    conn, pkg = setup
    ev = Evaluator(workload_from_package(pkg), conn, load_config(), OracleCatalog(conn),
                   Protocol(warmups=1, repeats=1))
    configs = ev.space.canonical_configs()
    assert len(configs) == TINY.canonical_size() == 9 * 9
    lookups_r1 = [s.id for s in ev.space.sites if s.name.endswith("_R1.K")]
    assert len(lookups_r1) == 2
    shared = [x for x in configs if all(x[i] == "LOOKUP_CACHE" for i in lookups_r1)]
    assert shared                                                  # ortak önbellekli konfigürasyonlar var
    failures = [(x, e.differences or e.error) for x in configs if not (e := ev.evaluate(x)).valid]
    assert not failures, "\n".join(map(str, failures[:5]))
