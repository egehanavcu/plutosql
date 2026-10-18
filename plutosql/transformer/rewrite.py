"""Konfigürasyon x ∈ 𝒳 → dönüştürülmüş PL/SQL kaynağı.

Yaklaşım: orijinal token akışı baştan sona yeniden üretilir. Aktif dönüşümler "birim" (unit) olarak
token aralıklarına bağlanır; bir birimin aralığına gelindiğinde onun ürettiği metin yazılır, aralığın
geri kalanı atlanır. Birimler iç içe olabilir (ör. BULK döngüsü içindeki önbellekli arama): dış birim
gövdesini üretirken iç aralıkları yine `render` ile, gerekli ad yerine koymalarıyla (substitution) üretir.
Dönüştürülmeyen her token (yorum ve boşluklar dahil) birebir korunur.

Birimler:
  _LookupUnit  P2 / LOOKUP_CACHE: SELECT INTO yerine dizi erişimi
  _LoopUnit    P1 / BULK_L, SET_BASED: OPEN..CLOSE aralığının yerine üretilen kod
  _HostUnit    önbellek bildirimi + yüklemesi için sarmalayıcı blok (dönüştürülmeyen bölge etrafında)

Üretilen tanımlayıcılar `px_<site>_...` önekini taşır; kaynakta `PX_` ile başlayan ad varsa hata verilir.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from antlr4 import InputStream, Token

from . import islands as isl
from .catalog import Catalog, UnknownTable, cache_key_for
from .generated.MiniPlsqlLexer import MiniPlsqlLexer as L
from .generated.MiniPlsqlParser import MiniPlsqlParser as P
from .islands import norm
from .sites import (
    BULK_LIMITS, LOOKUP_CACHE, P1, P2, ROW, SET_BASED, LookupInfo, RowLoopInfo, Site,
    SiteAnalysis, _COUNTER_RE, _LOOP_TYPES, compact_text, enclosing, inner,
)
from .space import Config, SearchSpace

INDENT = 2


class RewriteError(Exception):
    pass


@dataclass(frozen=True)
class RewriteResult:
    code: str
    config: Config          # kanonik konfigürasyon
    digest: str             # üretilen kodun SHA-256 özeti (ölçüm önbelleği anahtarı, ADR-003)


def rewrite(analysis: SiteAnalysis, x: Mapping[str, str], catalog: Catalog) -> RewriteResult:
    space = SearchSpace(tuple(analysis.sites))
    config = space.canonicalize(x)
    code = _Writer(analysis, config, catalog).render_all()
    return RewriteResult(code=code, config=config, digest=hashlib.sha256(code.encode("utf-8")).hexdigest())


# ================================================================ kod oluşturucu

class _Code:
    """Göreli girintili satırlar. İlk satır birimin başladığı sütuna, diğerleri base + seviye*INDENT'e yazılır."""

    def __init__(self) -> None:
        self.lines: list[tuple[int, str]] = []

    def add(self, level: int, text: str) -> None:
        self.lines.append((level, text))

    def embed(self, level: int, text: str, orig_col: int) -> None:
        """Orijinal kaynaktan gelen çok satırlı metni, ilk tokenının sütununu referans alarak yeniden girintiler."""
        first, *rest = text.split("\n")
        self.lines.append((level, first.rstrip()))
        for ln in rest:
            lead = len(ln) - len(ln.lstrip(" "))
            self.lines.append((level, ln[min(lead, orig_col):].rstrip()))

    def extend(self, level: int, other: "_Code") -> None:
        self.lines.extend((level + lvl, t) for lvl, t in other.lines)

    def render(self, base_col: int) -> str:
        out = []
        for i, (lvl, t) in enumerate(self.lines):
            if i == 0:
                out.append(t)
            else:
                out.append(" " * (base_col + lvl * INDENT) + t if t else "")
        return "\n".join(out)


def _lex(text: str) -> list[Token]:
    lexer = L(InputStream(text))
    lexer.removeErrorListeners()
    return lexer.getAllTokens()


