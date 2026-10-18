from pathlib import Path

import pytest

from plutosql.transformer import parse
from plutosql.transformer.sites import (
    BULK_ALTS, LOOKUP_CACHE, LOOKUP_SQL, P1, P2, ROW, SET_BASED, find_sites,
)
from plutosql.transformer.space import InvalidConfig, SearchSpace

FIXTURES = Path(__file__).parent / "fixtures"

DECLS = """
  CURSOR c IS SELECT id, k, amount FROM src WHERE flag = 'N';
  r      c%ROWTYPE;
  v      NUMBER;
  n      PLS_INTEGER := 0;
"""


def analyze(source: str):
    return find_sites(parse(source))


def loop_block(body: str, after: str = "", decls: str = DECLS) -> str:
    return f"""DECLARE{decls}BEGIN
  OPEN c;
  LOOP
    FETCH c INTO r;
    EXIT WHEN c%NOTFOUND;
{body}
  END LOOP;
  CLOSE c;
{after}
END;
"""


def only(analysis, pattern):
    sites = [s for s in analysis.sites if s.pattern == pattern]
    assert len(sites) == 1, sites
    return sites[0]


def rejection_reasons(analysis, pattern):
    return [r.reason for r in analysis.rejections if r.pattern == pattern]


# ---------------------------------------------------------------- referans boru hattı

@pytest.fixture(scope="module")
def baseline():
    return analyze((FIXTURES / "pipeline_baseline.sql").read_text(encoding="utf-8"))


def test_baseline_sites(baseline):
    s1, s2, s3 = baseline.sites
    assert [s.id for s in baseline.sites] == ["s1", "s2", "s3"]
    assert (s1.pattern, s2.pattern, s3.pattern) == (P1, P2, P1)
    assert s1.alternatives == (ROW, *BULK_ALTS, SET_BASED)
    assert s3.alternatives == (ROW, *BULK_ALTS, SET_BASED)
    assert s2.alternatives == (LOOKUP_SQL, LOOKUP_CACHE)
    assert (s1.parent, s2.parent, s3.parent) == (None, "s1", None)
    assert baseline.rejections == []


def test_baseline_row_loop_info(baseline):
    s1, _, s3 = baseline.sites
    assert s1.info.cursor == "C_STG" and s1.info.record == "R_STG"
    assert s1.info.shape == "DML"
    assert s1.info.counters == ("L_PROCESSED",)
    assert s1.info.row_locals == ("L_RATE",)
    assert s1.info.dml.insert_columns == ("TXN_ID", "ACCOUNT_ID", "TXN_DATE", "AMOUNT_TRY")
    assert s1.info.dml.insert_values[-1] == "r_stg.amount * l_rate"

    assert s3.info.shape == "UPSERT"
    assert s3.info.dml.table.alias == "S"
    assert [(e.column, e.expr) for e in s3.info.dml.where] == [
        ("ACCOUNT_ID", "r_enr.account_id"), ("SETTLE_DATE", "r_enr.settle_date")]
    assert s3.info.dml.set_pairs == (("TOTAL_AMOUNT", "s.total_amount + r_enr.total"),)
    assert s3.info.upsert_insert.table.name == "SETTLEMENT"


def test_baseline_lookup_info(baseline):
    info = baseline.sites[1].info
    assert info.table.name == "REF_FX_RATE"
    assert (info.value_columns, info.into_targets) == (("RATE",), ("L_RATE",))
    assert (info.key_column, info.key_expr) == ("CURRENCY", "r_stg.currency")


def test_baseline_space_and_canonicalization(baseline):
    space = SearchSpace(tuple(baseline.sites))
    assert space.size == 5 * 2 * 5
    # s1 = SET_BASED iken s2 etkisiz: 5 konfigürasyon çifti tek sınıfa düşer.
    assert len(space.canonical_configs()) == 50 - 5

    x = {"s1": SET_BASED, "s2": LOOKUP_CACHE, "s3": "BULK_500"}
    assert space.canonicalize(x) == {"s1": SET_BASED, "s2": LOOKUP_SQL, "s3": "BULK_500"}
    assert not space.is_canonical(x)
    y = {"s1": "BULK_50", "s2": LOOKUP_CACHE, "s3": ROW}
    assert space.canonicalize(y) == y
    assert space.default() == {"s1": ROW, "s2": LOOKUP_SQL, "s3": ROW}


