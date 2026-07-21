"""Pre-registered qualification gate for the optional jerk-QP baseline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class QPQualification:
    """Auditable result of applying every mandatory QP baseline gate."""

    qp_baseline_status: str
    qualified: bool
    sample_count: int
    nonfallback_count: int
    fallback_rate: float
    continuous_violation_count: int
    nonfallback_terminal_viable_rate: float
    runtime_p99_us: float
    deadline_miss_count: int
    failure_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QPSelection:
    """Selection result that never promotes a failed QP configuration."""

    qp_baseline_status: str
    selected_method_id: str | None
    qualifications: Mapping[str, QPQualification]


def _required_bool(row: Mapping[str, Any], *names: str) -> bool:
    for name in names:
        if name in row and row[name] is not None:
            return bool(row[name])
    raise ValueError(f"qualification sample is missing one of {names!r}")


def _runtime_us(row: Mapping[str, Any]) -> float:
    for name in ("governor_compute_us", "compute_us", "runtime_us"):
        if name in row and row[name] is not None:
            value = float(row[name])
            if np.isfinite(value) and value >= 0.0:
                return value
            raise ValueError(f"{name} must be finite and non-negative")
    raise ValueError("qualification sample is missing runtime in microseconds")


def qualify_qp_baseline(
    samples: Iterable[Mapping[str, Any]],
    *,
    max_fallback_rate: float = 0.05,
    max_runtime_p99_us: float = 1000.0,
    deadline_us: float = 10_000.0,
) -> QPQualification:
    """Apply the complete clean-validation qualification gate.

    The inequalities are intentionally strict for timing: P99 must be below
    1 ms and every cycle must be below the 10 ms control deadline.
    """

    rows = tuple(samples)
    if not rows:
        raise ValueError("QP qualification requires at least one sample")
    if not 0.0 <= max_fallback_rate <= 1.0:
        raise ValueError("max_fallback_rate must be in [0, 1]")
    if max_runtime_p99_us <= 0.0 or deadline_us <= 0.0:
        raise ValueError("runtime thresholds must be positive")

    fallback = np.asarray(
        [
            _required_bool(row, "fallback_applied", "fallback")
            for row in rows
        ],
        dtype=bool,
    )
    continuous_ok = np.asarray(
        [
            _required_bool(
                row,
                "command_continuous_constraints_satisfied",
                "command_segment_feasible",
                "continuous_constraints_satisfied",
            )
            for row in rows
        ],
        dtype=bool,
    )
    terminal_viable = np.asarray(
        [
            _required_bool(
                row,
                "command_stopping_viable",
                "command_terminal_viable",
            )
            for row in rows
        ],
        dtype=bool,
    )
    runtime_us = np.asarray([_runtime_us(row) for row in rows], dtype=float)

    nonfallback = ~fallback
    fallback_rate = float(np.mean(fallback))
    violation_count = int(np.count_nonzero(~continuous_ok))
    nonfallback_count = int(np.count_nonzero(nonfallback))
    terminal_rate = (
        float(np.mean(terminal_viable[nonfallback]))
        if nonfallback_count
        else 0.0
    )
    runtime_p99 = float(np.quantile(runtime_us, 0.99))
    deadline_misses = int(np.count_nonzero(runtime_us >= deadline_us))

    failures = []
    if fallback_rate > max_fallback_rate:
        failures.append("fallback_rate_exceeds_5_percent")
    if violation_count:
        failures.append("continuous_constraint_violation")
    if nonfallback_count == 0:
        failures.append("no_nonfallback_samples")
    elif terminal_rate < 1.0:
        failures.append("nonfallback_terminal_viability_below_100_percent")
    if runtime_p99 >= max_runtime_p99_us:
        failures.append("runtime_p99_not_below_1ms")
    if deadline_misses:
        failures.append("10ms_deadline_miss")

    qualified = not failures
    return QPQualification(
        qp_baseline_status="qualified" if qualified else "unqualified",
        qualified=qualified,
        sample_count=len(rows),
        nonfallback_count=nonfallback_count,
        fallback_rate=fallback_rate,
        continuous_violation_count=violation_count,
        nonfallback_terminal_viable_rate=terminal_rate,
        runtime_p99_us=runtime_p99,
        deadline_miss_count=deadline_misses,
        failure_reasons=tuple(failures),
    )


def select_qualified_qp(
    candidates: Mapping[str, Iterable[Mapping[str, Any]]],
) -> QPSelection:
    """Select only among configurations that pass every qualification gate."""

    qualifications = {
        method_id: qualify_qp_baseline(samples)
        for method_id, samples in candidates.items()
    }
    passed = [
        (method_id, result)
        for method_id, result in qualifications.items()
        if result.qualified
    ]
    if not passed:
        return QPSelection("unqualified", None, qualifications)
    selected, _ = min(
        passed,
        key=lambda item: (
            item[1].fallback_rate,
            item[1].runtime_p99_us,
            item[0],
        ),
    )
    return QPSelection("qualified", selected, qualifications)


__all__ = [
    "QPQualification",
    "QPSelection",
    "qualify_qp_baseline",
    "select_qualified_qp",
]
