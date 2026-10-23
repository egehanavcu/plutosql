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


SUMMARY = Path("results/summary")


def cmd_collect(args) -> int:
    from plutosql.analysis.collect import collect, save

    rows = collect(Path(args.root))
    save(rows, SUMMARY / "runs.jsonl")
    print(f"{len(rows)} koşu → {SUMMARY / 'runs.jsonl'}")
    return 0


def cmd_stats(args) -> int:
    import io
    import statistics

    from plutosql.analysis.collect import load
    from plutosql.analysis.stats import friedman, magnitude, pairwise

    rows = load(SUMMARY / "runs.jsonl")
    excluded = sorted(set(args.exclude or []))
    rows = [r for r in rows if r["workload"] not in excluded]
    out = io.StringIO()

    def w(line=""):
        print(line)
        out.write(line + "\n")

    present = sorted({r["condition"] for r in rows})
    w(f"koşullar: {present}; iş yükleri: {len({r['workload'] for r in rows})}; koşu: {len(rows)}")
    if excluded:
        w(f"hariç tutulan iş yükleri: {excluded}")
    for metric, lower in (("igd_plus", True), ("hv_ratio", False)):
        w(f"\n=== Friedman ({metric}, iş yükü ortalamaları; ortalama sıra, 1 = en iyi) ===")
        classical = [c for c in present if not c.startswith("llm")]
        for rho in sorted({r["rho"] for r in rows}):
            conds = [c for c in present if any(r["condition"] == c and r["rho"] == rho for r in rows)]
            # (a) klasikler, tüm iş yükleri; (b) tüm koşullar, hepsinin bulunduğu iş yükleri
            for label, cs in (("klasik", classical), ("tümü", conds)):
                if label == "tümü" and cs == classical:
                    continue
                fr = friedman(rows, rho, cs, metric, lower)
                if fr is None:
                    continue
                ranks = "  ".join(f"{c}={v:.2f}" for c, v in sorted(fr.mean_ranks.items(), key=lambda kv: kv[1]))
                w(f"ρ={rho:.1f} [{label:<6}] N={len(fr.workloads):>2} χ²={fr.statistic:5.1f} p={fr.p:.2g} "
                  f"CD={fr.critical_difference:.2f}  {ranks}")

    w("\n=== Çift karşılaştırmalar (IGD+; Mann–Whitney U, Holm; A12 = P(ilk koşul daha iyi)) ===")
    llms = [c for c in present if c.startswith("llm")]
    pairs = [(l, c) for l in llms for c in ("random", "sa", "greedy", "nsga2", "expert")]
    # ablation çiftleri: adlar (ön bilgi) ve ölçüm geri bildirimi katkıları
    for a, b in (("llm_named", "llm_opaque"), ("llm_named", "llm_blind"), ("llm_blind", "llm_opaque")):
        if a in llms and b in llms:
            pairs.append((a, b))
    for a, b in pairs:
        res = pairwise(rows, a, b)
        if not res:
            continue
        for rho in sorted({r.rho for r in res}):
            rr = [r for r in res if r.rho == rho]
            better = sum(r.p_holm < 0.05 and r.a12 > 0.5 for r in rr)
            worse = sum(r.p_holm < 0.05 and r.a12 < 0.5 for r in rr)
            med = statistics.median(r.a12 for r in rr)
            w(f"{a:>11} vs {b:<11} ρ={rho:.1f}: {len(rr):>2} iş yükü | anlamlı iyi {better:>2}, anlamlı kötü {worse:>2}, "
              f"fark yok {len(rr) - better - worse:>2} | medyan A12={med:.2f} ({magnitude(med)})")
    SUMMARY.mkdir(parents=True, exist_ok=True)
    name = "stats.txt" if not excluded else f"stats_excl_{'_'.join(w.split('_')[0] for w in excluded)}.txt"
    (SUMMARY / name).write_text(out.getvalue(), encoding="utf-8", newline="\n")
    return 0


