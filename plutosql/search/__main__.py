"""Arama deneyleri (ground truth üzerinde tekrar oynatma).

  python -m plutosql.search compare --results results/pipeline_baseline/<anahtar> \
      [--algorithms random greedy sa] [--rhos 0.1 0.2 0.3 0.4] [--seeds 30]

Ham koşular <results>/experiments/<zaman>.jsonl dosyasına yazılır; özet tablo ekrana basılır.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from plutosql.analysis.metrics import noise_epsilon

from .algorithms import ALGORITHMS
from .experiment import GroundTruth, run_grid, summarize
from .llm import CachedClient, GeminiClient, LLMPolicy, RandomClient
from .replay import ReplayEvaluator


def cmd_compare(args) -> int:
    directory = Path(args.results)
    replay = ReplayEvaluator.from_results(directory)
    records = replay.records
    gt = GroundTruth.from_records(records, noise_epsilon(records))
    print(f"|𝒳| = {replay.space.size}, |𝒳/~| = {len(replay.space.canonical_configs())}, "
          f"gerçek sınır: {len(gt.front)} nokta, ε (normalize) = ({gt.eps[0]:.4f}, {gt.eps[1]:.4f})")

    algorithms = {name: ALGORITHMS[name]() for name in args.algorithms if name != "llm"}
    llm_client = None
    if "llm" in args.algorithms:
        inner = GeminiClient.from_env(args.llm_model, thinking_budget=args.llm_thinking_budget)
        llm_client = CachedClient(inner, directory / "llm_cache" / f"{inner.model}.jsonl")
        algorithms["llm"] = _llm_policy(args, llm_client)
        thinking = "model varsayılanı" if args.llm_thinking_budget is None else args.llm_thinking_budget
        print(f"LLM: {inner.model}, sıcaklık={args.llm_temperature}, düşünme bütçesi={thinking}, "
              f"alan bilgisi={'kapalı' if args.llm_no_hints or args.llm_opaque_labels else 'açık'}, "
              f"opak etiketler={'açık' if args.llm_opaque_labels else 'kapalı'}")
    runs = run_grid(algorithms, replay, gt, args.rhos, range(args.seeds))
    if llm_client is not None:
        print(f"LLM API çağrısı: {llm_client.calls} (geri kalanı önbellekten)")

    out = directory / "experiments" / f"{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"settings": {"algorithms": args.algorithms, "rhos": args.rhos, "seeds": args.seeds,
                                         "llm_model": llm_client.model if llm_client else None,
                                         "llm_temperature": args.llm_temperature,
                                         "llm_thinking_budget": args.llm_thinking_budget,
                                         "llm_domain_hints": not (args.llm_no_hints or args.llm_opaque_labels),
                                         "llm_opaque_labels": args.llm_opaque_labels,
                                         "llm_history": args.llm_history, "llm_recent": args.llm_recent,
                                         "llm_proposals": args.llm_proposals,
                                         "eps_normalized": gt.eps, "front": gt.front}}) + "\n")
        for r in runs:
            f.write(json.dumps(r.to_dict()) + "\n")

    print(f"\n{'ρ':>5} {'B':>3}  {'algoritma':<8} {'HV ort.':>8} {'±std':>7} {'HV med.':>8} "
          f"{'ε+ med.':>8} {'kapsama':>8} {'tam sınır':>9}")
    for row in summarize(runs):
        print(f"{row['rho']:>5.2f} {row['budget']:>3}  {row['algorithm']:<8} {row['hv_mean']:>8.3f} "
              f"{row['hv_std']:>7.3f} {row['hv_median']:>8.3f} {row['eps_median']:>8.3f} "
              f"{row['coverage_mean']:>8.2f} {row['front_found_rate']:>9.0%}")
    llm_runs = [r for r in runs if r.algorithm == "llm"]
    if llm_runs:
        steps = sum(len(r.meta.get("decisions", [])) for r in llm_runs)
        fb = sum(r.meta.get("fallbacks", 0) for r in llm_runs)
        rt = sum(r.meta.get("retries", 0) for r in llm_runs)
        bad = sum(r.meta.get("malformed", 0) for r in llm_runs)
        print(f"\nLLM adımları: {steps}, yeniden sorma: {rt}, bozuk yanıt: {bad}, "
              f"rastgeleye düşme: {fb} ({fb / max(1, steps):.0%})")
    print(f"\nham koşular: {out}")
    return 0


def _llm_policy(args, client) -> LLMPolicy:
    return LLMPolicy(client=client, temperature=args.llm_temperature, domain_hints=not args.llm_no_hints,
                     opaque_labels=args.llm_opaque_labels, history=args.llm_history, recent=args.llm_recent,
                     proposals=args.llm_proposals)


def cmd_estimate(args) -> int:
    """LLM deneyinin çağrı ve istem boyutunu API'ye gitmeden tahmin eder (rastgele yanıt veren istemci)."""
    directory = Path(args.results)
    replay = ReplayEvaluator.from_results(directory)
    gt = GroundTruth.from_records(replay.records, noise_epsilon(replay.records))
    policy = _llm_policy(args, RandomClient(seed=0))
    runs = run_grid({"llm": policy}, replay, gt, args.rhos, range(args.seeds))
    calls = sum(r.meta["api_calls"] for r in runs)
    prompt_tokens = sum(r.meta["prompt_chars"] for r in runs) / 4          # ~4 karakter / token
    output_tokens = calls * 100 * args.llm_proposals    # ölçülen: ~100 çıktı tokenı / önerilen konfigürasyon
    print(f"|𝒳| = {replay.space.size}, koşu = {len(runs)} (ρ = {args.rhos}, tohum = {args.seeds}), "
          f"geçmiş = {args.llm_history}, k = {args.llm_proposals}")
    print(f"çağrı: {calls}  (koşu başına ort. {calls / len(runs):.1f}; yeniden sorma {sum(r.meta['retries'] for r in runs)})")
    print(f"istem tokenı: ~{prompt_tokens / 1e6:.2f} M  (çağrı başına ort. ~{prompt_tokens / max(1, calls):.0f}, "
          f"en büyük koşu ~{max(r.meta['prompt_chars'] for r in runs) / 4 / 1e3:.0f} k)")
    print(f"çıktı tokenı: ~{output_tokens / 1e6:.2f} M  (+ modelin düşünme tokenları; ölçülen ort. ~700/çağrı)")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--results", required=True, help="exhaustive çıktı dizini (measurements.jsonl + space.json)")
    common.add_argument("--rhos", nargs="+", type=float, default=[0.1, 0.2, 0.3, 0.4])
    common.add_argument("--seeds", type=int, default=30)
    common.add_argument("--llm-model", default=None, help="varsayılan: GEMINI_MODEL ya da gemini-3.8-flash")
    common.add_argument("--llm-temperature", type=float, default=0.2)
    common.add_argument("--llm-thinking-budget", type=int, default=None,
                        help="düşünme token bütçesi (0: kapalı; verilmezse model varsayılanı)")
    common.add_argument("--llm-opaque-labels", action="store_true",
                        help="alternatif adlarını ve alan bağlamını gizle; yalnızca yapı + ölçümler (ablation)")
    common.add_argument("--llm-no-hints", action="store_true", help="alternatif anlamlarını istemden çıkar (ablation)")
    common.add_argument("--llm-history", choices=["full", "compact"], default="full",
                        help="compact: Pareto + son N + site etki özeti (büyük bütçeler için)")
    common.add_argument("--llm-recent", type=int, default=10, help="compact geçmişte gösterilen son değerlendirme")
    common.add_argument("--llm-proposals", type=int, default=1, help="çağrı başına önerilen konfigürasyon sayısı (k)")

    parser = argparse.ArgumentParser(prog="python -m plutosql.search")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare", parents=[common], help="algoritma karşılaştırması (ground truth üzerinde)")
    p.add_argument("--algorithms", nargs="+", default=list(ALGORITHMS), choices=sorted([*ALGORITHMS, "llm"]))
    p.set_defaults(func=cmd_compare)
    e = sub.add_parser("estimate", parents=[common], help="LLM çağrı/token tahmini (API çağrısı yok)")
    e.set_defaults(func=cmd_estimate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
