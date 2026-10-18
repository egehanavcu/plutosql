"""Arama algoritmaları ve metrikler — sentetik uzay üzerinde (veritabanı gerekmez)."""

import itertools
import math

import pytest

from plutosql.analysis.metrics import (
    Normalizer, additive_epsilon, epsilon_coverage, hypervolume, noise_epsilon, nondominated,
)
from plutosql.harness.evaluator import BudgetExhausted
from plutosql.search.algorithms import ALGORITHMS, GreedySearch, RandomSearch, SimulatedAnnealing
from plutosql.search.base import Scalarizer, archive_of, neighbors, spread_weights
from plutosql.search.experiment import GroundTruth, run_grid, summarize
from plutosql.search.replay import ReplayEvaluator
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


def replay(space, records, budget=None):
    return ReplayEvaluator(space, records, budget)


# ---------------------------------------------------------------- uzay / değerlendirici

def test_from_description_canonicalizes(space):
    assert space.size == 18
    assert len(space.canonical_configs()) == 15          # s1=SET_BASED iken s2 etkisiz: 18 − 3
    assert space.canonicalize({"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "ROW"})["s2"] == "LOOKUP_SQL"


def test_replay_budget_counts_unique_code(space, records):
    ev = replay(space, records, budget=2)
    ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_CACHE", "s3": "ROW"})
    ev.evaluate({"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"})
    assert ev.budget_used == 1
    ev.evaluate(space.default())
    with pytest.raises(BudgetExhausted):
        ev.evaluate({"s1": "BULK_50", "s2": "LOOKUP_SQL", "s3": "ROW"})


def test_neighbors_are_canonical_and_distinct(space):
    x = {"s1": "SET_BASED", "s2": "LOOKUP_SQL", "s3": "ROW"}
    nb = neighbors(space, x)
    keys = [tuple(n.values()) for n in nb]
    assert len(keys) == len(set(keys)) == 4                # s1: 2, s2: 0 (soğurulmuş), s3: 2
    assert all(space.is_canonical(n) for n in nb)


# ---------------------------------------------------------------- algoritmalar

@pytest.mark.parametrize("name", sorted(ALGORITHMS))
@pytest.mark.parametrize("budget", [1, 3, 7])
def test_algorithms_respect_budget_and_start_with_default(space, records, name, budget):
    ev = replay(space, records, budget)
    result = ALGORITHMS[name]().run(ev, seed=3)
    assert ev.budget_used <= budget
    assert result.history[0].config == space.default()
    assert result.archive and all(e.valid for e in result.archive)


def test_random_with_full_budget_finds_entire_space(space, records):
    ev = replay(space, records, budget=100)
    RandomSearch().run(ev, seed=0)
    assert ev.budget_used == 15


@pytest.mark.parametrize("name", sorted(ALGORITHMS))
def test_algorithms_are_reproducible(space, records, name):
    a = ALGORITHMS[name]().run(replay(space, records, 6), seed=11)
    b = ALGORITHMS[name]().run(replay(space, records, 6), seed=11)
    assert [e.digest for e in a.history] == [e.digest for e in b.history]


def test_greedy_finds_separable_optimum_for_f1(space, records):
    # w = (1, 0) geçişi: toplamsal amaçta koordinat iniş f1'in küresel optimumuna ulaşır.
    ev = replay(space, records)
    GreedySearch(n_weights=2).run(ev, seed=0)              # ağırlıklar (0, 1) ve (1, 0)
    best_f1 = min(r["f1"] for r in records.values())
    assert min(e.f1 for e in ev.history) == best_f1


def test_sa_spends_whole_budget_when_possible(space, records):
    ev = replay(space, records, budget=9)
    SimulatedAnnealing().run(ev, seed=5)
    assert ev.budget_used == 9


def test_scalarizer_handles_invalid_and_weights():
    from plutosql.harness.evaluator import Evaluation
    ok = Evaluation({}, "d", 50.0, 20.0, True, (), None, False)
    bad = Evaluation({}, "e", 1.0, 1.0, False, ("x",), None, False)
    s = Scalarizer(base=(100.0, 10.0), weights=(1.0, 0.0))
    assert s(bad) == math.inf
    assert s(ok) == pytest.approx(0.5 + 1e-3 * (0.5 + 2.0))
    assert spread_weights(3)[0] == (0.5, 0.5) and set(spread_weights(3)) == {(0.0, 1.0), (0.5, 0.5), (1.0, 0.0)}


# ---------------------------------------------------------------- metrikler

def test_hypervolume_and_nondominated():
    pts = [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0), (0.6, 0.6)]
    assert nondominated(pts) == [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0)]
    # r = (1.1, 1.1): 1.1·0.1 + 0.6·0.5 + 0.1·0.5 = 0.46
    assert hypervolume(pts) == pytest.approx(0.11 + 0.30 + 0.05)
    assert hypervolume([]) == 0.0


def test_epsilon_indicators():
    front = [(0.0, 1.0), (1.0, 0.0)]
    assert additive_epsilon(front, front) == 0.0
    assert additive_epsilon([(0.1, 1.0)], front) == pytest.approx(1.0)
    assert additive_epsilon([], front) == math.inf
    assert epsilon_coverage([(0.05, 1.0)], front, (0.1, 0.1)) == 0.5
    assert epsilon_coverage([(0.05, 1.0)], front, (0.0, 0.0)) == 0.0


def test_normalizer_and_noise_epsilon(records):
    n = Normalizer.from_points([(10, 100), (20, 300)])
    assert n((15, 200)) == (0.5, 0.5) and n.scale((1, 20)) == (0.1, 0.1)
    assert noise_epsilon(records.values()) == (2.0, 0.0)


def test_run_grid_and_summary(space, records):
    rp = replay(space, records)
    gt = GroundTruth.from_records(list(records.values()), (2.0, 0.0))
    runs = run_grid({"random": RandomSearch(), "greedy": GreedySearch()}, rp, gt, [0.5, 1.0], range(3))
    assert len(runs) == 2 * 2 * 3
    full = [r for r in runs if r.algorithm == "random" and r.rho == 1.0]
    assert all(r.hv_ratio == pytest.approx(1.0) and r.front_found_at is not None for r in full)
    assert all(len(r.hv_curve) == r.budget for r in runs)
    assert all(a <= b + 1e-12 for r in runs for a, b in itertools.pairwise(r.hv_curve))   # monoton
    rows = summarize(runs)
    assert {(r["algorithm"], r["rho"]) for r in rows} == {(a, p) for a in ("random", "greedy") for p in (0.5, 1.0)}
