"""Kütüphanedeki üretilmiş iş yüklerinin tanımları (ADR-004).

Yeni bir tanım ekledikten sonra: python -m plutosql.workloads generate
"""

from __future__ import annotations

from .generator import GeneratedSpec, RefSpec, StageSpec

SPECS: dict[str, GeneratedSpec] = {}


def _add(spec: GeneratedSpec) -> None:
    SPECS[spec.name] = spec


# Pilot (H3): büyük ve seyrek erişilen referans tablosu (önbellek = tam tarama + büyük PGA) ile
# küçük referans tablosu; ardından toplama aşaması (aşamalar arası veri akışı).
_add(GeneratedSpec(
    name="g01_pilot",
    description="Pilot: büyük seyrek + küçük referans araması, ardından grp bazında upsert",
    n_src=20_000,
    refs=(
        RefSpec(rows=200_000, spread=0.02, pad=100),
        RefSpec(rows=50),
    ),
    stages=(
        StageSpec(kind="INSERT", lookups=(0, 1)),
        StageSpec(kind="UPSERT"),
    ),
))
