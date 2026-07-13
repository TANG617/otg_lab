from math import sin, cos
from typing import NamedTuple

from ruckig import InputParameter, OutputParameter, Ruckig


class TargetState(NamedTuple):
    """Target signal sample, independent of the Ruckig Pro Trackig API."""

    position: list
    velocity: list
    acceleration: list


# Create the target state signal
def model_ramp(t, ramp_vel=0.5, ramp_pos=1.0):
    on_ramp = t < ramp_pos / abs(ramp_vel)
    return TargetState(
        position=[t * ramp_vel] if on_ramp else [ramp_pos],
        velocity=[ramp_vel] if on_ramp else [0.0],
        acceleration=[0.0],
    )


def model_constant_acceleration(t, ramp_acc=0.05):
    return TargetState(
        position=[t * t * ramp_acc],
        velocity=[t * ramp_acc],
        acceleration=[ramp_acc],
    )


def model_sinus(t, ramp_vel=0.4):
    return TargetState(
        position=[sin(ramp_vel * t)],
        velocity=[ramp_vel * cos(ramp_vel * t)],
        acceleration=[-ramp_vel * ramp_vel * sin(ramp_vel * t)],
    )


if __name__ == '__main__':
    # Create ordinary (Community/standard) Ruckig instances.
    inp = InputParameter(1)
    out = OutputParameter(inp.degrees_of_freedom)
    delta_time = 0.01
    otg = Ruckig(inp.degrees_of_freedom, delta_time)

    # Set input parameters
    inp.current_position = [0.0]
    inp.current_velocity = [0.0]
    inp.current_acceleration = [0.0]

    inp.max_velocity = [0.8]
    inp.max_acceleration = [2.0]
    inp.max_jerk = [5.0]

    print('target | follow')

    # Re-plan toward the current target sample on every control cycle.
    steps, target_list, follow_list = [], [], []
    for t in range(500):
        target_state = model_ramp(delta_time * t)

        inp.target_position = target_state.position
        inp.target_velocity = target_state.velocity
        inp.target_acceleration = target_state.acceleration

        steps.append(t)
        res = otg.update(inp, out)
        if int(res) < 0:
            raise RuntimeError(f'Ruckig update failed at step {t}: {res}')

        out.pass_to_input(inp)

        print(
            '\t'.join([f'{p:0.3f}' for p in target_state.position] +
                      [f'{p:0.3f}' for p in out.new_position]),
            f'in {out.calculation_duration:0.2f} [µs]',
        )

        target_list.append(
            [target_state.position, target_state.velocity, target_state.acceleration])
        follow_list.append(
            [out.new_position, out.new_velocity, out.new_acceleration])

    # Plot the trajectory
    from pathlib import Path
    examples_path = Path(__file__).parent.absolute()

    import numpy as np
    import matplotlib.pyplot as plt

    follow_list = np.array(follow_list)
    target_list = np.array(target_list)

    plt.ylabel(f'DoF 1')
    plt.plot(steps, follow_list[:, 0], label='Follow Position')
    plt.plot(steps, follow_list[:, 1], label='Follow Velocity', linestyle='dotted')
    plt.plot(steps, follow_list[:, 2], label='Follow Acceleration', linestyle='dotted')
    plt.plot(steps, target_list[:, 0], color='r', label='Target Position')
    plt.grid(True)
    plt.legend()

    plt.savefig(examples_path / '14_trajectory.pdf')
