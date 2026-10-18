"""Pareto baskınlığı (iki amaç, ikisi de minimize)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """a, b'yi baskılar: her amaçta ≤ ve en az birinde <."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def pareto_front(items: Sequence[T], key: Callable[[T], Sequence[float]]) -> list[T]:
    """Baskılanmamış öğeler; f1'e (sonra f2'ye) göre sıralı. Aynı noktadaki öğelerin hepsi korunur."""
    points = [(key(it), it) for it in items]
    front = [it for p, it in points if not any(dominates(q, p) for q, _ in points)]
    return sorted(front, key=lambda it: tuple(key(it)))
