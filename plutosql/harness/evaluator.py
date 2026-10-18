"""Arama algoritmalarının kullandığı değerlendirici: x → (f1, f2, geçerlilik).

Bütçe (ADR-003): bir arama koşusunun bütçesi, istediği *benzersiz üretilen kod* sayısıyla ölçülür.
Aynı kodu üreten (kanonik olarak eşdeğer) konfigürasyonlar tekrar sayılmaz. Fiziksel ölçüm ise koşular
arasında paylaşılan depodan (MeasurementStore) gelebilir; bu, bütçe sayımını değiştirmez.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

import oracledb

from plutosql.db.connection import DbConfig
from plutosql.transformer import Catalog, SearchSpace, rewrite
from plutosql.transformer.space import Config

from .protocol import Protocol, measure, run_trial
from .store import MeasurementStore
from .validate import Outcome, compare
from .workload import CompilationError, Workload, prepare


class BudgetExhausted(Exception):
    pass


@dataclass(frozen=True)
class Evaluation:
    config: Config                 # kanonik
    digest: str
    f1: float | None               # median consistent gets
    f2: float | None               # median ΔPGA (bayt)
    valid: bool                    # referans ile anlamsal olarak eşdeğer mi
    differences: tuple[str, ...]
    error: str | None              # derleme hatası ya da beklenmeyen ORA kodu
    measured_now: bool             # False: depodan geldi

    @property
    def objectives(self) -> tuple[float, float]:
        return (self.f1, self.f2)


@dataclass
class Evaluator:
    workload: Workload
    admin: oracledb.Connection
    db: DbConfig
    catalog: Catalog
    protocol: Protocol = field(default_factory=Protocol)
    store: MeasurementStore | None = None
    budget: int | None = None
    validate: bool = True

    def __post_init__(self) -> None:
        self.analysis = self.workload.analyze(self.catalog)
        self.space = SearchSpace(tuple(self.analysis.sites))
        self.history: list[Evaluation] = []          # istek sırasıyla (LLM ajanı geçmişi buradan alır)
        self._requested: set[str] = set()
        self._memo: dict[str, Evaluation] = {}
        self._reference: Outcome | None = None

    # ---------------------------------------------------------------- bütçe
    @property
    def budget_used(self) -> int:
        return len(self._requested)

    @property
    def budget_left(self) -> int | None:
        return None if self.budget is None else self.budget - self.budget_used

    # ---------------------------------------------------------------- değerlendirme
    def evaluate(self, x: Mapping[str, str]) -> Evaluation:
        result = rewrite(self.analysis, x, self.catalog)
        if result.digest not in self._requested:
            if self.budget is not None and self.budget_used >= self.budget:
                raise BudgetExhausted(f"bütçe doldu ({self.budget})")
            self._requested.add(result.digest)

        ev = self._memo.get(result.digest)
        if ev is None:
            record = self.store.get(result.digest) if self.store is not None else None
            if record is not None:
                ev = evaluation_from_record(record, measured_now=False)
            else:
                record = self._measure(result.config, result.digest, result.code)
                if self.store is not None:
                    self.store.put(record)
                ev = evaluation_from_record(record, measured_now=True)
            self._memo[result.digest] = ev
        # Aynı kod, istenen konfigürasyonun kanonik biçimiyle raporlanır.
        ev = Evaluation(**{**asdict(ev), "config": result.config})
        self.history.append(ev)
        return ev

    def reference_outcome(self) -> Outcome:
        """Varsayılan (dönüştürülmemiş) kodun gözlemlenebilir sonucu."""
        if self._reference is None:
            code = rewrite(self.analysis, self.space.default(), self.catalog).code
            executable = prepare(self.admin, self.workload, code)
            _, self._reference, _ = run_trial(self.admin, self.db, executable, self.protocol, capture=True)
        return self._reference

    def _measure(self, config: Config, digest: str, code: str) -> dict:
        base = {
            "digest": digest,
            "config": config,
            "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            executable = prepare(self.admin, self.workload, code)
        except (CompilationError, oracledb.DatabaseError) as e:
            return {**base, "f1": None, "f2": None, "valid": False, "differences": [],
                    "error": f"derleme: {e}", "trials": [], "warmups": [], "parameters": {}}

        reference = self.reference_outcome() if self.validate else None
        if self.validate and self.workload.is_procedure:
            executable = prepare(self.admin, self.workload, code)      # referans derlemesinin üzerine
        m = measure(self.admin, self.db, executable, self.protocol, validate=self.validate)

        diffs = [f"{d.kind}:{d.name}: {d.detail}" for d in compare(reference, m.outcome)] if reference else []
        errors = {t.error for t in m.trials + m.warmup_trials}
        error = None
        if len(errors) > 1:
            error = f"denemeler arasında tutarsız sonuç: {sorted(errors, key=str)}"
            diffs.append(error)
        return {
            **base,
            "f1": m.f1,
            "f2": m.f2,
            "valid": not diffs,
            "differences": diffs,
            "error": error,
            "trials": [_trial_dict(t) for t in m.trials],
            "warmups": [_trial_dict(t) for t in m.warmup_trials],
            "parameters": m.parameters,
        }


def _trial_dict(t) -> dict:
    d = asdict(t)
    d["delta_pga"] = t.delta_pga
    d["pga_censored"] = t.pga_censored
    return d


def evaluation_from_record(rec: dict, measured_now: bool) -> Evaluation:
    return Evaluation(
        config=rec["config"],
        digest=rec["digest"],
        f1=rec["f1"],
        f2=rec["f2"],
        valid=rec["valid"],
        differences=tuple(rec["differences"]),
        error=rec["error"],
        measured_now=measured_now,
    )
