"""Çift yönlü multiset doğrulaması.

Bir çalıştırmanın gözlemlenebilir sonucu (Outcome):
  - hedef tabloların multiset'i: tüm kolonlar üzerinden GROUP BY + COUNT(*) (NULL'lar tek grup olur,
    satır çoklukları korunur),
  - OUT parametre değerleri,
  - fırlatılan istisna (ORA kodu) ya da None.
İki sonuç karşılaştırılırken her tablo için A∖B ve B∖A ayrı ayrı raporlanır.
"""

from __future__ import annotations

import decimal
from collections import Counter
from dataclasses import dataclass, field

import oracledb


def _exact_numbers(cursor, metadata):
    # NUMBER değerlerini Decimal olarak al: ikili kayan nokta yuvarlaması eşitliği bozmasın.
    if metadata.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(decimal.Decimal, arraysize=cursor.arraysize)
    return None


def table_multiset(connection, table: str) -> Counter:
    with connection.cursor() as cur:
        cols = [r[0] for r in cur.execute(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :t ORDER BY column_id", t=table)]
        if not cols:
            raise ValueError(f"tablo bulunamadı: {table}")
        col_list = ", ".join(f'"{c}"' for c in cols)
        cur.outputtypehandler = _exact_numbers
        rows = cur.execute(f'SELECT {col_list}, COUNT(*) FROM "{table}" GROUP BY {col_list}').fetchall()
    return Counter({tuple(r[:-1]): int(r[-1]) for r in rows})


@dataclass
class Outcome:
    tables: dict[str, Counter]
    out_params: dict[str, object] = field(default_factory=dict)
    error: int | None = None

    @classmethod
    def capture(cls, connection, tables, out_params=None, error=None) -> "Outcome":
        return cls({t: table_multiset(connection, t) for t in tables}, dict(out_params or {}), error)


@dataclass(frozen=True)
class Difference:
    kind: str                 # TABLE | OUT | ERROR
    name: str
    detail: str


def compare(expected: Outcome, actual: Outcome, sample: int = 3) -> list[Difference]:
    """Boş liste: eşdeğer. Aksi halde her farklılık için bir kayıt."""
    diffs: list[Difference] = []
    if expected.error != actual.error:
        diffs.append(Difference("ERROR", "exception", f"beklenen={_ora(expected.error)} gerçek={_ora(actual.error)}"))
    for name in sorted(set(expected.out_params) | set(actual.out_params)):
        e, a = expected.out_params.get(name), actual.out_params.get(name)
        if e != a:
            diffs.append(Difference("OUT", name, f"beklenen={e!r} gerçek={a!r}"))
    for table in sorted(set(expected.tables) | set(actual.tables)):
        e, a = expected.tables.get(table, Counter()), actual.tables.get(table, Counter())
        missing, extra = e - a, a - e                   # çift yönlü: eksik ve fazladan satırlar
        if missing or extra:
            diffs.append(Difference("TABLE", table,
                                    f"eksik={sum(missing.values())} {_sample(missing, sample)}; "
                                    f"fazla={sum(extra.values())} {_sample(extra, sample)}"))
    return diffs


def _ora(code: int | None) -> str:
    return "yok" if code is None else f"ORA-{code:05d}"


def _sample(c: Counter, n: int) -> str:
    items = list(c.items())[:n]
    return "[" + ", ".join(f"{row}×{k}" for row, k in items) + ("" if len(c) <= n else ", …") + "]"
