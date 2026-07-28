from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from otg_lab.csvio import (
    TRAJECTORY_CSV_HEADER,
    load_trajectory_csv,
    load_trajectory_metadata,
    write_trajectory_csv,
)
from otg_lab.generators import (
    ANALYTIC_GENERATOR_IDS,
    convert_value_column_csv,
    generate_analytic_trajectory,
    write_analytic_trajectory_csv,
)
from otg_lab.models import (
    ComponentSpec,
    MotionLimits,
    RunConfig,
    TrackingMethodSpec,
    Trajectory,
    TrajectoryMetadata,
)


def _position_only() -> Trajectory:
    return Trajectory(
        sample_index=np.arange(4),
        time_s=np.arange(4) * 0.01,
        position_rad=np.array([0.0, 0.1, -0.2, 0.3]),
    )


def test_trajectory_is_single_axis_uniform_and_owned() -> None:
    source = np.array([0.0, 0.1, 0.2])
    trajectory = Trajectory(
        sample_index=[0, 1, 2],
        time_s=[0.0, 0.01, 0.02],
        position_rad=source,
    )
    source[0] = 99.0
    assert trajectory.position_rad.tolist() == [0.0, 0.1, 0.2]
    assert not trajectory.position_rad.flags.writeable
    assert trajectory.dt == pytest.approx(0.01)
    assert trajectory.derivative_channels == ()

    with pytest.raises(ValueError, match="consecutive"):
        Trajectory([0, 2], [0.0, 0.01], [0.0, 0.1])
    with pytest.raises(ValueError, match="uniform"):
        Trajectory([0, 1, 2], [0.0, 0.01, 0.021], [0.0, 0.1, 0.2])
    with pytest.raises(ValueError, match="one-dimensional"):
        Trajectory([0, 1], [0.0, 0.01], [[0.0], [0.1]])


def test_empty_and_singleton_command_require_nominal_dt() -> None:
    with pytest.raises(ValueError, match="nominal_dt_s"):
        Trajectory([], [], [])
    empty = Trajectory([], [], [], nominal_dt_s=0.01)
    one = Trajectory([1], [0.01], [0.2], nominal_dt_s=0.01)
    assert empty.sample_count == 0
    assert one.sample_count == 1
    assert empty.dt == one.dt == 0.01


