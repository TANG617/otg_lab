import pytest

from otg_lab.qualification import qualify_qp_baseline, select_qualified_qp


def _sample(
    *,
    fallback=False,
    continuous=True,
    terminal=True,
    runtime_us=200.0,
):
    return {
        "fallback_applied": fallback,
        "command_continuous_constraints_satisfied": continuous,
        "command_stopping_viable": terminal,
        "governor_compute_us": runtime_us,
    }


def test_qp_qualification_requires_every_gate():
    samples = [_sample() for _ in range(100)]
    result = qualify_qp_baseline(samples)

    assert result.qualified
    assert result.qp_baseline_status == "qualified"
    assert result.fallback_rate == 0.0
    assert result.nonfallback_terminal_viable_rate == 1.0
    assert result.runtime_p99_us == pytest.approx(200.0)


def test_qp_qualification_reports_independent_failures():
    samples = [_sample() for _ in range(90)]
    samples.extend(_sample(fallback=True) for _ in range(8))
    samples.append(_sample(continuous=False, terminal=False, runtime_us=1500.0))
    samples.append(_sample(runtime_us=10_000.0))

    result = qualify_qp_baseline(samples)

    assert not result.qualified
    assert result.qp_baseline_status == "unqualified"
    assert result.fallback_rate == pytest.approx(0.08)
    assert result.continuous_violation_count == 1
    assert result.nonfallback_terminal_viable_rate < 1.0
    assert result.deadline_miss_count == 1
    assert set(result.failure_reasons) == {
        "fallback_rate_exceeds_5_percent",
        "continuous_constraint_violation",
        "nonfallback_terminal_viability_below_100_percent",
        "runtime_p99_not_below_1ms",
        "10ms_deadline_miss",
    }


def test_all_failed_qp_candidates_remain_unqualified_without_selection():
    selection = select_qualified_qp(
        {
            "fast_but_unsafe": [_sample(continuous=False)],
            "safe_but_slow": [_sample(runtime_us=2000.0)],
        }
    )

    assert selection.qp_baseline_status == "unqualified"
    assert selection.selected_method_id is None
    assert all(not result.qualified for result in selection.qualifications.values())


def test_qualification_rejects_missing_audit_data():
    with pytest.raises(ValueError, match="missing"):
        qualify_qp_baseline([{"fallback_applied": False, "compute_us": 1.0}])
