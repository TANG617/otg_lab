# Notation and timing

This file is the canonical notation contract for the manuscript. Symbols may
be shortened in display captions, but their time and information semantics
must not change.

## Indices, periods, and clocks

| Symbol | Definition |
|---|---|
| \(k\in\mathbb{Z}_{\ge0}\) | Control/sample index on the nominal grid |
| \(DT>0\) | Nominal control period; \(DT=0.01\,\mathrm{s}\) in the reported protocols |
| \(t_k=kDT\) | Nominal control time for tick \(k\) |
| \(\tau^{\mathrm{src}}_i\) | Physical source time represented by sample \(i\) |
| \(\tau^{\mathrm{avail}}_i\) | Earliest time an online algorithm may consume sample \(i\) |
| \(H\in\mathbb{Z}_{\ge0}\) | Prediction horizon in control periods |
| \(t_{k+H}=t_k+HDT\) | Prediction target time on the nominal grid |
| \(T_{\mathrm{free}}(x^{\mathrm{start}},x^{\mathrm{end}})\) | Unconstrained-by-minimum-duration OTG duration for the explicitly named start/end solve |
| \(v_{\max},a_{\max},j_{\max}\) | Symmetric per-axis velocity, acceleration, and jerk magnitudes; reported single-axis values are 4.1 rad/s, 8.2 rad/s², and 4000 rad/s³ |

Source time and availability time answer different questions. A delayed
measurement can satisfy
\(\tau^{\mathrm{avail}}_i>\tau^{\mathrm{src}}_i\). A future prediction represents
a state later than its availability:
\(t_{k+H}>\tau^{\mathrm{avail}}\). Neither relation licenses relabelling an
estimate as truth.

The development CSV is evaluated on fixed-grid semantics:
\(t_k=kDT\) with one row per \(10\) ms. Its stored elapsed-time/timestamp fields
do not define the evaluation grid for the reported Phase A CSV results.

## State symbols

For one axis, use \(x=[p,v,a]^\mathsf{T}\). For multiple axes, the same symbols
denote stacked vectors and bounds are componentwise.

| Symbol | Physical meaning | Represented time | Availability |
|---|---|---|---|
| \(p_k^{\mathrm{ref}}\) | Position-only reference sample used for position-following evaluation | Its stated source/grid time, normally \(t_k\) | Explicit sample arrival; by \(t_k\) only when stated |
| \(x_k^{\mathrm{cur}}=[p_k^{\mathrm{cur}},v_k^{\mathrm{cur}},a_k^{\mathrm{cur}}]^\mathsf{T}\) | State from which the command interval is planned | \(t_k\) | At control tick \(k\) |
| \(\hat{x}_{\ell\mid k}^{\mathrm{post}}\) | Causal estimator posterior for state time \(t_\ell\), using information available by \(t_k\) | \(t_\ell\), with \(\ell\le k\) online | \(t_k\) |
| \(\hat{x}_{k\mid k}^{\mathrm{post}}\) | Posterior synchronized/propagated to current control time when that propagation is explicitly performed | \(t_k\) | \(t_k\) |
| \(\bar{x}_{k+H\mid k}^{\mathrm{ref}}\) | Requested future-reference objective based on information available by \(k\); it need not be one-step reachable | \(t_{k+H}\) | \(t_k\) |
| \(x_{k+1}^{\mathrm{target}}\) | Executable-target governor output (use `executable target` in prose) | \(t_{k+1}\) | During tick \(k\) |
| \(x_{k+1}^{\mathrm{cmd}}\) | State endpoint actually issued for the next cycle by the executed command profile | \(t_{k+1}\) | End of tick \(k\), for interval \([t_k,t_{k+1}]\) |
| \(x_{k+1}^{\mathrm{meas}}\) | Measured feedback state labelled for the next measurement time | \(t_{k+1}\) or explicit source time | At its explicit arrival time |
| \(x_{k+1}^{\mathrm{plant}}\) | True simulated-plant state, only in simulation with truth fields | \(t_{k+1}\) | Simulator-defined |

The horizon \(H\) belongs to the future-reference objective and is not
silently set to one. The governor maps that possibly farther-horizon objective
to the adjacent executable target at \(t_{k+1}\). When the executable governor
is bypassed, \(x_{k+1}^{\mathrm{target}}\) is absent rather than silently
copied from the request. When the follower executes the direct governor
action, \(x_{k+1}^{\mathrm{cmd}}=x_{k+1}^{\mathrm{target}}\). With secondary
shaping or fallback they can differ, and the actual command algorithm must be
recorded.

The superscript `truth` is reserved for analytic/synthetic truth. It is never
used for a derivative of the development CSV.

## Causal information set and mapping

Let

