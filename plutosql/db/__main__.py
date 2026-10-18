"""Veritabanı yardımcı komutları.

  python -m plutosql.db wait                 # veritabanı bağlantı kabul edene kadar bekle
  python -m plutosql.db check                # sürüm, yetkiler, ölçüm istatistikleri
  python -m plutosql.db setup [--txn N ...]  # şema + sentetik veri (mevcut tabloları siler)
  python -m plutosql.db reset                # başlangıç durumuna dön
"""

from __future__ import annotations

import argparse
import sys
import time

import oracledb

from .connection import connect, load_config
from .pipeline import Scale, reset, row_counts, setup

MEASURED_STATS = ("consistent gets", "session pga memory", "session pga memory max",
                  "session uga memory", "session uga memory max")


def cmd_wait(args) -> int:
    cfg = load_config()
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            with connect(cfg) as conn:
                print(f"bağlandı: {cfg.dsn} ({conn.version})")
                return 0
        except oracledb.Error as e:
            if time.monotonic() > deadline:
                print(f"zaman aşımı: {e}", file=sys.stderr)
                return 1
            time.sleep(5)


def cmd_check(args) -> int:
    with connect() as conn, conn.cursor() as cur:
        print("sürüm             :", conn.version)
        print("kullanıcı / PDB   :", cur.execute(
            "SELECT USER, SYS_CONTEXT('USERENV', 'CON_NAME') FROM dual").fetchone())
        print("plsql_optimize_level:", cur.execute(
            "SELECT value FROM v$parameter WHERE name = 'plsql_optimize_level'").fetchone()
            if _can_read_parameters(cur) else "(v$parameter okunamıyor)")
        rows = cur.execute(
            """SELECT n.name, s.value FROM v$mystat s JOIN v$statname n ON n.statistic# = s.statistic#
                WHERE n.name IN (%s) ORDER BY n.name""" % ", ".join(f"'{n}'" for n in MEASURED_STATS)
        ).fetchall()
        for name, value in rows:
            print(f"  {name:<24} {value}")
        try:
            for table, n in row_counts(conn).items():
                print(f"  {table:<16} {n:>10} satır")
        except oracledb.Error:
            print("  şema kurulu değil (python -m plutosql.db setup)")
    return 0


def _can_read_parameters(cur) -> bool:
    try:
        cur.execute("SELECT 1 FROM v$parameter WHERE ROWNUM = 1").fetchone()
        return True
    except oracledb.Error:
        return False


def cmd_setup(args) -> int:
    scale = Scale(n_txn=args.txn, n_accounts=args.accounts, n_days=args.days)
    t0 = time.monotonic()
    with connect() as conn:
        setup(conn, scale)
        counts = row_counts(conn)
    print(f"kurulum tamam ({time.monotonic() - t0:.1f} sn), ölçek: {scale}")
    for table, n in counts.items():
        print(f"  {table:<16} {n:>10} satır")
    return 0


def cmd_reset(args) -> int:
    with connect() as conn:
        reset(conn)
    print("sıfırlandı")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plutosql.db")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=cmd_wait)

    sub.add_parser("check").set_defaults(func=cmd_check)

    p = sub.add_parser("setup")
    p.add_argument("--txn", type=int, default=Scale.n_txn)
    p.add_argument("--accounts", type=int, default=Scale.n_accounts)
    p.add_argument("--days", type=int, default=Scale.n_days)
    p.set_defaults(func=cmd_setup)

    sub.add_parser("reset").set_defaults(func=cmd_reset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
