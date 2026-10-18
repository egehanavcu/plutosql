"""İş yükü tanımı ve bir konfigürasyonun çalıştırılabilir hale getirilmesi.

İki tür iş yükü desteklenir:
  - Anonim blok: üretilen blok her denemede doğrudan çalıştırılır.
  - Saklı prosedür: üretilen `CREATE OR REPLACE PROCEDURE` ölçümden önce bir kez derlenir
    (PLSQL_OPTIMIZE_LEVEL doğrulanır, ADR-001); denemelerde `Invocation` ile çağrılır ve
    OUT parametreleri doğrulamaya katılır.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property

import oracledb

from plutosql.db import pipeline
from plutosql.db.scripts import split_script
from plutosql.transformer import Catalog, SiteAnalysis, find_sites, parse
from plutosql.workloads import workload_source

PLSQL_OPTIMIZE_LEVEL = 2


@dataclass(frozen=True)
class Invocation:
    """Saklı prosedür çağrısı: IN değerleri ve OUT parametre tipleri (python tipi: int, str, float...)."""
    procedure: str
    in_args: Mapping[str, object] = field(default_factory=dict)
    out_args: Mapping[str, type] = field(default_factory=dict)

    def call_text(self) -> str:
        names = [*self.in_args, *self.out_args]
        args = ", ".join(f"{n} => :{n}" for n in names)
        return f"BEGIN {self.procedure}({args}); END;"


@dataclass
class Workload:
    name: str
    source: str
    target_tables: tuple[str, ...]
    reset: Callable[[oracledb.Connection], None]
    fingerprint: Callable[[oracledb.Connection], dict]    # ölçüm bağlamı için değişmeyen veri durumu
    invocation: Invocation | None = None

    def analyze(self, catalog: Catalog) -> SiteAnalysis:
        return find_sites(parse(self.source), catalog)

    @cached_property
    def is_procedure(self) -> bool:
        return self.invocation is not None


class CompilationError(Exception):
    pass


@dataclass
class Executable:
    """Bir konfigürasyonun ölçüme hazır hali."""
    workload: Workload
    block: str | None = None              # anonim blok metni (prosedür iş yükünde None)

    def run(self, cursor) -> dict[str, object]:
        """Çalıştırır; OUT parametre değerlerini döndürür. Veritabanı hatası çağırana yayılır."""
        if self.block is not None:
            cursor.execute(self.block)
            return {}
        inv = self.workload.invocation
        out_vars = {n: cursor.var(t) for n, t in inv.out_args.items()}
        cursor.execute(inv.call_text(), {**inv.in_args, **out_vars})
        return {n: v.getvalue() for n, v in out_vars.items()}


def prepare(connection: oracledb.Connection, workload: Workload, code: str) -> Executable:
    """Üretilen kodu çalıştırılabilir hale getirir. Prosedürse derler ve derleme ayarını doğrular."""
    (stmt,) = split_script(code)
    if not workload.is_procedure:
        return Executable(workload, block=stmt)

    name = workload.invocation.procedure.split(".")[-1].upper()
    with connection.cursor() as cur:
        cur.execute(f"ALTER SESSION SET PLSQL_OPTIMIZE_LEVEL = {PLSQL_OPTIMIZE_LEVEL}")
        try:
            cur.execute(stmt)
        except oracledb.DatabaseError as e:
            # python-oracledb derleme uyarısını hata olarak bildirebilir; ayrıntı USER_ERRORS'ta.
            if e.args[0].code != 24344:
                raise
        errors = cur.execute(
            "SELECT line, position, text FROM user_errors WHERE name = :n AND type = 'PROCEDURE' ORDER BY sequence",
            n=name).fetchall()
        if errors:
            raise CompilationError("; ".join(f"{l}:{p} {t.strip()}" for l, p, t in errors))
        level = cur.execute(
            "SELECT plsql_optimize_level FROM user_plsql_object_settings WHERE name = :n AND type = 'PROCEDURE'",
            n=name).fetchone()
        if level is None or level[0] != PLSQL_OPTIMIZE_LEVEL:
            raise CompilationError(f"{name}: PLSQL_OPTIMIZE_LEVEL={level} (beklenen {PLSQL_OPTIMIZE_LEVEL})")
    return Executable(workload)


# ================================================================ hazır iş yükleri

def pipeline_workload() -> Workload:
    """Referans boru hattı: Staging → Transformation → Settlement (anonim blok)."""
    return Workload(
        name="pipeline_baseline",
        source=workload_source("pipeline_baseline"),
        target_tables=pipeline.TARGET_TABLES,
        reset=pipeline.reset,
        fingerprint=pipeline.seed_fingerprint,
    )


def package_workload(name: str) -> Workload:
    """Kütüphane paketinden (plutosql/workloads/library/<ad>) iş yükü."""
    from plutosql.workloads.package import WorkloadPackage

    return workload_from_package(WorkloadPackage.load(name))


def workload_from_package(pkg) -> Workload:
    return Workload(
        name=pkg.name,
        source=pkg.block,
        target_tables=tuple(pkg.mutable),
        reset=pkg.reset,
        fingerprint=pkg.fingerprint,
    )


def workload_names() -> list[str]:
    from plutosql.workloads.package import available

    return ["pipeline_baseline", *available()]


def get_workload(name: str) -> Workload:
    return pipeline_workload() if name == "pipeline_baseline" else package_workload(name)
