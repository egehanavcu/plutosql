"""SQL*Plus tarzı betikleri (deyimler `/` satırıyla ayrılır) python-oracledb ile çalıştırma."""

from __future__ import annotations

import re
from collections.abc import Mapping
from importlib import resources

_BIND_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def split_script(text: str) -> list[str]:
    """Yalnızca `/` içeren satırlarda böler; yalnızca yorumdan oluşan parçaları atar.

    PL/SQL bloklarının sonundaki `;` korunur (bloğun parçasıdır). SQL deyimleri `;` olmadan yazılmalıdır.
    """
    statements, current = [], []
    for line in text.splitlines():
        if line.strip() == "/":
            statements.append("\n".join(current))
            current = []
        else:
            current.append(line)
    statements.append("\n".join(current))
    return [s.strip() for s in statements if _has_code(s)]


def _has_code(chunk: str) -> bool:
    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return True
    return False


def bind_names(statement: str) -> set[str]:
    code = re.sub(r"'(?:[^']|'')*'", "''", statement)             # dizgi içindeki ':' bağlama değildir
    code = re.sub(r"--[^\n]*", "", code)
    return {m.lower() for m in _BIND_RE.findall(code)}


def run_script(connection, text: str, binds: Mapping[str, object] | None = None) -> int:
    """Betiği çalıştırır; her deyime yalnızca kullandığı bağlamaları geçirir. Çalışan deyim sayısını döner."""
    binds = {k.lower(): v for k, v in (binds or {}).items()}
    count = 0
    with connection.cursor() as cur:
        for stmt in split_script(text):
            names = bind_names(stmt)
            missing = names - set(binds)
            if missing:
                raise KeyError(f"bağlanmamış değişken(ler): {sorted(missing)}")
            cur.execute(stmt, {n: binds[n] for n in names})
            count += 1
    return count


def sql_resource(name: str) -> str:
    return resources.files("plutosql.db").joinpath("sql", name).read_text(encoding="utf-8")
