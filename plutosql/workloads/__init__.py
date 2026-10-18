"""Deneylerde kullanılan PL/SQL iş yükleri."""

from __future__ import annotations

from importlib import resources


def workload_source(name: str) -> str:
    """`plutosql/workloads/sql/<name>.sql` içeriği."""
    return resources.files(__name__).joinpath("sql", f"{name}.sql").read_text(encoding="utf-8")
