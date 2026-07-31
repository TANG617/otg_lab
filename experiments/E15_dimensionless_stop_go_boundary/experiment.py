"""E15: confirm both branches of the dimensionless stop/go boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import qmc

from otg_lab.confirmatory import (
    constant_velocity_trajectory,
    critical_reference_velocity,
    finish_compact_run,
    p_only_method,
    start_compact_run,
    summarize_tracking,
    tracking_config,
)
from otg_lab.experiment import ExperimentResult
from otg_lab.runio import write_json, write_rows_csv
from otg_lab.tracking import run_tracking

EXPERIMENT_ID = "E15"
SLUG = "dimensionless_stop_go_boundary"
DIRECTORY_NAME = f"{EXPERIMENT_ID}_{SLUG}"
TITLE = "Dimensionless stop-and-go boundary validation"
MAIN_START_S = 0.2
MAIN_END_S = 0.8
DURATION_S = 1.0
RHO_GRID = (
    0.5,
    0.8,
    0.9,
    0.95,
    0.98,
    0.99,
    0.995,
    1.0,
    1.005,
    1.01,
    1.02,
    1.05,
    1.1,
    1.2,
    2.0,
)
Q_GRID = (0.25, 0.5, 0.8, 0.95, 1.0, 1.05, 1.25, 2.0, 4.0)


def _profile(name: str) -> dict[str, tuple[float, ...] | int]:
    normalized = str(name).strip().lower()
    if normalized == "smoke":
        return {
            "dt_s": (0.005, 0.01),
            "q": (0.5, 2.0),
            "jerk": (1000.0,),
            "rho": (0.9, 0.95, 1.05, 1.1),
            "direction": (1.0,),
            "holdout_count": 8,
            "bisection_steps": 7,
        }
    if normalized != "confirmatory":
        raise ValueError("profile must be smoke or confirmatory")
    return {
        "dt_s": (0.002, 0.005, 0.01, 0.02),
        "q": Q_GRID,
        "jerk": (1000.0, 4000.0),
        "rho": RHO_GRID,
        "direction": (-1.0, 1.0),
        "holdout_count": 128,
        "bisection_steps": 10,
    }


def _evaluate(
    *,
    dt_s: float,
    q_value: float,
    jerk: float,
    rho: float,
    direction: float,
) -> dict[str, Any]:
    acceleration = q_value * jerk * dt_s / 4.0
    critical_velocity = critical_reference_velocity(acceleration, jerk, dt_s)
    reference_velocity = direction * rho * critical_velocity
    reference = constant_velocity_trajectory(
        reference_velocity,
        dt_s,
        duration_s=DURATION_S,
    )
    config = tracking_config(
        dt_s=dt_s,
        acceleration_rad_s2=acceleration,
        jerk_rad_s3=jerk,
    )
    tracking_run = run_tracking(reference, p_only_method(), config)
    metrics = summarize_tracking(
        reference,
        tracking_run,
        config.limits,
        start_time_s=MAIN_START_S,
        end_time_s=MAIN_END_S,
        input_id="constant_velocity_probe",
    )
    return {
        "dt_s": dt_s,
        "q": q_value,
        "branch": "jerk_limited" if q_value >= 1.0 else "acceleration_limited",
        "jerk_rad_s3": jerk,
        "acceleration_rad_s2": acceleration,
        "direction": int(direction),
        "rho": rho,
        "critical_velocity_rad_s": critical_velocity,
        "reference_velocity_rad_s": reference_velocity,
        "velocity_limit_inactive": abs(reference_velocity) < config.limits.max_velocity_rad_s,
        **metrics,
    }


def _holdout_configs(count: int) -> list[dict[str, float]]:
    exponent = int(np.ceil(np.log2(max(1, count))))
    samples = qmc.Sobol(d=3, scramble=True, seed=1501).random_base2(exponent)
    samples = samples[:count]
    output: list[dict[str, float]] = []
    for index, sample in enumerate(samples):
        dt_s = float(np.exp(np.log(0.002) + sample[0] * np.log(10.0)))
        q_value = float(np.exp(np.log(0.25) + sample[1] * np.log(16.0)))
        jerk = float(np.exp(np.log(1000.0) + sample[2] * np.log(4.0)))
        output.append(
            {
                "holdout_id": index,
                "dt_s": dt_s,
                "q": q_value,
                "jerk": jerk,
                "direction": -1.0 if index % 2 else 1.0,
            }
        )
    return output


def _empirical_threshold(
    config: dict[str, float],
    steps: int,
) -> dict[str, Any]:
    lower = 0.8
    upper = 1.2
    last: dict[str, Any] | None = None
    for _ in range(int(steps)):
        midpoint = 0.5 * (lower + upper)
        last = _evaluate(
            dt_s=config["dt_s"],
            q_value=config["q"],
            jerk=config["jerk"],
            rho=midpoint,
            direction=config["direction"],
        )
        pulse_fraction = last.get("rest_to_rest_pulse_fraction")
        if pulse_fraction is None:
            break
        if float(pulse_fraction) >= 0.5:
            lower = midpoint
        else:
            upper = midpoint
    rho_hat = 0.5 * (lower + upper)
    return {
        **config,
        "branch": "jerk_limited" if config["q"] >= 1.0 else "acceleration_limited",
        "rho_hat": rho_hat,
        "abs_rho_error": abs(rho_hat - 1.0),
        "completed": bool(last and last.get("completed")),
        "failure_reason": None if last is None else last.get("failure_reason"),
    }


def _write_figures(run_directory: Path, rows: list[dict[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(8.5, 5.0), constrained_layout=True)
    for branch, color in (
        ("acceleration_limited", "#4477AA"),
        ("jerk_limited", "#D55E00"),
    ):
        selected = [
            row
            for row in rows
            if row["branch"] == branch
            and row["completed"]
            and row["rest_to_rest_pulse_fraction"] is not None
        ]
        axis.scatter(
            [row["rho"] for row in selected],
            [row["rest_to_rest_pulse_fraction"] for row in selected],
            s=12,
            alpha=0.45,
            color=color,
            label=branch.replace("_", " "),
        )
    axis.axvline(1.0, color="black", linestyle="--", linewidth=1.2)
    axis.set(xlabel=r"$\rho=v_{ref}/v_{crit}$", ylabel="pulse fraction", ylim=(-0.03, 1.03))
    axis.legend(frameon=False)
    figures = run_directory / "figures"
    figures.mkdir(exist_ok=True)
    figure.savefig(figures / "boundary_collapse.png", dpi=200)
    figure.savefig(figures / "boundary_collapse.svg")
    plt.close(figure)


def run_confirmatory(
    *,
    project_root: str | Path,
    runs_root: str | Path | None = None,
    create_figures: bool = True,
    profile: str | None = None,
) -> ExperimentResult:
    profile_name = profile or os.environ.get(
        "OTG_CONFIRMATORY_PROFILE", "confirmatory"
    )
    declared = _profile(profile_name)
    run = start_compact_run(
        project_root,
        experiment_id=EXPERIMENT_ID,
        directory_name=DIRECTORY_NAME,
        title=TITLE,
        runs_root=runs_root,
        resolved_spec={
            "profile": profile_name,
            "matrix": declared,
            "primary_rule": "rho<=0.95 pulse>=0.95; rho>=1.05 pulse<=0.05",
            "holdout_rule": "abs(rho_hat-1)<=0.02",
            "diagnostic_rule": (
                "q=1 and rho=1 is the exact two-regime/behavior seam; native "
                "solver failures there are reported but are not primary failures"
            ),
        },
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for dt_s in declared["dt_s"]:
        for q_value in declared["q"]:
            for jerk in declared["jerk"]:
                for rho in declared["rho"]:
                    for direction in declared["direction"]:
                        row = _evaluate(
                            dt_s=float(dt_s),
                            q_value=float(q_value),
                            jerk=float(jerk),
                            rho=float(rho),
                            direction=float(direction),
                        )
                        exact_seam = bool(
                            np.isclose(float(q_value), 1.0, rtol=0.0, atol=1e-15)
                            and np.isclose(float(rho), 1.0, rtol=0.0, atol=1e-15)
                        )
                        row["evidence_role"] = (
                            "exact_boundary_seam_diagnostic"
                            if exact_seam
                            else "required_grid"
                        )
                        row["required"] = not exact_seam
                        rows.append(row)
                        if not row["completed"]:
                            failures.append(row)

    holdout_rows = [
        _empirical_threshold(config, int(declared["bisection_steps"]))
        for config in _holdout_configs(int(declared["holdout_count"]))
    ]
    for row in holdout_rows:
        row["evidence_role"] = "required_sobol_holdout"
        row["required"] = True
        if not row["completed"]:
            failures.append(row)

    required_failures = [row for row in failures if row["required"]]
    exact_seam_rows = [
        row for row in rows if row["evidence_role"] == "exact_boundary_seam_diagnostic"
    ]

    boundary_rows = [row for row in rows if row["rho"] <= 0.95 or row["rho"] >= 1.05]
    classification_pass = all(
        bool(
            row["completed"]
            and row["rest_to_rest_pulse_fraction"] is not None
            and (
                float(row["rest_to_rest_pulse_fraction"]) >= 0.95
                if row["rho"] <= 0.95
                else float(row["rest_to_rest_pulse_fraction"]) <= 0.05
            )
        )
        for row in boundary_rows
    )
    completed_holdout = [row for row in holdout_rows if row["completed"]]
    holdout_accuracy = (
        0.0
        if not completed_holdout
        else float(np.mean([row["abs_rho_error"] <= 0.02 for row in completed_holdout]))
    )
    acceptance = {
        "profile": profile_name,
        "grid_run_count": len(rows),
        "grid_completed_count": sum(bool(row["completed"]) for row in rows),
        "required_grid_count": sum(bool(row["required"]) for row in rows),
        "required_grid_completed_count": sum(
            bool(row["completed"]) for row in rows if row["required"]
        ),
        "exact_seam_count": len(exact_seam_rows),
        "exact_seam_failure_count": sum(
            not bool(row["completed"]) for row in exact_seam_rows
        ),
        "diagnostic_failure_count": len(failures) - len(required_failures),
        "required_failure_count": len(required_failures),
        "classification_pass": classification_pass,
        "holdout_count": len(holdout_rows),
        "holdout_completed_count": len(completed_holdout),
        "holdout_within_two_percent_fraction": holdout_accuracy,
        "holdout_max_abs_rho_error": (
            None
            if not completed_holdout
            else max(float(row["abs_rho_error"]) for row in completed_holdout)
        ),
        "accepted": bool(
            classification_pass
            and holdout_accuracy >= 0.99
            and len(completed_holdout) == len(holdout_rows)
            and not required_failures
        ),
    }
    write_rows_csv(run.run_directory / "boundary_grid.csv", rows)
    write_rows_csv(run.run_directory / "holdout_thresholds.csv", holdout_rows)
    write_json(run.run_directory / "acceptance.json", acceptance)
    (run.run_directory / "acceptance_summary.md").write_text(
        "# E15 acceptance\n\n"
        f"- Profile: `{profile_name}`\n"
        f"- Grid completed: {acceptance['grid_completed_count']}/{len(rows)}\n"
        f"- Required grid completed: "
        f"{acceptance['required_grid_completed_count']}/"
        f"{acceptance['required_grid_count']}\n"
        f"- Exact q=1, rho=1 seam failures (diagnostic): "
        f"{acceptance['exact_seam_failure_count']}/"
        f"{acceptance['exact_seam_count']}\n"
        f"- Boundary classification: **{classification_pass}**\n"
        f"- Holdout within 2%: {holdout_accuracy:.3f}\n"
        f"- Accepted: **{acceptance['accepted']}**\n",
        encoding="utf-8",
    )
    if create_figures:
        _write_figures(run.run_directory, rows)
    return finish_compact_run(
        run,
        outputs={
            "boundary_grid": "boundary_grid.csv",
            "holdout_thresholds": "holdout_thresholds.csv",
            "acceptance": "acceptance.json",
            "summary": "acceptance_summary.md",
        },
        failures=failures,
        required_failure_count=len(required_failures),
    )


if __name__ == "__main__":
    result = run_confirmatory(project_root=Path(__file__).resolve().parents[2])
    print(result.run_directory)
