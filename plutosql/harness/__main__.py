"""Harness komutları.

  python -m plutosql.harness sites                      # siteler, alternatifler, |𝒳|, |𝒳/~|
  python -m plutosql.harness measure s1=BULK_500 s2=LOOKUP_CACHE s3=ROW
  python -m plutosql.harness exhaustive                 # tüm kanonik konfigürasyonlar → ground truth

Ölçümler results/<iş yükü>/<bağlam özeti>/measurements.jsonl dosyasına eklenir; aynı bağlamdaki
önceki ölçümler yeniden kullanılır (yarıda kalan koşu kaldığı yerden devam eder).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from plutosql.analysis.pareto import pareto_front
from plutosql.db.connection import ROOT, connect, load_config
from plutosql.transformer import OracleCatalog

from .evaluator import Evaluation, Evaluator
from .protocol import Protocol
from .store import MeasurementStore
from .workload import Workload, get_workload, workload_names



def measurement_context(workload: Workload, admin, protocol: Protocol) -> tuple[dict, str]:
    """Ölçümlerin geçerli olduğu koşullar ve dizin anahtarı. Yalnızca değişmeyen durum girer."""
    context = {
        "workload": workload.name,
        "source_sha256": hashlib.sha256(workload.source.encode("utf-8")).hexdigest(),
        "data": workload.fingerprint(admin),
        "protocol": protocol.to_dict(),
        "db_version": admin.version,
    }
    key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:12]
    return context, key


def build_evaluator(args) -> Evaluator:
    workload = get_workload(args.workload)
    db = load_config()
    admin = connect(db)
    protocol = Protocol(warmups=args.warmups, repeats=args.repeats)
    context, key = measurement_context(workload, admin, protocol)
    store = MeasurementStore(Path(args.results) / workload.name / key / "measurements.jsonl", context)
    return Evaluator(workload, admin, db, OracleCatalog(admin), protocol, store=store)


def fmt_row(ev: Evaluation) -> str:
    cfg = " ".join(f"{k}={v}" for k, v in ev.config.items())
    if ev.f1 is None:
        return f"{cfg:<48} {'—':>10} {'—':>10}  {ev.error}"
    flag = "" if ev.valid else "  GEÇERSİZ: " + " | ".join(ev.differences)[:200]
    src = "" if ev.measured_now else "  (depo)"
    return f"{cfg:<48} {ev.f1:>10.0f} {ev.f2 / 1024:>10.0f}{src}{flag}"


HEADER = f"{'konfigürasyon':<48} {'CG (f1)':>10} {'ΔPGA KB':>10}"


def cmd_sites(args) -> int:
    workload = get_workload(args.workload)
    with connect() as admin:
        analysis = workload.analyze(OracleCatalog(admin))
    from plutosql.transformer import SearchSpace
    space = SearchSpace(tuple(analysis.sites))
    print(json.dumps(space.describe(), ensure_ascii=False, indent=2))
    for r in analysis.rejections:
        print("elendi:", r)
    return 0


def cmd_measure(args) -> int:
    ev_ = build_evaluator(args)
    x = dict(kv.split("=", 1) for kv in args.config)
    full = {**ev_.space.default(), **x}
    print(HEADER)
    print(fmt_row(ev_.evaluate(full)))
    print(f"depo: {ev_.store.path}")
    return 0


def cmd_exhaustive(args) -> int:
    ev_ = build_evaluator(args)
    configs = ev_.space.canonical_configs()
    space_file = ev_.store.path.parent / "space.json"
    # Tekrar oynatma (plutosql.search) uzayı veritabanı olmadan buradan kurar.
    space_file.write_text(json.dumps(ev_.space.describe(), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    print(f"|𝒳| = {ev_.space.size}, |𝒳/~| = {len(configs)}, depo: {ev_.store.path} ({len(ev_.store)} kayıt)")
    print(HEADER)
    t0 = time.monotonic()
    results = []
    for x in configs:
        ev = ev_.evaluate(x)
        results.append(ev)
        print(fmt_row(ev), flush=True)
    print(f"süre: {time.monotonic() - t0:.0f} sn")

    valid = [e for e in results if e.valid and e.f1 is not None]
    front = pareto_front(valid, key=lambda e: e.objectives)
    print(f"\nPareto sınırı ({len(front)} / {len(valid)} geçerli konfigürasyon):")
    print(HEADER)
    for e in front:
        print(fmt_row(e))
    invalid = [e for e in results if not e.valid]
    if invalid:
        print(f"\n{len(invalid)} geçersiz konfigürasyon", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plutosql.harness")
    parser.add_argument("--workload", default="pipeline_baseline", choices=workload_names())
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--results", default=str(ROOT / "results"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sites").set_defaults(func=cmd_sites)
    p = sub.add_parser("measure")
    p.add_argument("config", nargs="*", help="site=alternatif (verilmeyen siteler varsayılan)")
    p.set_defaults(func=cmd_measure)
    sub.add_parser("exhaustive").set_defaults(func=cmd_exhaustive)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
