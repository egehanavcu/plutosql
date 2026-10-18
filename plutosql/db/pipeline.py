"""Referans boru hattının kurulumu ve deneme öncesi sıfırlama."""

from __future__ import annotations

from dataclasses import dataclass

from .scripts import run_script, sql_resource

TABLES = ("REF_FX_RATE", "STG_TXN", "TXN_ENRICHED", "SETTLEMENT", "STG_TXN_SEED", "SETTLEMENT_SEED")
TARGET_TABLES = ("STG_TXN", "TXN_ENRICHED", "SETTLEMENT")     # boru hattının yazdığı tablolar


@dataclass(frozen=True)
class Scale:
    n_txn: int = 20_000
    n_accounts: int = 500
    n_days: int = 30

    def binds(self) -> dict[str, int]:
        return {"n_txn": self.n_txn, "n_accounts": self.n_accounts, "n_days": self.n_days}


def drop_schema(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("BEGIN DBMS_STATS.UNLOCK_SCHEMA_STATS(USER); END;")
        for t in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t} PURGE")
        cur.execute("DROP PROCEDURE IF EXISTS pluto_reset")


def setup(connection, scale: Scale = Scale()) -> None:
    """Şemayı sıfırdan kurar, veriyi üretir, başlangıç durumunu yükler ve istatistikleri sabitler.

    İstatistikler başlangıç durumunda (TXN_ENRICHED boş) toplanıp kilitlenir: otomatik istatistik
    işi denemeler arasında yürütme planlarını değiştiremez.
    """
    drop_schema(connection)
    run_script(connection, sql_resource("schema.sql"))
    run_script(connection, sql_resource("data.sql"), scale.binds())
    reset(connection)
    with connection.cursor() as cur:
        cur.execute("""
            BEGIN
              DBMS_STATS.GATHER_SCHEMA_STATS(USER, no_invalidate => FALSE);
              DBMS_STATS.LOCK_SCHEMA_STATS(USER);
            END;""")


def reset(connection) -> None:
    with connection.cursor() as cur:
        cur.callproc("pluto_reset")


def row_counts(connection) -> dict[str, int]:
    with connection.cursor() as cur:
        return {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
