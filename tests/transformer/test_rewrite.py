"""Rewriter testleri.

Golden dosyalar: tests/transformer/golden/<fixture>__<konfig>.sql
Bilinçli bir çıktı değişikliğinden sonra yeniden üretmek için:
    PLUTOSQL_UPDATE_GOLDEN=1 python -m pytest tests/transformer/test_rewrite.py
"""

import os
from pathlib import Path

import pytest

from plutosql.transformer import SearchSpace, find_sites, parse
from plutosql.transformer.catalog import StaticCatalog, cache_key_for, parse_type, Column
from plutosql.transformer.rewrite import RewriteError, rewrite
from plutosql.transformer.sites import P1, P2
from plutosql.workloads import workload_source

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def baseline(pipeline_catalog):
    src = workload_source("pipeline_baseline")
    return find_sites(parse(src), pipeline_catalog)


def test_default_config_is_identity(baseline, pipeline_catalog):
    space = SearchSpace(tuple(baseline.sites))
    assert rewrite(baseline, space.default(), pipeline_catalog).code == baseline.parse.source


def test_every_canonical_config_parses_and_is_unique(baseline, pipeline_catalog):
    space = SearchSpace(tuple(baseline.sites))
    digests = {}
    for x in space.canonical_configs():
        result = rewrite(baseline, x, pipeline_catalog)
        parse(result.code)                                   # üretilen kod gramere uymalı
        digests.setdefault(result.digest, []).append(x)
    assert len(digests) == len(space.canonical_configs()) == 45


