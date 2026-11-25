---
name: stuck-crewmate-recovery
description: >-
  Load when the session-start digest reports an ordinary direct report's endpoint dead, unverified, or not recorded, on an actionable blocker or cleanup refusal, during supervision reviews of unresolved blockers or failed cleanup, after a stale wake, looping pane, repeated confusion, an answered-by-brief question, an unresponsive crewmate, or a failed steer, after observing a defeated Claude or Codex bypass, and when a live crewmate reports its no-mistakes pipeline dead, unreachable, or timed out.
user-invocable: false
metadata:
  internal: true
---

# stuck-crewmate-recovery

Use this playbook when the session-start digest reports an ordinary direct report's endpoint dead, unverified, or not recorded, or when a direct report is stale, looping, repeatedly confused, asking a question its brief already answers, unresponsive, or when a steer failed to land.
A stale or dead-endpoint report for a worker whose pull request has already landed is not a recovery case: the work is finished, so close the task through ordinary teardown (`AGENTS.md` section 7 for firstmate, the landed-work rule in `bin/fm-branch-prompt.sh` for the supervision branch) instead of this playbook, never with `--force`.

A lane parked on review (`bin/fm-crew-state.sh` reads `source: review-park`) stopped its agent on purpose and is not a recovery case; relaunch it only through `bin/fm-park.sh resume`.