\[
\mathcal I_k =
\left\{
  p_i^{\mathrm{ref}},\tau_i^{\mathrm{src}},\tau_i^{\mathrm{avail}}
  : \tau_i^{\mathrm{avail}}\le t_k
\right\}
\cup \{x_k^{\mathrm{cur}}\}
\]

be the information available when tick \(k\) is computed. The online task is

\[
\mathcal I_k
\longmapsto
\hat{x}_{\ell\mid k}^{\mathrm{post}}
\longmapsto
\bar{x}_{k+H\mid k}^{\mathrm{ref}}
\longmapsto
x_{k+1}^{\mathrm{target}}
\longmapsto
x_{k+1}^{\mathrm{cmd}} .
\]

A compact problem statement may be written

\[
p_{0:k}^{\mathrm{ref}},x_k^{\mathrm{cur}}
\longmapsto x_{k+1}^{\mathrm{cmd}},
\]

provided the text states that \(p_{0:k}^{\mathrm{ref}}\) means only samples
available by \(t_k\), not the entire offline file.

## Target/output timing contract

For the ordinary state-to-state Phase A call:

\[
\boxed{\mathrm{target}[k]\longrightarrow\mathrm{output}[k+1]}
\]

The target passed during control tick \(k\) is an endpoint request, while the
returned/passed-forward output state belongs to \(t_{k+1}\). Therefore a target
whose state time is \(t_k\) and a command whose state time is \(t_{k+1}\) are
not time-aligned even if they share the same array index in implementation.

The experiment's one-cycle index relationship is not asserted to be:

- a universal lower bound on physical robot lag;
- estimator delay;
- network/actuator delay;
- prediction horizon;
- or proof that an arbitrary target is reachable within \(DT\).

These quantities must be reported separately.

## Estimator timing examples

### Backward differences

\[
\hat v_k^{\mathrm{BW}}
=\frac{p_k-p_{k-1}}{DT},
\qquad
\hat a_k^{\mathrm{BW}}
=\frac{p_k-2p_{k-1}+p_{k-2}}{DT^2}.
\]

The velocity approximation is naturally centred near \(t_k-DT/2\), while the
second difference is centred at \(t_{k-1}\). Combining both with \(p_k\) without
a propagation model mixes state times.

### Offline zero-delay-labelled centered differences

\[
\hat v_k^{\mathrm{CD}}
=\frac{p_{k+1}-p_{k-1}}{2DT},
\qquad
\hat a_k^{\mathrm{CD}}
=\frac{p_{k+1}-2p_k+p_{k-1}}{DT^2}.
\]

These formulas are aligned to \(t_k\) at interior points but require
\(p_{k+1}\). They are noncausal when labelled available at \(t_k\).

### Causal delayed centered estimate

After \(p_k\) arrives, the same three-sample stencil can estimate \(t_{k-1}\):

\[
\hat v_{k-1\mid k}^{\mathrm{CD}}
=\frac{p_k-p_{k-2}}{2DT},
\qquad
\hat a_{k-1\mid k}^{\mathrm{CD}}
=\frac{p_k-2p_{k-1}+p_{k-2}}{DT^2}.
\]

It is causal with one-sample group delay. If a constant-acceleration model is
used to propagate velocity,

\[
\hat v_{k\mid k}
=\hat v_{k-1\mid k}+\hat a_{k-1\mid k}DT,
\qquad
\hat a_{k\mid k}=\hat a_{k-1\mid k},
\]

the propagated state's model assumptions and remaining acceleration time
semantics must be stated. Propagation does not erase the estimator's source
provenance.

## Command dynamics

For one constant jerk \(j_k\) over \([t_k,t_{k+1}]\):

\[
a_{k+1}=a_k+j_kDT,
\]

\[
v_{k+1}=v_k+a_kDT+\frac{1}{2}j_kDT^2,
\]

\[
p_{k+1}=p_k+v_kDT+\frac{1}{2}a_kDT^2
+\frac{1}{6}j_kDT^3.
\]

These equations define the direct one-step command and exact adjacent-state
consistency. They do not describe an ordinary Ruckig prefix that contains more
than one jerk segment.

For a piecewise-constant-jerk profile with boundaries
\(0=s_0<s_1<\cdots<s_m=DT\), integrate each \(j_r\) on
\([s_r,s_{r+1}]\) in order. Velocity extrema are checked at endpoints and at
any interior time where acceleration crosses zero. Acceleration extrema occur
at segment endpoints; jerk is checked per segment.

## Feasibility predicates

The manuscript must not use the bare word “feasible” when one of the following
is intended.

### Point admissibility

A state \(x=[p,v,a]^\mathsf{T}\) is point-admissible when it is finite and

\[
|v|\le v_{\max},\qquad |a|\le a_{\max}.
\]

Position bounds, if an application adds them, are a separate constraint. Point
admissibility alone says nothing about one-step reachability or adjacent-state
consistency.

