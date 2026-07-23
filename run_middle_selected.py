"""Reproduce the historical central-difference vs position-only figures.

The default settings match ``results/middle-selected-2`` while writing to a
new directory so the historical images are never overwritten.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ruckig import InputParameter, OutputParameter, Ruckig

from run_output import prepare_run_directory

DT = 0.01
DURATION = 3.0
SETTLE_TIME = 2.0
SINE_AMPLITUDE = 0.37
MAX_VELOCITY = 4.1
DEFAULT_MAX_ACCELERATION = 16.4
DEFAULT_MAX_JERK = 800
DEFAULT_DERIVATIVE_SCALE = 0.5

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "plot_data.csv"


def elementary_curves():
    time = np.arange(0.0, DURATION + DT / 2.0, DT)
    tau = time / DURATION
    smootherstep = (
        35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    )
    parameter = DURATION * smootherstep
    centered = parameter - DURATION / 2.0
    omega = 2.0 * np.pi / DURATION
    return {
        "quadratic_with_extremum": (
            0.5 * centered**2,
            r"7th-order time-scaled quadratic: $y=0.5(s(t)-1.5)^2$",
        ),
        "cubic": (
            0.12 * centered**3,
            r"7th-order time-scaled cubic: $y=0.12(s(t)-1.5)^3$",
        ),
        "sine": (
            SINE_AMPLITUDE * np.sin(omega * parameter),
            rf"7th-order time-scaled sine: $y={SINE_AMPLITUDE}\sin(2\pi s(t)/3)$",
        ),
    }


def csv_curve():
    values = np.genfromtxt(CSV_PATH, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError(f"{CSV_PATH} must contain at least 3 finite values")
    return values, "CSV value (10 ms per row)"


def append_settle(position):
    original_count = position.size
    settle_count = int(round(SETTLE_TIME / DT))
    position = np.concatenate((position, np.full(settle_count, position[-1])))
    time = np.arange(position.size) * DT
    return time, position, original_count


def central_position_differences(position, original_count):
    """Match the offline central differences used by the historical figures."""
    velocity = np.gradient(position, DT, edge_order=2)
    acceleration = np.gradient(velocity, DT, edge_order=2)
    velocity[0] = 0.0
    acceleration[0] = 0.0
    velocity[original_count - 1 :] = 0.0
    acceleration[original_count - 1 :] = 0.0
    return velocity, acceleration


def run_ruckig(
    time,
    position,
    velocity,
    acceleration,
    max_acceleration,
    max_jerk,
):
    otg = Ruckig(1, DT)
    inp = InputParameter(1)
    out = OutputParameter(1)
    inp.current_position = [float(position[0])]
    inp.current_velocity = [0.0]
    inp.current_acceleration = [0.0]
    inp.max_velocity = [MAX_VELOCITY]
    inp.max_acceleration = [max_acceleration]
    inp.max_jerk = [max_jerk]

    planned = np.empty_like(position)
    for index, current_time in enumerate(time):
        inp.target_position = [float(position[index])]
        inp.target_velocity = [float(velocity[index])]
        inp.target_acceleration = [float(acceleration[index])]
        result = otg.update(inp, out)
        if int(result) < 0:
            raise RuntimeError(
                f"Ruckig error {result} at t={current_time:.3f}s"
            )
        planned[index] = out.new_position[0]
        out.pass_to_input(inp)
    return planned


def plan_experiments(
    raw_position,
    max_acceleration,
    max_jerk,
    derivative_scale,
):
    time, position, original_count = append_settle(raw_position)
    velocity, acceleration = central_position_differences(
        position, original_count
    )
    velocity = np.clip(
        derivative_scale * velocity,
        -MAX_VELOCITY,
        MAX_VELOCITY,
    )
    acceleration = np.clip(
        derivative_scale * acceleration,
        -max_acceleration,
        max_acceleration,
    )
    zero = np.zeros_like(position)

    targets = {
        "3. position central-difference velocity & acceleration": (
            velocity,
            acceleration,
        ),
        "5. position only (velocity = acceleration = 0)": (zero, zero),
    }
    planned = {
        label: run_ruckig(
            time,
            position,
            target_velocity,
            target_acceleration,
            max_acceleration,
            max_jerk,
        )
        for label, (target_velocity, target_acceleration) in targets.items()
    }
    return time, position, original_count, planned


def draw_figure(
    dataset_name,
    data,
    output_dir,
    max_acceleration,
    max_jerk,
    derivative_scale,
):
    raw_position, title = data
    time, target, original_count, experiments = plan_experiments(
        raw_position,
        max_acceleration,
        max_jerk,
        derivative_scale,
    )

    fig, axis = plt.subplots(figsize=(16, 9), dpi=150)
    axis.plot(
        time[:original_count],
        target[:original_count],
        "k--",
        linewidth=1.0,
        label="Target position",
    )
    for label, planned in experiments.items():
        axis.plot(time, planned, linewidth=0.7, label=label)
    axis.set_title(title)
    axis.set_xlabel("Time [s]")
    axis.set_ylabel("Position")
    axis.grid(True, alpha=0.3)
    axis.legend(fontsize=11)
    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"ruckig_{dataset_name}.png"
    fig.savefig(output, dpi=600, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    plt.close(fig)
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate central-difference vs position-only figures."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="override the automatically named directory under runs/",
    )
    parser.add_argument(
        "--max-acceleration",
        type=float,
        default=DEFAULT_MAX_ACCELERATION,
    )
    parser.add_argument("--max-jerk", type=float, default=DEFAULT_MAX_JERK)
    parser.add_argument(
        "--derivative-scale",
        type=float,
        default=DEFAULT_DERIVATIVE_SCALE,
        help="scale applied to central-difference velocity and acceleration",
    )
    args = parser.parse_args()
    if args.max_acceleration <= 0 or args.max_jerk <= 0:
        parser.error("limits must be positive")
    if args.derivative_scale < 0:
        parser.error("--derivative-scale must be non-negative")
    return args


def main():
    args = parse_args()
    output_dir = prepare_run_directory(
        "middle-selected",
        {
            "dt": f"{DT * 1000:g}ms",
            "vmax": MAX_VELOCITY,
            "amax": args.max_acceleration,
            "jmax": args.max_jerk,
            "dscale": args.derivative_scale,
        },
        args.output_dir,
    )
    datasets = elementary_curves()
    datasets["csv"] = csv_curve()
    for dataset_name, data in datasets.items():
        output = draw_figure(
            dataset_name,
            data,
            output_dir,
            args.max_acceleration,
            args.max_jerk,
            args.derivative_scale,
        )
        print(f"Saved: {output}")
    print(f"Run directory: {output_dir}")


if __name__ == "__main__":
    main()