def substitute(text: str, subst: Mapping[str, str], qualify: Mapping[str, str] | None = None) -> str:
    """Bir ifade metninde ad yerine koyma.

    subst:   normalize ad → yerine yazılacak metin (nokta ile nitelenmemiş adlara uygulanır)
    qualify: normalize kolon adı → nitelik; bu adlar subst'tan önce gelir (Oracle'da kolon değişkeni gölgeler)
    """
    toks = _lex(text)
    out = []
    prev = None
    for i, t in enumerate(toks):
        piece = t.text
        if t.channel == Token.DEFAULT_CHANNEL:
            nxt = next((u for u in toks[i + 1:] if u.channel == Token.DEFAULT_CHANNEL), None)
            free = isl.is_name(t) and not (prev is not None and prev.type == L.DOT)
            if free and qualify and norm(t.text) in qualify and not (nxt is not None and nxt.type in (L.DOT, L.LPAREN)):
                piece = f"{qualify[norm(t.text)]}.{t.text}"
            elif free and norm(t.text) in subst:
                piece = subst[norm(t.text)]
            prev = t
        out.append(piece)
    return "".join(out)


# ================================================================ birimler

class _Unit:
    start: int
    stop: int

    def render(self, w: "_Writer", subst: Mapping[str, str]) -> str:
        raise NotImplementedError


@dataclass
class _CacheGroup:
    """Aynı (tablo, anahtar) üzerindeki aktif LOOKUP_CACHE siteleri: tek dizi, tek yükleme.

    Tek üyeli grup eski adlandırmayı (`px_<site>_…`) korur; böylece ortak önbellek yokken üretilen kod
    ve ölçüm özetleri değişmez. Çok üyeli grup `px_c<n>_…` adlarını kullanır.
    """
    members: list[Site]
    prefix: str

    @property
    def site(self) -> Site:
        return self.members[0]

    @property
    def names(self) -> dict[str, str]:
        p = self.prefix
        return {"val": f"{p}_val_t", "map": f"{p}_map", "dup": f"{p}_dup", "row": f"{p}_r"}

    @property
    def value_columns(self) -> list[str]:
        cols: list[str] = []
        for s in self.members:
            cols += [c for c in s.info.value_columns if c not in cols]
        return cols


class _LookupUnit(_Unit):
    def __init__(self, site: Site, group: _CacheGroup):
        self.site, self.group = site, group
        self.start, self.stop = site.start_token, site.stop_token

    def render(self, w: "_Writer", subst: Mapping[str, str]) -> str:
        info: LookupInfo = self.site.info
        key = w.cache_key(self.site)
        names = self.group.names
        k = substitute(info.key_expr, subst)
        idx = key.convert(k)
        miss = [f"{k} IS NULL"]
        if key.out_of_domain(k):
            miss.append(key.out_of_domain(k))
        miss.append(f"NOT {names['map']}.EXISTS({idx})")

        c = _Code()
        c.add(0, f"IF {' OR '.join(miss)} THEN")
        c.add(1, "RAISE NO_DATA_FOUND;")
        c.add(0, "END IF;")
        c.add(0, f"IF {names['dup']}.EXISTS({idx}) THEN")
        c.add(1, "RAISE TOO_MANY_ROWS;")
        c.add(0, "END IF;")
        for col, target in zip(info.value_columns, info.into_targets):
            c.add(0, f"{substitute(w.spell_ref(target), subst)} := {names['map']}({idx}).{w.spell(col)};")
        return c.render(w.column(self.start))


class _HostUnit(_Unit):
    """Dönüştürülmemiş bir bölgeyi, içindeki önbelleklerin bildirim ve yüklemesiyle sarar."""

    def __init__(self, start: int, stop: int, caches: list[_CacheGroup]):
        self.start, self.stop, self.caches = start, stop, caches

    def render(self, w: "_Writer", subst: Mapping[str, str]) -> str:
        c = _Code()
        c.add(0, "DECLARE")
        for group in self.caches:
            c.extend(1, w.cache_decl(group))
        c.add(0, "BEGIN")
        for group in self.caches:
            c.extend(1, w.cache_load(group))
        c.embed(1, w.render(self.start, self.stop, subst, skip=self), w.column(self.start))
        c.add(0, "END;")
        return c.render(w.column(self.start))


