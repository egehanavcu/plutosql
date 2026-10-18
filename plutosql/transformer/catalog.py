"""Şema kataloğu: tablo kolonları ve tipleri.

Rewriter iki yerde katalog bilgisine ihtiyaç duyar:
  1. LOOKUP_CACHE: Associative Array anahtar tipi (`INDEX BY`) anahtar kolonun tipinden türetilir.
  2. SQL içi ad çözümleme: Oracle'da çıplak bir ad hem hedef tablonun kolonu hem PL/SQL değişkeniyse
     kolon kazanır. Değişken yerine koyma (substitution) yapılırken bu adlar korunur.

`StaticCatalog` testler ve çevrimdışı kullanım içindir; `OracleCatalog` canlı veritabanının
USER_TAB_COLUMNS görünümünü okur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .islands import norm


class UnknownTable(KeyError):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str                  # NUMBER, VARCHAR2, CHAR, DATE, ...
    data_length: int | None = None  # bayt
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True


class Catalog(Protocol):
    def columns(self, table: str) -> Mapping[str, Column]:
        """Normalize tablo adı → {normalize kolon adı: Column}. Bilinmeyen tabloda UnknownTable."""
        ...


# ================================================================ statik katalog

_TYPE_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z_][A-Za-z0-9_ ]*?)\s*"
    r"(?:\(\s*(?P<a>\d+)\s*(?:(?P<unit>BYTE|CHAR)\s*)?(?:,\s*(?P<b>-?\d+)\s*)?\))?\s*$",
    re.IGNORECASE,
)


def parse_type(spec: str) -> tuple[str, int | None, int | None, int | None]:
    """`VARCHAR2(10)`, `NUMBER(9,0)`, `NUMBER`, `INTEGER`, `DATE` → (tip, uzunluk, kesinlik, ölçek)."""
    m = _TYPE_RE.match(spec)
    if not m:
        raise ValueError(f"tip ayrıştırılamadı: {spec!r}")
    t = " ".join(m.group("type").upper().split())
    a = int(m.group("a")) if m.group("a") else None
    b = int(m.group("b")) if m.group("b") else None
    if t in ("INTEGER", "INT", "SMALLINT"):
        return "NUMBER", 22, 38, 0
    if t == "NUMBER":
        return "NUMBER", 22, a, (b if b is not None else (0 if a is not None else None))
    if t in ("VARCHAR2", "VARCHAR", "NVARCHAR2", "CHAR", "NCHAR", "RAW"):
        length = a
        if t.startswith("N") or (m.group("unit") or "").upper() == "CHAR":
            length = a * 4 if a is not None else None   # AL32UTF8 üst sınırı
        return ("VARCHAR2" if t == "VARCHAR" else t), length, None, None
    return t, None, None, None


class StaticCatalog:
    """Sözlükten kurulan katalog: {"TABLO": {"KOLON": "VARCHAR2(3)", ...}, ...}.

    Tip metninin sonunda `NOT NULL` geçerse kolon NOT NULL kabul edilir.
    """

    def __init__(self, tables: Mapping[str, Mapping[str, str]]):
        self._tables: dict[str, dict[str, Column]] = {}
        for table, cols in tables.items():
            parsed = {}
            for col, spec in cols.items():
                nullable = not re.search(r"\bNOT\s+NULL\s*$", spec, re.IGNORECASE)
                spec = re.sub(r"\bNOT\s+NULL\s*$", "", spec, flags=re.IGNORECASE)
                t, length, prec, scale = parse_type(spec)
                parsed[norm(col)] = Column(norm(col), t, length, prec, scale, nullable)
            self._tables[norm(table)] = parsed

    def columns(self, table: str) -> Mapping[str, Column]:
        try:
            return self._tables[table.split(".")[-1]]
        except KeyError:
            raise UnknownTable(table) from None


# ================================================================ Oracle kataloğu

class OracleCatalog:
    """Canlı şemadan (USER_TAB_COLUMNS) okuyan, sonuçları önbellekleyen katalog.

    `connection` bir python-oracledb bağlantısıdır; bu modül oracledb'yi doğrudan içe aktarmaz.
    """

    _SQL = """
        SELECT column_name, data_type, data_length, data_precision, data_scale, nullable
          FROM user_tab_columns
         WHERE table_name = :t
         ORDER BY column_id
    """

    def __init__(self, connection):
        self._conn = connection
        self._cache: dict[str, dict[str, Column]] = {}

    def columns(self, table: str) -> Mapping[str, Column]:
        name = table.split(".")[-1]
        if name not in self._cache:
            with self._conn.cursor() as cur:
                cur.execute(self._SQL, t=name)
                rows = cur.fetchall()
            if not rows:
                raise UnknownTable(table)
            self._cache[name] = {
                r[0]: Column(r[0], r[1], r[2], r[3], r[4], r[5] == "Y") for r in rows
            }
        return self._cache[name]


# ================================================================ önbellek anahtar tipi

@dataclass(frozen=True)
class CacheKey:
    """LOOKUP_CACHE için anahtar tipi ve dönüşümleri.

    index_by:  `INDEX BY` tipi
    convert:   anahtar ifadesini dizi indeksine çeviren şablon
    out_of_domain: indekse sığmayan (orijinal SQL'de zaten eşleşmeyecek) anahtarları yakalayan koşul
    """
    index_by: str
    convert: Callable[[str], str]
    out_of_domain: Callable[[str], str | None]


def cache_key_for(col: Column) -> CacheKey | None:
    """Kolon tipi Associative Array anahtarına anlam kaybı olmadan eşlenebiliyorsa CacheKey, değilse None.

    - VARCHAR2/NVARCHAR2(n)  → INDEX BY VARCHAR2(n); n bayttan uzun anahtar tabloda bulunamaz.
    - NUMBER(p<=9, 0)        → INDEX BY PLS_INTEGER (değer aralığı PLS_INTEGER'a sığar).
    - Diğer NUMBER           → INDEX BY VARCHAR2(64), TO_CHAR ile kanonik metin.
    - CHAR/NCHAR (boşluk doldurmalı karşılaştırma), DATE/TIMESTAMP vb. → desteklenmez.
    """
    if col.data_type in ("VARCHAR2", "NVARCHAR2") and col.data_length:
        n = col.data_length
        return CacheKey(
            index_by=f"VARCHAR2({n})",
            convert=lambda e: e,
            out_of_domain=lambda e, n=n: f"LENGTHB({e}) > {n}",
        )
    if col.data_type == "NUMBER":
        if col.scale == 0 and col.precision is not None and col.precision <= 9:
            # Kesirli bir anahtar PLS_INTEGER'a atanırken yuvarlanır; orijinal SQL'de eşleşmez.
            return CacheKey("PLS_INTEGER", lambda e: e, lambda e: f"{e} <> TRUNC({e})")
        return CacheKey("VARCHAR2(64)", lambda e: f"TO_CHAR({e})", lambda e: None)
    return None