def test_space_validation(baseline):
    space = SearchSpace(tuple(baseline.sites))
    with pytest.raises(InvalidConfig):
        space.canonicalize({"s1": ROW, "s2": LOOKUP_SQL})
    with pytest.raises(InvalidConfig):
        space.canonicalize({"s1": ROW, "s2": "LOOKUP_MAGIC", "s3": ROW})


def test_describe_is_json_serializable(baseline):
    import json
    d = SearchSpace(tuple(baseline.sites)).describe()
    json.dumps(d)
    assert d["size"] == 50 and d["canonical_size"] == 45
    assert d["sites"][1]["parent"] == "s1"


def test_transformed_code_has_no_sites():
    a = analyze((FIXTURES / "pipeline_transformed.sql").read_text(encoding="utf-8"))
    assert a.sites == []


# ---------------------------------------------------------------- P1 kabul / eleme

def test_update_with_counter_excludes_set_based():
    a = analyze(loop_block("    UPDATE dst SET amount = r.amount WHERE id = r.id;\n    n := n + 1;"))
    s = only(a, P1)
    assert s.alternatives == (ROW, *BULK_ALTS)
    assert "sayaç" in dict(s.excluded)[SET_BASED]


def test_update_without_counter_allows_merge():
    a = analyze(loop_block("    UPDATE dst d SET d.amount = r.amount WHERE d.id = r.id AND d.k = r.k;"))
    s = only(a, P1)
    assert SET_BASED in s.alternatives
    assert [e.column for e in s.info.dml.where] == ["ID", "K"]


def test_update_of_key_column_excludes_set_based():
    a = analyze(loop_block("    UPDATE dst SET id = r.id + 1 WHERE id = r.id;"))
    assert "ORA-38104" in dict(only(a, P1).excluded)[SET_BASED]


def test_delete_excludes_set_based():
    a = analyze(loop_block("    DELETE FROM dst WHERE id = r.id;"))
    s = only(a, P1)
    assert s.alternatives == (ROW, *BULK_ALTS)


def test_insert_select_form_excludes_set_based():
    a = analyze(loop_block("    INSERT INTO dst SELECT * FROM src2 WHERE id = r.id;"))
    assert SET_BASED not in only(a, P1).alternatives


def test_row_local_assignment_in_prelude():
    a = analyze(loop_block("    v := r.amount * 2;\n    INSERT INTO dst (id, amount) VALUES (r.id, v);"))
    s = only(a, P1)
    assert s.info.row_locals == ("V",) and SET_BASED in s.alternatives


def test_lookup_reading_written_table_rejects_loop():
    body = ("    SELECT amount INTO v FROM dst WHERE id = r.id;\n"
            "    INSERT INTO dst (id, amount) VALUES (r.id, v);")
    a = analyze(loop_block(body))
    assert not [s for s in a.sites if s.pattern == P1]
    assert any("uygulanabilir alternatif yok" in r for r in rejection_reasons(a, P1))


@pytest.mark.parametrize("body, after, expected", [
    ("    IF r.amount > 0 THEN INSERT INTO dst (id) VALUES (r.id); END IF;", "", "desteklenmeyen deyim"),
    ("    v := 1;", "", "DML yok"),
    ("    INSERT INTO dst (id) VALUES (r.id);\n    v := r.amount;", "", "DML çekirdeğinden sonra"),
    ("    INSERT INTO dst (id) VALUES (r.id);", "  v := r.amount;", "döngü dışında"),
    ("    n := n + 1;\n    INSERT INTO dst (id, seq) VALUES (r.id, n);", "", "sayaç"),
    ("    INSERT INTO dst (id) VALUES (r.id);\n    v := c%ROWCOUNT;", "", "imleç özniteliği"),
])
def test_p1_rejections(body, after, expected):
    a = analyze(loop_block(body, after))
    assert not [s for s in a.sites if s.pattern == P1]
    assert any(expected in r for r in rejection_reasons(a, P1)), a.rejections


def test_missing_close_rejected():
    src = f"""DECLARE{DECLS}BEGIN
  OPEN c;
  LOOP
    FETCH c INTO r;
    EXIT WHEN c%NOTFOUND;
    INSERT INTO dst (id) VALUES (r.id);
  END LOOP;
END;"""
    assert any("CLOSE" in r for r in rejection_reasons(analyze(src), P1))


