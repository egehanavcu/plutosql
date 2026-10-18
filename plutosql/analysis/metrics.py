"""Çözüm kalitesi göstergeleri (iki amaç, minimize).

Normalizasyon: ground truth'taki tüm geçerli konfigürasyonların amaç aralığı [min, max] → [0, 1].
Böylece farklı ölçekteki CG ve ΔPGA aynı ağırlıkta katkı yapar ve göstergeler iş yükleri arasında
karşılaştırılabilir olur.

- Hiperhacim (HV): referans nokta r = (1.1, 1.1) (normalize uzayda nadir noktasının biraz ötesi).
  HV oranı = HV(arşiv) / HV(gerçek Pareto sınırı).
- Toplamsal ε göstergesi I_ε+(A, P*): arşivi gerçek sınırın her noktasını baskılayacak hale getirmek için
  gereken en küçük kaydırma. 0 = gerçek sınırı tamamen kapsıyor.
- ε-kapsama: gerçek sınır noktalarından, arşivdeki bir nokta tarafından ε toleransıyla baskılananların oranı.
  ε, ölçüm gürültüsünden türetilir (noise_epsilon): gürültü düzeyindeki farklar ayırt edilmez.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

Point = tuple[float, float]
REFERENCE = (1.1, 1.1)


@dataclass(frozen=True)
class Normalizer:
    lo: Point
    hi: Point

    @classmethod
    def from_points(cls, points: Iterable[Point]) -> "Normalizer":
        pts = list(points)
        lo = (min(p[0] for p in pts), min(p[1] for p in pts))
        hi = (max(p[0] for p in pts), max(p[1] for p in pts))
        return cls(lo, hi)

    def __call__(self, p: Point) -> Point:
        return tuple((v - l) / (h - l) if h > l else 0.0 for v, l, h in zip(p, self.lo, self.hi))

    def scale(self, delta: Point) -> Point:
        """Ham birimdeki bir farkı normalize birime çevirir (ε için)."""
        return tuple(d / (h - l) if h > l else 0.0 for d, l, h in zip(delta, self.lo, self.hi))


def nondominated(points: Iterable[Point]) -> list[Point]:
    pts = sorted(set(points))
    front, best_f2 = [], float("inf")
    for p in pts:                                   # f1 artan; f2 kesin azalmalı
        if p[1] < best_f2:
            front.append(p)
            best_f2 = p[1]
    return front


def hypervolume(points: Iterable[Point], ref: Point = REFERENCE) -> float:
    """2B hiperhacim (minimizasyon): referans noktasıyla sınırlı, baskılanan alan."""
    front = [p for p in nondominated(points) if p[0] < ref[0] and p[1] < ref[1]]
    hv, prev_f2 = 0.0, ref[1]
    for f1, f2 in front:                            # f1 artan, f2 azalan
        hv += (ref[0] - f1) * (prev_f2 - f2)
        prev_f2 = f2
    return hv


def additive_epsilon(archive: Sequence[Point], reference_front: Sequence[Point]) -> float:
    """I_ε+(A, P*) = max_{p∈P*} min_{a∈A} max_i (a_i − p_i). Arşiv boşsa +∞."""
    if not archive:
        return float("inf")
    return max(min(max(a[0] - p[0], a[1] - p[1]) for a in archive) for p in reference_front)


def epsilon_coverage(archive: Sequence[Point], reference_front: Sequence[Point], eps: Point) -> float:
    """Gerçek sınır noktalarından, arşivde ε-baskılayanı olanların oranı."""
    if not reference_front:
        return 1.0
    covered = sum(
        1 for p in reference_front
        if any(a[0] <= p[0] + eps[0] and a[1] <= p[1] + eps[1] for a in archive)
    )
    return covered / len(reference_front)


def noise_epsilon(records: Iterable[dict], quantile: float = 0.95) -> Point:
    """Ham birimde gürültü toleransı: konfigürasyon başına deneme aralığının (max−min) üst yüzdeliği.

    Medyan raporlandığı için tek bir sıçrama aralığı şişirebilir; bu yüzden en büyük değer yerine
    yüzdelik kullanılır.
    """
    r1, r2 = [], []
    for rec in records:
        trials = rec.get("trials") or []
        if len(trials) < 2:
            continue
        cg = [t["consistent_gets"] for t in trials]
        pga = [t["delta_pga"] for t in trials]
        r1.append(max(cg) - min(cg))
        r2.append(max(pga) - min(pga))
    if not r1:
        return (0.0, 0.0)
    return (_quantile(r1, quantile), _quantile(r2, quantile))


def _quantile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return float(values[0])
    return statistics.quantiles(sorted(values), n=100, method="inclusive")[round(q * 100) - 1]
