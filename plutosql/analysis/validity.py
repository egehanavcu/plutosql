"""Geçerlilik tehditleri deneyleri.

1) Protokol duyarlılığı (ör. WORKAREA_SIZE_POLICY = AUTO / MANUAL): aynı iş yükünün iki ground truth'u
   karşılaştırılır — konfigürasyon sıralamasının korunması (Kendall τ), Pareto sınırlarının örtüşmesi
   (Jaccard), bir sınırın diğerinin normalize uzayında IGD+ değeri ve klasik algoritma sıralaması.

2) Tekrar oynatmanın canlı doğrulaması: aynı algoritma/tohum/bütçe ile canlı (taze ölçümlü) ve ground
   truth üzerinden tekrar oynatılan koşular karşılaştırılır — istenen konfigürasyon dizileri aynı mı,
   canlı ölçümler ground truth'tan ne kadar sapıyor, ulaşılan arşivin kalitesi (ground truth değerleriyle
   IGD+) ne kadar farklı.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from scipy import stats as st

from plutosql.analysis.metrics import Normalizer, igd_plus, nondominated
from plutosql.analysis.pareto import pareto_front


def _by_config(records: list[dict], space) -> dict[tuple, dict]:
    return {tuple(space.canonicalize(r["config"]).values()): r for r in records if r["valid"]}


@dataclass(frozen=True)
class SensitivityReport:
    n: int
    kendall_f1: float
    kendall_f2: float
    front_a: int
    front_b: int
    front_jaccard: float
    igd_b_front_in_a: float          # B'nin sınırındaki konfigürasyonlar A'nın değerleriyle ölçülünce A'nın sınırına uzaklığı
    f2_median_ratio: float           # medyan f2(B) / f2(A)


def sensitivity(space, records_a: list[dict], records_b: list[dict]) -> SensitivityReport:
    a, b = _by_config(records_a, space), _by_config(records_b, space)
    keys = sorted(set(a) & set(b))
    f1a, f1b = [a[k]["f1"] for k in keys], [b[k]["f1"] for k in keys]
    f2a, f2b = [a[k]["f2"] for k in keys], [b[k]["f2"] for k in keys]
    front_a = set(pareto_front(keys, key=lambda k: (a[k]["f1"], a[k]["f2"])))
    front_b = set(pareto_front(keys, key=lambda k: (b[k]["f1"], b[k]["f2"])))
    norm = Normalizer.from_points([(a[k]["f1"], a[k]["f2"]) for k in keys])
    true_front = nondominated(norm((a[k]["f1"], a[k]["f2"])) for k in keys)
    chosen = [norm((a[k]["f1"], a[k]["f2"])) for k in front_b]
    return SensitivityReport(
        n=len(keys),
        kendall_f1=float(st.kendalltau(f1a, f1b).statistic),
        kendall_f2=float(st.kendalltau(f2a, f2b).statistic),
        front_a=len(front_a), front_b=len(front_b),
        front_jaccard=len(front_a & front_b) / len(front_a | front_b),
        igd_b_front_in_a=igd_plus(chosen, true_front),
        f2_median_ratio=statistics.median(y / x for x, y in zip(f2a, f2b) if x),
    )


@dataclass(frozen=True)
class LiveReplayRow:
    algorithm: str
    seed: int
    budget: int
    same_sequence: bool              # canlı ve tekrar oynatılan koşu aynı konfigürasyonları aynı sırayla istedi mi
    shared_prefix: int               # ortak başlangıç dizisi uzunluğu (benzersiz kod)
    igd_live: float                  # canlı arşiv konfigürasyonlarının ground truth değerleriyle IGD+
    igd_replay: float
    f1_rel_dev: float                # canlı ölçümün ground truth'tan medyan göreli sapması
    f2_rel_dev: float


def _sequence(history) -> list[str]:
    seen, out = set(), []
    for e in history:
        if e.digest not in seen:
            seen.add(e.digest)
            out.append(e.digest)
    return out


def archive_quality(history, gt_by_digest: dict[str, dict], norm: Normalizer, front) -> float:
    """Arşivin (geçmişin baskılanmamış kısmı, ground truth değerleriyle) gerçek sınıra IGD+ uzaklığı."""
    pts = [(gt_by_digest[d]["f1"], gt_by_digest[d]["f2"]) for d in _sequence(history)
           if d in gt_by_digest and gt_by_digest[d]["valid"]]
    return igd_plus([norm(p) for p in nondominated(pts)], front)


def compare_live_replay(algorithm: str, seed: int, budget: int, live_history, replay_history,
                        gt_by_digest: dict[str, dict], norm: Normalizer, front) -> LiveReplayRow:
    ls, rs = _sequence(live_history), _sequence(replay_history)
    prefix = 0
    for x, y in zip(ls, rs):
        if x != y:
            break
        prefix += 1
    devs1, devs2 = [], []
    for e in live_history:
        g = gt_by_digest.get(e.digest)
        if g and e.f1 is not None and g["f1"]:
            devs1.append(abs(e.f1 - g["f1"]) / g["f1"])
            devs2.append(abs(e.f2 - g["f2"]) / g["f2"])
    return LiveReplayRow(
        algorithm, seed, budget, ls == rs, prefix,
        archive_quality(live_history, gt_by_digest, norm, front),
        archive_quality(replay_history, gt_by_digest, norm, front),
        statistics.median(devs1) if devs1 else 0.0,
        statistics.median(devs2) if devs2 else 0.0,
    )


# ================================================================ amaçların süreyle ilişkisi

@dataclass(frozen=True)
class RuntimeReport:
    """Bir iş yükünde amaçların (f1, f2) duvar saati süresiyle ilişkisi.

    Süre = denemelerin medyan `elapsed_ms` değeri (istemci tarafı, tüm çağrı). Gürültülüdür ve hedef
    değildir; burada yalnızca f1'i küçültmenin pratik karşılığını göstermek için kullanılır.
    """
    workload: str
    n: int
    spearman_f1_time: float
    spearman_f2_time: float
    time_cv_median: float               # konfigürasyon içi süre değişkenliği (medyan CV)
    speedup_min_f1: float               # varsayılan süre / en düşük f1'li konfigürasyonun süresi
    speedup_best: float                 # varsayılan süre / en hızlı konfigürasyonun süresi
    front_time_regret: float            # Pareto sınırındaki en hızlı nokta / genel en hızlı (1 = sınır en hızlıyı içerir)


def _median_time(rec: dict) -> float:
    return statistics.median(t["elapsed_ms"] for t in rec["trials"])


def runtime_relation(workload: str, space, records: list[dict]) -> RuntimeReport:
    valid = [r for r in records if r["valid"] and r.get("trials")]
    t = [_median_time(r) for r in valid]
    f1 = [r["f1"] for r in valid]
    f2 = [r["f2"] for r in valid]
    cvs = []
    for r in valid:
        xs = [x["elapsed_ms"] for x in r["trials"]]
        m = statistics.fmean(xs)
        cvs.append(statistics.pstdev(xs) / m if m else 0.0)
    default = next(i for i, r in enumerate(valid) if space.canonicalize(r["config"]) == space.default())
    i_min_f1 = min(range(len(valid)), key=lambda i: (f1[i], t[i]))
    front = pareto_front(list(range(len(valid))), key=lambda i: (f1[i], f2[i]))
    return RuntimeReport(
        workload=workload, n=len(valid),
        spearman_f1_time=float(st.spearmanr(f1, t).statistic),
        spearman_f2_time=float(st.spearmanr(f2, t).statistic),
        time_cv_median=statistics.median(cvs),
        speedup_min_f1=t[default] / t[i_min_f1],
        speedup_best=t[default] / min(t),
        front_time_regret=min(t[i] for i in front) / min(t),
    )
