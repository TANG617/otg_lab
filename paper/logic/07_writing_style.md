# Writing style

## Voice and paragraph structure

- Write the manuscript in English.
- Use concise technical prose and answer-first paragraphs: state the result or
  distinction, then explain its basis and boundary.
- Give each paragraph one main idea.
- Prefer concrete subjects: “the governor constructs,” “the protocol records,”
  or “the three references show.” Avoid anthropomorphic systems that “know,”
  “believe,” or “understand.”
- Separate observation, interpretation, and limitation. Do not turn a plausible
  mechanism into an observed cause without an isolating experiment.
- Use active voice when it makes responsibility clearer; passive voice is
  acceptable for protocol facts where the actor is irrelevant.

## Claim strength

Match verbs to the claim registry:

- Formal/interface work: **define, formulate, derive, construct, distinguish**.
- Conditional method behavior: **enforces under the stated model, verifies,
  preserves**.
- Empirical results: **observed, recorded, reduced under the tested
  conditions, supports**.
- Negative/unevaluated results: **did not improve, did not establish, remains
  unresolved, was not evaluated**.
- Corrective evidence: **exposed, reclassified, withdrew the confirmatory
  interpretation**.

Do not use **prove**, **demonstrate**, or **establish** for an unsupported,
negative, exploratory/confounded, or not-evaluated claim. A mathematical
derivation may use **proves** only for the exact proposition and assumptions
given next to it.

Do not write:

- obviously;
- clearly, unless the result is mathematically immediate from the preceding
  line;
- significant, unless statistical significance, test, family, and threshold
  are defined;
- superior, optimal, high-performance, breakthrough, state of the art;
- first or the first without a complete, verified novelty review;
- real-robot improvement, deployment ready, production safe, universally
  optimal, safety certified.

Avoid marketing, rhetorical questions, and claims of inevitability.

## Evidence-aware paragraph template

Results paragraphs use:

1. **Question:** the isolated scientific question;
2. **Result:** source-backed quantities and denominator;
3. **Interpretation:** the narrow inference allowed by the claim;
4. **Boundary:** population, information condition, and prohibited extension.

Do not mechanically print these labels if natural prose remains equally
auditable, but preserve all four functions.

## Required terminology

Use:

- ordinary Ruckig;
- Ruckig Tracking Interface;
- API class `Trackig`;
- tracking-aware follower;
- executable-target governor;
- position-only reference stream;
- reference following;
- state-to-state online trajectory generator (OTG);
- raw target, executable target, native command, shielded command, fallback
  command;
- source time, availability time, control time, prediction time, command time,
  and measured-state time.

Prefer **reference following**, **command generation**, and **constrained
execution**. Use **robot tracking**, **controller**, and **closed-loop
tracking** only for properly qualified prior/future work; the current evidence
has no robot/HIL result.

Hyphenation:

- position-only;
- state-to-state;
- next-cycle;
- one-step;
- one-step-reachable;
- jerk-limited;
- time-explicit;
- profile-aware;
- piecewise-constant-jerk;
- same-follower;
- post-freeze (adjective), after the freeze (noun phrase).

Do not alternate “governor,” “projector,” and “shield” for the same object.
Projection, governance, shielding, and fallback are distinct operations.

## Truth and information conditions

- Use **analytic truth** or **synthetic truth** only where truth fields exist.
- Use **ground truth** only for analytic/synthetic truth and only when the
  distinction adds value.
- CSV derivatives are **estimates**, **finite-difference targets**, or
  **diagnostics**, never truth.
- `oracle` always carries an explicit noncausal/future-information label.
- `causal` means all consumed samples were available by the decision time; it
  does not mean zero delay.
- Do not call an offline-centered row online, deployable, or causal.
- Do not call a prediction a future measurement or reference truth.

## Method identity

- Target components P/PV/PVA do not define the follower.
- “Ordinary Ruckig” means the unshielded native Ruckig prefix was executed.
- “Viability-shielded Ruckig” is a separate method.
- “Direct” means the selected constant-jerk action was executed without
  secondary Ruckig shaping.
- If fallback changes the algorithm, name the fallback controller and call the
  aggregate mixed when appropriate.
- Historical v3 names may be quoted only with their actual fallback behavior.

## Quantities and units

- Typeset units with `siunitx`: `\SI{10}{\milli\second}`,
  `\SI{4.1}{\radian\per\second}`,
  `\SI{8.2}{\radian\per\second\squared}`, and
  `\SI{4000}{\radian\per\second\cubed}`.
- Variables are italic; named operators and textual subscripts are upright.
- Define every percentage change as
  \((\text{baseline}-\text{candidate})/|\text{baseline}|\) or another explicit
  formula before use.
- Pair a percentage with the baseline/candidate absolute values or a generated
  table that shows them.
- State units in table headers and axis labels, not in every cell.
- Use a leading zero for magnitudes below one.
- Use meaningful precision based on source and display purpose; do not imply
  physical precision from binary floating-point output.
- “Zero” includes the explicit denominator: “0 of 42,199 recorded cycles.”
- Do not mix RMSE, MAE, maximum absolute error, or lag.
- Do not mix target, command, simulated-plant, or measured acceleration.
- Do not mix acceleration-difference/sampled jerk, direct `new_jerk`, or
  internal profile jerk.
- Do not mix estimator delay, prediction horizon, command delay, plant delay,
  or correlation lag.

All key empirical numbers in the manuscript come from generated macros or
tables. Do not hand-copy values from this logic layer.

## Mathematical style

- Vectors may use bold lowercase or an explicitly defined stacked \(x\); choose
  one convention in `notation.tex` and keep it.
- Use \(\mathrm{d}\) for differentials and upright operator names.
- Put equation assumptions before or immediately after the equation.
- Refer to exact clock/index relationships rather than “current” or “future”
  without a symbol.
- Use “point admissibility,” “one-step reachability,” “sampled-sequence
  consistency,” “stopping viability,” and “next-step existence” exactly as
  defined; never collapse them into bare “feasibility.”
- `minimum_duration` is a lower bound on planned duration, not an arrival
  deadline.
- An ordinary Ruckig prefix is piecewise constant jerk; never replace it in
  prose/equations with one average jerk.

## Citations

- Cite primary papers for algorithms and novelty context.
- Cite official documentation for current API spelling and behavior.
- Do not use a tutorial/blog as the sole support for an algorithmic novelty
  statement.
- A citation supports the immediately associated sentence, not an entire broad
  paragraph.
- Do not infer a conclusion from a search snippet or unverified abstract.
- Leave `CITATION_NEEDED` in the logic/literature workflow only; none may remain
  in the compiled main text.

## Figures, tables, and captions

- A caption states dataset/evidence class, causal information condition,
  current/frozen/post-freeze status, units, and the narrow takeaway.
- Do not use a clever headline that asserts causality beyond the plot.
- Preserve negative methods and denominators; do not show only the best row.
- Keep colors redundant with markers, line styles, labels, or hatch patterns.
- Use “post-hoc visualization of frozen data” when newly rendering immutable
  bounded v3 data.

## Special rules for corrected evidence

If 77.38% appears:

- keep it out of title, abstract, contribution list, and conclusion;
- call it an observed exploratory mixed/confounded-baseline regression;
- include the frozen interval and `predicted_p` fallback denominator;
- say the confirmatory classification was withdrawn;
- say v3 bytes were unchanged and v3 was not rerun;
- do not use “improvement” without “observed” and the comparison identity.

Post-freeze compatibility/profile-aware results must be labelled
**post-freeze regression/infrastructure**, never **v3 confirmation**.

