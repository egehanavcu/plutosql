from pathlib import Path

import pytest
from antlr4.tree.Tree import ParseTree

from plutosql.transformer import PlsqlSyntaxError, parse
from plutosql.transformer.generated.MiniPlsqlParser import MiniPlsqlParser as P
from plutosql.transformer.parsing import token_text_roundtrip
from plutosql.workloads import workload_source

FIXTURES = Path(__file__).parent / "fixtures"
WORKLOADS = Path(__file__).parents[2] / "plutosql" / "workloads" / "sql"
FIXTURE_FILES = sorted(FIXTURES.glob("*.sql")) + [WORKLOADS / "pipeline_baseline.sql"]


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def find_all(node: ParseTree, ctx_type) -> list:
    """Ağaçtaki belirli tipteki tüm düğümler (ön-sıra)."""
    found = []
    if isinstance(node, ctx_type):
        found.append(node)
    for i in range(node.getChildCount()):
        found.extend(find_all(node.getChild(i), ctx_type))
    return found


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_fixture_parses(path: Path):
    parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_token_roundtrip_is_lossless(path: Path):
    source = path.read_text(encoding="utf-8")
    assert token_text_roundtrip(parse(source)) == source


def test_baseline_structure():
    result = parse(workload_source("pipeline_baseline"))
    tree = result.tree

    cursors = [c.identifier().getText() for c in find_all(tree, P.CursorDeclarationContext)]
    assert cursors == ["c_stg", "c_enr"]

    # ADR-001: baseline döngüleri açık imleç kalıbında, cursor FOR değil.
    assert len(find_all(tree, P.BasicLoopStatementContext)) == 2
    assert find_all(tree, P.CursorForLoopStatementContext) == []
    fetches = find_all(tree, P.FetchStatementContext)
    assert [f.identifier().getText() for f in fetches] == ["c_stg", "c_enr"]
    assert all(f.BULK() is None for f in fetches)

    select_into = find_all(tree, P.SelectIntoStatementContext)
    assert len(select_into) == 1
    assert select_into[0].intoTargets().getText() == "l_rate"
    assert "ref_fx_rate" in result.text(select_into[0].sqlTail())

    targets = [d.getText() for d in find_all(tree, P.DmlTargetContext)]
    assert targets == ["txn_enriched", "settlement", "settlement", "stg_txn"]


def test_transformed_forms_structure():
    tree = parse(load("pipeline_transformed.sql")).tree

    fetch = find_all(tree, P.FetchStatementContext)[0]
    assert fetch.BULK() is not None and fetch.expression().getText() == "500"

    forall = find_all(tree, P.ForallStatementContext)[0]
    assert forall.dmlStatement().insertStatement().dmlTarget().getText() == "txn_enriched"

    table_types = find_all(tree, P.TableTypeDefContext)
    assert [t.INDEX() is not None for t in table_types] == [False, True, True]

    assert len(find_all(tree, P.MergeStatementContext)) == 1
    assert len(find_all(tree, P.InlineCursorSourceContext)) == 1


def test_procedure_parameters():
    tree = parse(load("procedure_out_params.sql")).tree
    proc = find_all(tree, P.CreateProcedureContext)[0]
    assert proc.qualifiedName().getText() == "settle_account"

    params = proc.parameterList().parameter()
    modes = [
        ("IN OUT" if p.IN() and p.OUT() else "OUT" if p.OUT() else "IN")
        for p in params
    ]
    assert modes == ["IN", "IN", "OUT", "IN OUT"]
    assert params[3].NOCOPY() is not None
    assert len(find_all(tree, P.FunctionDeclarationContext)) == 1


def test_keywords_are_case_insensitive():
    parse("begin NULL; end;")
    parse("BeGiN nUlL; EnD;")


@pytest.mark.parametrize(
    "source",
    [
        "begin if 1 = 1 then null; end; end;",                  # END IF eksik
        "begin loop null; end; end;",                            # END LOOP eksik
        "declare x number begin null; end;",                     # bildirimde `;` eksik
        "begin open c; fetch c into; end;",                      # INTO hedefi yok
        "begin x := ; end;",                                     # boş ifade
    ],
)
def test_invalid_syntax_raises(source: str):
    with pytest.raises(PlsqlSyntaxError) as exc:
        parse(source)
    assert exc.value.issues
