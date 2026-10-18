"""Analiz komutları.

  python -m plutosql.analysis coupling --results results/<iş yükü>/<anahtar>
  python -m plutosql.analysis front    --results results/<iş yükü>/<anahtar>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from plutosql.analysis.coupling import additivity, format_report
from plutosql.analysis.pareto import pareto_front
from plutosql.search.replay import load_records
from plutosql.transformer.space import SearchSpace


def _load(directory: Path):
    space = SearchSpace.from_description(json.loads((directory / "space.json").read_text(encoding="utf-8")))
    records = list(load_records(directory / "measurements.jsonl", space).values())
    return space, records


def cmd_coupling(args) -> int:
    space, records = _load(Path(args.results))
    print(f"|𝒳/~| = {len(space.canonical_configs())}, geçerli ölçüm = {sum(r['valid'] for r in records)}")
    for objective in ("f1", "f2"):
        for scale in ("raw", "log"):
            print(format_report(additivity(space, records, objective, scale)))
    return 0


def cmd_front(args) -> int:
    space, records = _load(Path(args.results))
    valid = [r for r in records if r["valid"]]
    front = pareto_front(valid, key=lambda r: (r["f1"], r["f2"]))
    pga = [r["f2"] for r in valid]
    cg = [r["f1"] for r in valid]
    print(f"geçerli: {len(valid)}/{len(records)}  CG aralığı: {min(cg):.0f}–{max(cg):.0f}  "
          f"ΔPGA aralığı: {min(pga) / 1024:.0f}–{max(pga) / 1024:.0f} KB")
    print(f"Pareto sınırı: {len(front)} nokta")
    for r in front:
        cfg = " ".join(f"{k}={v}" for k, v in space.canonicalize(r["config"]).items())
        print(f"  {cfg:<64} CG={r['f1']:>8.0f}  ΔPGA={r['f2'] / 1024:>7.0f} KB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plutosql.analysis")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("coupling", cmd_coupling), ("front", cmd_front)):
        p = sub.add_parser(name)
        p.add_argument("--results", required=True)
        p.set_defaults(func=fn)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
