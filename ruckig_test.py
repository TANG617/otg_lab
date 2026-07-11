"""实时 position-only 状态估计器与 Ruckig 跟踪对比实验。"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig

from realtime_estimators import build_estimators


DT = 0.01  # 100 Hz / 10 ms
DURATION = 3.0
SETTLE_TIME = 2.0
MAX_VELOCITY = 4.1
MAX_ACCELERATION = 16
MAX_JERK = 3200
SINE_AMPLITUDE = 0.37
CSV_PATH = Path(__file__).with_name("plot_data.csv")
OUTPUT_DIR = Path(__file__).parent / "estimator"


def elementary_curves():
    """三条具有静止首尾状态的七次时间缩放参考曲线。"""
    time = np.arange(0.0, DURATION + DT / 2.0, DT)
    tau = time / DURATION
    h = 35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    parameter = DURATION * h
    centered = parameter - DURATION / 2.0
    omega = 2.0 * np.pi / DURATION
    return {
        "quadratic_with_extremum": (
            0.5 * centered**2,
            r"7th-order time-scaled quadratic",
        ),
        "cubic": (0.12 * centered**3, r"7th-order time-scaled cubic"),
        "sine": (
            SINE_AMPLITUDE * np.sin(omega * parameter),
            r"7th-order time-scaled sine",
        ),
    }


def csv_curve():
    """只读取 value；每行固定当作相隔 10 ms。"""
    values = np.genfromtxt(CSV_PATH, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError(f"{CSV_PATH} 的 value 列至少需要 3 个有效数值")
    return values, "CSV value (fixed 10 ms per row)"


def append_settle(position):
    settle_count = int(round(SETTLE_TIME / DT))
    return np.concatenate((position, np.full(settle_count, position[-1])))


def target_state_is_feasible(velocity, acceleration):
    """Ruckig 的目标状态必要可行性检查。"""
    velocity_limit = MAX_VELOCITY * (1.0 - 1e-8)
    acceleration_limit_hard = MAX_ACCELERATION * (1.0 - 1e-8)
    if abs(velocity) > velocity_limit or abs(acceleration) > acceleration_limit_hard:
        return False
    available_velocity = max(0.0, MAX_VELOCITY - abs(velocity))
    acceleration_limit = np.sqrt(2.0 * MAX_JERK * available_velocity)
    acceleration_limit *= 1.0 - 1e-8
    return abs(acceleration) <= acceleration_limit


def project_target_state(state):
    """沿同一比例缩放 v/a，避免分别裁剪破坏二者比例。"""
    state = np.asarray(state, dtype=float)
    if not np.all(np.isfinite(state)):
        return np.array([state[0] if np.isfinite(state[0]) else 0.0, 0.0, 0.0]), True
    if target_state_is_feasible(state[1], state[2]):
        return state, False

    velocity, acceleration = state[1:]
    low, high = 0.0, 1.0
    for _ in range(50):
        scale = 0.5 * (low + high)
        if target_state_is_feasible(scale * velocity, scale * acceleration):
            low = scale
        else:
            high = scale
    projected = state.copy()
    projected[1:] *= low
    return projected, True


def run_realtime_method(position, estimator):
    """模拟 100 Hz 实时循环；收到 p[k] 后生成 t[k+1] 的控制输出。"""
    otg = Ruckig(1, DT)
    inp = InputParameter(1)
    out = OutputParameter(1)
    inp.current_position = [float(position[0])]
    inp.current_velocity = [0.0]
    inp.current_acceleration = [0.0]
    inp.max_velocity = [MAX_VELOCITY]
    inp.max_acceleration = [MAX_ACCELERATION]
    inp.max_jerk = [MAX_JERK]
    # The estimator predicts a state at t + lookahead. Tell Ruckig when that
    # state should be reached instead of asking for an ASAP state-to-state move.
    inp.minimum_duration = max(DT, estimator.lookahead)

    count = position.size
    planned_position = np.empty(count)
    planned_velocity = np.empty(count)
    planned_acceleration = np.empty(count)
    target_states = np.empty((count, 3))
    ruckig_compute_us = []
    planned_position[0] = position[0]
    planned_velocity[0] = 0.0
    planned_acceleration[0] = 0.0
    target_states[0] = [position[0], 0.0, 0.0]
    projection_count = 0

    for k in range(count - 1):
        candidate = estimator.step(position[k])
        candidate, projected = project_target_state(candidate)
        projection_count += int(projected)
        target_states[k + 1] = candidate

        inp.target_position = [float(candidate[0])]
        inp.target_velocity = [float(candidate[1])]
        inp.target_acceleration = [float(candidate[2])]
        result = otg.update(inp, out)
        if int(result) < 0:
            raise RuntimeError(
                f"{estimator.name}: Ruckig error {result} at k={k}, t={k * DT:.3f}s"
            )

        planned_position[k + 1] = out.new_position[0]
        planned_velocity[k + 1] = out.new_velocity[0]
        planned_acceleration[k + 1] = out.new_acceleration[0]
        ruckig_compute_us.append(out.calculation_duration)
        out.pass_to_input(inp)

    return {
        "position": planned_position,
        "velocity": planned_velocity,
        "acceleration": planned_acceleration,
        "target_states": target_states,
        "projection_rate": projection_count / max(1, count - 1),
        "estimator_compute_us": np.asarray(estimator.compute_us),
        "ruckig_compute_us": np.asarray(ruckig_compute_us),
        "delay_ms": estimator.delay_ms,
        "lookahead_ms": estimator.lookahead_ms,
    }


def best_lag_metrics(reference, output, max_lag_samples=100):
    """返回最佳整体时间平移、平移后 RMSE；正值表示输出滞后。"""
    best = (np.inf, 0)
    for lag in range(-max_lag_samples, max_lag_samples + 1):
        if lag > 0:
            ref_part, out_part = reference[:-lag], output[lag:]
        elif lag < 0:
            ref_part, out_part = reference[-lag:], output[:lag]
        else:
            ref_part, out_part = reference, output
        rmse = float(np.sqrt(np.mean((out_part - ref_part) ** 2)))
        if rmse < best[0]:
            best = (rmse, lag)
    return best[1] * DT * 1000.0, best[0]


def summarize(name, reference, original_count, estimator_name, result):
    ref = reference[:original_count]
    output = result["position"][:original_count]
    error = output - ref
    acceleration = result["acceleration"][:original_count]
    jerk = np.diff(acceleration) / DT
    target = result["target_states"][:original_count]
    target_jerk = np.diff(target[:, 2]) / DT
    lag_ms, aligned_rmse = best_lag_metrics(ref, output)
    estimate_us = result["estimator_compute_us"]
    ruckig_us = result["ruckig_compute_us"]
    return {
        "dataset": name,
        "method": estimator_name,
        "explicit_delay_ms": result["delay_ms"],
        "prediction_lookahead_ms": result["lookahead_ms"],
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(np.abs(error))),
        "max_error": float(np.max(np.abs(error))),
        "best_lag_ms": lag_ms,
        "lag_aligned_rmse": aligned_rmse,
        "target_projection_rate": result["projection_rate"],
        "target_max_velocity": float(np.max(np.abs(target[:, 1]))),
        "target_max_acceleration": float(np.max(np.abs(target[:, 2]))),
        "target_p99_jerk": float(np.percentile(np.abs(target_jerk), 99)),
        "output_max_velocity": float(np.max(np.abs(result["velocity"][:original_count]))),
        "output_max_acceleration": float(np.max(np.abs(acceleration))),
        "output_max_jerk": float(np.max(np.abs(jerk))) if jerk.size else 0.0,
        "estimator_compute_p50_us": float(np.percentile(estimate_us, 50)),
        "estimator_compute_p99_us": float(np.percentile(estimate_us, 99)),
        "ruckig_compute_p99_us": float(np.percentile(ruckig_us, 99)),
    }


def draw_comparison(name, title, time, target, original_count, results):
    fig, (ax_position, ax_error) = plt.subplots(
        2,
        1,
        figsize=(16, 12),
        dpi=150,
        sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0]},
    )
    ax_position.plot(
        time[:original_count],
        target[:original_count],
        "k--",
        linewidth=2.5,
        label="Target position",
    )
    for method, result in results.items():
        ax_position.plot(time, result["position"], linewidth=1.35, label=method)
        ax_error.plot(
            time[:original_count],
            result["position"][:original_count] - target[:original_count],
            linewidth=1.1,
            label=method,
        )

    ax_position.set_title(f"{title} — real-time position-only estimators")
    ax_position.set_ylabel("Position")
    ax_position.grid(True, alpha=0.3)
    ax_position.legend(fontsize=8, ncol=2)
    ax_error.axhline(0.0, color="black", linewidth=0.8)
    ax_error.set_xlabel("Time [s]")
    ax_error.set_ylabel("Tracking error")
    ax_error.grid(True, alpha=0.3)
    fig.tight_layout()

    output = OUTPUT_DIR / f"ruckig_{name}.png"
    fig.savefig(output, dpi=600, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def run_dataset(name, data):
    raw_position, title = data
    original_count = raw_position.size
    position = append_settle(raw_position)
    time = np.arange(position.size) * DT
    estimators = build_estimators(DT, MAX_VELOCITY, MAX_ACCELERATION, MAX_JERK)

    results = {}
    metrics = []
    for estimator in estimators:
        result = run_realtime_method(position, estimator)
        results[estimator.name] = result
        metrics.append(
            summarize(name, position, original_count, estimator.name, result)
        )
    plot_results = results
    if name == "csv":
        # Keep the full metrics table, but make the dense CSV figure readable by
        # showing only the three methods with the lowest time-aligned RMSE.
        top_three = sorted(metrics, key=lambda row: row["rmse"])[:6]
        plot_results = {row["method"]: results[row["method"]] for row in top_three}
        title += " — top 3 methods by RMSE"
    output = draw_comparison(
        name, title, time, position, original_count, plot_results
    )
    return output, metrics


def write_metrics(rows):
    output = OUTPUT_DIR / "realtime_metrics.csv"
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output


def main():
    datasets = elementary_curves()
    datasets["csv"] = csv_curve()
    all_metrics = []

    for name, data in datasets.items():
        output, metrics = run_dataset(name, data)
        all_metrics.extend(metrics)
        print(f"Saved: {output}")

    metrics_output = write_metrics(all_metrics)
    print(f"Saved: {metrics_output}")
    print("\nCSV summary (sorted by RMSE):")
    csv_metrics = sorted(
        (row for row in all_metrics if row["dataset"] == "csv"),
        key=lambda row: row["rmse"],
    )
    for row in csv_metrics:
        print(
            f"{row['method']:<43} "
            f"RMSE={row['rmse']:.6f}  "
            f"lag={row['best_lag_ms']:+.0f}ms  "
            f"est.p99={row['estimator_compute_p99_us']:.1f}us"
        )


if __name__ == "__main__":
    main()
