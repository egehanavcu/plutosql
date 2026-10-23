"""Karşılaştırmalı deney: algoritmalar × bütçe oranları ρ × tohumlar, ground truth üzerinde.

Her koşu için:
  - HV oranı (son arşiv), toplamsal ε, ε-kapsama,
  - her değerlendirmeden sonraki HV oranı (yakınsama eğrisi, bütçe adımı başına),
  - gerçek sınırın tamamen bulunduğu bütçe adımı (yoksa None).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from plutosql.analysis.metrics import (
    Normalizer, additive_epsilon, epsilon_coverage, hypervolume, igd_plus, nondominated,
)

from .base import SearchAlgorithm, archive_of
from .replay import ReplayEvaluator


@dataclass
class GroundTruth:
    normalizer: Normalizer
    front: list[tuple[float, float]]         # normalize gerçek Pareto sınırı
    hv: float
    eps: tuple[float, float]                 # normalize gürültü toleransı

    @classmethod
    def from_records(cls, records: Sequence[dict], noise_eps_raw: tuple[float, float]) -> "GroundTruth":
        pts = [(r["f1"], r["f2"]) for r in records if r["valid"] and r["f1"] is not None]
        norm = Normalizer.from_points(pts)
        front = nondominated(norm(p) for p in pts)
        return cls(norm, front, hypervolume(front), norm.scale(noise_eps_raw))


@dataclass
class RunRecord:
    algorithm: str
    rho: float
    budget: int
    seed: int
    evaluations: int                          # bütçeden düşen benzersiz kod sayısı
    hv_ratio: float
    eps_additive: float
    eps_coverage: float
    front_found_at: int | None                # gerçek sınır noktalarının hepsi bulunduğunda kullanılan bütçe
    hv_curve: list[float]                     # bütçe adımı k (1..budget) sonundaki HV oranı
    igd_plus: float | None = None             # normalize uzayda IGD+ (düşük iyi)
    archive: list[list[float]] = field(default_factory=list)   # son arşiv, ham (f1, f2)
    meta: dict = field(default_factory=dict)  # algoritmaya özgü iz (LLM: kararlar, yeniden sorma, düşme)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_run(history, gt: GroundTruth, budget: int) -> tuple[list[float], int | None]:
    """Bütçe adımı başına HV oranı eğrisi ve gerçek sınırın tamamlandığı adım."""
    curve, seen, pts = [], set(), []
    found_at = None
    front = set(gt.front)
    for e in history:
        if e.digest in seen:
            continue
        seen.add(e.digest)
        if e.valid and e.f1 is not None:
            pts.append(gt.normalizer(e.objectives))
        curve.append(hypervolume(pts) / gt.hv if gt.hv else 1.0)
        if found_at is None and front <= set(pts):
            found_at = len(seen)
    curve += [curve[-1] if curve else 0.0] * (budget - len(curve))
    return curve[:budget], found_at


def run_grid(algorithms: dict[str, SearchAlgorithm], replay: ReplayEvaluator, gt: GroundTruth,
             rhos: Sequence[float], seeds: Sequence[int]) -> list[RunRecord]:
    size = replay.space.size
    out = []
    for rho in rhos:
        budget = max(1, math.ceil(rho * size))
        for name, algo in algorithms.items():
            for seed in seeds:
                ev = replay.fresh(budget)
                result = algo.run(ev, seed)
                raw_arch = [list(e.objectives) for e in archive_of(result.history)]
                arch = [gt.normalizer(tuple(p)) for p in raw_arch]
                curve, found_at = evaluate_run(result.history, gt, budget)
                out.append(RunRecord(
                    algorithm=name, rho=rho, budget=budget, seed=seed,
                    evaluations=ev.budget_used,
                    hv_ratio=hypervolume(arch) / gt.hv if gt.hv else 1.0,
                    eps_additive=additive_epsilon(arch, gt.front),
                    eps_coverage=epsilon_coverage(arch, gt.front, gt.eps),
                    front_found_at=found_at,
                    hv_curve=curve,
                    igd_plus=igd_plus(arch, gt.front),
                    archive=raw_arch,
                    meta=result.meta,
                ))
    return out


def summarize(runs: Sequence[RunRecord]) -> list[dict]:
    """(algoritma, ρ) başına özet: HV oranı ortalama/std/medyan, ε, kapsama, sınırı tamamen bulma oranı."""
    groups: dict[tuple[str, float], list[RunRecord]] = {}
    for r in runs:
        groups.setdefault((r.algorithm, r.rho), []).append(r)
    rows = []
    for (algo, rho), rs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        hv = [r.hv_ratio for r in rs]
        rows.append({
            "algorithm": algo,
            "rho": rho,
            "budget": rs[0].budget,
            "runs": len(rs),
            "hv_mean": statistics.fmean(hv),
            "hv_std": statistics.pstdev(hv),
            "hv_median": statistics.median(hv),
            "eps_median": statistics.median(r.eps_additive for r in rs),
            "coverage_mean": statistics.fmean(r.eps_coverage for r in rs),
            "front_found_rate": sum(r.front_found_at is not None for r in rs) / len(rs),
        })
    return rows