def test_equivalent_configs_produce_identical_code(baseline, pipeline_catalog):
    a = rewrite(baseline, {"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "ROW"}, pipeline_catalog)
    b = rewrite(baseline, {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"}, pipeline_catalog)
    assert a.code == b.code and a.digest == b.digest
    assert a.config == b.config == {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"}


def test_transformed_output_has_no_remaining_row_loops(baseline, pipeline_catalog):
    out = rewrite(baseline, {"s1": "BULK_500", "s2": "LOOKUP_CACHE", "s3": "SET_BASED"}, pipeline_catalog)
    again = find_sites(parse(out.code), pipeline_catalog)
    assert [s for s in again.sites if s.pattern == P1] == []


GOLDEN_CASES = {
    "s1-BULK_500__s2-LOOKUP_CACHE__s3-BULK_50": {"s1": "BULK_500", "s2": "LOOKUP_CACHE", "s3": "BULK_50"},
    "s1-SET_BASED__s2-LOOKUP_SQL__s3-SET_BASED": {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "SET_BASED"},
    "s1-ROW__s2-LOOKUP_CACHE__s3-ROW": {"s1": "ROW", "s2": "LOOKUP_CACHE", "s3": "ROW"},
    "s1-BULK_50__s2-LOOKUP_SQL__s3-ROW": {"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"},
}


@pytest.mark.parametrize("name", GOLDEN_CASES)
def test_golden(name, baseline, pipeline_catalog):
    code = rewrite(baseline, GOLDEN_CASES[name], pipeline_catalog).code
    path = GOLDEN / f"pipeline_baseline__{name}.sql"
    if os.environ.get("PLUTOSQL_UPDATE_GOLDEN"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(code, encoding="utf-8", newline="\n")
    assert code == path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- tek tek davranışlar

CATALOG = StaticCatalog({
    "SRC": {"ID": "NUMBER(9)", "K": "NUMBER(9)", "AMOUNT": "NUMBER(18,2)", "FLAG": "CHAR(1)"},
    "DST": {"ID": "NUMBER(9)", "AMOUNT": "NUMBER(18,2)", "V": "NUMBER"},
    "REF_INT": {"ID": "NUMBER(9,0)", "RATE": "NUMBER"},
    "REF_NUM": {"ID": "NUMBER", "RATE": "NUMBER"},
    "REF_CHR": {"ID": "CHAR(3)", "RATE": "NUMBER"},
})

DECLS = """
  CURSOR c IS SELECT id, k, amount FROM src WHERE flag = 'N';
  r      c%ROWTYPE;
  v      NUMBER;
"""


def loop_block(body: str) -> str:
    return f"""DECLARE{DECLS}BEGIN
  OPEN c;
  LOOP
    FETCH c INTO r;
    EXIT WHEN c%NOTFOUND;
{body}
  END LOOP;
  CLOSE c;
END;
"""


def lookup_block(table: str) -> str:
    return loop_block(f"    SELECT rate INTO v FROM {table} WHERE id = r.k;\n"
                      "    INSERT INTO dst (id, amount) VALUES (r.id, r.amount * v);")


def test_cache_key_types():
    int_key = cache_key_for(Column("X", "NUMBER", 22, 9, 0))
    assert int_key.index_by == "PLS_INTEGER"
    num_key = cache_key_for(Column("X", "NUMBER", 22, None, None))
    assert num_key.index_by == "VARCHAR2(64)" and num_key.convert("k") == "TO_CHAR(k)"
    assert cache_key_for(Column("X", "VARCHAR2", 10)).index_by == "VARCHAR2(10)"
    assert cache_key_for(Column("X", "CHAR", 3)) is None
    assert cache_key_for(Column("X", "DATE")) is None


def test_parse_type():
    assert parse_type("NUMBER(9)") == ("NUMBER", 22, 9, 0)
    assert parse_type("number(18, 2)") == ("NUMBER", 22, 18, 2)
    assert parse_type("NUMBER") == ("NUMBER", 22, None, None)
    assert parse_type("VARCHAR2(10 CHAR)") == ("VARCHAR2", 40, None, None)
    assert parse_type("INTEGER") == ("NUMBER", 22, 38, 0)


def test_integer_key_cache_uses_pls_integer():
    a = find_sites(parse(lookup_block("ref_int")), CATALOG)
    code = rewrite(a, {"s1": "ROW", "s2": "LOOKUP_CACHE"}, CATALOG).code
    assert "INDEX BY PLS_INTEGER" in code
    assert "r.k <> TRUNC(r.k)" in code
    parse(code)


def test_numeric_key_cache_uses_canonical_text():
    a = find_sites(parse(lookup_block("ref_num")), CATALOG)
    code = rewrite(a, {"s1": "BULK_50", "s2": "LOOKUP_CACHE"}, CATALOG).code
    assert "INDEX BY VARCHAR2(64)" in code
    assert "px_s2_map.EXISTS(TO_CHAR(px_s1_rows(px_s1_i).k))" in code
    parse(code)


def test_char_key_is_rejected_with_catalog():
    a = find_sites(parse(lookup_block("ref_chr")), CATALOG)
    assert [s for s in a.sites if s.pattern == P2] == []
    assert any("CHAR" in r.reason for r in a.rejections)


def test_bulk_does_not_substitute_target_column_with_same_name():
    # `v` hem satır-yerel değişken hem DST kolonu: UPDATE içinde Oracle onu kolona çözer.
    body = ("    v := r.amount * 2;\n"
            "    UPDATE dst SET amount = v, v = v + 1 WHERE id = r.id;")
    a = find_sites(parse(loop_block(body)), CATALOG)
    code = rewrite(a, {"s1": "BULK_500"}, CATALOG).code
    assert "SET amount = v, v = v + 1 WHERE id = px_s1_rows(px_s1_i).id" in code
    assert "px_s1_v" not in code                      # v DML'de değer olarak okunmuyor
    parse(code)


def test_bulk_copies_row_locals_read_by_insert():
    body = ("    v := r.amount * 2;\n"
            "    INSERT INTO dst (id, v) VALUES (r.id, v);")
    a = find_sites(parse(loop_block(body)), CATALOG)
    code = rewrite(a, {"s1": "BULK_50"}, CATALOG).code
    assert "INSERT INTO dst (id, v) VALUES (px_s1_rows(px_s1_i).id, px_s1_v(px_s1_i));" in code
    assert "px_s1_v(px_s1_i) := v;" in code


def test_set_based_insert_inlines_row_locals():
    body = ("    v := r.amount * 2;\n"
            "    INSERT INTO dst (id, amount) VALUES (r.id, v + 1);")
    a = find_sites(parse(loop_block(body)), CATALOG)
    code = rewrite(a, {"s1": "SET_BASED"}, CATALOG).code
    assert "INSERT INTO dst (id, amount)\n  SELECT px_src.id,\n         (px_src.amount * 2) + 1\n" in code
    assert "px_s1_miss" not in code                   # arama yok → ön denetim yok
    parse(code)


def test_set_based_update_qualifies_target_columns():
    body = "    UPDATE dst SET amount = amount + r.amount WHERE id = r.id;"
    a = find_sites(parse(loop_block(body)), CATALOG)
    code = rewrite(a, {"s1": "SET_BASED"}, CATALOG).code
    assert "MERGE INTO dst\n" in code
    assert "ON (dst.id = px_src.id)" in code
    assert "UPDATE SET dst.amount = dst.amount + px_src.amount;" in code
    parse(code)


def test_cache_in_non_p1_loop_wraps_labelled_loop():
    src = """DECLARE
  v NUMBER;
BEGIN
  <<outer>>
  FOR i IN 1 .. 10 LOOP
    SELECT rate INTO v FROM ref_int WHERE id = i;
    EXIT outer WHEN v > 5;
  END LOOP outer;
END;
"""
    a = find_sites(parse(src), CATALOG)
    code = rewrite(a, {"s1": "LOOKUP_CACHE"}, CATALOG).code
    assert code.index("DECLARE\n    TYPE px_s1_val_t") < code.index("<<outer>>")
    parse(code)


def test_unknown_table_raises(baseline):
    empty = StaticCatalog({})
    with pytest.raises(RewriteError):
        rewrite(baseline, {"s1": "ROW", "s2": "LOOKUP_CACHE", "s3": "ROW"}, empty)


def test_reserved_prefix_in_source_raises():
    a = find_sites(parse(loop_block("    INSERT INTO dst (id) VALUES (r.id);").replace("v      NUMBER", "px_v NUMBER")), CATALOG)
    with pytest.raises(RewriteError):
        rewrite(a, {s.id: s.default for s in a.sites}, CATALOG)
