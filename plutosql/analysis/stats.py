"""İstatistiksel karşılaştırma.

Birincil metrik IGD+ (düşük iyi; HV'nin aksine doymaz). İkincil: HV oranı, tam sınırı bulma oranı.

- İş yükü × ρ başına çift karşılaştırma: Mann–Whitney U (iki yönlü; tohumlar bağımsız, gruplar
  farklı büyüklükte olabilir) ve Vargha–Delaney A12 = P(X, Y'den iyi) + ½·P(eşit).
  A12 yorumu (Vargha & Delaney, 2000): |A12 − 0.5| ≥ 0.06 küçük, ≥ 0.14 orta, ≥ 0.21 büyük.
  Çoklu karşılaştırma: aynı karşılaştırma ailesi (ör. llm_named vs random, tüm iş yükleri) içinde Holm.
- İş yükleri genelinde: koşul başına iş yükü ortalaması → Friedman testi, ortalama sıralar ve
  Nemenyi kritik farkı (α = 0.05).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from scipy import stats as st

# Nemenyi (Demšar 2006) q_0.05 değerleri, k = koşul sayısı
_NEMENYI_Q05 = {2: 1.960, 3: 2.343, 4: 2.569, 5: 2.728, 6: 2.850, 7: 2.949, 8: 3.031, 9: 3.102, 10: 3.164}


def a12(x: Sequence[float], y: Sequence[float], lower_is_better: bool = True) -> float:
    """Vargha–Delaney A12: x'ten rastgele bir değerin y'den rastgele bir değerden iyi olma olasılığı."""
    wins = ties = 0
    for a in x:
        for b in y:
            if a == b:
                ties += 1
            elif (a < b) == lower_is_better:
                wins += 1
    return (wins + 0.5 * ties) / (len(x) * len(y))


def magnitude(a: float) -> str:
    d = abs(a - 0.5)
    return "büyük" if d >= 0.21 else "orta" if d >= 0.14 else "küçük" if d >= 0.06 else "ihmal"


def mann_whitney(x: Sequence[float], y: Sequence[float]) -> float:
    if len(set(x) | set(y)) == 1:
        return 1.0
    return float(st.mannwhitneyu(x, y, alternative="two-sided").pvalue)


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm–Bonferroni düzeltilmiş p değerleri (girdiyle aynı sırada)."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def _metric(row: dict, metric: str) -> float:
    v = row[metric]
    if metric == "front_found":
        return 1.0 if row["front_found_at"] is not None else 0.0
    return float("inf") if v is None else float(v)


def group(rows: list[dict], metric: str) -> dict[tuple[str, float, str], list[float]]:
    out: dict[tuple[str, float, str], list[float]] = defaultdict(list)
    for r in rows:
        out[(r["workload"], r["rho"], r["condition"])].append(_metric(r, metric))
    return out


@dataclass(frozen=True)
class PairResult:
    workload: str
    rho: float
    a: str
    b: str
    n_a: int
    n_b: int
    median_a: float
    median_b: float
    a12: float
    p: float
    p_holm: float = float("nan")


def pairwise(rows: list[dict], a: str, b: str, metric: str = "igd_plus",
             lower_is_better: bool = True) -> list[PairResult]:
    """a ile b'yi ortak oldukları her (iş yükü, ρ) hücresinde karşılaştırır; Holm tüm hücreler üzerinde."""
    g = group(rows, metric)
    cells = sorted({(w, rho) for (w, rho, c) in g if c == a} & {(w, rho) for (w, rho, c) in g if c == b})
    res = []
    for w, rho in cells:
        x, y = g[(w, rho, a)], g[(w, rho, b)]
        res.append(PairResult(w, rho, a, b, len(x), len(y), statistics.median(x), statistics.median(y),
                              a12(x, y, lower_is_better), mann_whitney(x, y)))
    adj = holm([r.p for r in res])
    return [PairResult(**{**r.__dict__, "p_holm": p}) for r, p in zip(res, adj)]


@dataclass(frozen=True)
class FriedmanResult:
    rho: float
    conditions: tuple[str, ...]
    workloads: tuple[str, ...]
    mean_ranks: dict[str, float]
    statistic: float
    p: float
    critical_difference: float


def friedman(rows: list[dict], rho: float, conditions: Sequence[str], metric: str = "igd_plus",
             lower_is_better: bool = True) -> FriedmanResult | None:
    """Koşul başına iş yükü ortalamaları üzerinde Friedman testi (yalnızca tüm koşulların bulunduğu iş yükleri)."""
    g = group([r for r in rows if r["rho"] == rho], metric)
    workloads = sorted({w for (w, _, c) in g} )
    workloads = [w for w in workloads if all((w, rho, c) in g for c in conditions)]
    if len(workloads) < 2 or len(conditions) < 2:
        return None
    sign = 1 if lower_is_better else -1
    table = [[sign * statistics.fmean(g[(w, rho, c)]) for c in conditions] for w in workloads]
    ranks = [st.rankdata(row) for row in table]                       # 1 = en iyi
    mean_ranks = {c: float(statistics.fmean(r[i] for r in ranks)) for i, c in enumerate(conditions)}
    stat, p = st.friedmanchisquare(*zip(*table)) if len(conditions) >= 3 else (float("nan"), float("nan"))
    k, n = len(conditions), len(workloads)
    cd = _NEMENYI_Q05.get(k, float("nan")) * math.sqrt(k * (k + 1) / (6 * n))
    return FriedmanResult(rho, tuple(conditions), tuple(workloads), mean_ranks, float(stat), float(p), cd)


def summary_table(rows: list[dict], conditions: Sequence[str]) -> list[dict]:
    """(iş yükü, ρ, koşul) başına medyan IGD+, ortalama HV, tam sınır oranı."""
    out = []
    by = defaultdict(list)
    for r in rows:
        by[(r["workload"], r["rho"], r["condition"])].append(r)
    for (w, rho, c), rs in sorted(by.items()):
        if c not in conditions:
            continue
        out.append({
            "workload": w, "rho": rho, "condition": c, "n": len(rs),
            "igd_plus_median": statistics.median(_metric(r, "igd_plus") for r in rs),
            "hv_mean": statistics.fmean(r["hv_ratio"] for r in rs),
            "front_found_rate": statistics.fmean(_metric(r, "front_found") for r in rs),
        })
    return out
