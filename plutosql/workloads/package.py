"""İş yükü paketi: bir dizinde PL/SQL bloğu, şema, veri ve tablo rolleri.

plutosql/workloads/library/<ad>/
  manifest.json   {"name", "prefix", "description", "binds", "mutable": [...], "immutable": [...],
                   "seeded": {"TABLO": "TABLO_SEED"}, "spec": {...}}
  schema.sql      tablolar (seed tabloları dahil); "/" ayırıcılı
  data.sql        değişmez tablolar ve seed tabloları için veri; bağlamalar manifest'teki "binds"
  block.sql       optimize edilecek blok

Değişken (mutable) tablolar her denemeden önce TRUNCATE edilir ve seed'i varsa doğrudan yol ile yeniden
yüklenir (`<önek>_RESET` prosedürü otomatik üretilir). Parmak izi değişmez tablolar + seed tablolarından
alınır (ölçüm bağlamı; bkz. harness).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import oracledb

from plutosql.db.scripts import run_script, split_script

LIBRARY = Path(str(resources.files("plutosql.workloads").joinpath("library")))


@dataclass
class WorkloadPackage:
    name: str
    prefix: str
    description: str
    block: str
    schema_sql: str
    data_sql: str
    mutable: list[str]
    immutable: list[str]
    seeded: dict[str, str] = field(default_factory=dict)
    binds: dict = field(default_factory=dict)
    spec: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- dosya
    @classmethod
    def load(cls, name: str, root: Path = LIBRARY) -> "WorkloadPackage":
        d = Path(root) / name
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        return cls(
            name=m["name"], prefix=m["prefix"], description=m.get("description", ""),
            block=(d / "block.sql").read_text(encoding="utf-8"),
            schema_sql=(d / "schema.sql").read_text(encoding="utf-8"),
            data_sql=(d / "data.sql").read_text(encoding="utf-8"),
            mutable=m["mutable"], immutable=m["immutable"], seeded=m.get("seeded", {}),
            binds=m.get("binds", {}), spec=m.get("spec", {}),
        )

    def save(self, root: Path = LIBRARY) -> Path:
        d = Path(root) / self.name
        d.mkdir(parents=True, exist_ok=True)
        manifest = {"name": self.name, "prefix": self.prefix, "description": self.description,
                    "binds": self.binds, "mutable": self.mutable, "immutable": self.immutable,
                    "seeded": self.seeded, "spec": self.spec}
        files = {"manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                 "schema.sql": self.schema_sql, "data.sql": self.data_sql, "block.sql": self.block}
        for fname, text in files.items():
            (d / fname).write_text(text, encoding="utf-8", newline="\n")
        return d

    @property
    def tables(self) -> list[str]:
        return [*self.immutable, *self.mutable, *self.seeded.values()]

    # ---------------------------------------------------------------- veritabanı
    def reset_procedure_sql(self) -> str:
        lines = [f"CREATE OR REPLACE PROCEDURE {self.prefix}_RESET AS", "BEGIN"]
        for t in self.mutable:
            lines.append(f"  EXECUTE IMMEDIATE 'TRUNCATE TABLE {t}';")
        for t in self.mutable:
            if t in self.seeded:
                lines.append(f"  INSERT /*+ APPEND */ INTO {t} SELECT * FROM {self.seeded[t]};")
                lines.append("  COMMIT;")
        lines.append(f"END {self.prefix}_RESET;")
        return "\n".join(lines)

    def drop(self, connection) -> None:
        with connection.cursor() as cur:
            for t in self.tables:
                try:
                    cur.execute(f"BEGIN DBMS_STATS.UNLOCK_TABLE_STATS(USER, '{t}'); END;")
                except oracledb.DatabaseError:               # tablo henüz yok
                    pass
                cur.execute(f"DROP TABLE IF EXISTS {t} PURGE")
            cur.execute(f"DROP PROCEDURE IF EXISTS {self.prefix}_RESET")

    def setup(self, connection) -> None:
        """Tabloları sıfırdan kurar, veriyi üretir, başlangıç durumunu yükler, istatistikleri sabitler."""
        self.drop(connection)
        run_script(connection, self.schema_sql)
        run_script(connection, self.data_sql, self.binds)
        with connection.cursor() as cur:
            cur.execute(self.reset_procedure_sql())
        self.reset(connection)
        with connection.cursor() as cur:
            for t in self.tables:
                cur.execute(f"BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '{t}', no_invalidate => FALSE); "
                            f"DBMS_STATS.LOCK_TABLE_STATS(USER, '{t}'); END;")

    def reset(self, connection) -> None:
        with connection.cursor() as cur:
            cur.callproc(f"{self.prefix}_RESET")

    def fingerprint(self, connection) -> dict[str, list[int]]:
        out = {}
        with connection.cursor() as cur:
            for t in [*self.immutable, *self.seeded.values()]:
                cols = [r[0] for r in cur.execute(
                    "SELECT column_name FROM user_tab_columns WHERE table_name = :t ORDER BY column_id", t=t)]
                sums = ", ".join(f'NVL(SUM(ORA_HASH("{c}")), 0)' for c in cols)
                out[t] = [int(v) for v in cur.execute(f'SELECT COUNT(*), {sums} FROM "{t}"').fetchone()]
        return out

    def row_counts(self, connection) -> dict[str, int]:
        with connection.cursor() as cur:
            return {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.tables}


def available() -> list[str]:
    if not LIBRARY.exists():
        return []
    return sorted(p.name for p in LIBRARY.iterdir() if (p / "manifest.json").exists())


def validate_scripts(pkg: WorkloadPackage) -> None:
    """Betiklerin ayrıştırılabildiğini ve blokta tek deyim olduğunu denetler (veritabanı gerekmez)."""
    if len(split_script(pkg.block)) != 1:
        raise ValueError(f"{pkg.name}: block.sql tek bir blok içermeli")
    split_script(pkg.schema_sql)
    split_script(pkg.data_sql)