def test_position_only_csv_round_trip_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "reference.csv"
    trajectory = _position_only()
    metadata = TrajectoryMetadata.for_trajectory(
        trajectory,
        trajectory_id="recorded",
        source={"type": "fixture"},
    )
    write_trajectory_csv(path, trajectory, metadata)

    assert tuple(path.read_text(encoding="utf-8").splitlines()[0].split(",")) == (
        TRAJECTORY_CSV_HEADER
    )
    assert path.read_text(encoding="utf-8").splitlines()[1].endswith(",,,")
    loaded = load_trajectory_csv(path, require_metadata=True)
    loaded_metadata = load_trajectory_metadata(path)
    np.testing.assert_array_equal(loaded.sample_index, trajectory.sample_index)
    np.testing.assert_array_equal(loaded.time_s, trajectory.time_s)
    np.testing.assert_array_equal(loaded.position_rad, trajectory.position_rad)
    assert loaded.velocity_rad_s is None
    assert loaded_metadata.csv_sha256 is not None

    path.write_text(
        path.read_text(encoding="utf-8").replace("0.10000000000000001", "0.2"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_trajectory_csv(path)


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        (
            "sample_index,time_s,position_rad,velocity_rad_s,"
            "acceleration_rad_s2,jerk_rad_s3\n"
            "0,0,0,0,,\n1,0.01,1,,,\n",
            "entirely populated or entirely blank",
        ),
        (
            "sample_index,time_s,position_rad,velocity_rad_s,"
            "acceleration_rad_s2,jerk_rad_s3\n"
            "0,0,0,,,\n1,0.01,nan,,,\n",
            "position_rad must be finite",
        ),
        (
            "time_s,sample_index,position_rad,velocity_rad_s,"
            "acceleration_rad_s2,jerk_rad_s3\n",
            "header must be exactly",
        ),
    ],
)
def test_csv_rejects_partial_nonfinite_and_wrong_header(
    tmp_path: Path,
    csv_text: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(csv_text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_trajectory_csv(path)


def test_all_analytic_generators_have_truth_and_stationary_endpoints() -> None:
    for generator_id in ANALYTIC_GENERATOR_IDS:
        trajectory = generate_analytic_trajectory(generator_id)
        assert trajectory.sample_count == 501
        assert trajectory.derivative_channels == (
            "velocity_rad_s",
            "acceleration_rad_s2",
            "jerk_rad_s3",
        )
        assert trajectory.velocity_rad_s is not None
        assert trajectory.acceleration_rad_s2 is not None
        assert trajectory.jerk_rad_s3 is not None
        np.testing.assert_allclose(
            trajectory.velocity_rad_s[[0, 300, -1]],
            0.0,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            trajectory.acceleration_rad_s2[[0, 300, -1]],
            0.0,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            trajectory.jerk_rad_s3[[0, 300, -1]],
            0.0,
            atol=1e-12,
        )


def test_analytic_generator_persists_through_canonical_loader(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sine.csv"
    generated = write_analytic_trajectory_csv(
        path,
        "sine",
        {"dt_s": 0.02, "duration_s": 2.0, "settle_duration_s": 0.2},
    )
    loaded = load_trajectory_csv(path, require_metadata=True)
    np.testing.assert_array_equal(loaded.position_rad, generated.position_rad)
    np.testing.assert_array_equal(loaded.jerk_rad_s3, generated.jerk_rad_s3)
    metadata = load_trajectory_metadata(path)
    assert metadata.generator_id == "sine"
    assert metadata.generator_params["dt_s"] == 0.02


def test_value_column_converter_uses_declared_fixed_grid(
    tmp_path: Path,
) -> None:
    source = tmp_path / "recorded_source.csv"
    source.write_text(
        "elapsed time,timestamp,topic,value\n"
        "100,999,a,0.25\n"
        "-2,3,b,0.5\n"
        "7,4,c,-0.75\n",
        encoding="utf-8",
    )
    output = tmp_path / "recorded.csv"
    trajectory = convert_value_column_csv(
        source,
        output,
        dt_s=0.01,
        trajectory_id="recorded",
    )
    np.testing.assert_array_equal(trajectory.time_s, [0.0, 0.01, 0.02])
    np.testing.assert_array_equal(trajectory.position_rad, [0.25, 0.5, -0.75])
    assert trajectory.velocity_rad_s is None
    metadata = load_trajectory_metadata(output)
    assert metadata.source["other_columns_ignored"] is True
    assert metadata.source_sha256 is not None
    loaded = load_trajectory_csv(output, require_metadata=True)
    np.testing.assert_array_equal(loaded.position_rad, trajectory.position_rad)


def test_method_and_run_config_contracts_are_stable() -> None:
    no_op = ComponentSpec("none")
    method = TrackingMethodSpec(
        method_id="baseline",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zoh"),
        target_builder=ComponentSpec("p"),
        governor=no_op,
        follower=ComponentSpec("direct"),
    )
    same = TrackingMethodSpec(
        method_id="baseline",
        estimator=ComponentSpec("position_only"),
        predictor=ComponentSpec("zoh"),
        target_builder=ComponentSpec("p"),
        governor=ComponentSpec("none"),
        follower=ComponentSpec("direct"),
    )
    assert method.fingerprint == same.fingerprint
    assert len(method.fingerprint) == 64

    config = RunConfig(
        limits=MotionLimits(4.1, 8.2, 4000.0),
        dt_s=0.01,
    )
    assert config.measurement_policy == "position_only"
    assert config.resolved_dt(_position_only()) == pytest.approx(0.01)
    with pytest.raises(ValueError, match="does not match"):
        RunConfig(
            limits=MotionLimits(4.1, 8.2, 4000.0),
            dt_s=0.02,
        ).resolved_dt(_position_only())


def test_metadata_rejects_unrecognized_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.meta.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "otg.trajectory.v1",
                "trajectory_id": "x",
                "kind": "reference",
                "dt_s": 0.01,
                "channel_semantics": {
                    "position_rad": "truth",
                    "velocity_rad_s": "unavailable",
                    "acceleration_rad_s2": "unavailable",
                    "jerk_rad_s3": "unavailable",
                },
                "unknown": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unexpected keys"):
        load_trajectory_metadata(path)
