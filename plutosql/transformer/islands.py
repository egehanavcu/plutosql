"""Opak SQL adaları üzerinde token düzeyinde hafif analiz.

Gramer SQL ifadelerinin gövdesini opak token dizisi (sqlTail / selectItems) olarak bırakır.
Site tanıyıcılarının ihtiyaç duyduğu sınırlı yapı (INSERT kolon/değer listeleri, UPDATE SET çiftleri,
WHERE içindeki `kolon = ifade` eşitlikleri) burada çıkarılır. Tanınmayan biçimler için None döner;
çağıran taraf bunu "alternatif uygulanamaz" olarak yorumlar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from antlr4 import CommonTokenStream, ParserRuleContext, Token

from .generated.MiniPlsqlLexer import MiniPlsqlLexer as L

_NAME_RE = re.compile(r'^(?:[A-Za-z][A-Za-z0-9_$#]*|"[^"]+")$')


def norm(name: str) -> str:
    """Oracle ad normalizasyonu: tırnaksız adlar büyük harfe çevrilir, tırnaklılar olduğu gibi kalır."""
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1]
    return name.upper()


def is_name(tok: Token) -> bool:
    return bool(_NAME_RE.match(tok.text))


def code_tokens(stream: CommonTokenStream, ctx: ParserRuleContext | None) -> list[Token]:
    """Bir bağlamın varsayılan kanaldaki tokenları (yorum/boşluk hariç)."""
    if ctx is None or ctx.start is None or ctx.stop is None or ctx.stop.tokenIndex < ctx.start.tokenIndex:
        return []
    return [
        t for t in stream.tokens[ctx.start.tokenIndex: ctx.stop.tokenIndex + 1]
        if t.channel == Token.DEFAULT_CHANNEL
    ]


def source_text(stream: CommonTokenStream, toks: list[Token]) -> str:
    """Token dizisinin orijinal kaynak metni (aradaki yorum/boşluklar dahil)."""
    if not toks:
        return ""
    return stream.getText(toks[0].tokenIndex, toks[-1].tokenIndex)


def compact(toks: list[Token]) -> str:
    """Boşluksuz, büyük harfli karşılaştırma anahtarı."""
    return "".join(t.text for t in toks).upper()


def split_top_level(toks: list[Token], sep_type: int) -> list[list[Token]]:
    """Parantez derinliği 0 olan ayraçlardan böler. `BETWEEN a AND b` içindeki AND ayraç sayılmaz."""
    parts: list[list[Token]] = [[]]
    depth = 0
    pending_between = False
    for t in toks:
        if t.type == L.LPAREN:
            depth += 1
        elif t.type == L.RPAREN:
            depth -= 1
        elif depth == 0 and t.type == L.BETWEEN:
            pending_between = True
        elif depth == 0 and t.type == sep_type:
            if sep_type == L.AND and pending_between:
                pending_between = False
            else:
                parts.append([])
                continue
        parts[-1].append(t)
    return parts


def find_top_level(toks: list[Token], pred) -> int:
    depth = 0
    for i, t in enumerate(toks):
        if t.type == L.LPAREN:
            depth += 1
        elif t.type == L.RPAREN:
            depth -= 1
        elif depth == 0 and pred(t):
            return i
    return -1


def word(text: str):
    """Lexer'da anahtar kelime olmayan SQL kelimeleri (SET, VALUES, WHERE ...) için eşleyici."""
    upper = text.upper()
    return lambda t: t.text.upper() == upper


def strip_parens(toks: list[Token]) -> list[Token] | None:
    """`( ... )` ise içini döndürür; dıştaki parantezler tek bir çift değilse None."""
    if len(toks) < 2 or toks[0].type != L.LPAREN or toks[-1].type != L.RPAREN:
        return None
    depth = 0
    for i, t in enumerate(toks):
        if t.type == L.LPAREN:
            depth += 1
        elif t.type == L.RPAREN:
            depth -= 1
            if depth == 0 and i != len(toks) - 1:
                return None
    return toks[1:-1]


