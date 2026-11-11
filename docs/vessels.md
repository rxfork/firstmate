# Local vessels

This interface supports local Linux anchoring and starting work, not moving between harbors.

## Terms used here

- **Vessel**: the portable Firstmate working environment at `<anchor>/.firstmate`, including its operational state and working repositories.
- **Anchor**: the physical filesystem directory where the vessel resides.
- **Harbor**: the host where the vessel resides, not a TCP port.
- **Ship task**: an implementation task assigned to a worker, not the portable environment.

## Local setup

`fm-anchor.sh` derives and exports the vessel layout, tmux worker environment and portable tool-state locations for `init`, `check` and `run`.
The personal dotfiles launcher supplies the confirmation prompt, Nono policy, Pi flags and personal preference defaults.
Pi runs directly in the current terminal pane; tmux is for workers only.
Existing ordinary Firstmate home and backend selection remain unchanged.

```sh
# Core supplies the environment before executing COMMAND:
bash bin/fm-anchor.sh init --anchor /absolute/anchor
bash bin/fm-anchor.sh check --anchor /absolute/anchor
bash bin/fm-anchor.sh run --anchor /absolute/anchor -- COMMAND [ARG...]
```

Initialization requires a fresh vessel and never adopts nonempty unregistered state.
It creates the selected directories, records the explicitly selected backend and writes `.firstmate.json` beside the vessel.
That small record contains the physical anchor, local harbor, anchored flag and selected directory options; it contains no credentials or arbitrary runtime arguments.
Subsequent launches require the same recorded configuration and an anchored vessel at this harbor.
A raised flag, foreign harbor, backend mismatch or path change refuses before the supplied command starts; boarding never repairs metadata silently.

The caller's normal layout keeps Firstmate state/config/projects, Treehouse worktrees, Pi conversations, No-mistakes state, Lavish state and tmux sockets below the vessel.
Host tooling, preferences and authentication remain host inputs.
These checks cover selected directory settings, not every possible reference inside application configuration or Git storage.
There is no automatic state collection, path rewriting or backend conversion.

## Environment layers

Core supplies portable directory defaults and defaults No-mistakes to descendant daemon mode with `NM_TEST_START_DAEMON=1` (No-mistakes 1.111+).
Explicit supported directory overrides must still resolve inside the vessel and match its recorded configuration; conflicting inherited anchor/home settings refuse.
The tmux socket directory and cache are always vessel-local, and ambient multiplexer routing is cleared.
`run` exports the resolved environment before executing its command, so Nono and Pi inherit it without shell sourcing or `eval`.

Core's `FM_WORKER_ENV_FORWARD` default selects portable runtime variables.
Callers can append their default forwarding names with `FM_WORKER_ENV_FORWARD_DEFAULTS`, without restating core's list or assigning values to those variables.
An explicitly supplied `FM_WORKER_ENV_FORWARD`, including an empty string, replaces the combined defaults.
Nested execution preserves the resolved list rather than appending defaults again.
The existing worker launcher copies the selected current values (or clears selected unset values), then removes the forwarding control list from the worker itself.

## Locks and stopping work

The anchor lock only serializes initialization and startup checks.
Firstmate's existing session lock still governs the supervisor; this command adds no process-tree manager or live-writer certification.
Closing the visible interface does not prove background work has stopped.
Stopping work is manual; transfer and automated raising of the anchor are not implemented here.

## Verification

Run `bash bin/fm-test-run.sh tests/fm-anchor.test.sh` for the executable anchoring/gate contract.
The core cases also exercise portable environment defaults, caller forwarding defaults, explicit overrides including empty, and nested execution.
Interactive Pi/Nono startup and tmux worker launch must be tested in a terminal with PTY access; these checks are not a claim that an interactive session ran.
