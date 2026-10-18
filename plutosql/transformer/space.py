"""Konfigürasyon uzayı 𝒳 = A_1 × … × A_K ve kanonikleştirme (ADR-003)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from .sites import ABSORBING_ALTERNATIVES, Site

Config = dict[str, str]


class InvalidConfig(ValueError):
    pass


@dataclass(frozen=True)
class SearchSpace:
    sites: tuple[Site, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.sites)

    @property
    def size(self) -> int:
        """|𝒳| — kartezyen çarpım (bütçe oranı ρ = B / |𝒳| bununla hesaplanır)."""
        return math.prod(len(s.alternatives) for s in self.sites)

    def site(self, site_id: str) -> Site:
        for s in self.sites:
            if s.id == site_id:
                return s
        raise KeyError(site_id)

    def default(self) -> Config:
        """Orijinal kod (tüm siteler dönüştürülmemiş)."""
        return {s.id: s.default for s in self.sites}

    def validate(self, x: Mapping[str, str]) -> None:
        if set(x) != set(self.ids):
            missing = sorted(set(self.ids) - set(x))
            extra = sorted(set(x) - set(self.ids))
            raise InvalidConfig(f"site kümesi uyuşmuyor (eksik={missing}, fazla={extra})")
        for s in self.sites:
            if x[s.id] not in s.alternatives:
                raise InvalidConfig(f"{s.id}: {x[s.id]!r} ∉ {list(s.alternatives)}")

    def is_absorbed(self, x: Mapping[str, str], site_id: str) -> bool:
        """Bir atası soğurucu bir alternatif seçtiyse site etkisizdir."""
        parent = self.site(site_id).parent
        while parent is not None:
            if x[parent] in ABSORBING_ALTERNATIVES:
                return True
            parent = self.site(parent).parent
        return False

    def canonicalize(self, x: Mapping[str, str]) -> Config:
        """Etkisiz sitelerin değerini varsayılana çeker; aynı kodu üreten konfigürasyonlar aynı sonucu verir."""
        self.validate(x)
        return {
            s.id: (s.default if self.is_absorbed(x, s.id) else x[s.id])
            for s in self.sites
        }

    def is_canonical(self, x: Mapping[str, str]) -> bool:
        return self.canonicalize(x) == dict(x)

    def __iter__(self) -> Iterator[Config]:
        for values in itertools.product(*(s.alternatives for s in self.sites)):
            yield dict(zip(self.ids, values))

    def canonical_configs(self) -> list[Config]:
        """𝒳/~ — benzersiz kanonik konfigürasyonlar (kaynak sırasıyla)."""
        seen: dict[tuple, Config] = {}
        for x in self:
            c = self.canonicalize(x)
            seen.setdefault(tuple(c.values()), c)
        return list(seen.values())

    def describe(self) -> dict:
        """LLM ajanı ve raporlama için JSON'a çevrilebilir özet."""
        return {
            "size": self.size,
            "canonical_size": len(self.canonical_configs()),
            "absorbing_alternatives": sorted(ABSORBING_ALTERNATIVES),
            "sites": [s.to_dict() for s in self.sites],
        }