def test_fetch_into_scalars_rejected():
    decls = "\n  CURSOR c IS SELECT id FROM src;\n  v NUMBER;\n"
    src = f"""DECLARE{decls}BEGIN
  OPEN c;
  LOOP
    FETCH c INTO v;
    EXIT WHEN c%NOTFOUND;
    INSERT INTO dst (id) VALUES (v);
  END LOOP;
  CLOSE c;
END;"""
    assert any("ROWTYPE" in r for r in rejection_reasons(analyze(src), P1))


def test_cursor_for_loop_reported_with_adr():
    src = "BEGIN FOR r IN (SELECT id FROM src) LOOP INSERT INTO dst (id) VALUES (r.id); END LOOP; END;"
    a = analyze(src)
    assert a.sites == []
    assert any("ADR-001" in r for r in rejection_reasons(a, P1))


# ---------------------------------------------------------------- P2 kabul / eleme

def test_lookup_with_alias_and_reversed_equality():
    body = ("    SELECT f.rate, f.scale INTO v, n FROM ref_fx f WHERE r.k = f.ccy;\n"
            "    INSERT INTO dst (id, amount) VALUES (r.id, r.amount * v);")
    a = analyze(loop_block(body))
    info = only(a, P2).info
    assert info.value_columns == ("RATE", "SCALE") and info.into_targets == ("V", "N")
    assert (info.key_column, info.key_expr) == ("CCY", "r.k")
    assert only(a, P2).parent == only(a, P1).id


def test_lookup_outside_loop_is_not_a_site():
    a = analyze(f"DECLARE{DECLS}BEGIN SELECT rate INTO v FROM ref_fx WHERE ccy = 'TRY'; END;")
    assert a.sites == [] and a.rejections == []


def test_lookup_in_non_p1_loop_has_no_parent():
    src = f"""DECLARE{DECLS}BEGIN
  FOR i IN 1 .. 10 LOOP
    SELECT rate INTO v FROM ref_fx WHERE ccy_id = i;
  END LOOP;
END;"""
    s = only(analyze(src), P2)
    assert s.parent is None


def test_scope_includes_loop_iterators():
    from plutosql.transformer.generated.MiniPlsqlParser import MiniPlsqlParser as Prs
    from plutosql.transformer.sites import find_all, scope_at
    src = """BEGIN
  FOR r IN (SELECT id FROM src) LOOP
    FOR i IN 1 .. 3 LOOP
      FORALL j IN 1 .. 2 DELETE FROM dst WHERE id = j;
    END LOOP;
  END LOOP;
END;"""
    dml = find_all(parse(src).tree, Prs.DeleteStatementContext)[0]
    assert {"R", "I", "J"} <= set(scope_at(dml).variables)


@pytest.mark.parametrize("lookup, expected", [
    ("SELECT rate INTO v FROM ref_fx WHERE ccy = r.k AND d = r.id;", "bileşik anahtar"),
    ("SELECT rate INTO v FROM ref_fx a, ref_b b WHERE a.ccy = r.k;", "tek tablo"),
    ("SELECT rate INTO v FROM ref_fx WHERE ccy = r.k ORDER BY d;", "yan tümce"),
    ("SELECT NVL(rate, 1) INTO v FROM ref_fx WHERE ccy = r.k;", "düz kolon"),
    ("SELECT rate INTO v FROM ref_fx WHERE ccy > r.k;", "kolon = ifade"),
])
def test_p2_rejections(lookup, expected):
    a = analyze(loop_block(f"    {lookup}\n    INSERT INTO dst (id, amount) VALUES (r.id, v);"))
    assert not [s for s in a.sites if s.pattern == P2]
    assert any(expected in r for r in rejection_reasons(a, P2)), a.rejections
    # P2'ye uymayan arama, P1'in küme tabanlı sürümünü engeller ama toplu sürümleri değil.
    s1 = only(a, P1)
    assert SET_BASED not in s1.alternatives and set(BULK_ALTS) <= set(s1.alternatives)


def test_lookup_on_modified_table_rejected():
    a = analyze(loop_block(
        "    SELECT rate INTO v FROM ref_fx WHERE ccy = r.k;\n"
        "    INSERT INTO dst (id, amount) VALUES (r.id, v);",
        after="  UPDATE ref_fx SET rate = rate * 1.1;"))
    assert any("önbellek bayatlayabilir" in r for r in rejection_reasons(a, P2))