class _LoopUnit(_Unit):
    def __init__(self, site: Site, alternative: str, caches: list[_CacheGroup]):
        self.site, self.alternative, self.caches = site, alternative, caches
        self.start, self.stop = site.start_token, site.stop_token

    def render(self, w: "_Writer", subst: Mapping[str, str]) -> str:
        if self.alternative == SET_BASED:
            code = _SetBasedGen(w, self.site).generate()
        else:
            limit = int(self.alternative.removeprefix("BULK_"))
            assert limit in BULK_LIMITS
            code = _BulkGen(w, self.site, limit, self.caches).generate()
        return code.render(w.column(self.start))


# ================================================================ P1 gövde parçaları

@dataclass
class _LoopParts:
    prelude: list                     # ön deyimler (atama / SELECT INTO bağlamları), sayaçlar hariç
    core: list                        # [DmlStatementContext] ya da [update, insert]


def _loop_parts(site: Site) -> _LoopParts:
    stmts = site.node.statementList().statement()[2:]
    prelude, core = [], []
    for st in stmts:
        s = inner(st)
        if isinstance(s, P.AssignmentStatementContext) and _COUNTER_RE.match(compact_text(s)):
            continue
        if core:
            if isinstance(s, P.IfStatementContext):          # upsert'in INSERT kolu
                core.append(inner(s.statementList(0).statement(0)).dmlStatement())
            continue
        if isinstance(s, P.SqlStatementContext) and s.dmlStatement() is not None:
            core.append(s.dmlStatement())
        else:
            prelude.append(s.selectIntoStatement() if isinstance(s, P.SqlStatementContext) else s)
    return _LoopParts(prelude, core)


class _BulkGen:
    """BULK COLLECT ... LIMIT L + FORALL.

    Ön deyimler (atamalar, aramalar) parti içinde satır satır çalışır; DML'de kullanılan satır-yerel
    değişkenler indeksli dizilere kopyalanır. Upsert: FORALL UPDATE → SQL%BULK_ROWCOUNT ile eşleşmeyen
    satırlar → FORALL ... VALUES OF ile INSERT.
    """

    def __init__(self, w: "_Writer", site: Site, limit: int, caches: list[_CacheGroup]):
        self.w, self.site, self.limit, self.caches = w, site, limit, caches
        self.info: RowLoopInfo = site.info
        self.parts = _loop_parts(site)
        s = site.id
        self.rows, self.i, self.ins = f"px_{s}_rows", f"px_{s}_i", f"px_{s}_ins"

    def _dml_locals(self) -> list[str]:
        """DML'de değer olarak okunan satır-yerel değişkenler (hedef kolonla aynı adlı olanlar hariç)."""
        used = []
        for dml in self.parts.core:
            protected = self.w.protected_in_dml(dml, set(self.info.row_locals))
            for t in self.w.ctx_tokens(dml):
                n = norm(t.text) if isl.is_name(t) else None
                if n in self.info.row_locals and t.tokenIndex not in protected and n not in used:
                    if not _after_dot(self.w.ctx_tokens(dml), t):
                        used.append(n)
        return used

    def generate(self) -> _Code:
        w, info, s = self.w, self.info, self.site.id
        cur = w.spell(info.cursor)
        rows, i = self.rows, self.i
        locals_ = self._dml_locals()
        local_arr = {v: f"px_{s}_{w.spell(v).lower()}" for v in locals_}
        rec_sub = {info.record: f"{rows}({i})"}
        dml_sub = {**rec_sub, **{v: f"{arr}({i})" for v, arr in local_arr.items()}}

        c = _Code()
        c.add(0, "DECLARE")
        c.add(1, f"TYPE {rows}_t IS TABLE OF {cur}%ROWTYPE;")
        c.add(1, f"{rows} {rows}_t;")
        for v, arr in local_arr.items():
            c.add(1, f"TYPE {arr}_t IS TABLE OF {w.spell(v)}%TYPE INDEX BY PLS_INTEGER;")
            c.add(1, f"{arr} {arr}_t;")
        if info.shape == "UPSERT":
            c.add(1, f"TYPE {self.ins}_t IS TABLE OF PLS_INTEGER INDEX BY PLS_INTEGER;")
            c.add(1, f"{self.ins} {self.ins}_t;")
        for group in self.caches:
            c.extend(1, w.cache_decl(group))
        c.add(0, "BEGIN")
        for group in self.caches:
            c.extend(1, w.cache_load(group))
        c.add(1, f"OPEN {cur};")
        c.add(1, "LOOP")
        c.add(2, f"FETCH {cur} BULK COLLECT INTO {rows} LIMIT {self.limit};")
        c.add(2, f"EXIT WHEN {rows}.COUNT = 0;")
        if self.parts.prelude:
            c.add(2, f"FOR {i} IN 1 .. {rows}.COUNT LOOP")
            for st in self.parts.prelude:
                c.embed(3, w.render_ctx(st, rec_sub), w.column(st.start.tokenIndex))
            for v, arr in local_arr.items():
                c.add(3, f"{arr}({i}) := {w.spell(v)};")
            c.add(2, "END LOOP;")

        first = self.parts.core[0]
        c.add(2, f"FORALL {i} IN 1 .. {rows}.COUNT")
        c.embed(3, w.render_dml(first, dml_sub), w.column(first.start.tokenIndex))
        if info.shape == "UPSERT":
            second = self.parts.core[1]
            c.add(2, f"FOR {i} IN 1 .. {rows}.COUNT LOOP")
            c.add(3, f"IF SQL%BULK_ROWCOUNT({i}) = 0 THEN")
            c.add(4, f"{self.ins}({self.ins}.COUNT + 1) := {i};")
            c.add(3, "END IF;")
            c.add(2, "END LOOP;")
            c.add(2, f"FORALL {i} IN VALUES OF {self.ins}")
            c.embed(3, w.render_dml(second, dml_sub), w.column(second.start.tokenIndex))
            c.add(2, f"{self.ins}.DELETE;")
        for counter in info.counters:
            n = w.spell(counter)
            c.add(2, f"{n} := {n} + {rows}.COUNT;")
        c.add(1, "END LOOP;")
        c.add(1, f"CLOSE {cur};")
        c.add(0, "END;")
        return c


