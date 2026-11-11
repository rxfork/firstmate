# Local vessels

This interface supports local Linux anchoring and starting work, not moving between harbors.

## Terms used here

- **Vessel**: the portable Firstmate working environment at `<anchor>/.firstmate`, including its operational state and working repositories.
- **Anchor**: the physical filesystem directory where the vessel resides.
- **Harbor**: the host where the vessel resides, not a TCP port.
- **Ship task**: an implementation task assigned to a worker, not the portable environment.

## Local setup

A caller supplies `FM_HOME=<anchor>/.firstmate`, explicitly selects `FM_BACKEND=tmux` and supplies the directory settings listed in `bin/fm-anchor.sh`.
The personal dotfiles launcher starts Pi under Nono directly in the current terminal pane and selects tmux for workers only.
It supplies its own environment defaults; shared core supplies no personal harness flags or worker forwarding list.
Existing ordinary Firstmate home and backend selection remain unchanged.

```sh
# With the caller's directory environment already selected:
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

## Locks and stopping work

The anchor lock only serializes initialization and startup checks.
Firstmate's existing session lock still governs the supervisor; this command adds no process-tree manager or live-writer certification.
Closing the visible interface does not prove background work has stopped.
Stopping work is manual; transfer and automated raising of the anchor are not implemented here.

## Verification

Run `bash bin/fm-test-run.sh tests/fm-anchor.test.sh` for the executable anchoring/gate contract.
The caller's `just check-firstmate` tests its runtime policy, direct primary Pi launch and literal argument preservation without opening worker panes.
Set `FIRSTMATE_TEST_CORE` to this checkout's `bin/fm-anchor.sh` while running those caller tests to exercise the real caller/core boundary.
Interactive Pi/Nono startup and tmux worker launch must be tested in a terminal with PTY access; these checks are not a claim that an interactive session ran.
