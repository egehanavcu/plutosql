"""Geçerlilik analizleri — sentetik uzay (conftest) üzerinde."""

import pytest

from plutosql.analysis.metrics import Normalizer, nondominated
from plutosql.analysis.validity import compare_live_replay, sensitivity
from plutosql.harness.evaluator import Evaluation
from plutosql.search.algorithms import RandomSearch
from plutosql.search.replay import ReplayEvaluator


def test_sensitivity_identical_and_reversed(space, records):
    recs = list(records.values())
    same = sensitivity(space, recs, recs)
    assert same.kendall_f1 == pytest.approx(1.0) and same.front_jaccard == 1.0
    assert same.igd_b_front_in_a == pytest.approx(0.0) and same.f2_median_ratio == pytest.approx(1.0)
    flipped = [{**r, "f1": 1000 - r["f1"], "f2": r["f2"] * 0.5} for r in recs]
    rep = sensitivity(space, recs, flipped)
    assert rep.kendall_f1 == pytest.approx(-1.0)
    assert rep.f2_median_ratio == pytest.approx(0.5)


def test_live_replay_comparison(space, records):
    by_digest = {r["digest"]: r for r in records.values()}
    valid = list(records.values())
    norm = Normalizer.from_points([(r["f1"], r["f2"]) for r in valid])
    front = nondominated(norm((r["f1"], r["f2"])) for r in valid)
    a = RandomSearch().run(ReplayEvaluator(space, records, 6), seed=2)
    b = RandomSearch().run(ReplayEvaluator(space, records, 6), seed=2)
    # "canlı" koşuyu %10 gürültüyle taklit et
    noisy = [Evaluation(e.config, e.digest, e.f1 * 1.1, e.f2, e.valid, e.differences, e.error, True) for e in a.history]
    row = compare_live_replay("random", 2, 6, noisy, b.history, by_digest, norm, front)
    assert row.same_sequence and row.shared_prefix == 6
    assert row.igd_live == pytest.approx(row.igd_replay)
    assert row.f1_rel_dev == pytest.approx(0.1) and row.f2_rel_dev == 0.0
    other = RandomSearch().run(ReplayEvaluator(space, records, 6), seed=3)
    assert not compare_live_replay("random", 2, 6, a.history, other.history, by_digest, norm, front).same_sequence