class _SetBasedGen:
    """Küme tabanlı DML: INSERT → INSERT ... SELECT, UPDATE / upsert → MERGE.

    Satır-yerel atamalar satır içi ifadeye, P2 aramaları skaler alt sorguya dönüşür. Orijinal SELECT INTO'nun
    NO_DATA_FOUND davranışı bir ön denetim sorgusuyla, TOO_MANY_ROWS davranışı ORA-01427 eşlemesiyle korunur.
    """

    SRC = "px_src"

    def __init__(self, w: "_Writer", site: Site):
        self.w, self.site = w, site
        self.info: RowLoopInfo = site.info
        self.parts = _loop_parts(site)
        self.guards: list[str] = []        # NOT EXISTS (...) koşulları

    def _environment(self) -> dict[str, str]:
        """Satır-yerel değişken → SQL ifadesi (kaynak sırasıyla, öncekiler sonrakilere yerleştirilerek)."""
        env: dict[str, str] = {self.info.record: self.SRC}
        lookups = {s.node: s for s in self.w.sites if s.pattern == P2}
        for n, st in enumerate(self.parts.prelude, start=1):
            if isinstance(st, P.AssignmentStatementContext):
                target = norm(st.variableRef().getText())
                env[target] = f"({substitute(self.w.text(st.expression()), env)})"
                continue
            site = lookups.get(st)
            if site is None:                                   # tanıyıcı bunu SET_BASED'e izin vermezdi
                raise RewriteError(f"{self.site.id}: SET_BASED için P2 olmayan arama")
            info: LookupInfo = site.info
            alias = f"px_l{n}"
            key = substitute(info.key_expr, env)
            src = (f"{self.w.spell_ref(info.table.name)} {alias} "
                   f"WHERE {alias}.{self.w.spell(info.key_column)} = {key}")
            self.guards.append(f"NOT EXISTS (SELECT 1 FROM {src})")
            for col, target in zip(info.value_columns, info.into_targets):
                env[target] = f"(SELECT {alias}.{self.w.spell(col)} FROM {src})"
        return env

    def generate(self) -> _Code:
        w, info, s = self.w, self.info, self.site.id
        env = self._environment()
        query = w.cursor_query(self.site)

        body = _Code()
        if info.dml.kind == "INSERT" and info.shape == "DML":
            self._insert_select(body, env, query)
        else:
            self._merge(body, env, query)

        c = _Code()
        if not self.guards:
            c.lines = body.lines
            return c
        miss, tmr = f"px_{s}_miss", f"px_{s}_tmr"
        c.add(0, "DECLARE")
        c.add(1, f"{miss} PLS_INTEGER;")
        c.add(1, f"{tmr} EXCEPTION;")
        c.add(1, f"PRAGMA EXCEPTION_INIT({tmr}, -1427);")
        c.add(0, "BEGIN")
        c.add(1, f"SELECT COUNT(*) INTO {miss}")
        self._from_source(c, 1, query)
        for n, g in enumerate(self.guards):
            c.add(1, f"{' WHERE' if n == 0 else '    OR'} {g}")
        c.lines[-1] = (c.lines[-1][0], c.lines[-1][1] + ";")
        c.add(1, f"IF {miss} > 0 THEN")
        c.add(2, "RAISE NO_DATA_FOUND;")
        c.add(1, "END IF;")
        c.extend(1, body)
        c.add(0, "EXCEPTION")
        c.add(1, f"WHEN {tmr} THEN")
        c.add(2, "RAISE TOO_MANY_ROWS;")
        c.add(0, "END;")
        return c

    def _from_source(self, c: _Code, level: int, query: tuple[str, int]) -> None:
        text, col = query
        c.add(level, "  FROM (")
        c.embed(level + 3, text, col)
        c.add(level, f"       ) {self.SRC}")

    def _insert_select(self, c: _Code, env: dict, query) -> None:
        dml = self.info.dml
        head = self.w.insert_head(self.parts.core[0])
        values = [substitute(v, env) for v in dml.insert_values]
        c.add(0, head)
        for n, v in enumerate(values):
            sep = "," if n < len(values) - 1 else ""
            c.add(0, f"{'SELECT' if n == 0 else '      '} {v}{sep}")
        self._from_source(c, 0, query)
        c.lines[-1] = (c.lines[-1][0], c.lines[-1][1] + ";")
        for counter in self.info.counters:
            n = self.w.spell(counter)
            c.add(0, f"{n} := {n} + SQL%ROWCOUNT;")

    def _merge(self, c: _Code, env: dict, query) -> None:
        w, dml, ins = self.w, self.info.dml, self.info.upsert_insert
        target_ctx = self.parts.core[0].getChild(0).dmlTarget()
        qual = w.spell(dml.table.alias) if dml.table.alias else w.text(target_ctx).split(".")[-1]
        cols = w.columns(dml.table.name)
        qualify = {col: qual for col in cols}

        def expr(text: str) -> str:
            return substitute(text, env, qualify)

        target = w.text(target_ctx) + (f" {w.spell(dml.table.alias)}" if dml.table.alias else "")
        text, col = query
        c.add(0, f"MERGE INTO {target}")
        c.add(0, "USING (")
        c.embed(3, text, col)
        c.add(0, f"      ) {self.SRC}")
        on = [f"{qual}.{w.spell(e.column)} = {expr(e.expr)}" for e in dml.where]
        c.add(0, f"   ON ({on[0]}" + ("" if len(on) > 1 else ")"))
        for n, cond in enumerate(on[1:], start=2):
            c.add(0, f"       AND {cond}" + (")" if n == len(on) else ""))
        c.add(0, " WHEN MATCHED THEN")
        sets = [f"{qual}.{w.spell(col_)} = {expr(e)}" for col_, e in dml.set_pairs]
        for n, a in enumerate(sets):
            c.add(0, f"   {'UPDATE SET' if n == 0 else '          '} {a}" + ("," if n < len(sets) - 1 else ""))
        if ins is not None:
            c.add(0, " WHEN NOT MATCHED THEN")
            c.add(0, f"   INSERT ({', '.join(w.spell(col_) for col_ in ins.insert_columns)})")
            c.add(0, f"   VALUES ({', '.join(substitute(v, env) for v in ins.insert_values)})")
        c.lines[-1] = (c.lines[-1][0], c.lines[-1][1] + ";")


