"""CSV-first, fixed-grid, single-axis trajectory tracking experiments."""

from .analysis import (
    AnalysisSpec,
    ComparisonSpec,
    ComparisonTable,
    EvaluationWindow,
    MethodPair,
    MetricSet,
    MetricSpec,
    MetricTable,
    ReferenceAnalysis,
    analyze_reference,
    analyze_tracking,
    compare_methods,
)
from .csvio import (
    load_trajectory_csv,
    load_trajectory_metadata,
    write_trajectory_csv,
)
from .experiment import (
    ExperimentInput,
    ExperimentResult,
    ExperimentSpec,
    InputGate,
    load_tracking_run_artifacts,
    run_experiment,
)
from .generators import (
    convert_value_column_csv,
    generate_analytic_trajectory,
    write_analytic_trajectory_csv,
)
from .models import (
    ComponentSpec,
    Measurement,
    MotionLimits,
    RunConfig,
    State,
    TrackingMethodSpec,
    TrackingRun,
    Trajectory,
    TrajectoryMetadata,
)
from .tracking import run_tracking

__all__ = [
    "AnalysisSpec",
    "ComparisonSpec",
    "ComparisonTable",
    "ComponentSpec",
    "EvaluationWindow",
    "ExperimentInput",
    "ExperimentResult",
    "ExperimentSpec",
    "InputGate",
    "Measurement",
    "MethodPair",
    "MetricSet",
    "MetricSpec",
    "MetricTable",
    "MotionLimits",
    "ReferenceAnalysis",
    "RunConfig",
    "State",
    "TrackingMethodSpec",
    "TrackingRun",
    "Trajectory",
    "TrajectoryMetadata",
    "analyze_reference",
    "analyze_tracking",
    "compare_methods",
    "convert_value_column_csv",
    "generate_analytic_trajectory",
    "load_trajectory_csv",
    "load_trajectory_metadata",
    "load_tracking_run_artifacts",
    "run_experiment",
    "run_tracking",
    "write_analytic_trajectory_csv",
    "write_trajectory_csv",
]
