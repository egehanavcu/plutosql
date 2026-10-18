"""Optimize edilebilir hedef bölgelerin (site) tespiti.

P1 — Satır bazlı DML döngüsü:
    OPEN c; LOOP FETCH c INTO r; EXIT WHEN c%NOTFOUND; <gövde> END LOOP; CLOSE c;
    Gövde: (satır-yerel atama | arama | sayaç)*  çekirdek  (sayaç)*
    Çekirdek: tek DML  ya da  UPDATE + IF SQL%ROWCOUNT = 0 THEN INSERT END IF  (upsert)
    A = {ROW, BULK_50, BULK_500, BULK_5000, SET_BASED}

P2 — Döngü içi referans araması:
    SELECT k1[, k2..] INTO v1[, v2..] FROM ref [a] WHERE anahtar = <ifade>;   (bir döngünün içinde)
    A = {LOOKUP_SQL, LOOKUP_CACHE}

Her alternatif yalnızca anlamı koruyabildiği durumlarda A_i'ye eklenir; elenen alternatifler ve
hiç site olamayan adaylar gerekçeleriyle raporlanır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Union

from antlr4 import InputStream, ParserRuleContext
from antlr4.tree.Tree import ParseTree

from . import islands as isl
from .generated.MiniPlsqlLexer import MiniPlsqlLexer as L
from .generated.MiniPlsqlParser import MiniPlsqlParser as P
from .catalog import Catalog, UnknownTable, cache_key_for
from .islands import Equality, TableRef, norm
from .parsing import ParseResult

P1 = "P1_ROW_DML_LOOP"
P2 = "P2_LOOP_LOOKUP"

ROW = "ROW"
BULK_LIMITS = (50, 500, 5000)
BULK_ALTS = tuple(f"BULK_{n}" for n in BULK_LIMITS)
SET_BASED = "SET_BASED"
LOOKUP_SQL = "LOOKUP_SQL"
LOOKUP_CACHE = "LOOKUP_CACHE"

# Bir ebeveyn sitede bu alternatiflerden biri seçilirse çocuk siteler etkisiz kalır (ADR-003).
ABSORBING_ALTERNATIVES = frozenset({SET_BASED})


# ================================================================ veri modeli

@dataclass(frozen=True)
class DmlInfo:
    kind: str                                         # INSERT | UPDATE | DELETE | MERGE
    table: TableRef
    text: str
    insert_columns: tuple[str, ...] | None = None
    insert_values: tuple[str, ...] | None = None      # ifade metinleri, kolonlarla aynı sırada
    set_pairs: tuple[tuple[str, str], ...] | None = None
    where: tuple[Equality, ...] | None = None         # yalnızca `kolon = ifade` eşitliklerinin birleşimiyse


@dataclass(frozen=True)
class LookupInfo:
    table: TableRef
    value_columns: tuple[str, ...]
    into_targets: tuple[str, ...]
    key_column: str
    key_expr: str


@dataclass(frozen=True)
class PreludeStep:
    kind: str            # ASSIGN | LOOKUP
    target: str          # atanan değişken(ler), virgülle
    text: str


@dataclass(frozen=True)
class RowLoopInfo:
    cursor: str
    cursor_query: str
    record: str
    shape: str                                  # DML | UPSERT
    prelude: tuple[PreludeStep, ...]
    dml: DmlInfo                                # DML çekirdeği ya da upsert'in UPDATE'i
    upsert_insert: DmlInfo | None
    counters: tuple[str, ...]
    row_locals: tuple[str, ...]
    record_fields: tuple[str, ...]


@dataclass(frozen=True)
class Site:
    id: str
    pattern: str
    name: str
    alternatives: tuple[str, ...]
    default: str
    start_token: int
    stop_token: int
    line: int
    info: Union[RowLoopInfo, LookupInfo]
    parent: str | None = None
    excluded: tuple[tuple[str, str], ...] = ()         # (alternatif, gerekçe)
    node: ParserRuleContext | None = field(default=None, compare=False, repr=False)

    def contains(self, other: "Site") -> bool:
        return self.start_token <= other.start_token and other.stop_token <= self.stop_token

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "name": self.name,
            "line": self.line,
            "alternatives": list(self.alternatives),
            "default": self.default,
            "parent": self.parent,
            "excluded": {alt: reason for alt, reason in self.excluded},
        }


@dataclass(frozen=True)
class Rejection:
    pattern: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.pattern} @ satır {self.line}: {self.reason}"


@dataclass
class SiteAnalysis:
    parse: ParseResult
    sites: list[Site]
    rejections: list[Rejection]

    def site(self, site_id: str) -> Site:
        return next(s for s in self.sites if s.id == site_id)


class _Reject(Exception):
    pass


# ================================================================ ağaç yardımcıları

def walk(node: ParseTree):
    yield node
    for i in range(node.getChildCount()):
        yield from walk(node.getChild(i))


def find_all(node: ParseTree, ctx_type) -> list:
    return [n for n in walk(node) if isinstance(n, ctx_type)]


def enclosing(ctx: ParserRuleContext, types) -> ParserRuleContext | None:
    p = ctx.parentCtx
    while p is not None and not isinstance(p, types):
        p = p.parentCtx
    return p


def inner(stmt: P.StatementContext) -> ParserRuleContext:
    return stmt.getChild(0)


def compact_text(ctx: ParserRuleContext) -> str:
    return ctx.getText().upper()


_LOOP_TYPES = (P.BasicLoopStatementContext, P.WhileLoopStatementContext,
               P.NumericForLoopStatementContext, P.CursorForLoopStatementContext)
_ITERATOR_TYPES = (P.NumericForLoopStatementContext, P.CursorForLoopStatementContext,
                   P.ForallStatementContext)
_SCOPE_TYPES = (P.AnonymousBlockContext, P.BlockContext, P.CreateProcedureContext,
                P.ProcedureDeclarationContext, P.FunctionDeclarationContext)


@dataclass
class Scope:
    variables: dict[str, ParserRuleContext]
    cursors: dict[str, P.CursorDeclarationContext]
    subprograms: set[str]


def scope_at(ctx: ParserRuleContext) -> Scope:
    """`ctx` noktasında görünen bildirimler (içteki kapsam dıştakini gölgeler)."""
    scope = Scope({}, {}, set())
    p = enclosing(ctx, _SCOPE_TYPES + _ITERATOR_TYPES)
    while p is not None:
        if isinstance(p, _ITERATOR_TYPES):
            # FOR i IN ..., FOR r IN (cursor), FORALL i IN ...: örtük döngü değişkeni
            ident = p.identifier() if isinstance(p, P.ForallStatementContext) else p.identifier(0)
            scope.variables.setdefault(norm(ident.getText()), p)
            p = enclosing(p, _SCOPE_TYPES + _ITERATOR_TYPES)
            continue
        params = p.parameterList() if hasattr(p, "parameterList") else None
        if params is not None:
            for prm in params.parameter():
                scope.variables.setdefault(norm(prm.identifier().getText()), prm)
        decls = p.declareSection()
        if decls is not None:
            for d in decls.declaration():
                if d.variableDeclaration():
                    v = d.variableDeclaration()
                    scope.variables.setdefault(norm(v.identifier().getText()), v)
                elif d.cursorDeclaration():
                    c = d.cursorDeclaration()
                    scope.cursors.setdefault(norm(c.identifier().getText()), c)
                elif d.procedureDeclaration():
                    scope.subprograms.add(norm(d.procedureDeclaration().identifier().getText()))
                elif d.functionDeclaration():
                    scope.subprograms.add(norm(d.functionDeclaration().identifier().getText()))
        p = enclosing(p, _SCOPE_TYPES + _ITERATOR_TYPES)
    return scope


# ================================================================ SQL ada ayrıştırma

class _Ctx:
    """Analiz sırasında paylaşılan durum."""

    def __init__(self, result: ParseResult):
        self.result = result
        self.stream = result.tokens
        self.dml_targets = {
            norm_table(t) for t in find_all(result.tree, P.DmlTargetContext)
        }

    def toks(self, ctx) -> list:
        return isl.code_tokens(self.stream, ctx)

    def text(self, ctx) -> str:
        return self.result.text(ctx)


def norm_table(target: P.DmlTargetContext) -> str:
    return ".".join(norm(i.getText()) for i in target.qualifiedName().identifier())


def _split_alias(tail: list, stop_words: set[str]):
    """Tablo adından sonra gelen opsiyonel alias tokenını ayırır."""
    if tail and isl.is_name(tail[0]) and tail[0].text.upper() not in stop_words:
        return tail[0], tail[1:]
    return None, tail


def parse_dml(cx: _Ctx, dml: P.DmlStatementContext, variables: set[str]) -> DmlInfo:
    stmt = dml.getChild(0)
    target = stmt.dmlTarget()
    name_toks = cx.toks(target)
    tail = cx.toks(stmt.sqlTail())
    text = cx.text(stmt)

    if isinstance(stmt, P.InsertStatementContext):
        alias, rest = _split_alias(tail, {"VALUES", "SELECT", "WITH"})
        table = isl.table_ref(name_toks, alias)
        vi = isl.find_top_level(rest, isl.word("VALUES"))
        cols = vals = None
        if vi >= 0:
            col_inner = isl.strip_parens(rest[:vi])
            val_inner = isl.strip_parens(rest[vi + 1:])
            if col_inner is not None and val_inner is not None:
                c = [isl.column_ref(part, table.qualifiers) for part in isl.split_top_level(col_inner, L.COMMA)]
                v = [isl.source_text(cx.stream, part) for part in isl.split_top_level(val_inner, L.COMMA)]
                if all(c) and len(c) == len(v):
                    cols, vals = tuple(c), tuple(v)
        return DmlInfo("INSERT", table, text, insert_columns=cols, insert_values=vals)

    if isinstance(stmt, P.UpdateStatementContext):
        alias, rest = _split_alias(tail, {"SET"})
        table = isl.table_ref(name_toks, alias)
        if not rest or rest[0].text.upper() != "SET":
            return DmlInfo("UPDATE", table, text)
        wi = isl.find_top_level(rest, isl.word("WHERE"))
        set_toks = rest[1:wi] if wi >= 0 else rest[1:]
        pairs = []
        for part in isl.split_top_level(set_toks, L.COMMA):
            eq = isl.find_top_level(part, lambda t: t.type == L.EQ)
            col = isl.column_ref(part[:eq], table.qualifiers) if eq > 0 else None
            if col is None:
                pairs = None
                break
            pairs.append((col, isl.source_text(cx.stream, part[eq + 1:])))
        where = isl.parse_equalities(cx.stream, rest[wi + 1:], table, variables) if wi >= 0 else None
        return DmlInfo("UPDATE", table, text,
                       set_pairs=tuple(pairs) if pairs else None,
                       where=tuple(where) if where else None)

    if isinstance(stmt, P.DeleteStatementContext):
        alias, rest = _split_alias(tail, {"WHERE"})
        table = isl.table_ref(name_toks, alias)
        wi = isl.find_top_level(rest, isl.word("WHERE"))
        where = isl.parse_equalities(cx.stream, rest[wi + 1:], table, variables) if wi == 0 else None
        return DmlInfo("DELETE", table, text, where=tuple(where) if where else None)

    return DmlInfo("MERGE", isl.table_ref(name_toks, None), text)


_LOOKUP_FORBIDDEN = {"GROUP", "ORDER", "HAVING", "CONNECT", "START", "UNION", "INTERSECT",
                     "MINUS", "FETCH", "OFFSET", "JOIN", "FOR", "ROWNUM"}


def match_lookup(cx: _Ctx, sel: P.SelectIntoStatementContext, variables: set[str]) -> LookupInfo:
    """P2 kalıbı: tek tablodan tek anahtar eşitliğiyle kolon(lar) okuma. Uymazsa _Reject."""
    if sel.BULK() is not None:
        raise _Reject("BULK COLLECT INTO arama değil")

    tail = cx.toks(sel.sqlTail())
    # FROM <ad>(.<ad>)* [alias] WHERE <koşul>
    i = 0
    name_toks = []
    while i < len(tail) and isl.is_name(tail[i]):
        name_toks.append(tail[i])
        if i + 1 < len(tail) and tail[i + 1].type == L.DOT:
            name_toks.append(tail[i + 1])
            i += 2
        else:
            i += 1
            break
    if not name_toks:
        raise _Reject("FROM sonrası tek tablo bekleniyor")
    alias = None
    if i < len(tail) and isl.is_name(tail[i]) and tail[i].text.upper() != "WHERE":
        alias = tail[i]
        i += 1
    if i >= len(tail) or tail[i].text.upper() != "WHERE":
        raise _Reject("tek tablo + WHERE bekleniyor (join / çoklu tablo / koşulsuz okuma)")
    cond = tail[i + 1:]
    if any(isl.find_top_level(cond, isl.word(w)) >= 0 for w in _LOOKUP_FORBIDDEN) \
            or isl.find_top_level(cond, lambda t: t.type == L.COMMA) >= 0:
        raise _Reject("WHERE sonrası desteklenmeyen yan tümce")

    table = isl.table_ref(name_toks, alias)
    items = isl.split_top_level(cx.toks(sel.selectItems()), L.COMMA)
    columns = [isl.column_ref(it, table.qualifiers) for it in items]
    if not all(columns):
        raise _Reject("SELECT listesinde yalnızca düz kolon referansı desteklenir")
    targets = [norm(v.getText()) for v in sel.intoTargets().variableRef()]
    if len(targets) != len(columns):
        raise _Reject("SELECT listesi ile INTO hedefleri uyuşmuyor")

    eqs = isl.parse_equalities(cx.stream, cond, table, variables)
    if eqs is None:
        raise _Reject("WHERE koşulu `kolon = ifade` biçiminde değil")
    if len(eqs) != 1:
        raise _Reject(f"bileşik anahtar ({len(eqs)} eşitlik) desteklenmiyor")
    key = eqs[0]
    if any(t in isl.referenced_names(_tokenize(key.expr)) for t in targets):
        raise _Reject("anahtar ifadesi INTO hedefine bağlı")
    return LookupInfo(table, tuple(columns), tuple(targets), key.column, key.expr)


def _tokenize(text: str) -> list:
    lexer = L(InputStream(text))
    lexer.removeErrorListeners()
    return [t for t in lexer.getAllTokens() if t.channel == 0]


# ================================================================ P1

_COUNTER_RE = re.compile(r"^(\w+):=\1\+1;$")


@dataclass
class _Body:
    prelude: list[PreludeStep]
    prelude_lookups: list[P.SelectIntoStatementContext]
    prelude_assigns: list[P.AssignmentStatementContext]
    core: list[P.DmlStatementContext]       # [dml] ya da [update, insert]
    shape: str
    counters: list[str]


def _classify_body(cx: _Ctx, stmts: list[P.StatementContext], record: str) -> _Body:
    body = _Body([], [], [], [], "", [])
    state = "pre"
    i = 0
    while i < len(stmts):
        s = inner(stmts[i])
        txt = compact_text(s)
        m = _COUNTER_RE.match(txt)
        if isinstance(s, P.AssignmentStatementContext) and m:
            body.counters.append(norm(m.group(1)))
        elif state == "pre" and isinstance(s, P.AssignmentStatementContext):
            ref = s.variableRef()
            if ref.getChildCount() != 1:
                raise _Reject(f"gövdede koleksiyon/alan ataması: {cx.text(s)!r}")
            name = norm(ref.getText())
            if name == record:
                raise _Reject("döngü kaydına atama yapılıyor")
            body.prelude.append(PreludeStep("ASSIGN", name, cx.text(s)))
            body.prelude_assigns.append(s)
        elif state == "pre" and isinstance(s, P.SqlStatementContext) and s.selectIntoStatement():
            sel = s.selectIntoStatement()
            targets = ",".join(norm(v.getText()) for v in sel.intoTargets().variableRef())
            body.prelude.append(PreludeStep("LOOKUP", targets, cx.text(sel)))
            body.prelude_lookups.append(sel)
        elif state == "pre" and isinstance(s, P.SqlStatementContext) and s.dmlStatement():
            dml = s.dmlStatement()
            nxt = inner(stmts[i + 1]) if i + 1 < len(stmts) else None
            if dml.updateStatement() and isinstance(nxt, P.IfStatementContext) and _is_upsert_if(nxt):
                ins = nxt.statementList(0).statement(0).getChild(0).dmlStatement()
                body.core = [dml, ins]
                body.shape = "UPSERT"
                i += 1
            else:
                body.core = [dml]
                body.shape = "DML"
            state = "post"
        else:
            where = "DML çekirdeğinden sonra" if state == "post" else "döngü gövdesinde"
            raise _Reject(f"{where} desteklenmeyen deyim: {cx.text(s).splitlines()[0]!r}")
        i += 1
    if not body.core:
        raise _Reject("döngü gövdesinde DML yok")
    return body


def _is_upsert_if(ifs: P.IfStatementContext) -> bool:
    if ifs.ELSIF() or ifs.ELSE() or len(ifs.statementList()) != 1:
        return False
    if compact_text(ifs.expression(0)) not in ("SQL%ROWCOUNT=0", "0=SQL%ROWCOUNT"):
        return False
    then = ifs.statementList(0).statement()
    if len(then) != 1:
        return False
    s = inner(then[0])
    return isinstance(s, P.SqlStatementContext) and s.dmlStatement() is not None \
        and s.dmlStatement().insertStatement() is not None


def _cursor_query_text(cx: _Ctx, cur: P.CursorDeclarationContext) -> str:
    return cx.text(cur.selectQuery())


def match_row_loop(cx: _Ctx, loop: P.BasicLoopStatementContext, lookups: dict) -> Site:
    """P1 kalıbı. Elenen alternatifler Site.excluded içinde; kalıp hiç uymazsa _Reject."""
    stmts = loop.statementList().statement()
    fetch = inner(stmts[0])
    cursor = norm(fetch.identifier().getText())

    # --- OPEN c; <loop>; CLOSE c;
    loop_stmt = loop.parentCtx
    sl = loop_stmt.parentCtx
    siblings = sl.statement()
    k = next(j for j, st in enumerate(siblings) if st is loop_stmt)
    opn = inner(siblings[k - 1]) if k > 0 else None
    cls = inner(siblings[k + 1]) if k + 1 < len(siblings) else None
    if not (isinstance(opn, P.OpenStatementContext) and norm(opn.identifier().getText()) == cursor):
        raise _Reject(f"döngüden hemen önce `OPEN {cursor};` yok")
    if opn.LPAREN() or opn.FOR():
        raise _Reject("parametreli / OPEN FOR imleç desteklenmiyor")
    if not (isinstance(cls, P.CloseStatementContext) and norm(cls.identifier().getText()) == cursor):
        raise _Reject(f"döngüden hemen sonra `CLOSE {cursor};` yok")

    # --- FETCH c INTO r; EXIT WHEN c%NOTFOUND;
    targets = fetch.intoTargets().variableRef()
    if len(targets) != 1 or targets[0].getChildCount() != 1:
        raise _Reject("FETCH tek bir kayıt değişkenine yapılmalı")
    record = norm(targets[0].getText())
    if len(stmts) < 2:
        raise _Reject("FETCH sonrası EXIT yok")
    ex = inner(stmts[1])
    if not (isinstance(ex, P.ExitStatementContext) and ex.identifier() is None and ex.expression() is not None
            and compact_text(ex.expression()) == f"{cursor}%NOTFOUND"):
        raise _Reject(f"FETCH'ten hemen sonra `EXIT WHEN {cursor}%NOTFOUND;` bekleniyor")

    scope = scope_at(loop)
    cur = scope.cursors.get(cursor)
    if cur is None:
        raise _Reject(f"imleç bildirimi bulunamadı: {cursor}")
    if cur.cursorParameter():
        raise _Reject("parametreli imleç desteklenmiyor")
    rec_decl = scope.variables.get(record)
    if not (isinstance(rec_decl, P.VariableDeclarationContext)
            and isinstance(rec_decl.datatype(), P.AnchoredTypeContext)
            and norm(rec_decl.datatype().qualifiedName().getText()) == cursor
            and norm(rec_decl.datatype().identifier().getText()) == "ROWTYPE"):
        raise _Reject(f"FETCH hedefi `{cursor}%ROWTYPE` tipinde bir kayıt değil")

    body_stmts = stmts[2:]
    body_toks = [t for st in body_stmts for t in cx.toks(st)]
    if any(isl.is_name(t) and norm(t.text) == cursor for t in body_toks):
        raise _Reject("gövdede imleç özniteliğine erişiliyor")
    if any(t.type == L.EXIT or t.type == L.CONTINUE for t in body_toks):
        raise _Reject("gövdede ek EXIT/CONTINUE var")

    # Kayıt değişkeni döngü dışında kullanılıyorsa toplu/küme sürümleri son satır değerini koruyamaz.
    span = (cx.toks(opn)[0].tokenIndex, cx.toks(cls)[-1].tokenIndex)
    for t in cx.stream.tokens:
        if t.channel == 0 and isl.is_name(t) and norm(t.text) == record \
                and not span[0] <= t.tokenIndex <= span[1] \
                and not rec_decl.start.tokenIndex <= t.tokenIndex <= rec_decl.stop.tokenIndex:
            raise _Reject(f"kayıt değişkeni `{record}` döngü dışında da kullanılıyor")

    body = _classify_body(cx, body_stmts, record)

    counters = set(body.counters)
    for c in counters:
        uses = sum(1 for t in body_toks if isl.is_name(t) and norm(t.text) == c)
        if uses != 2 * body.counters.count(c):
            raise _Reject(f"sayaç `{c}` gövdede başka amaçla da kullanılıyor")

    row_locals = tuple(dict.fromkeys(
        name for step in body.prelude for name in step.target.split(",")))
    variables = set(scope.variables) | set(row_locals)
    dml = parse_dml(cx, body.core[0], variables)
    upsert_insert = parse_dml(cx, body.core[1], variables) if body.shape == "UPSERT" else None

    excluded: list[tuple[str, str]] = []

    # --- BULK uygulanabilirliği: gövdedeki okumalar DML hedefini görmemeli (yazdıktan sonra okuma).
    written = {dml.table.name} | ({upsert_insert.table.name} if upsert_insert else set())
    read_tables = set()
    for sel in body.prelude_lookups:
        read_tables |= {norm(t.text) for t in cx.toks(sel.sqlTail()) if isl.is_name(t)}
    raw = written & read_tables
    bulk_ok = not raw
    if not bulk_ok:
        reason = f"gövde, yazdığı tabloyu okuyor ({', '.join(sorted(raw))}); toplu yürütme sırası değiştirir"
        excluded += [(a, reason) for a in BULK_ALTS]

    # --- SET_BASED uygulanabilirliği
    set_reason = _set_based_obstacle(cx, body, body_stmts, record, row_locals, dml, upsert_insert,
                                     cur, scope, lookups, bulk_ok)
    if set_reason:
        excluded.append((SET_BASED, set_reason))

    excluded_alts = {a for a, _ in excluded}
    alternatives = tuple(a for a in (ROW, *BULK_ALTS, SET_BASED) if a not in excluded_alts)
    if len(alternatives) < 2:
        raise _Reject("uygulanabilir alternatif yok: " + "; ".join(sorted({r for _, r in excluded})))

    info = RowLoopInfo(
        cursor=cursor,
        cursor_query=_cursor_query_text(cx, cur),
        record=record,
        shape=body.shape,
        prelude=tuple(body.prelude),
        dml=dml,
        upsert_insert=upsert_insert,
        counters=tuple(dict.fromkeys(body.counters)),
        row_locals=row_locals,
        record_fields=tuple(isl.record_fields(body_toks, record)),
    )
    site = Site(
        id="", pattern=P1, name=f"{cursor} → {dml.table.name}",
        alternatives=alternatives, default=ROW,
        start_token=span[0], stop_token=span[1], line=opn.start.line,
        info=info, excluded=tuple(excluded), node=loop,
    )
    return site


def _set_based_obstacle(cx, body: _Body, body_stmts, record: str, row_locals: tuple[str, ...],
                        dml: DmlInfo, ins: DmlInfo | None,
                        cur: P.CursorDeclarationContext, scope: Scope, lookups: dict,
                        bulk_ok: bool) -> str | None:
    if not bulk_ok:
        return "gövde, yazdığı tabloyu okuyor"
    # Kayıt bütün olarak (alan erişimi olmadan) kullanılıyorsa SQL kaynağına çevrilemez.
    body_toks = [t for st in body_stmts for t in cx.toks(st)]
    for i, t in enumerate(body_toks):
        if isl.is_name(t) and norm(t.text) == record and (i == 0 or body_toks[i - 1].type != L.DOT)                 and (i + 1 == len(body_toks) or body_toks[i + 1].type != L.DOT):
            return f"kayıt `{record}` bütün olarak kullanılıyor"
    # Döngüler arası taşınan değer: satır-yerel bir değişken, bu yinelemede atanmadan okunuyor.
    assigned: set[str] = set()
    for step, ctx in _prelude_contexts(body):
        reads = isl.referenced_names(cx.toks(ctx)) & set(row_locals)
        carried = reads - assigned
        if carried:
            return f"döngüler arası bağımlılık: {', '.join(sorted(carried))} atanmadan önce okunuyor"
        assigned |= set(step.target.split(","))
    # İfadeler `FROM (imleç sorgusu) px_src` içine taşınınca çıplak değişken adları imleç kolonlarına çözülebilir.
    binds = _unqualified_names(body_toks) & set(scope.variables)
    binds -= {record, *row_locals, *body.counters}
    if binds:
        cols = cursor_columns(cx.toks(cur.selectQuery()))
        if cols is None:
            return f"imleç kolonları belirlenemedi; değişkenler ({', '.join(sorted(binds))}) çakışabilir"
        clash = binds & cols
        if clash:
            return f"değişken adı imleç kolonuyla çakışıyor: {', '.join(sorted(clash))}"
    qtoks = cx.toks(cur.selectQuery())
    if isl.find_top_level(qtoks, lambda t: t.type == L.FOR) >= 0:
        return "imleç sorgusu FOR UPDATE içeriyor"
    for sel in body.prelude_lookups:
        if sel not in lookups:
            return f"gövdedeki arama P2 kalıbına uymuyor: {cx.text(sel).splitlines()[0]!r}"
    for a in body.prelude_assigns:
        toks = cx.toks(a.expression())
        if any(t.type in (L.PERCENT, L.TRUE, L.FALSE) for t in toks):
            return f"atama SQL'e taşınamaz: {cx.text(a)!r}"
        if isl.referenced_names(toks) & scope.subprograms:
            return f"atama yerel alt programı çağırıyor (SQL'den çağrılamaz): {cx.text(a)!r}"

    if body.shape == "UPSERT":
        if body.counters:
            return "upsert gövdesinde sayaç var (MERGE etkilenen satır sayısı FETCH sayısına eşit değil)"
        if dml.set_pairs is None or dml.where is None:
            return "upsert UPDATE'i `SET k = ifade ... WHERE anahtar = ifade` biçiminde değil"
        if ins.insert_columns is None:
            return "upsert INSERT'ü `INSERT INTO t (kolonlar) VALUES (...)` biçiminde değil"
        if ins.table.name != dml.table.name:
            return "upsert UPDATE ve INSERT farklı tablolara yazıyor"
        return _merge_key_conflict(dml)

    if dml.kind == "INSERT":
        if dml.insert_columns is None:
            return "INSERT `(kolonlar) VALUES (...)` biçiminde değil"
        return None
    if dml.kind == "UPDATE":
        if body.counters:
            return "UPDATE gövdesinde sayaç var (MERGE etkilenen satır sayısı FETCH sayısına eşit değil)"
        if dml.set_pairs is None or dml.where is None:
            return "UPDATE `SET ... WHERE anahtar = ifade [AND ...]` biçiminde değil"
        return _merge_key_conflict(dml)
    return f"{dml.kind} için küme tabanlı dönüşüm desteklenmiyor"


def _unqualified_names(toks: list) -> set[str]:
    """Nokta ile nitelenmemiş, fonksiyon çağrısı olmayan adlar."""
    names = set()
    for i, t in enumerate(toks):
        if not isl.is_name(t) or (i > 0 and toks[i - 1].type == L.DOT):
            continue
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None and nxt.type in (L.DOT, L.LPAREN):
            continue
        names.add(norm(t.text))
    return names


def cursor_columns(qtoks: list) -> set[str] | None:
    """İmleç sorgusunun SELECT listesindeki kolon adları; `*`, WITH vb. nedeniyle belirlenemezse None."""
    if not qtoks or qtoks[0].type != L.SELECT:
        return None
    end = isl.find_top_level(qtoks, lambda t: t.type == L.FROM)
    if end < 0:
        return None
    items = qtoks[1:end]
    if items and items[0].text.upper() in ("DISTINCT", "UNIQUE", "ALL"):
        items = items[1:]
    cols = set()
    for item in isl.split_top_level(items, L.COMMA):
        if not item or any(t.type == L.STAR for t in item):
            return None
        last = item[-1]
        if not isl.is_name(last):
            continue                                  # adsız ifade: r.alan ile erişilemez
        if len(item) == 1 or item[-2].type in (L.AS, L.DOT, L.RPAREN) or isl.is_name(item[-2]):
            cols.add(norm(last.text))
    return cols


def _prelude_contexts(body: _Body):
    """Prelude adımları ve okunan kısımlarının bağlamları (atamada sağ taraf, aramada WHERE)."""
    assigns = iter(body.prelude_assigns)
    lookups = iter(body.prelude_lookups)
    for step in body.prelude:
        if step.kind == "ASSIGN":
            yield step, next(assigns).expression()
        else:
            yield step, next(lookups).sqlTail()


def _merge_key_conflict(dml: DmlInfo) -> str | None:
    keys = {e.column for e in dml.where}
    clash = keys & {c for c, _ in dml.set_pairs}
    if clash:
        return f"ON anahtarı güncelleniyor ({', '.join(sorted(clash))}); MERGE'de geçersiz (ORA-38104)"
    return None


def _cache_key_obstacle(catalog: "Catalog", info: LookupInfo) -> str | None:
    try:
        col = catalog.columns(info.table.name).get(info.key_column)
    except UnknownTable:
        return f"katalogda tablo yok: {info.table.name}"
    if col is None:
        return f"katalogda kolon yok: {info.table.name}.{info.key_column}"
    if cache_key_for(col) is None:
        return f"anahtar tipi ({col.data_type}) Associative Array indeksine anlam kaybı olmadan eşlenemiyor"
    return None


# ================================================================ analiz giriş noktası

def find_sites(result: ParseResult, catalog: "Catalog | None" = None) -> SiteAnalysis:
    """Siteleri bulur. Katalog verilirse P2 anahtar kolonunun önbelleğe uygunluğu da denetlenir."""
    cx = _Ctx(result)
    tree = result.tree
    rejections: list[Rejection] = []

    # --- P2 adayları: bir döngü içindeki SELECT INTO
    lookup_sites: dict[P.SelectIntoStatementContext, Site] = {}
    for sel in find_all(tree, P.SelectIntoStatementContext):
        if enclosing(sel, _LOOP_TYPES) is None:
            continue
        line = sel.start.line
        try:
            scope = scope_at(sel)
            info = match_lookup(cx, sel, set(scope.variables))
        except _Reject as r:
            rejections.append(Rejection(P2, line, str(r)))
            continue
        if info.table.name in cx.dml_targets:
            rejections.append(Rejection(
                P2, line, f"`{info.table.name}` blokta değiştiriliyor; önbellek bayatlayabilir"))
            continue
        if catalog is not None:
            key_reason = _cache_key_obstacle(catalog, info)
            if key_reason:
                rejections.append(Rejection(P2, line, key_reason))
                continue
        toks = cx.toks(sel)
        lookup_sites[sel] = Site(
            id="", pattern=P2, name=f"{info.table.name}.{info.key_column}",
            alternatives=(LOOKUP_SQL, LOOKUP_CACHE), default=LOOKUP_SQL,
            start_token=toks[0].tokenIndex, stop_token=toks[-1].tokenIndex, line=line,
            info=info, node=sel,
        )

    # --- P1 adayları: ilk deyimi tekil FETCH olan basit döngüler
    loop_sites: list[Site] = []
    for loop in find_all(tree, P.BasicLoopStatementContext):
        stmts = loop.statementList().statement()
        first = inner(stmts[0])
        if not isinstance(first, P.FetchStatementContext) or first.BULK() is not None:
            continue
        try:
            loop_sites.append(match_row_loop(cx, loop, lookup_sites))
        except _Reject as r:
            rejections.append(Rejection(P1, loop.start.line, str(r)))
    for cfl in find_all(tree, P.CursorForLoopStatementContext):
        if find_all(cfl, P.DmlStatementContext):
            rejections.append(Rejection(
                P1, cfl.start.line,
                "cursor FOR döngüsü (PLSQL_OPTIMIZE_LEVEL=2'de örtük array fetch, ADR-001); "
                "açık OPEN/FETCH kalıbı bekleniyor"))

    # --- kimlikler (kaynak sırası) ve ebeveyn ilişkileri
    ordered = sorted([*loop_sites, *lookup_sites.values()], key=lambda s: s.start_token)
    sites: list[Site] = []
    for n, s in enumerate(ordered, start=1):
        sites.append(replace(s, id=f"s{n}"))
    final = []
    for s in sites:
        parents = [p for p in sites if p is not s and p.pattern == P1 and p.contains(s)]
        parent = max(parents, key=lambda p: p.start_token).id if parents else None
        final.append(replace(s, parent=parent))

    rejections.sort(key=lambda r: r.line)
    return SiteAnalysis(parse=result, sites=final, rejections=rejections)
