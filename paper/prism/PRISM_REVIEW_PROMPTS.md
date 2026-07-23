# Prism review prompts

Each prompt below is scoped to review. Do not invent evidence, experimental
results, citations, or stronger conclusions. Report proposed changes with the
affected section and claim IDs.

## A. Structural review

> Review the manuscript against `logic/01_claim_evidence_matrix.md` and
> `logic/02_argument_outline.md`. Check whether every section advances the
> intended argument, whether transitions expose the correct reader question,
> and whether contributions are supported later. Do not add conclusions
> outside the registered evidence. Flag detours, missing links, and duplicated
> argument before proposing concise edits.

## B. Reasoning review

> Audit causal reasoning, target/output time semantics, information
> availability, and comparison fairness. Distinguish estimator delay,
> prediction horizon, command delay, and evaluation lag. Check that
> ordinary-Ruckig, shielded, direct, mixed, and fallback executions retain
> separate method identities. Identify each unsupported causal step and cite
> the relevant claim ID.

## C. Equation and notation review

> Compare every equation and symbol with
> `logic/03_notation_and_timing.md`. Check indices, represented times,
> availability times, units, vector/scalar meaning, constraint subscripts,
> and adjacent-state dynamics. Verify that point admissibility, one-step
> reachability, sampled-sequence consistency, stopping viability, and
> next-step existence are not conflated. Suggest notation fixes without
> changing the scientific claim.

## D. Citation review

> Check whether every citation actually supports the sentence that cites it.
> Separate academic algorithm or novelty support from official software/API
> behavior. Flag missing primary sources, unverifiable metadata, overbroad
> paraphrases, and uncited technical claims. Do not infer a DOI, author,
> result, or priority claim from memory.

## E. Results-boundary review

> Audit every result against the registered evidence scope. Check that the
> development CSV is not presented as an independent real-data test, its
> derivatives are estimates rather than truth, and the future oracle is not
> described as online. Check that frozen v3 direct safety/runtime evidence is
> separated from the confounded baseline comparison and from post-freeze code
> regression. The confounded 77.38% value must not appear in the title,
> abstract, contributions, or conclusion.

## F. Language review

> Improve clarity, concision, paragraph focus, and terminology while
> preserving the exact strength and scope of every claim. Do not replace
> “observed” or “supports under the tested conditions” with stronger causal or
> universal verbs. Remove marketing, anthropomorphism, rhetorical
> exaggeration, and unsupported uses of “significant,” “clearly,” “first,”
> “optimal,” “safe,” or “real robot.”

## G. Final arXiv review

> Perform a final consistency audit across title, abstract, contributions,
> results, figure captions, discussion, conclusion, and limitations. Verify
> that confirmed claims are stated consistently, negative results remain
> visible, and unresolved PVA, independent-real-data, and hardware questions
> remain explicit. Also flag unresolved citations/references, missing assets,
> placeholder text other than author metadata, local paths, or material that
> would make the source bundle non-self-contained.