### Stopping viability

Under symmetric jerk and velocity limits, the directional terminal stopping
envelope used here additionally requires

\[
a>0\Rightarrow v+\frac{a^2}{2j_{\max}}\le v_{\max},
\]

\[
a<0\Rightarrow v-\frac{a^2}{2j_{\max}}\ge -v_{\max},
\]

together with point V/A admissibility. It states that acceleration can be
removed at bounded jerk without crossing the approached velocity boundary.

### One-step reachability

A requested \(x_{k+1}\) is one-step reachable from \(x_k^{\mathrm{cur}}\) if an
allowed command profile on \([t_k,t_{k+1}]\) reaches that exact state while
satisfying continuous V/A/J limits. For the direct governor, the allowed
profile is one constant jerk and the three integration equations above must
hold exactly.

An ordinary-Ruckig diagnostic
\(T_{\mathrm{free}}(x_k^{\mathrm{cur}},x^{\mathrm{end}})\le DT\) concerns
only that named start/end solve. The endpoint must be identified as requested,
native, or committed. The duration must not be copied from a requested target
to a different committed fallback command and is not a substitute for auditing
the profile that was executed.

### Sampled-sequence consistency

A command sequence is sampled-sequence consistent when every executed profile
\(\pi_k\) connects the actual planning state \(x_k^{\mathrm{cur}}\) to the
committed endpoint \(x_{k+1}^{\mathrm{cmd}}\) while satisfying the stated
dynamics and continuous limits. For the direct condition:

\[
\exists j_k\in[-j_{\max},j_{\max}]
\quad\text{s.t.}\quad
x_{k+1}^{\mathrm{cmd}}
=\Phi_{DT}(x_k^{\mathrm{cur}},j_k)
\]

with continuous V/A/J legality for every interval. This reduces to
target-to-target adjacency only in the special case where each executable
target is executed unchanged and the next planning state is the previous
command. Under measured or hybrid feedback, or after secondary shaping or
fallback, target-to-target differences are not the executed profile.
Individually point-admissible samples need not be sequence-consistent.

### Next-step existence

A terminal command has next-step existence when at least one allowed action
over the following period preserves segment constraints and the stopping
envelope. In the current direct construction this is an invariant check
condition. For unshielded ordinary Ruckig it is a diagnostic and must not
silently trigger an algorithm-changing replacement.

## Method and command identity

| Term | Canonical definition |
|---|---|
| **native command** | Command profile produced by the declared native follower and executed without replacement |
| **ordinary Ruckig** | Unshielded state-to-state Ruckig update whose native control-period prefix is executed |
| **shielded command** | Native candidate inspected by an explicitly declared viability/safety shield and possibly replaced or changed by that shield |
| **viability-shielded Ruckig** | Method identity that permits the explicit shield; it is not ordinary Ruckig |
| **direct constant-jerk execution** | The executable-target governor's single constant-jerk action is the command, without secondary Ruckig shaping |
| **fallback command** | Explicit replacement command used after the requested/native path fails; its controller and reason are recorded |
| **mixed method** | An aggregate in which algorithm-changing fallbacks prevent one pure executed algorithm identity |
| **tracking-aware follower** | Generic capability for constrained following of a moving reference; may include `Trackig`, a governor, or jerk-QP/MPC |

`fallback_requested` and `fallback_applied` are distinct. A status-only request
that does not change the command is not recorded as an applied fallback.
Applied fallback that changes the algorithm implies
`native_command_executed=false`.

## Evaluation semantics

- Primary position-following errors compare the state named by the protocol
  (for example command or plant position at command time) against
  \(p^{\mathrm{ref}}\) on the explicitly stated evaluation clock.
- Lag is an alignment diagnostic and never substitutes for RMSE.
- `prediction_time`, estimator delay, target time, command time, measured-state
  time, plant delay, and best-lag estimate remain separate quantities.
- `acceleration_difference_jerk=(a_{k+1}^{\mathrm{cmd}}-
  a_k^{\mathrm{cur}})/DT` is a sampled endpoint diagnostic. It is not internal
  Ruckig jerk for a multi-segment prefix.
- `new_jerk` is used only where a direct constant-jerk value is genuinely
  available.
- `internal_trajectory_jerk`/profile jerk refers to the maximum or stated
  quantity from the continuous piecewise profile.

## Canonical terminology

Use exactly:

- **ordinary Ruckig**
- **Ruckig Tracking Interface**
- API class name **`Trackig`**
- **tracking-aware follower**
- **executable-target governor**
- **position-only reference stream**
- **reference following**
- **state-to-state online trajectory generator (OTG)**

Prefer **reference following**, **command generation**, and **constrained
execution**. Use **robot tracking**, **controller**, and **closed-loop
tracking** only when a source genuinely includes robot/hardware feedback. The
present evidence does not.