def _after_dot(toks: list[Token], t: Token) -> bool:
    i = toks.index(t)
    return i > 0 and toks[i - 1].type == L.DOT


# ================================================================ yazıcı

class _Writer:
    def __init__(self, analysis: SiteAnalysis, config: Config, catalog: Catalog):
        self.result = analysis.parse
        self.stream = analysis.parse.tokens
        self.stream.fill()
        self.tokens = self.stream.tokens
        self.sites = analysis.sites
        self.config = config
        self.catalog = catalog
        self._spelling: dict[str, str] = {}
        for t in self.tokens:
            if t.channel == Token.DEFAULT_CHANNEL and isl.is_name(t):
                self._spelling.setdefault(norm(t.text), t.text)
                if norm(t.text).startswith("PX_"):
                    raise RewriteError(f"kaynakta ayrılmış önekli ad var: {t.text}")
        self._units_at: dict[int, list[_Unit]] = {}
        self._build_units()

    # ---------------------------------------------------------------- birimleri kur
    def _build_units(self) -> None:
        space = SearchSpace(tuple(self.sites))
        active_caches = [
            s for s in self.sites
            if s.pattern == P2 and self.config[s.id] == LOOKUP_CACHE and not space.is_absorbed(self.config, s.id)
        ]
        groups = self._cache_groups(active_caches)
        units: list[_Unit] = [_LookupUnit(s, g) for g in groups for s in g.members]

        p1_spans = {(s.start_token, s.stop_token): s.id for s in self.sites if s.pattern == P1}
        hosted: dict[str, list[_CacheGroup]] = {}
        orphan: dict[tuple[int, int], list[_CacheGroup]] = {}
        for g in groups:
            rng = self._group_range(g)
            if rng in p1_spans:                                  # tek döngünün içinde: o döngünün bloğu
                hosted.setdefault(p1_spans[rng], []).append(g)
            else:
                orphan.setdefault(rng, []).append(g)

        for s in self.sites:
            if s.pattern != P1:
                continue
            alt = self.config[s.id]
            if alt != ROW:
                units.append(_LoopUnit(s, alt, hosted.get(s.id, [])))
            elif s.id in hosted:
                units.append(_HostUnit(s.start_token, s.stop_token, hosted[s.id]))
        for (start, stop), caches in orphan.items():
            units.append(_HostUnit(start, stop, caches))

        for u in units:
            self._units_at.setdefault(u.start, []).append(u)
        for lst in self._units_at.values():
            lst.sort(key=lambda u: -u.stop)                    # en dıştaki önce

    @staticmethod
    def _cache_groups(active: list[Site]) -> list[_CacheGroup]:
        """(tablo, anahtar kolonu) aynı olan aktif önbellekleri gruplar (kaynak sırasıyla)."""
        by_sig: dict[tuple[str, str], list[Site]] = {}
        for s in active:
            by_sig.setdefault((s.info.table.name, s.info.key_column), []).append(s)
        groups, shared = [], 0
        for members in by_sig.values():
            if len(members) == 1:
                groups.append(_CacheGroup(members, f"px_{members[0].id}"))
            else:
                shared += 1
                groups.append(_CacheGroup(members, f"px_c{shared}"))
        return groups

    def _member_range(self, site: Site) -> tuple[int, int]:
        if site.parent is not None:
            parent = next(s for s in self.sites if s.id == site.parent)
            return parent.start_token, parent.stop_token
        return self._host_range(site)

    def _group_range(self, group: _CacheGroup) -> tuple[int, int]:
        """Grubun bildirim + yükleme bloğunun saracağı deyim aralığı.

        Tek üye: eski davranış (ebeveyn P1 açıklığı ya da en dış döngü). Çok üye: üyelerin aralıklarını
        kapsayan, en derin ortak deyim listesindeki ardışık deyimler (varsa ilk deyimin etiketi dahil).
        """
        ranges = {self._member_range(s) for s in group.members}
        if len(ranges) == 1:
            return next(iter(ranges))
        lists = [self._statement_lists(s.node) for s in group.members]
        common = [sl for sl in lists[0] if all(sl in other for other in lists[1:])]
        sl = common[0]                                           # en derin ortak liste
        stmts = sl.statement()
        lo = min(next(i for i, st in enumerate(stmts) if st.stop.tokenIndex >= a) for a, _ in ranges)
        hi = max(max(i for i, st in enumerate(stmts) if st.start.tokenIndex <= b) for _, b in ranges)
        first = stmts[lo]
        k = sl.children.index(first)
        if k > 0 and isinstance(sl.children[k - 1], P.LabelContext):
            first = sl.children[k - 1]
        return first.start.tokenIndex, stmts[hi].stop.tokenIndex

    @staticmethod
    def _statement_lists(node) -> list:
        """Düğümü içeren deyim listeleri, içten dışa."""
        out, p = [], node.parentCtx
        while p is not None:
            if isinstance(p, P.StatementListContext):
                out.append(p)
            p = p.parentCtx
        return out

    def _host_range(self, site: Site) -> tuple[int, int]:
        """P1 dışı bir aramayı içeren en dış döngü deyimi (varsa etiketi dahil)."""
        loop = enclosing(site.node, _LOOP_TYPES)
        while True:
            outer = enclosing(loop, _LOOP_TYPES)
            if outer is None:
                break
            loop = outer
        stmt = loop.parentCtx
        sl = stmt.parentCtx
        k = sl.children.index(stmt)
        first = sl.children[k - 1] if k > 0 and isinstance(sl.children[k - 1], P.LabelContext) else stmt
        return first.start.tokenIndex, stmt.stop.tokenIndex

    # ---------------------------------------------------------------- render
    def render_all(self) -> str:
        last = len(self.tokens) - 1
        while last >= 0 and self.tokens[last].type == Token.EOF:
            last -= 1
        return self.render(0, last, {})

    def render(self, start: int, stop: int, subst: Mapping[str, str],
               protected: frozenset[int] = frozenset(), skip: _Unit | None = None) -> str:
        out: list[str] = []
        prev: Token | None = None
        i = start
        while i <= stop:
            unit = next((u for u in self._units_at.get(i, []) if u is not skip and u.stop <= stop), None)
            if unit is not None:
                out.append(unit.render(self, subst))
                prev = self.tokens[unit.stop]
                i = unit.stop + 1
                continue
            t = self.tokens[i]
            if t.type == Token.EOF:
                break
            piece = t.text
            if t.channel == Token.DEFAULT_CHANNEL:
                if (subst and i not in protected and isl.is_name(t) and norm(t.text) in subst
                        and not (prev is not None and prev.type == L.DOT)):
                    piece = subst[norm(t.text)]
                prev = t
            out.append(piece)
            i += 1
        return "".join(out)

    def render_ctx(self, ctx, subst: Mapping[str, str]) -> str:
        return self.render(ctx.start.tokenIndex, ctx.stop.tokenIndex, subst)

    def render_dml(self, dml: P.DmlStatementContext, subst: Mapping[str, str]) -> str:
        protected = self.protected_in_dml(dml, set(subst))
        return self.render(dml.start.tokenIndex, dml.stop.tokenIndex, subst, protected)

    # ---------------------------------------------------------------- yardımcılar
    def ctx_tokens(self, ctx) -> list[Token]:
        return isl.code_tokens(self.stream, ctx)

    def text(self, ctx) -> str:
        return self.result.text(ctx)

    def column(self, token_index: int) -> int:
        return self.tokens[token_index].column

    def spell(self, name: str) -> str:
        """Normalize addan kaynaktaki yazılışa dön (yoksa güvenli biçimde üret)."""
        if name in self._spelling:
            return self._spelling[name]
        return name if re.fullmatch(r"[A-Z][A-Z0-9_$#]*", name) else f'"{name}"'

    def spell_ref(self, ref: str) -> str:
        return ".".join(self.spell(part) for part in ref.split("."))

    def _has_table(self, table: str) -> bool:
        try:
            self.catalog.columns(table)
            return True
        except UnknownTable:
            return False

    def columns(self, table: str) -> set[str]:
        try:
            return set(self.catalog.columns(table))
        except UnknownTable as e:
            raise RewriteError(f"katalogda tablo yok: {e}") from None

    def protected_in_dml(self, dml: P.DmlStatementContext, names: set[str]) -> frozenset[int]:
        """DML içinde yerine koyma yapılmaması gereken tokenlar.

        INSERT: kolon listesi. UPDATE/DELETE/MERGE: hedef tablonun kolonuyla aynı adı taşıyan çıplak adlar
        (Oracle bu adları kolona çözer).
        """
        stmt = dml.getChild(0)
        toks = self.ctx_tokens(stmt)
        target_stop = stmt.dmlTarget().stop.tokenIndex
        protected = {t.tokenIndex for t in toks if t.tokenIndex <= target_stop}
        if isinstance(stmt, P.InsertStatementContext):
            tail = [t for t in toks if t.tokenIndex > target_stop]
            vi = isl.find_top_level(tail, isl.word("VALUES"))
            if vi >= 0:
                protected |= {t.tokenIndex for t in tail[:vi]}
            return frozenset(protected)
        clashing = names & self.columns(norm_table_ctx(stmt.dmlTarget())) if names else set()
        for k, t in enumerate(toks):
            if isl.is_name(t) and norm(t.text) in clashing and not (k > 0 and toks[k - 1].type == L.DOT):
                protected.add(t.tokenIndex)
        return frozenset(protected)

    def insert_head(self, dml: P.DmlStatementContext) -> str:
        """`INSERT INTO t (kolonlar)` — VALUES öncesi orijinal metin, tek satıra indirgenmiş."""
        stmt = dml.getChild(0)
        toks = self.ctx_tokens(stmt)
        vi = isl.find_top_level(toks, isl.word("VALUES"))
        return " ".join(isl.source_text(self.stream, toks[:vi]).split())

    def cursor_query(self, site: Site) -> tuple[str, int]:
        scope_cursor = _find_cursor_decl(site)
        q = scope_cursor.selectQuery()
        return self.text(q), self.column(q.start.tokenIndex)

    # ---------------------------------------------------------------- önbellek kodu
    def cache_key(self, site: Site):
        info: LookupInfo = site.info
        col = self.catalog.columns(info.table.name).get(info.key_column)             if info.table.name and self._has_table(info.table.name) else None
        key = cache_key_for(col) if col is not None else None
        if key is None:
            raise RewriteError(f"{site.id}: {info.table.name}.{info.key_column} önbellek anahtarı olamaz")
        return key

    def cache_decl(self, group: _CacheGroup) -> _Code:
        info: LookupInfo = group.site.info
        key = self.cache_key(group.site)
        n = group.names
        table = self.spell_ref(info.table.name)
        fields = ", ".join(f"{self.spell(col)} {table}.{self.spell(col)}%TYPE" for col in group.value_columns)
        c = _Code()
        c.add(0, f"TYPE {n['val']} IS RECORD ({fields});")
        c.add(0, f"TYPE {n['map']}_t IS TABLE OF {n['val']} INDEX BY {key.index_by};")
        c.add(0, f"{n['map']} {n['map']}_t;")
        c.add(0, f"TYPE {n['dup']}_t IS TABLE OF BOOLEAN INDEX BY {key.index_by};")
        c.add(0, f"{n['dup']} {n['dup']}_t;")
        return c

    def cache_load(self, group: _CacheGroup) -> _Code:
        info: LookupInfo = group.site.info
        key = self.cache_key(group.site)
        n = group.names
        r = n["row"]
        idx = key.convert(f"{r}.px_k")
        key_col = self.spell(info.key_column)
        select = ", ".join([f"{key_col} AS px_k"]
                           + [f"{self.spell(col)} AS px_v{j}" for j, col in enumerate(group.value_columns, start=1)])
        head = f"FOR {r} IN ("
        pad = " " * len(head)
        c = _Code()
        c.add(0, f"{head}SELECT {select}")
        c.add(0, f"{pad}  FROM {self.spell_ref(info.table.name)}")
        c.add(0, f"{pad} WHERE {key_col} IS NOT NULL) LOOP")
        c.add(1, f"IF {n['map']}.EXISTS({idx}) THEN")
        c.add(2, f"{n['dup']}({idx}) := TRUE;")
        c.add(1, "ELSE")
        for j, col in enumerate(group.value_columns, start=1):
            c.add(2, f"{n['map']}({idx}).{self.spell(col)} := {r}.px_v{j};")
        c.add(1, "END IF;")
        c.add(0, "END LOOP;")
        return c


def norm_table_ctx(target: P.DmlTargetContext) -> str:
    return ".".join(norm(i.getText()) for i in target.qualifiedName().identifier())


def _find_cursor_decl(site: Site) -> P.CursorDeclarationContext:
    from .sites import scope_at
    return scope_at(site.node).cursors[site.info.cursor]