def cmd_expert_sensitivity(args) -> int:
    """Uzman tablosunun bileşenlerini alternatiflerle değiştirip LLM (adlar) karşılaştırmasını tekrarlar."""
    import csv
    import statistics as stt

    from plutosql.analysis.collect import load, save
    from plutosql.analysis.expert_sensitivity import FACTORS, evaluate, run_variants, variants

    cache = SUMMARY / "expert_variants.jsonl"
    if cache.exists() and not args.rerun:
        variant_rows = load(cache)
    else:
        print(f"{len(variants())} varyant koşuluyor (ρ={args.rho})", flush=True)
        variant_rows = run_variants(args.rho)
        save(variant_rows, cache)
    res = evaluate(variant_rows, load(SUMMARY / "runs.jsonl"), args.rho)
    with (SUMMARY / "expert_sensitivity.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0]))
        w.writeheader()
        w.writerows(res)
    orig = next(r for r in res if r["asıl"])
    n = len(res)

    def dist(key, fmt="{:.2f}"):
        xs = sorted(r[key] for r in res)
        return (f"min {fmt.format(xs[0])}  medyan {fmt.format(stt.median(xs))}  max {fmt.format(xs[-1])}  "
                f"(asıl {fmt.format(orig[key])})")

    better_than_orig = sum(r["uzman_ort_IGD+"] < orig["uzman_ort_IGD+"] for r in res)
    print(f"{n} varyant, ρ={args.rho}")
    print(f"uzman ort. IGD+       : {dist('uzman_ort_IGD+', '{:.4f}')}; asıldan iyi varyant {better_than_orig}/{n - 1}")
    print(f"uzman Friedman sırası : {dist('uzman_sıra')}")
    print(f"sıra farkı (uzman−LLM): {dist('sıra_farkı')}; CD={orig['CD']:.2f}; "
          f"anlamlı olduğu varyant {sum(r['farkı_anlamlı'] for r in res)}/{n}")
    print(f"LLM anlamlı iyi       : {dist('llm_iyi', '{:.0f}')}")
    print(f"LLM anlamlı kötü      : {dist('llm_kötü', '{:.0f}')}")
    print(f"LLM medyan A12        : {dist('llm_A12_medyan')}")
    print(f"uzman klasiklerin en iyisi: {sum(r['uzman_klasik_en_iyi'] for r in res)}/{n} varyant")
    print("bileşen etkisi (seçenek başına ortalama uzman sırası; ilk seçenek asıl tablo):")
    for k in FACTORS:
        opts: dict[str, list[float]] = {}
        for r in res:
            opts.setdefault(r[k], []).append(r["uzman_sıra"])
        print(f"  {k:<10} " + "  ".join(f"{o}={stt.fmean(v):.2f}" for o, v in opts.items()))
    return 0


def cmd_sensitivity(args) -> int:
    """İki ground truth (ör. AUTO / MANUAL) karşılaştırması + klasik algoritma sıralamaları."""
    import statistics as stt

    from plutosql.analysis.collect import CONDITIONS
    from plutosql.analysis.metrics import noise_epsilon
    from plutosql.analysis.validity import sensitivity
    from plutosql.search.experiment import GroundTruth, run_grid
    from plutosql.search.replay import ReplayEvaluator

    space_a, rec_a = _load(Path(args.a))
    _, rec_b = _load(Path(args.b))
    rep = sensitivity(space_a, rec_a, rec_b)
    print(f"n={rep.n}  Kendall τ: f1={rep.kendall_f1:.3f} f2={rep.kendall_f2:.3f}  "
          f"|P*| A={rep.front_a} B={rep.front_b} Jaccard={rep.front_jaccard:.2f}  "
          f"B sınırının A'daki IGD+={rep.igd_b_front_in_a:.4f}  medyan f2(B)/f2(A)={rep.f2_median_ratio:.2f}")
    classical = [c for c in CONDITIONS if c.kind == "classical"]
    for label, d in (("A", Path(args.a)), ("B", Path(args.b))):
        replay = ReplayEvaluator.from_results(d)
        gt = GroundTruth.from_records(replay.records, noise_epsilon(replay.records))
        cells = []
        for rho in (0.1, 0.2, 0.4):
            means = {}
            for c in classical:
                runs = run_grid({c.name: c.algorithm()}, replay, gt, [rho], range(30))
                means[c.name] = stt.fmean(r.igd_plus for r in runs)
            order = " < ".join(sorted(means, key=means.get))
            cells.append(f"ρ={rho}: {order}")
        print(f"{label} sıralama (IGD+ ortalaması): " + " | ".join(cells))
    return 0


def cmd_live_replay(args) -> int:
    """Canlı (taze ölçümlü) arama koşularını tekrar oynatmayla karşılaştırır. Veritabanı gerekir."""
    import json as js
    import math

    from plutosql.analysis.metrics import Normalizer, nondominated
    from plutosql.analysis.validity import compare_live_replay
    from plutosql.db.connection import connect, load_config
    from plutosql.harness.evaluator import Evaluator
    from plutosql.harness.protocol import Protocol
    from plutosql.harness.workload import get_workload
    from plutosql.search.algorithms import ALGORITHMS
    from plutosql.search.replay import ReplayEvaluator
    from plutosql.transformer import OracleCatalog

    gt_dir = next(Path("results", args.workload).glob("*/space.json")).parent
    replay = ReplayEvaluator.from_results(gt_dir)
    by_digest = {r["digest"]: r for r in replay.records}
    valid = [r for r in replay.records if r["valid"]]
    norm = Normalizer.from_points([(r["f1"], r["f2"]) for r in valid])
    front = nondominated(norm((r["f1"], r["f2"])) for r in valid)
    budget = max(1, math.ceil(args.rho * replay.space.size))

    db = load_config()
    admin = connect(db)
    catalog = OracleCatalog(admin)
    workload = get_workload(args.workload)
    out_path = Path("results/validity") / f"live_replay_{args.workload}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"{args.workload}: B={budget} (ρ={args.rho}); canlı koşular taze ölçümle (depo yok)")
    for name in args.algorithms:
        for seed in range(args.seeds):
            live = Evaluator(workload, admin, db, catalog, Protocol(), store=None, budget=budget)
            if live.space.ids != replay.space.ids:
                raise SystemExit("canlı ve kayıtlı uzay uyuşmuyor")
            live_res = ALGORITHMS[name]().run(live, seed)
            replay_res = ALGORITHMS[name]().run(replay.fresh(budget), seed)
            row = compare_live_replay(name, seed, budget, live_res.history, replay_res.history, by_digest, norm, front)
            invalid = sum(1 for e in live_res.history if not e.valid)
            print(f"  {name:<7} tohum={seed}  aynı dizi={row.same_sequence!s:<5} ortak önek={row.shared_prefix:>3}/{budget}  "
                  f"IGD+ canlı={row.igd_live:.4f} replay={row.igd_replay:.4f}  "
                  f"sapma f1={row.f1_rel_dev:.2%} f2={row.f2_rel_dev:.2%}  geçersiz={invalid}", flush=True)
            with out_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(js.dumps({"workload": args.workload, "rho": args.rho, **row.__dict__,
                                  "live_invalid": invalid}) + "\n")
    admin.close()
    return 0


def cmd_runtime(args) -> int:
    """f1/f2 ile duvar saati süresinin ilişkisi (tüm ground truth'lar) → results/summary/runtime.csv"""
    import csv
    import statistics as stt

    from plutosql.analysis.collect import ground_truth_dirs
    from plutosql.analysis.validity import runtime_relation

    reports = []
    for workload, d in ground_truth_dirs(Path(args.root)).items():
        space, records = _load(d)
        reports.append(runtime_relation(workload, space, records))
    print(f"{'iş yükü':<22} {'n':>4} {'ρ(f1,t)':>8} {'ρ(f2,t)':>8} {'CV(t)':>6} {'hız min-f1':>10} "
          f"{'hız en iyi':>10} {'sınır pişmanlığı':>16}")
    for r in reports:
        print(f"{r.workload:<22} {r.n:>4} {r.spearman_f1_time:>8.2f} {r.spearman_f2_time:>8.2f} "
              f"{r.time_cv_median:>6.1%} {r.speedup_min_f1:>9.1f}× {r.speedup_best:>9.1f}× {r.front_time_regret:>16.2f}")
    print(f"medyan ρ(f1,t)={stt.median(r.spearman_f1_time for r in reports):.2f}  "
          f"medyan ρ(f2,t)={stt.median(r.spearman_f2_time for r in reports):.2f}  "
          f"medyan sınır pişmanlığı={stt.median(r.front_time_regret for r in reports):.2f}")
    SUMMARY.mkdir(parents=True, exist_ok=True)
    with (SUMMARY / "runtime.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reports[0].__dict__))
        w.writeheader()
        w.writerows(r.__dict__ for r in reports)
    return 0


def cmd_figures(args) -> int:
    from plutosql.analysis.figures import make_all

    for path in make_all():
        print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plutosql.analysis")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("coupling", cmd_coupling), ("front", cmd_front)):
        p = sub.add_parser(name)
        p.add_argument("--results", required=True)
        p.set_defaults(func=fn)
    p = sub.add_parser("collect", help="tüm koşuları saklanan veriden yeniden üret → results/summary/runs.jsonl")
    p.add_argument("--root", default="results")
    p.set_defaults(func=cmd_collect)
    p = sub.add_parser("stats", help="Friedman + çift karşılaştırmalar → results/summary/stats.txt")
    p.add_argument("--exclude", nargs="*", help="hariç tutulacak iş yükleri (ör. geliştirmede kullanılanlar)")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("expert-sensitivity", help="uzman tablosunun duyarlılığı → results/summary/expert_sensitivity.*")
    p.add_argument("--rho", type=float, default=0.1)
    p.add_argument("--rerun", action="store_true", help="kayıtlı varyant koşularını yok say")
    p.set_defaults(func=cmd_expert_sensitivity)
    p = sub.add_parser("runtime", help="amaçların duvar saati süresiyle ilişkisi → results/summary/runtime.csv")
    p.add_argument("--root", default="results")
    p.set_defaults(func=cmd_runtime)
    sub.add_parser("figures", help="tez grafikleri + veri tabloları → results/figures/").set_defaults(func=cmd_figures)
    p = sub.add_parser("sensitivity", help="iki ground truth'un karşılaştırması (ör. AUTO / MANUAL)")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.set_defaults(func=cmd_sensitivity)
    p = sub.add_parser("live-replay", help="canlı ölçümle tekrar oynatmanın karşılaştırması (veritabanı gerekir)")
    p.add_argument("--workload", required=True)
    p.add_argument("--rho", type=float, default=0.2)
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--algorithms", nargs="+", default=["random", "greedy", "sa"])
    p.set_defaults(func=cmd_live_replay)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
