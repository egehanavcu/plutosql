"""Üreteç ve paket testleri (veritabanı gerekmez)."""

import pytest

from plutosql.db.scripts import split_script
from plutosql.transformer import SearchSpace, find_sites, parse
from plutosql.transformer.sites import P1, P2
from plutosql.workloads.generator import GeneratedSpec, RefSpec, StageSpec, generate, static_catalog
from plutosql.workloads.package import WorkloadPackage, available
from plutosql.workloads.specs import SPECS

SAMPLE = GeneratedSpec(
    name="t_sample", description="test", n_src=100,
    refs=(RefSpec(rows=1000, spread=0.1, pad=20), RefSpec(rows=10, key_type="VARCHAR2")),
    stages=(StageSpec("INSERT", (0, 1)), StageSpec("INSERT", (0,), selectivity=30), StageSpec("UPSERT")),
)


@pytest.mark.parametrize("spec", [SAMPLE, *SPECS.values()], ids=lambda s: s.name)
def test_generated_block_has_expected_space(spec):
    pkg = generate(spec)
    (block,) = split_script(pkg.block)
    analysis = find_sites(parse(pkg.block), static_catalog(spec))
    assert analysis.rejections == [], analysis.rejections
    space = SearchSpace(tuple(analysis.sites))
    assert len(space.canonical_configs()) == spec.canonical_size()
    n_lookups = sum(len(st.lookups) for st in spec.stages)
    assert sum(s.pattern == P2 for s in analysis.sites) == n_lookups
    assert sum(s.pattern == P1 for s in analysis.sites) == len(spec.stages)
    # her P1 sitesinde tüm alternatifler uygulanabilir olmalı (üreteç anlam koruma kurallarına uyar)
    assert all(len(s.alternatives) == 5 for s in analysis.sites if s.pattern == P1)


def test_sample_canonical_size():
    assert SAMPLE.canonical_size() == 17 * 9 * 5


def test_package_roundtrip(tmp_path):
    pkg = generate(SAMPLE)
    pkg.save(tmp_path)
    again = WorkloadPackage.load("t_sample", tmp_path)
    assert again.block == pkg.block and again.mutable == ["T_SAMPLE_T1", "T_SAMPLE_T2", "T_SAMPLE_T3"]
    assert again.seeded == {"T_SAMPLE_T3": "T_SAMPLE_T3_SEED"}
    assert "TRUNCATE TABLE T_SAMPLE_T1" in again.reset_procedure_sql()
    assert "INSERT /*+ APPEND */ INTO T_SAMPLE_T3 SELECT * FROM T_SAMPLE_T3_SEED" in again.reset_procedure_sql()


def test_library_matches_specs():
    """Commit edilmiş paketler specs.py ile güncel olmalı (generate unutulmasın)."""
    for name, spec in SPECS.items():
        assert name in available()
        saved = WorkloadPackage.load(name)
        fresh = generate(spec)
        assert (saved.block, saved.schema_sql, saved.data_sql) == (fresh.block, fresh.schema_sql, fresh.data_sql)


@pytest.mark.parametrize("bad", [
    GeneratedSpec("x", "", 10, (), (StageSpec("UPSERT"), StageSpec("INSERT"))),
    GeneratedSpec("x", "", 10, (RefSpec(5),), (StageSpec("INSERT", (1,)),)),
    GeneratedSpec("x", "", 10, (RefSpec(5, spread=0),), (StageSpec("INSERT", (0,)),)),
])
def test_invalid_specs_rejected(bad):
    with pytest.raises(ValueError):
        generate(bad)
