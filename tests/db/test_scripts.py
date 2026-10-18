from plutosql.db.scripts import bind_names, split_script, sql_resource


def test_split_script_keeps_plsql_semicolons_and_drops_comment_chunks():
    text = """-- başlık
CREATE TABLE t (x NUMBER)
/
-- yalnızca yorum
/
CREATE OR REPLACE PROCEDURE p AS
BEGIN
  NULL;
END;
/
"""
    stmts = split_script(text)
    assert len(stmts) == 2
    assert stmts[0].endswith("CREATE TABLE t (x NUMBER)")
    assert stmts[1].endswith("END;")


def test_bind_names_ignore_strings_comments_and_assignment():
    stmt = "SELECT :a, ':b', x FROM t WHERE y = :A -- :c\n AND z := 1"
    assert bind_names(stmt) == {"a"}


def test_packaged_scripts_split():
    schema = split_script(sql_resource("schema.sql"))
    assert any("CREATE OR REPLACE PROCEDURE pluto_reset" in s for s in schema)
    assert all(not s.rstrip().endswith("/") for s in schema)
    data = split_script(sql_resource("data.sql"))
    assert set().union(*(bind_names(s) for s in data)) == {"n_txn", "n_accounts", "n_days"}


def test_schema_matches_test_catalog():
    """schema.sql ile testlerdeki StaticCatalog aynı kolonları tanımlamalı."""
    import re
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[1] / "transformer"))
    from conftest import PIPELINE_TABLES

    for stmt in split_script(sql_resource("schema.sql")):
        m = re.match(r"(?:--[^\n]*\n)*\s*CREATE TABLE (\w+) \(", stmt, re.IGNORECASE)
        if not m:
            continue
        table = m.group(1).upper()
        cols = {c.upper() for c in re.findall(r"^\s+(\w+)\s+(?:NUMBER|VARCHAR2|DATE)", stmt, re.MULTILINE)}
        assert cols == set(PIPELINE_TABLES[table]), table