Follow the crew-hosted Lavish board contract in [`docs/configuration.md`](../../../docs/configuration.md#crew-hosted-lavish-review-boards) when recovering a worker that hosts a board.

Interrupt, stop, and relaunch a worker through `bin/fm-control.sh <task-id> interrupt|exit|relaunch`, which resolves the recorded runtime itself, verifies each action, and never tears down or discards anything ([`docs/agent-control.md`](../../../docs/agent-control.md)).
That plane covers workers running in this home; a remotely placed secondmate is refused by name and reconciled through `secondmate-provisioning` instead.
Load `harness-adapters` before a resume command or a harness-specific skill invocation, and whenever the adapter's own quirks matter.
The target window's harness is recorded as `harness=` in `state/<id>.meta`.

## Blocker ownership

For each actionable blocker or refusal, identify what stopped, inspect current evidence, and use the relevant existing recovery or completion workflow.
Firstmate owns the next action, including when a worker must perform a bounded recovery step.
Resolve authorized recovery without waiting for the captain to rediscover the stopped task.
Before leaving it unresolved, establish one of:

- Recovery in progress, with a concrete next action and its owner recorded on the existing work item.
- A genuine external wait, recording what is awaited, who can provide it, and the observable event or existing scheduled review that resumes work.
- An authority decision, promptly presented under `AGENTS.md` section 9 with the evidence, concrete question, options, and recommendation; record and resume through `captain-hold-lifecycle`.

Revisit unresolved blockers and failed cleanup during existing supervision reviews until resolved, explicitly deferred, or superseded with evidence.
Repeated unchanged evidence calls for reassessing the recovery path, not another identical retry or progress report.
Preserve unanswered decisions through the existing decision and reminder mechanisms rather than repeating unchanged notifications.
A durable blocked record alone is not a next action.
Scope the blocker to the affected task and its dependents, and continue unrelated supervision and authorized work within existing locks and capacity limits.
If a shared prerequisite actually blocks other work, name that prerequisite and which work it blocks.

Recovery preserves all existing safety and authority boundaries: keep unlanded work, ownership evidence, validation ownership, authentication, required checks, and merge authority intact.
A refusal does not authorize force, discard, rewriting ownership records to pass a guard, duplicate workers or validation runs, arbitrary restarts, unlimited retries, or expanded project scope.
When no safe authorized step remains, ask the concrete authority question immediately; an intentional idle worker or empty queue requires no invented work.

## Managed approval failover

After observing a Codex command-approval prompt under bypass or a Claude policy refusal of a bypass session, ask the captain for explicit approval to change the owning home's worker permission posture and relaunch the worker.
Only after that explicit word, load `harness-adapters` and use `FM_HOME=<owning-home> bin/fm-approval-failover.sh apply <id>`; its header owns persistent configuration and guarded recovery, with no automatic detection.
Report the saved model-reviewed remedy and actual recovery result, preserving explicit settings and work rather than changing vendor policy or answering approval or hook-trust prompts.
A `backend=t3code` worker already runs at T3's `full-access` runtime mode, so the command refuses it before changing anything; approve its pending request in T3 Code itself ([T3 pending-request procedure](../../../docs/t3code-backend.md#push-events-and-polling-fallback)) or move the work to another backend.

## Session-start reconciliation for a dead ordinary direct report

This procedure covers ordinary `kind=ship` and `kind=scout` direct reports.
Load `secondmate-provisioning` instead for `kind=secondmate` recovery.

For a REMOTE secondmate, `fm-crew-state` and `fm-peek` read the actual remote endpoint over `fm-on.sh`, and `fm-send` reports a delivered-with-pending-confirmation steer as delivered (their headers own the contracts); an `unknown-remote` read or unreachable-host failure means the remote state could not be read, never that the mate is dead or the send failed.
Recover a genuinely stuck remote mate only through `bin/fm-spawn.sh <id> --secondmate`, never raw herdr pane close/kill surgery, which strands the endpoint binding.

Treat the digest's endpoint result as a presence signal, not proof that the task's work or validation run is gone.
Read the targeted current state with `bin/fm-crew-state.sh <id>` before deciding to relaunch.
A no-mistakes run matched to the crew's branch and current code remains authoritative when the endpoint is dead: handle a terminal or parked run through the normal lifecycle, and keep supervising an active run instead of creating a duplicate worker.

When no authoritative run accounts for the task, inspect only its recorded backend and worktree inventory.
Use `treehouse status` for treehouse-backed tmux, herdr, zellij, cmux, Paseo, Rex, t3code, tern, or hc tasks, and use the recorded `orca_worktree_id=` and `terminal=` for Orca tasks.
Do not sweep another home's endpoints or infer ownership from a matching window label.

Before relaunch, prove that no live agent still owns the recorded task and that the existing worktree remains available.
Preserve its uncommitted changes and commits, keep the same task identity, and resume or relaunch the recorded harness in that existing worktree with the same brief plus a concise progress note.
A HERDR endpoint that is not merely idle but destroyed - a pane or workspace removed in Herdr churn - is recovered by that same relaunch, which creates one fresh endpoint in the existing worktree and rebinds the task's record to it; nothing special is needed, and the worktree is untouched ([`docs/agent-control.md`](../../../docs/agent-control.md) "Reclaiming a task whose endpoint is gone").
That relaunch proves the endpoint is destroyed before it rebinds, so a Herdr server that was merely stopped is adopted back rather than duplicated.
For a local vessel tmux endpoint reported unverified, use the normal relaunch command after checking current task and validation ownership as above; it checks the original terminal session before creating a replacement.
If it finds a surviving process or lacks the original identity, preserve the work, use the reported evidence to look for the original endpoint, and apply Blocker ownership before escalating.
See the [endpoint reclaim contract](../../../docs/agent-control.md#reclaiming-a-task-whose-endpoint-is-gone).
On T3 Code there is no replacement relaunch: the thread keeps its conversation, so a turn or provider change continues the same agent; preserve the task record and worktree and follow [`docs/t3code-backend.md`](../../../docs/t3code-backend.md#restart-and-liveness-behavior) instead of trying to respawn it.
Do not work around these refusals by respawning; preserve the recorded task and follow the backend's recovery contract.
That reclaim is the owning home's operation only, and a secondmate is the one exception: recover it through `bin/fm-spawn.sh <id> --secondmate` as above.
Do not use a fresh generic spawn while the recorded worktree is unaccounted for, because allocating another worktree can split one task across two copies.
If the worktree or ownership cannot be reconciled safely, preserve all state and the conflicting evidence, then apply Blocker ownership above rather than leaving a failed or blocked record as the final action.

## A live crewmate claiming the pipeline is dead

This is the inverse of the dead-endpoint case above: the worker is alive and the pipeline it declares dead usually is too.
A drive call blocks until the next gate or outcome, far longer than a harness lets one command run, and the daemon accepts a response immediately and runs the round in the background.
So a crewmate's timed-out, killed, or errored drive call leaves it waiting on a read it never got, and the "the daemon is gone" conclusion it draws from that is a guess, not evidence.

Read the two authoritative sources yourself before believing the claim:

1. `no-mistakes daemon status` for the socket.
2. `no-mistakes axi status --run <id>` for the run, or `bin/fm-crew-state.sh <id>`, which already folds this contradiction in and reports a non-socket daemon-or-timeout `blocked:` line over a running or fixing run with fresh activity as superseded because the run is alive.

A refused connection or missing socket from `daemon status` is positive daemon-down evidence and must be escalated even if the persisted run record still says running or fixing; that record can be stale after the daemon exits.
Otherwise, if the run is still running or fixing with recent activity, the claim is wrong: steer the crewmate to reattach with `no-mistakes axi run` from its own worktree, which is safe and idempotent while the run still matches its `HEAD`, and tell it a timeout is not daemon death.
Nothing reaches the captain in that case.

Never restart, stop, or update the shared daemon on a crewmate's claim.
It is one instance serving every lane and home, so a restart kills other lanes' in-flight runs.
Only positive socket refusal or absence is a daemon-down finding; escalate that finding, or a failed run record that names a daemon error, to the captain.

## Live-endpoint escalation

Escalate in order:

1. Peek the pane, and check the task's steering inbox (`state/<id>.inbox/`) for unhandled `*.msg` records - a stale wake naming an unread firstmate instruction means the worker never acknowledged a durable steer, and the record itself shows exactly what was intended.
   A worker whose current state is a declared `paused:` wait has only said what it is waiting for, so verify the condition it names - its own background job's terminal record or process, for example - and when that condition has already cleared, steer the worker to read the result and resume instead of reporting the wait as healthy.
2. If the crewmate is waiting on a question its brief already answers, follow the [T3 pending-request procedure](../../../docs/t3code-backend.md#push-events-and-polling-fallback) for a T3 runtime request.
   On pane backends, answer in one line via `FM_HOME=<this-firstmate-home> bin/fm-send.sh` from an active firstmate session unless `FM_HOME` is already set to the active firstmate home.
3. If the crewmate is confused or looping, interrupt with `FM_HOME=<this-firstmate-home> bin/fm-control.sh <task-id> interrupt`, then redirect with one corrective line through `fm-send`.
4. If the crewmate is genuinely wedged after redirection and its backend supports a replacement, relaunch it with `FM_HOME=<this-firstmate-home> bin/fm-control.sh <task-id> relaunch --note '<progress so far>'`, which stops the agent, carries the brief plus that note into a replacement in the same local copy, and restores the prior record if the replacement cannot start.
   On T3 Code, do not relaunch; preserve the task and report the stuck thread with its current server status for operator action.
   Pass `--harness`, `--model`, or `--effort` on that same command when the worker should come back on a different runtime.
   Genuine wedging means looping, unresponsive, repeating the same obstacle, or truly dead.
   A low context reading is not wedging; modern harnesses auto-compact and keep going.
   The worktree and commits persist, so relaunch is cheap.
5. If a second relaunch fails too, write `failed` to the backlog and tell the captain the plain failure, preserved work, and consequence using `AGENTS.md` section 9; do not mention metadata, harness, window, or worktree unless the path itself is needed for action.
