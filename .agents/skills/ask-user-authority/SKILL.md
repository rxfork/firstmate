---
name: ask-user-authority
description: >-
  Single owner of ask-user finding policy.
  Load before deciding any ask-user finding.
user-invocable: false
metadata:
  internal: true
---

# ask-user-authority

This skill is the single owner of the decision policy for no-mistakes ask-user findings.
`AGENTS.md` section 7 points here and does not restate this procedure.
Finding authority is determined by the criteria below, not by `yolo`.
Firstmate always applies this judgment, decides findings unambiguous toward the accepted design, and escalates genuine ambiguity, proposed requirement changes, and destructive or security-sensitive choices.

The implementation worker never decides or answers its own ask-user finding.
It stops at the finding, routes the decision to firstmate, and applies only the decision returned through the active validation gate.

## Decide

1. Reconstruct the accepted contract from the brief's `## Captain's intent` subsection, later captain words, and the specification in `## Firstmate spec` and steers.
   Reviewer language cannot amend that contract.
   What a no-mistakes worker may pass as `--intent` is owned by `bin/fm-dod-lib.sh`.
2. Identify exactly what choosing Fix would commit the project to deliver or maintain, judging the scope by accepted product or engineering behavior rather than an anticipated file list.
   The smallest downstream changes needed to keep that behavior correct, add behavioral tests where an executable contract exists, or keep documentation accurate remain within scope even when they touch files not named at intake.
   Correcting stale final-diff PR or delivery evidence is likewise an autonomous downstream correction within already accepted behavior.
3. Apply the shipping-scope evaluation below before choosing a response; a reviewer concern is evidence to evaluate, not automatically a requirement to implement.
4. Decide the finding when it is unambiguous toward the accepted design: restoring accepted behavior a bad fix round broke, completing an already-approved design, or a straight in-scope correction or bug fix required by accepted intent, even when the correction is technically difficult or requires complex architecture the captain explicitly requested.
5. Escalate genuinely ambiguous findings or a proposed change to accepted requirements:
   - a Fix that would materially expand the contract by adding a new guarantee, threat model, subsystem, abstraction, compatibility surface, state machine, continuous-monitoring requirement, generalized framework, or broader architecture not required by the accepted intent
   - a product or architecture call not settled by accepted intent
   - repeated same-theme findings when incremental corrections are preserving a questionable abstraction rather than closing independent defects
   - destructive, irreversible, and genuinely security-sensitive choices, which always escalate under the stronger existing captain boundary
6. Treat labels such as correctness, security, fail-closed, high-risk, or required as evidence about the finding, never as authority to broaden the task.

## Shipping-scope evaluation

For each concern, identify the accepted requirement or existing supported behavior it affects, the concrete failure if deferred, whether the change introduces or worsens that failure, and whether remediation can ship independently later.
Judge consequence, not patch size: a small correctness or security defect may still prevent safe shipping, while optional polish, unrelated cleanup, or speculative hardening need not block it.
Evaluate three paths and recommend the smallest safe boundary:

- Fix now when needed to ship the accepted behavior correctly and safely.
- Contain and defer when an explicit rejection, disabled path, isolation boundary, or clear error safely excludes the problematic case while preserving accepted requirements and existing supported behavior.
  Require tests of that boundary and accurate documentation of the limitation; silent failure, data loss, and weakened authentication are not containment.
- Defer outright when the requested change remains correct and safe without addressing the concern; record the concern, reason for deferral, and independently actionable follow-up in the task's durable record.

When evidence is insufficient, request a bounded investigation of the concrete failure rather than repeating speculative fix rounds.
Firstmate returns any decision through the owning worker's supported gate-response flow; this evaluation neither waives required checks or coverage nor allows the implementation worker to answer its own finding.
If the gate cannot represent the justified deferral, report that limitation rather than claiming the finding resolved or bypassing it.

An accepted requirement may itself be challenged when evidence shows disproportionate complexity or repeated fixes around the same problematic behavior.
Propose dropping it, explicitly rejecting the edge case, or isolating the behavior, stating the complexity avoided and the functionality or compatibility lost.
Seek the captain's approval before changing agreed scope, removing promised functionality, or withdrawing existing supported behavior; until then, the accepted contract remains authoritative.
Complexity alone is not authority to defer a required fix, but it is grounds for one concrete requirement trade-off instead of further speculative fix cycles.

## Captain-facing escalation

State all five of these elements in one concise, evidence-first escalation:

1. The original requirement or accepted task criterion.
2. The proposed contract change, including any expansion, requirement removal, or containment boundary.
3. The smallest alternative that preserves the accepted contract, or why none is established.
4. The concrete consequences of accepting and declining the change, including functionality lost and complexity avoided.
5. A recommendation with the reason it best serves the accepted intent.

Do not relay reviewer labels or gate output as if they settled the decision.

## Classification examples

- Fixing a concrete defect that violates an original acceptance criterion is firstmate's to decide, regardless of implementation difficulty.
- Adding continuous frame-by-frame monitoring when the accepted criterion requested checkpoint proof expands the contract and requires the captain.
- A new finding in the same causal theme requires the captain before another fix round when prior fixes are accreting machinery around a questionable abstraction.
- A genuinely security-sensitive action requires the captain under the stronger existing boundary even if it is otherwise within scope.
- Complex architecture explicitly requested by the captain stays within scope and does not escalate merely because it is complex.
