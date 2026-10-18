"""Sentetik arama uzayı: veritabanı olmadan algoritma ve ajan testleri için."""

import pytest

from plutosql.transformer.space import SearchSpace

# s1 (P1, 3 alternatif) ⊃ s2 (P2, 2 alternatif), s3 (P1, 3 alternatif): |𝒳| = 18, SET_BASED s2'yi soğurur.
DESC = {
    "size": 18,
    "sites": [
        {"id": "s1", "pattern": "P1", "name": "a", "line": 1, "alternatives": ["ROW", "BULK_50", "SET_BASED"],
         "default": "ROW", "parent": None},
        {"id": "s2", "pattern": "P2", "name": "b", "line": 2, "alternatives": ["LOOKUP_SQL", "LOOKUP_CACHE"],
         "default": "LOOKUP_SQL", "parent": "s1"},
        {"id": "s3", "pattern": "P1", "name": "c", "line": 3, "alternatives": ["ROW", "BULK_50", "SET_BASED"],
         "default": "ROW", "parent": None},
    ],
}
COST = {  # toplamsal sentetik amaçlar: (f1, f2) katkıları
    ("s1", "ROW"): (100, 10), ("s1", "BULK_50"): (60, 14), ("s1", "SET_BASED"): (20, 30),
    ("s2", "LOOKUP_SQL"): (50, 0), ("s2", "LOOKUP_CACHE"): (10, 6),
    ("s3", "ROW"): (80, 10), ("s3", "BULK_50"): (50, 12), ("s3", "SET_BASED"): (30, 20),
}


@pytest.fixture(scope="module")
def space():
    return SearchSpace.from_description(DESC)


@pytest.fixture(scope="module")
def records(space):
    out = {}
    for c in space.canonical_configs():
        f1 = sum(COST[(k, v)][0] for k, v in c.items() if not (k == "s2" and c["s1"] == "SET_BASED"))
        f2 = sum(COST[(k, v)][1] for k, v in c.items() if not (k == "s2" and c["s1"] == "SET_BASED"))
        key = tuple(c.values())
        out[key] = {"digest": "|".join(key), "config": c, "f1": f1, "f2": f2, "valid": True,
                    "differences": [], "error": None,
                    "trials": [{"consistent_gets": f1, "delta_pga": f2}, {"consistent_gets": f1 + 2, "delta_pga": f2}]}
    return out
