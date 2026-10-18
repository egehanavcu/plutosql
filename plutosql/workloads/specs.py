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

# ---------------------------------------------------------------- H2: ortak önbellek kuplajı
# Aynı referans tablosu iki aşamada aranır; ikisi de önbelleklenirse tablo bir kez yüklenir.

_add(GeneratedSpec(
    name="g02_shared_dense",
    description="Ortak büyük ve yoğun erişilen referans (2 INSERT aşaması), ardından upsert",
    n_src=20_000,
    refs=(RefSpec(rows=100_000, spread=0.5, pad=60),),
    stages=(
        StageSpec(kind="INSERT", lookups=(0,)),
        StageSpec(kind="INSERT", lookups=(0,), selectivity=30),
        StageSpec(kind="UPSERT"),
    ),
))

_add(GeneratedSpec(
    name="g03_shared_sparse",
    description="Ortak büyük seyrek referans + 2. aşamada küçük referans, ardından upsert",
    n_src=20_000,
    refs=(RefSpec(rows=200_000, spread=0.02, pad=100), RefSpec(rows=50)),
    stages=(
        StageSpec(kind="INSERT", lookups=(0,)),
        StageSpec(kind="INSERT", lookups=(0, 1), selectivity=50),
        StageSpec(kind="UPSERT"),
    ),
))

_add(GeneratedSpec(
    name="g04_shared_lowsel",
    description="Ortak referans; 1. aşama düşük seçicilikli (az arama: tek başına önbellek kârsız)",
    n_src=20_000,
    refs=(RefSpec(rows=100_000, spread=0.3, pad=60),),
    stages=(
        StageSpec(kind="INSERT", lookups=(0,), selectivity=5),
        StageSpec(kind="INSERT", lookups=(0,)),
        StageSpec(kind="UPSERT"),
    ),
))

_add(GeneratedSpec(
    name="g05_shared_twopairs",
    description="İki ortak referans: R1 ve R2 iki aşamada da aranıyor (iki kuplajlı çift)",
    n_src=20_000,
    refs=(RefSpec(rows=50_000, spread=0.4, pad=40), RefSpec(rows=80_000, spread=0.1, pad=80)),
    stages=(
        StageSpec(kind="INSERT", lookups=(0, 1)),
        StageSpec(kind="INSERT", lookups=(0, 1), selectivity=60),
    ),
))

# ---------------------------------------------------------------- H3: önbellek ödünleşiminin yönü

_add(GeneratedSpec(
    name="g06_three_refs",
    description="Üç aşama, üç farklı referans: küçük / orta / büyük seyrek (ortak yok)",
    n_src=20_000,
    refs=(RefSpec(rows=50), RefSpec(rows=5_000, spread=1.0), RefSpec(rows=200_000, spread=0.01, pad=100)),
    stages=(
        StageSpec(kind="INSERT", lookups=(0,)),
        StageSpec(kind="INSERT", lookups=(1,)),
        StageSpec(kind="INSERT", lookups=(2,)),
    ),
))

_add(GeneratedSpec(
    name="g07_wide_dense",
    description="Geniş satırlı büyük referans, yoğun erişim; küçük referans; ardından upsert",
    n_src=20_000,
    refs=(RefSpec(rows=150_000, spread=1.0, pad=400), RefSpec(rows=20)),
    stages=(
        StageSpec(kind="INSERT", lookups=(0, 1)),
        StageSpec(kind="UPSERT"),
    ),
))

_add(GeneratedSpec(
    name="g08_varchar_keys",
    description="VARCHAR2 anahtarlı referanslar (dizi indeksi metin), ardından upsert",
    n_src=20_000,
    refs=(RefSpec(rows=50_000, spread=0.2, key_type="VARCHAR2", pad=50),
          RefSpec(rows=30, key_type="VARCHAR2")),
    stages=(
        StageSpec(kind="INSERT", lookups=(0, 1)),
        StageSpec(kind="UPSERT"),
    ),
))

# ---------------------------------------------------------------- ölçek ve kontrol

_add(GeneratedSpec(
    name="g09_scale_60k",
    description="g02 yapısı, 3 kat kaynak veri (ölçek etkisi)",
    n_src=60_000,
    refs=(RefSpec(rows=100_000, spread=0.5, pad=60),),
    stages=(
        StageSpec(kind="INSERT", lookups=(0,)),
        StageSpec(kind="INSERT", lookups=(0,), selectivity=30),
        StageSpec(kind="UPSERT"),
    ),
    salt=1,
))

_add(GeneratedSpec(
    name="g10_no_lookups",
    description="Kontrol: arama yok, yalnızca satır/toplu/küme tabanlı seçimler (3 aşama)",
    n_src=20_000,
    refs=(),
    stages=(
        StageSpec(kind="INSERT"),
        StageSpec(kind="INSERT", selectivity=50),
        StageSpec(kind="UPSERT"),
    ),
))
