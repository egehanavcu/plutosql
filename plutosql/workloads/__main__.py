"""İş yükü kütüphanesi komutları.

  python -m plutosql.workloads list               # kütüphanedeki paketler ve beklenen |𝒳/~|
  python -m plutosql.workloads generate [ad ...]  # specs.py → library/<ad>/ (dosyaları yeniden yazar)
  python -m plutosql.workloads setup <ad>         # paketin tablolarını ve verisini veritabanında kur
"""

from __future__ import annotations

import argparse
import time

from .generator import generate
from .package import WorkloadPackage, available, validate_scripts
from .specs import SPECS


def cmd_list(args) -> int:
    for name in available():
        pkg = WorkloadPackage.load(name)
        size = pkg.spec.get("expected_canonical_size", "?")
        print(f"{name:<24} |𝒳/~|≈{size:<6} {pkg.description}")
    return 0


def cmd_generate(args) -> int:
    names = args.names or list(SPECS)
    unknown = [n for n in names if n not in SPECS]
    if unknown:
        raise SystemExit(f"bilinmeyen tanım(lar): {unknown}")
    for name in names:
        pkg = generate(SPECS[name])
        validate_scripts(pkg)
        path = pkg.save()
        print(f"{name}: {path} (beklenen |𝒳/~| = {SPECS[name].canonical_size()})")
    return 0


def cmd_setup(args) -> int:
    from plutosql.db.connection import connect

    pkg = WorkloadPackage.load(args.name)
    t0 = time.monotonic()
    with connect() as conn:
        pkg.setup(conn)
        counts = pkg.row_counts(conn)
    print(f"{pkg.name} kuruldu ({time.monotonic() - t0:.1f} sn)")
    for table, n in counts.items():
        print(f"  {table:<20} {n:>10} satır")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plutosql.workloads")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    p = sub.add_parser("generate")
    p.add_argument("names", nargs="*", help=f"verilmezse hepsi: {', '.join(SPECS)}")
    p.set_defaults(func=cmd_generate)
    p = sub.add_parser("setup")
    p.add_argument("name")
    p.set_defaults(func=cmd_setup)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
