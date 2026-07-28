"""Composable, single-axis tracking components.

This module is the small public construction layer for the CSV-first tracking
engine.  The numerical implementations remain in the focused estimator,
predictor, governor, and follower modules; this module gives those
implementations stable IDs and one uniform factory interface.

Custom experiment components can either be registered globally with
``register_component`` or supplied directly through ``ComponentSpec.factory``.
Factories are invoked once per run, so no state is shared between methods or
input trajectories.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

from .estimators import make_estimator
from .followers import (
    DirectExecutableFollower,
    RuckigFollower,
    scalar_project_target_state,
)
from .governors import (
    GovernorResult,
    JerkQPGovernor,
    OneStepBoundedJerkGovernor,
)
from .governors import (
    MotionLimits as NumericalMotionLimits,
)
from .models import ComponentSpec, MotionLimits, RunConfig, Trajectory
from .predictors import make_predictor, select_target_components
from .types import TimedState

ComponentFactory = Callable[[ComponentSpec, "ComponentContext"], Any]
COMPONENT_KINDS = ("estimator", "predictor", "target_builder", "governor", "follower")


@dataclass(frozen=True)
class ComponentContext:
    """Resolved inputs available while constructing one method component."""

    dt_s: float
    limits: MotionLimits
    numerical_limits: NumericalMotionLimits
    trajectory: Trajectory
    run_config: RunConfig


class TargetBuilder:
    """Select the P, PV, or PVA components of a predicted target."""

    def __init__(self, components: str) -> None:
        normalized = str(components).strip().lower()
        if normalized not in {"p", "pv", "pva"}:
            raise ValueError("target components must be one of p, pv, pva")
        self.components = normalized
        self.name = normalized

    def reset(self) -> None:
        """Target builders are stateless; provided for a uniform lifecycle."""

    def build(self, prediction: TimedState) -> TimedState:
        return select_target_components(prediction, self.components)

    def __call__(self, prediction: TimedState) -> TimedState:
        return self.build(prediction)


class IdentityGovernor:
    """Pass a target through unchanged while preserving governor diagnostics."""

    name = "none"

    def __init__(self, dt_s: float, limits: NumericalMotionLimits) -> None:
        self.dt_s = float(dt_s)
        self.limits = limits
        self.command_state: np.ndarray | None = None

    def reset(self, state: np.ndarray | None = None) -> None:
        self.command_state = None if state is None else np.asarray(state, dtype=float).copy()

    def update(
        self,
        raw_target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        target = np.asarray(raw_target, dtype=float)
        if target.shape == (3,):
            target = target.reshape(1, 3)
        if target.shape != (1, 3) or not np.all(np.isfinite(target)):
            raise ValueError("identity governor target must be a finite single-axis state")
        current = self.command_state if current_state is None else current_state
        if current is None:
            raise ValueError("identity governor requires an initial/current state")
        current = np.asarray(current, dtype=float).reshape(1, 3)
        self.command_state = target.copy()
        return GovernorResult(
            executable_state=target.copy(),
            jerk=np.asarray([(target[0, 2] - current[0, 2]) / self.dt_s]),
            target_time=float(control_time + self.dt_s),
            target_feasible=True,
            target_projected=False,
            fallback=False,
            fallback_reason="",
            solver_status="not_run:identity",
            iterations=0,
            compute_us=0.0,
            distortion=np.zeros_like(target),
            sequence=target[None, :, :],
            requested_target_feasible=True,
            safety_guarantee=True,
            fallback_requested=False,
            fallback_applied=False,
        )


class ScalarProjectionGovernor(IdentityGovernor):
    """Explicit point-wise V/A projection retained for diagnostic experiments."""

    name = "scalar_projection"

    def update(
        self,
        raw_target: np.ndarray,
        *,
        control_time: float,
        current_state: np.ndarray | None = None,
    ) -> GovernorResult:
        raw = np.asarray(raw_target, dtype=float)
        if raw.shape == (3,):
            raw = raw.reshape(1, 3)
        projected, changed = scalar_project_target_state(raw, self.limits)
        result = super().update(
            projected,
            control_time=control_time,
            current_state=current_state,
        )
        return GovernorResult(
            **{
                **result.__dict__,
                "target_projected": bool(changed),
                "solver_status": "scalar_projection",
                "distortion": projected - raw,
            }
        )


_REGISTRY: dict[str, dict[str, ComponentFactory]] = {
    kind: {} for kind in COMPONENT_KINDS
}
COMPONENT_REGISTRY: Mapping[str, Mapping[str, ComponentFactory]] = MappingProxyType(
    {
        kind: MappingProxyType(factories)
        for kind, factories in _REGISTRY.items()
    }
)


def _normalize_id(value: str) -> str:
    normalized = str(value).strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("component_id must not be empty")
    return normalized


def register_component(
    kind: str,
    component_id: str,
    factory: ComponentFactory,
    *,
    replace: bool = False,
) -> None:
    """Register a stable component factory.

    Registered factories receive the original :class:`ComponentSpec` and a
    fully resolved :class:`ComponentContext`.  Replacing a built-in is opt-in
    to keep experiment identities stable.
    """

    normalized_kind = _normalize_id(kind)
    if normalized_kind not in _REGISTRY:
        raise KeyError(
            f"unknown component kind {kind!r}; expected one of "
            f"{', '.join(COMPONENT_KINDS)}"
        )
    normalized_id = _normalize_id(component_id)
    if not callable(factory):
        raise TypeError("component factory must be callable")
    if normalized_id in _REGISTRY[normalized_kind] and not replace:
        raise ValueError(
            f"{normalized_kind} component {normalized_id!r} is already registered"
        )
    _REGISTRY[normalized_kind][normalized_id] = factory


def available_components(kind: str | None = None) -> Mapping[str, Any]:
    """Return a read-only snapshot of the component registry."""

    if kind is not None:
        normalized = _normalize_id(kind)
        if normalized not in _REGISTRY:
            raise KeyError(f"unknown component kind {kind!r}")
        return MappingProxyType(dict(_REGISTRY[normalized]))
    return MappingProxyType(
        {
            name: MappingProxyType(dict(factories))
            for name, factories in _REGISTRY.items()
        }
    )


def _invoke_custom_factory(
    factory: Callable[..., Any],
    spec: ComponentSpec,
    context: ComponentContext,
) -> Any:
    """Call a per-spec custom factory without imposing irrelevant kwargs."""

    params = dict(spec.params)
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(**params)

    injectable = {
        "context": context,
        "component_spec": spec,
        "spec": spec,
        "dt_s": context.dt_s,
        "limits": context.limits,
        "numerical_limits": context.numerical_limits,
        "trajectory": context.trajectory,
        "run_config": context.run_config,
    }
    for name, parameter in signature.parameters.items():
        if (
            name not in params
            and name in injectable
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ):
            params[name] = injectable[name]
    signature.bind(**params)
    return factory(**params)


def build_component(
    kind: str,
    spec: ComponentSpec,
    context: ComponentContext,
) -> Any:
    """Construct one fresh component from a spec and resolved run context."""

    normalized_kind = _normalize_id(kind)
    if normalized_kind not in _REGISTRY:
        raise KeyError(f"unknown component kind {kind!r}")
    if not isinstance(spec, ComponentSpec):
        raise TypeError("component configuration must be a ComponentSpec")
    if spec.factory is not None:
        component = _invoke_custom_factory(spec.factory, spec, context)
    else:
        component_id = _normalize_id(spec.component_id)
        try:
            factory = _REGISTRY[normalized_kind][component_id]
        except KeyError as error:
            choices = ", ".join(sorted(_REGISTRY[normalized_kind]))
            raise KeyError(
                f"unknown {normalized_kind} component {spec.component_id!r}; "
                f"available IDs are {choices}"
            ) from error
        component = factory(spec, context)
    if component is None:
        raise TypeError(
            f"{normalized_kind} factory for {spec.component_id!r} returned None"
        )
    return component


def build_estimator(spec: ComponentSpec, context: ComponentContext) -> Any:
    return build_component("estimator", spec, context)


def build_predictor(spec: ComponentSpec, context: ComponentContext) -> Any:
    return build_component("predictor", spec, context)


def build_target_builder(spec: ComponentSpec, context: ComponentContext) -> Any:
    return build_component("target_builder", spec, context)


def build_governor(spec: ComponentSpec, context: ComponentContext) -> Any:
    return build_component("governor", spec, context)


def build_follower(spec: ComponentSpec, context: ComponentContext) -> Any:
    return build_component("follower", spec, context)


def _estimator_factory(spec: ComponentSpec, context: ComponentContext) -> Any:
    params = dict(spec.params)
    params.setdefault("nominal_dt", context.dt_s)
    params.setdefault("allow_variable_dt", False)
    if _normalize_id(spec.component_id) in {
        "jerk_limited",
        "jerk_limited_differentiator",
    }:
        params.setdefault("max_velocity", context.limits.max_velocity_rad_s)
        params.setdefault(
            "max_acceleration",
            context.limits.max_acceleration_rad_s2,
        )
        params.setdefault("max_jerk", context.limits.max_jerk_rad_s3)
    return make_estimator(spec.component_id, **params)


def _predictor_factory(spec: ComponentSpec, context: ComponentContext) -> Any:
    params = dict(spec.params)
    normalized = _normalize_id(spec.component_id)
    if normalized in {
        "oracle",
        "oracle_future_state",
        "oracle_future_state_offline",
    }:
        if params.pop("noncausal_diagnostic", False) is not True:
            raise ValueError(
                "oracle predictor requires "
                "noncausal_diagnostic=True in ComponentSpec params"
            )
        trajectory = context.trajectory
        if trajectory.velocity_rad_s is None or trajectory.acceleration_rad_s2 is None:
            raise ValueError("oracle predictor requires reference velocity and acceleration")
        params.setdefault("truth_times", trajectory.time_s)
        params.setdefault("truth_position", trajectory.position_rad)
        params.setdefault("truth_velocity", trajectory.velocity_rad_s)
        params.setdefault("truth_acceleration", trajectory.acceleration_rad_s2)
        params.setdefault("truth_jerk", trajectory.jerk_rad_s3)
        params.setdefault("out_of_range", "clip")
    return make_predictor(spec.component_id, **params)


def _target_factory(spec: ComponentSpec, context: ComponentContext) -> TargetBuilder:
    del context
    components = dict(spec.params).pop("components", spec.component_id)
    if dict(spec.params).keys() - {"components"}:
        unknown = sorted(dict(spec.params).keys() - {"components"})
        raise TypeError(f"unknown target builder parameters: {', '.join(unknown)}")
    return TargetBuilder(str(components))


def _identity_governor_factory(
    spec: ComponentSpec, context: ComponentContext
) -> IdentityGovernor:
    if spec.params:
        raise TypeError("the none governor accepts no parameters")
    return IdentityGovernor(context.dt_s, context.numerical_limits)


def _projection_governor_factory(
    spec: ComponentSpec, context: ComponentContext
) -> ScalarProjectionGovernor:
    if spec.params:
        raise TypeError("scalar_projection accepts no parameters")
    return ScalarProjectionGovernor(context.dt_s, context.numerical_limits)


def _one_step_governor_factory(
    spec: ComponentSpec, context: ComponentContext
) -> OneStepBoundedJerkGovernor:
    params = dict(spec.params)
    params.pop("measured_state_mode", None)
    return OneStepBoundedJerkGovernor(
        1,
        context.dt_s,
        context.numerical_limits,
        measured_state_mode="measured",
        **params,
    )


def _qp_governor_factory(
    spec: ComponentSpec, context: ComponentContext
) -> JerkQPGovernor:
    return JerkQPGovernor(
        1,
        context.dt_s,
        context.numerical_limits,
        **dict(spec.params),
    )


def _direct_follower_factory(
    spec: ComponentSpec, context: ComponentContext
) -> DirectExecutableFollower:
    return DirectExecutableFollower(
        1,
        context.dt_s,
        context.numerical_limits,
        **dict(spec.params),
    )


def _ruckig_follower_factory(
    spec: ComponentSpec,
    context: ComponentContext,
    *,
    safety_shield: bool,
) -> RuckigFollower:
    params = dict(spec.params)
    configured_shield = params.pop("safety_shield", safety_shield)
    if bool(configured_shield) != safety_shield:
        expected = (
            "ruckig_viability_shield" if configured_shield else "ordinary ruckig"
        )
        raise ValueError(
            "safety_shield is part of the stable follower ID; use "
            f"{expected!r} instead of overriding it in params"
        )
    minimum_duration = context.run_config.minimum_duration_s
    if minimum_duration is None or float(minimum_duration) <= 0.0:
        minimum_duration = context.dt_s
    return RuckigFollower(
        1,
        context.dt_s,
        context.numerical_limits,
        minimum_duration=float(minimum_duration),
        safety_shield=safety_shield,
        **params,
    )


def _ordinary_ruckig_factory(
    spec: ComponentSpec, context: ComponentContext
) -> RuckigFollower:
    return _ruckig_follower_factory(spec, context, safety_shield=False)


def _shielded_ruckig_factory(
    spec: ComponentSpec, context: ComponentContext
) -> RuckigFollower:
    return _ruckig_follower_factory(spec, context, safety_shield=True)


def numerical_limits(limits: MotionLimits) -> NumericalMotionLimits:
    """Convert the public scalar limits into the existing numerical type."""

    return NumericalMotionLimits.broadcast(
        1,
        limits.max_velocity_rad_s,
        limits.max_acceleration_rad_s2,
        limits.max_jerk_rad_s3,
    )


def component_context(
    trajectory: Trajectory,
    run_config: RunConfig,
    dt_s: float,
) -> ComponentContext:
    return ComponentContext(
        dt_s=float(dt_s),
        limits=run_config.limits,
        numerical_limits=numerical_limits(run_config.limits),
        trajectory=trajectory,
        run_config=run_config,
    )


for _estimator_id in (
    "position_only",
    "position",
    "p",
    "raw_backward_difference",
    "raw_backward",
    "delay_one_centered_difference",
    "delay_one_centered",
    "local_poly",
    "alpha_beta_gamma",
    "ca_kf",
    "robust_ca_kf",
    "cj_kf",
    "jerk_limited_differentiator",
):
    register_component("estimator", _estimator_id, _estimator_factory)

for _predictor_id in (
    "zero_order_hold",
    "zoh",
    "constant_velocity",
    "cv",
    "constant_acceleration",
    "ca",
    "constant_jerk",
    "cj",
    "local_polynomial",
    "local_poly",
    "oracle",
):
    register_component("predictor", _predictor_id, _predictor_factory)

for _target_id in ("p", "pv", "pva"):
    register_component("target_builder", _target_id, _target_factory)

register_component("governor", "none", _identity_governor_factory)
register_component("governor", "identity", _identity_governor_factory)
register_component("governor", "one_step", _one_step_governor_factory)
register_component(
    "governor", "one_step_bounded_jerk", _one_step_governor_factory
)
register_component("governor", "jerk_qp", _qp_governor_factory)
register_component("governor", "jerk_qp_mpc", _qp_governor_factory)
register_component("governor", "scalar_projection", _projection_governor_factory)

register_component("follower", "direct", _direct_follower_factory)
register_component("follower", "direct_executable", _direct_follower_factory)
register_component("follower", "ruckig", _ordinary_ruckig_factory)
register_component(
    "follower", "ordinary_ruckig_unshielded", _ordinary_ruckig_factory
)
register_component(
    "follower", "ruckig_viability_shield", _shielded_ruckig_factory
)
register_component(
    "follower",
    "ordinary_ruckig_with_viability_shield",
    _shielded_ruckig_factory,
)


__all__ = [
    "COMPONENT_KINDS",
    "COMPONENT_REGISTRY",
    "ComponentContext",
    "IdentityGovernor",
    "ScalarProjectionGovernor",
    "TargetBuilder",
    "available_components",
    "build_component",
    "build_estimator",
    "build_follower",
    "build_governor",
    "build_predictor",
    "build_target_builder",
    "component_context",
    "numerical_limits",
    "register_component",
]