def column_ref(toks: list[Token], qualifiers: set[str]) -> str | None:
    """`kolon` ya da `nitelik.kolon` (nitelik ∈ qualifiers) biçimindeyse normalize kolon adı."""
    if len(toks) == 1 and is_name(toks[0]):
        return norm(toks[0].text)
    if (len(toks) == 3 and is_name(toks[0]) and toks[1].type == L.DOT and is_name(toks[2])
            and norm(toks[0].text) in qualifiers):
        return norm(toks[2].text)
    return None


def referenced_names(toks: list[Token]) -> set[str]:
    """Bir token dizisinde geçen, nokta ile başlamayan (yani nitelikli adın ilk parçası olan) adlar."""
    names = set()
    prev = None
    for t in toks:
        if is_name(t) and not (prev is not None and prev.type == L.DOT):
            names.add(norm(t.text))
        prev = t
    return names


def record_fields(toks: list[Token], record: str) -> list[str]:
    """`record.alan` referanslarındaki alan adları (kaynak sırasıyla, tekrarsız)."""
    fields: list[str] = []
    for i in range(len(toks) - 2):
        if (is_name(toks[i]) and norm(toks[i].text) == record and toks[i + 1].type == L.DOT
                and is_name(toks[i + 2]) and (i == 0 or toks[i - 1].type != L.DOT)):
            f = norm(toks[i + 2].text)
            if f not in fields:
                fields.append(f)
    return fields


# ---------------------------------------------------------------- yapılandırılmış ada bilgileri

@dataclass(frozen=True)
class Equality:
    """WHERE içindeki `hedef_kolon = ifade` eşitliği."""
    column: str
    expr: str


@dataclass(frozen=True)
class TableRef:
    name: str            # normalize tablo adı (şema dahil olabilir: "APP.T")
    alias: str | None

    @property
    def qualifiers(self) -> set[str]:
        q = {self.name.split(".")[-1], self.name}
        if self.alias:
            q.add(self.alias)
        return q


def table_ref(name_toks: list[Token], alias_tok: Token | None) -> TableRef:
    name = ".".join(norm(t.text) for t in name_toks if t.type != L.DOT)
    return TableRef(name=name, alias=norm(alias_tok.text) if alias_tok is not None else None)


def parse_equalities(stream: CommonTokenStream, toks: list[Token], table: TableRef,
                     variables: set[str]) -> list[Equality] | None:
    """`a = x AND b = y ...` biçimindeki koşulu ayrıştırır.

    Her eşitlikte bir taraf hedef tablonun kolonu, diğer taraf PL/SQL ifadesi olmalıdır.
    Çıplak bir ad, kapsamda bildirilmiş bir PL/SQL değişkeniyse kolon sayılmaz.
    """
    result = []
    for conj in split_top_level(toks, L.AND):
        eq = find_top_level(conj, lambda t: t.type == L.EQ)
        if eq <= 0 or eq == len(conj) - 1:
            return None
        left, right = conj[:eq], conj[eq + 1:]
        lcol = _target_column(left, table, variables)
        rcol = _target_column(right, table, variables)
        if lcol and not rcol:
            col, expr = lcol, right
        elif rcol and not lcol:
            col, expr = rcol, left
        else:
            return None
        if _mentions_table(expr, table):
            return None
        result.append(Equality(column=col, expr=source_text(stream, expr)))
    return result


def _target_column(toks: list[Token], table: TableRef, variables: set[str]) -> str | None:
    if len(toks) == 1 and norm(toks[0].text) in variables:
        return None
    return column_ref(toks, table.qualifiers)


def _mentions_table(toks: list[Token], table: TableRef) -> bool:
    for i in range(len(toks) - 1):
        if is_name(toks[i]) and toks[i + 1].type == L.DOT and norm(toks[i].text) in table.qualifiers:
            if i == 0 or toks[i - 1].type != L.DOT:
                return True
    return False
