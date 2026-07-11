"""比较不同目标导数输入下，Ruckig 对多种一维曲线的跟踪结果。"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ruckig import InputParameter, OutputParameter, Result, Ruckig


DT = 0.01  # 10 ms
DURATION = 3.0
SETTLE_TIME = 2.0
MAX_VELOCITY = 4.1
s = 4.0
MAX_ACCELERATION = 4.1*s
MAX_JERK = 4.1*s*s
# 0.45 时七次时间缩放后的峰值 jerk 约为 48.74/s^3；降至 0.37
# 后峰值约为 40.07/s^3，可以满足 MAX_JERK = 41.0/s^3。
SINE_AMPLITUDE = 0.37
CSV_PATH = Path(__file__).with_name("plot_data.csv")


def elementary_curves():
    """用七次时间缩放生成三种曲线，并计算关于物理时间的解析导数。"""
    t = np.arange(0.0, DURATION + DT / 2.0, DT)
    tau = t / DURATION

    # 七次 smootherstep：位置参数及其前三阶导数在首尾均为 0。
    h = 35.0 * tau**4 - 84.0 * tau**5 + 70.0 * tau**6 - 20.0 * tau**7
    dh = 140.0 * tau**3 - 420.0 * tau**4 + 420.0 * tau**5 - 140.0 * tau**6
    ddh = 420.0 * tau**2 - 1680.0 * tau**3 + 2100.0 * tau**4 - 840.0 * tau**5

    # s 与原始 t 具有相同的 0~3 s 参数范围。
    s = DURATION * h
    ds_dt = dh
    d2s_dt2 = ddh / DURATION
    u = s - DURATION / 2.0

    def time_derivatives(df_ds, d2f_ds2):
        """链式法则：f(s(t)) 关于物理时间 t 的一、二阶导数。"""
        velocity = df_ds * ds_dt
        acceleration = d2f_ds2 * ds_dt**2 + df_ds * d2s_dt2
        return velocity, acceleration

    quadratic_velocity, quadratic_acceleration = time_derivatives(
        u, np.ones_like(t)
    )
    cubic_velocity, cubic_acceleration = time_derivatives(
        0.36 * u**2, 0.72 * u
    )
    omega = 2.0 * np.pi / DURATION
    sine_velocity, sine_acceleration = time_derivatives(
        SINE_AMPLITUDE * omega * np.cos(omega * s),
        -SINE_AMPLITUDE * omega**2 * np.sin(omega * s),
    )

    return {
        # 几何曲线的极值点位于参数 s=1.5 s。
        "quadratic_with_extremum": (
            t,
            0.5 * u**2,
            quadratic_velocity,
            quadratic_acceleration,
            r"7th-order time-scaled quadratic: $y=0.5(s(t)-1.5)^2$",
        ),
        "cubic": (
            t,
            0.12 * u**3,
            cubic_velocity,
            cubic_acceleration,
            r"7th-order time-scaled cubic: $y=0.12(s(t)-1.5)^3$",
        ),
        "sine": (
            t,
            SINE_AMPLITUDE * np.sin(omega * s),
            sine_velocity,
            sine_acceleration,
            rf"7th-order time-scaled sine: $y={SINE_AMPLITUDE}\sin(2\pi s(t)/3)$",
        ),
    }


def csv_curve():
    """只读取 CSV 的 value 列；每行固定视为相隔 10 ms。"""
    values = np.genfromtxt(CSV_PATH, delimiter=",", names=True)["value"]
    values = np.atleast_1d(values).astype(float)
    if values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError(f"{CSV_PATH} 的 value 列至少需要 3 个有效数值")

    times = np.arange(values.size) * DT
    # CSV 没有解析函数，中心差分作为第 1/2 组的导数估计。
    velocity = np.gradient(values, DT, edge_order=2)
    acceleration = np.gradient(velocity, DT, edge_order=2)
    return times, values, velocity, acceleration, "CSV value (10 ms per row)"


def append_settle(position, velocity, acceleration):
    """在数据末尾增加静止段，让 Ruckig 有时间到达最终目标。"""
    settle_count = int(round(SETTLE_TIME / DT))
    position = np.concatenate((position, np.full(settle_count, position[-1])))
    velocity = np.concatenate((velocity, np.zeros(settle_count)))
    acceleration = np.concatenate((acceleration, np.zeros(settle_count)))
    # 最后一个原始采样点开始要求停在终点。
    velocity[len(velocity) - settle_count - 1] = 0.0
    acceleration[len(acceleration) - settle_count - 1] = 0.0
    times = np.arange(position.size) * DT
    return times, position, velocity, acceleration


def central_position_differences(position, original_count):
    """仅从位置计算中心差分速度，并继续中心差分得到加速度。"""
    velocity = np.gradient(position, DT, edge_order=2)
    acceleration = np.gradient(velocity, DT, edge_order=2)

    # 七次曲线的首尾速度、加速度已知为零；settle 段同样保持静止。
    velocity[0] = 0.0
    acceleration[0] = 0.0
    velocity[original_count - 1 :] = 0.0
    acceleration[original_count - 1 :] = 0.0
    return velocity, acceleration


def legal_target_derivatives(velocity, acceleration):
    """将目标状态限制在 Ruckig 配置的运动学约束内。"""
    return (
        np.clip(velocity, -MAX_VELOCITY, MAX_VELOCITY),
        np.clip(acceleration, -MAX_ACCELERATION, MAX_ACCELERATION),
    )


def run_ruckig(times, position, velocity, acceleration):
    otg = Ruckig(1, DT)
    inp = InputParameter(1)
    out = OutputParameter(1)

    inp.current_position = [float(position[0])]
    inp.current_velocity = [0.0]
    inp.current_acceleration = [0.0]
    inp.max_velocity = [MAX_VELOCITY]
    inp.max_acceleration = [MAX_ACCELERATION]
    inp.max_jerk = [MAX_JERK]

    planned = np.empty_like(position)
    for i, t in enumerate(times):
        inp.target_position = [float(position[i])]
        inp.target_velocity = [float(velocity[i])]
        inp.target_acceleration = [float(acceleration[i])]
        result = otg.update(inp, out)
        if result == Result.Error:
            raise RuntimeError(f"Ruckig 规划失败，t={t:.3f} s")
        planned[i] = out.new_position[0]
        out.pass_to_input(inp)
    return planned


def plan_experiments(position, true_velocity, true_acceleration):
    original_count = position.size
    times, position, true_velocity, true_acceleration = append_settle(
        position, true_velocity, true_acceleration
    )
    diff_velocity, diff_acceleration = central_position_differences(
        position, original_count
    )
    zero = np.zeros_like(position)

    inputs = {
        # "1. true/estimated velocity & acceleration": (
        #     true_velocity,
        #     true_acceleration,
        # ),
        # "2. true/estimated velocity, acceleration = 0": (true_velocity, zero),
        "3. position central-difference velocity & acceleration": (
            diff_velocity*0.1,
            diff_acceleration*0.1,
        ),
        # "4. position backward-difference velocity, acceleration = 0": (
        #     diff_velocity,
        #     zero,
        # ),
        "5. position only (velocity = acceleration = 0)": (zero, zero),
    }

    planned = {}
    for label, (velocity, acceleration) in inputs.items():
        velocity, acceleration = legal_target_derivatives(velocity, acceleration)
        planned[label] = run_ruckig(times, position, velocity, acceleration)
    return times, position, original_count, planned


def draw_figure(name, data):
    _, position, velocity, acceleration, title = data
    times, target, original_count, experiments = plan_experiments(
        position, velocity, acceleration
    )

    # 大尺寸画布 + 高 DPI PNG；同时输出 SVG，便于无限放大查看细节。
    fig, ax = plt.subplots(figsize=(16, 9), dpi=150)
    ax.plot(
        times[:original_count],
        target[:original_count],
        "k--",
        linewidth=2.2,
        label="Target position",
    )
    for label, planned in experiments.items():
        ax.plot(times, planned, linewidth=1.5, label=label)

    ax.set_title(title)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Position")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=11)
    fig.tight_layout()
    output = Path(__file__).with_name(f"ruckig_{name}.png")
    fig.savefig(output, dpi=600, bbox_inches="tight")
    fig.savefig(output.with_suffix(".svg"), format="svg", bbox_inches="tight")
    return output


def main():
    datasets = elementary_curves()
    datasets["csv"] = csv_curve()

    for name, data in datasets.items():
        output = draw_figure(name, data)
        print(f"Saved: {output}")
    # plt.show()


if __name__ == "__main__":
    main()
