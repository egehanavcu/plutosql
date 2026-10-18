"""Ölçüm protokolü: izole sıcak ölçüm (fresh-session hot protocol).

Bir deneme (trial):
  1. Yönetim oturumunda iş yükü başlangıç durumuna sıfırlanır.
  2. Yeni bir oturum açılır; oturum ayarları uygulanır (PLSQL_OPTIMIZE_LEVEL = 2 ...).
  3. İstatistik sorgusu iki kez çalıştırılır (ayrıştırma ve çalışma alanı maliyeti ölçüme girmesin),
     ardından başlangıç değerleri okunur.
  4. Kod çalıştırılır; bitiş değerleri okunur.
  5. (İsteğe bağlı) aynı oturumda sonuç yakalanır (multiset doğrulaması) — istatistikler okunduktan sonra.

Metrikler:
  f1 = Δ consistent gets
  f2 = ΔPGA = session pga memory max (bitiş) − session pga memory (başlangıç)
Buffer cache ve shared pool sıcak kalır; PGA sayacı oturuma bağlı olduğundan her deneme yeni oturumdur.
Bir konfigürasyon: `warmups` ısınma + `repeats` ölçüm; raporlanan değer medyandır.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict, dataclass, field

import oracledb

from plutosql.db.connection import DbConfig, connect

from .validate import Outcome
from .workload import PLSQL_OPTIMIZE_LEVEL, Executable

STAT_NAMES = (
    "consistent gets",
    "db block gets",
    "session pga memory",
    "session pga memory max",
    "session uga memory max",
    "execute count",
    "parse count (hard)",
    "recursive calls",
)
RECORDED_PARAMETERS = ("plsql_optimize_level", "optimizer_adaptive_plans",
                       "optimizer_adaptive_statistics", "optimizer_features_enable")


@dataclass(frozen=True)
class Protocol:
    warmups: int = 1
    repeats: int = 5
    session_settings: tuple[str, ...] = (
        f"ALTER SESSION SET PLSQL_OPTIMIZE_LEVEL = {PLSQL_OPTIMIZE_LEVEL}",
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Trial:
    consistent_gets: int
    db_block_gets: int
    pga_before: int              # session pga memory (başlangıç)
    pga_max_before: int          # session pga memory max (başlangıç; bağlantı anının tepe değeri)
    pga_max_after: int
    uga_max_after: int
    executions: int              # execute count farkı (bağlam geçişi göstergesi)
    hard_parses: int
    recursive_calls: int
    elapsed_ms: float
    error: int | None = None

    @property
    def delta_pga(self) -> int:
        return self.pga_max_after - self.pga_before

    @property
    def pga_censored(self) -> bool:
        """Çalıştırma, oturumun bağlantı anındaki tepe değerini aşmadıysa gerçek tepe bilinmez (üst sınır)."""
        return self.pga_max_after <= self.pga_max_before


@dataclass
class Measurement:
    trials: list[Trial]
    warmup_trials: list[Trial]
    outcome: Outcome | None = None
    parameters: dict[str, str] = field(default_factory=dict)

    @property
    def f1(self) -> float:
        return statistics.median(t.consistent_gets for t in self.trials)

    @property
    def f2(self) -> float:
        return statistics.median(t.delta_pga for t in self.trials)

    @property
    def error(self) -> int | None:
        return self.outcome.error if self.outcome else self.trials[0].error


class _StatReader:
    def __init__(self, cursor):
        self.cursor = cursor
        ids = dict(cursor.execute(
            "SELECT name, statistic# FROM v$statname WHERE name IN (%s)" % ", ".join(f"'{n}'" for n in STAT_NAMES)
        ).fetchall())
        missing = set(STAT_NAMES) - set(ids)
        if missing:
            raise RuntimeError(f"v$statname'de yok: {sorted(missing)}")
        self.by_id = {v: k for k, v in ids.items()}
        self.sql = "SELECT statistic#, value FROM v$mystat WHERE statistic# IN (%s)" % ", ".join(
            str(i) for i in ids.values())

    def read(self) -> dict[str, int]:
        return {self.by_id[i]: int(v) for i, v in self.cursor.execute(self.sql).fetchall()}


def session_parameters(cursor) -> dict[str, str]:
    rows = cursor.execute(
        "SELECT name, value FROM v$parameter WHERE name IN (%s)" % ", ".join(f"'{p}'" for p in RECORDED_PARAMETERS)
    ).fetchall()
    return dict(rows)


def run_trial(admin: oracledb.Connection, db: DbConfig, executable: Executable, protocol: Protocol,
              capture: bool = False) -> tuple[Trial, Outcome | None, dict[str, str]]:
    executable.workload.reset(admin)
    with connect(db) as conn, conn.cursor() as cur:
        for stmt in protocol.session_settings:
            cur.execute(stmt)
        params = session_parameters(cur)
        stats = _StatReader(cur)
        stats.read()
        stats.read()
        before = stats.read()

        error, outs = None, {}
        t0 = time.perf_counter()
        try:
            outs = executable.run(cur)
        except oracledb.DatabaseError as e:
            error = e.args[0].code
        elapsed = (time.perf_counter() - t0) * 1000
        after = stats.read()

        trial = Trial(
            consistent_gets=after["consistent gets"] - before["consistent gets"],
            db_block_gets=after["db block gets"] - before["db block gets"],
            pga_before=before["session pga memory"],
            pga_max_before=before["session pga memory max"],
            pga_max_after=after["session pga memory max"],
            uga_max_after=after["session uga memory max"],
            executions=after["execute count"] - before["execute count"],
            hard_parses=after["parse count (hard)"] - before["parse count (hard)"],
            recursive_calls=after["recursive calls"] - before["recursive calls"],
            elapsed_ms=elapsed,
            error=error,
        )
        outcome = Outcome.capture(conn, executable.workload.target_tables, outs, error) if capture else None
        conn.rollback()
    return trial, outcome, params


def measure(admin: oracledb.Connection, db: DbConfig, executable: Executable, protocol: Protocol,
            validate: bool = True) -> Measurement:
    """Isınma denemeleri (ilki doğrulama sonucunu da yakalar) + ölçüm denemeleri."""
    warm, outcome, params = [], None, {}
    for k in range(protocol.warmups):
        trial, out, params = run_trial(admin, db, executable, protocol, capture=validate and k == 0)
        warm.append(trial)
        outcome = outcome or out
    trials = []
    for _ in range(protocol.repeats):
        trial, _, params = run_trial(admin, db, executable, protocol)
        trials.append(trial)
    if validate and outcome is None:            # warmups == 0
        _, outcome, _ = run_trial(admin, db, executable, protocol, capture=True)
    return Measurement(trials=trials, warmup_trials=warm, outcome=outcome, parameters=params)
