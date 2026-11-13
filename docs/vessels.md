# Vessels

This interface supports Linux anchoring, starting work and stopping source work before sailing between harbors at the same physical anchor path.

## Terms used here

- **Vessel**: the portable Firstmate working environment at `<anchor>/.firstmate/<name>`, including its operational state and working repositories.
- **Anchor**: the physical filesystem directory where the vessel resides.
- **Harbor**: the host where the vessel resides, not a TCP port.
- **Ship task**: an implementation task assigned to a worker, not the portable environment.

## Named vessels

The default vessel is `flagship`; `--vessel NAME` selects another independent vessel for any operation.
Names use 1-64 lowercase letters, digits, underscores or hyphens, starting with a letter or digit.
The anchor contains one `.firstmate/` container, with each payload at `NAME/` and its `NAME.json` record and `NAME.lock` coordination file alongside it.
An interrupted initialization reserves its identity in `NAME.init.json`; `open` or `init` resumes it before allowing work, without adopting unrelated unregistered files.
Each vessel has its own identity, repositories, conversations, tool state, tmux sockets and one active harbor.
Other named vessels may remain active at different harbors; sailing, stopping and scuttling affect only the selected vessel.
There is no shared manifest or anchor-wide operation lock.

```sh
bash bin/fm-anchor.sh init --anchor /absolute/anchor --vessel red --agent pi
bash bin/fm-anchor.sh list --anchor /absolute/anchor
```

`list` prints sorted, locally registered names, including inactive caches, without SSH, initialization or writes.
New names can be selected without first appearing in that list.
Secondmates remain delegated supervisors, not independent vessels; this interface does not relocate their homes or rewrite remote routes.
Only files within the selected payload are transported.

## Local setup

`fm-anchor.sh` derives and exports the vessel layout, tmux worker environment and portable tool-state locations for `init`, `check` and `run`.
The personal dotfiles launcher supplies the confirmation prompt, Nono policy, Pi flags and personal preference defaults.
Pi runs directly in the current terminal pane; tmux is for workers only.
Existing ordinary Firstmate home and backend selection remain unchanged.

```sh
# Core supplies the environment before executing COMMAND:
bash bin/fm-anchor.sh init --anchor /absolute/anchor --agent pi
bash bin/fm-anchor.sh check --anchor /absolute/anchor
bash bin/fm-anchor.sh run --anchor /absolute/anchor -- COMMAND [ARG...]
```

Initialization requires `--agent NAME` and a fresh vessel; it never adopts nonempty unregistered state.
It creates the selected directories, records the backend, writes the selected No-mistakes agent to `$NM_HOME/config.yaml` and writes `NAME.json` beside the vessel.
The name must be a No-mistakes-supported agent identifier; syntactically it uses lowercase letters, digits and hyphens, starting with a letter.
Reinitialization and later launches preserve existing preferences; no agent is guessed or rewritten during startup.
That small record contains the physical anchor, local harbor, anchored flag, selected directory options and routing hints; it contains no credentials or arbitrary runtime arguments.
`bin/fm-vessel.py` owns the versioned record and transfer sequence.
Subsequent launches require the same recorded configuration and an anchored vessel at this harbor.
A raised flag, foreign harbor, backend mismatch or path change refuses before the supplied command starts; boarding never repairs metadata silently.

The caller's normal layout keeps Firstmate state/config/projects, Treehouse worktrees, Pi conversations, No-mistakes state, Lavish state and tmux sockets below the vessel.
Host tooling, preferences and authentication remain host inputs.
These checks cover selected directory settings, not every possible reference inside application configuration or Git storage.
There is no automatic state collection, path rewriting or backend conversion.
Every harbor must use the same physical anchor path and selected directory options.

## Working repositories

`init` and `run` discover independent repositories under the anchor, skipping `.firstmate`, directory symlinks, linked Git worktrees and repository contents.
For each missing vessel project they clone local `main`, falling back to local `master`, into the selected projects directory, preserving its anchor-relative path; a repository at the anchor itself uses the anchor's basename.
The source checkout becomes remote `local`, its original origin URL becomes remote `origin`, and the selected branch tracks `origin` with a single-branch fetch specification.
Seeding makes no request to origin; the first origin fetch populates its remote-tracking ref.
Normal local cloning reuses immutable Git objects through hardlinks where possible, without borrowing an outside object directory.
Borrowed source objects are dissociated so the vessel remains usable after outside repositories disappear.
Existing vessel clones are left untouched, so host repositories cannot overwrite travelling work.
Repositories without an origin URL or either local branch are skipped with a diagnostic during initialization and launch.
Other Git errors and clone failures still stop initialization or launch; fix the source checkout and retry.
Committed history from the selected branch is seeded, including unpublished commits, but other branches and uncommitted changes are not.
The `local` remote is optional convenience; ordinary vessel work uses origin, since the outside checkout may not exist on another harbor.
No manifests or host provisioning tools are required.

Vessel workers require tmux and must use projects from the vessel's selected projects directory.
New dispatch is refused while the vessel is reserved for sailing.
Their working copies and common Git directories must remain inside the vessel, so Treehouse's Git pointers remain usable after sailing at the same physical path.

## Environment layers

Core supplies portable directory defaults and defaults No-mistakes to descendant daemon mode with `NM_TEST_START_DAEMON=1` (No-mistakes 1.111+).
Explicit supported directory overrides must still resolve inside the vessel and match its recorded configuration; conflicting inherited anchor/home settings refuse.
The tmux socket directory is vessel-local and ambient multiplexer routing is cleared.
Host XDG paths, including an unset `XDG_CACHE_HOME`, remain unchanged and are not vessel directory options.
Existing records with the former vessel-cache option remain readable, but that option is no longer exported or required for launch.
`run` exports the resolved environment before executing its command, so Nono and Pi inherit it without shell sourcing or `eval`.

`FM_WORKER_ENV_FORWARD` is one cumulative, whitespace-separated list of variable names.
Personal environment configuration supplies names; core appends its required portable runtime names while preserving those additions.
An empty or unset incoming list does not disable core's forwarding.
Nested execution retains the same names; repeated names are harmless.
The existing worker launcher copies the selected current values (or clears selected unset values), then removes the forwarding control list from the worker itself.

## Sailing

Install matching core, Python 3.9+, SSH and rsync on both harbors.
Harbors need distinct hostnames, and their lowercase forms must work as noninteractive SSH destinations using ordinary host configuration.
Hostname components are normalized to lowercase and known harbors are deduplicated, including existing mixed-case records; SSH user name case is preserved.
Read-only checking does not rewrite records; `sail` persists canonical routing names.
Core is resolved remotely from `${XDG_DATA_HOME:-$HOME/.local/share}/firstmate`, independently of the development copy.
Sailing reserves the source, stops known work and refuses to copy while identified source processes or stop failures remain.
Closing Pi alone does not prove that background work has stopped.

```sh
# At an empty destination, supply only the source harbor:
bash bin/fm-anchor.sh sail --anchor /absolute/anchor --from desktop
# Returning to a previously visited harbor discovers the current location:
bash bin/fm-anchor.sh sail --anchor /absolute/anchor
# From a separate terminal on the source, send the vessel before going offline:
bash bin/fm-anchor.sh sail --anchor /absolute/anchor --to desktop
```

Without `--to`, core tries the last-known harbor first, then known harbors, following newer hints from reachable copies of the same vessel.
Those hints are not proof of presence: the peer must report its own anchored vessel and a usable payload.
Conflicting discovered holders, mismatched identities and failed lookups stop rather than choosing arbitrarily or initializing a replacement.
A local, already anchored vessel needs no SSH discovery; launching can still clone newly discovered projects.

The anchored harbor is authoritative; a registered unanchored local copy is disposable cache, not independent working state.
Core incrementally mirrors the source into the same `.firstmate/NAME/` directory with rsync `--archive --delete --delete-excluded --partial`, updates the origin and enables the destination only after sync and acknowledgement succeed.
Files removed at the source are deleted from the receiving cache, unchanged files are reused, and interrupted transfers retry against that same cache.
There is no staging directory or automatic archive of the previous cache.
Host tools, authentication, tmux sockets and the old primary-session lock are not transported.
Durable state, repositories, linked Git worktrees, uncommitted changes and primary conversations are copied without rewriting paths or preferences.
Existing files under a vessel's `cache/` directory still travel as ordinary payload; no host cache directory is redirected or collected.
Other persisted runtime records are left to ordinary Firstmate recovery; transfer does not resume worker processes.
The origin retains its payload only as an unanchored cache; discovery never treats such a cache as an anchored source.
A matching in-progress handoff can resume at its reserved destination, including recovery after a lost origin acknowledgement.
The sibling record and lock and other anchor contents are outside rsync's deletion scope.
After interruption, retry `sail` at the destination; copy or origin-acknowledgement failure keeps that destination inactive and never falls through to initialization.
If every reachable hint is stale, supply a known source explicitly with `--from` rather than guessing or creating a replacement.

Reopening an unfinished sail prompts to resume sailing, cancel the transfer, or quit without changes.
Resuming at the source sends the vessel to its reserved destination; resuming at the destination pulls it from the source.
Cancelling at the source reopens it there; cancelling at the destination forwards the request to the source and exits without launching an incomplete local vessel.
Explicit cancellation is also available as `sail --cancel` (or `firstmate ANCHOR sail --cancel` through the personal launcher).
Cancellation locks and revokes the destination's inactive arrival before reopening the source; it preserves both payloads and invalidates delayed activation requests.
Each handoff is identified separately, including push retries, so an old acknowledgement cannot activate a later transfer.
An active, unreachable, busy or unrelated destination prevents cancellation rather than risking two active copies.
A lost cancellation acknowledgement leaves the source inactive, and the same cancellation can be retried.

```sh
bash bin/fm-anchor.sh sail --anchor /absolute/anchor --cancel
```

### Sending before the source goes offline

`--to HARBOR` initiates the transfer at the authoritative source, using only outbound SSH and rsync to the destination.
Both harbors must be online during transfer, with matching core installed and the same physical anchor directory already present.
Run it from a separate terminal, outside the source Pi session; stopping that session must not kill its own transfer command.
The receiver reserves an inactive cache, source work stops, and the payload is mirrored using the same exclusions as pulling.
Only after verification and source acknowledgement does the destination become anchored.
Wait for success before taking the source offline; the destination can then launch locally without contacting it.

Push failures retain the source payload and any receiving cache for retry with the same `--to` target.
If activation succeeded but its acknowledgement was lost, retry recognizes the active destination without copying over its newer work.
An arbitrary inactive source cache cannot be pushed to a new harbor, and an unrelated or already anchored destination is never replaced.
There is no offline queue, reverse SSH requirement, automatic remote installation or replacement-vessel initialization.

## Locks and stopping work

Each vessel's coordination lock serializes its core operations; pulling holds the destination lock and pushing holds the source lock through transfer.
Source preparation reserves the vessel before stopping work, blocking new ordinary launches and worker dispatch.
It uses the existing worker exit command, home-scoped watcher stop, validation-daemon stop without force and the vessel's private tmux server.
An identified local primary Pi receives SIGTERM through a stable kernel process handle; its owned children must finish before copying.
Working copies, uncommitted changes, leases and durable task records are preserved rather than torn down.

Unfamiliar processes or a refused stop are sent to the existing source primary inbox for cleanup when that primary is available.
Core launches no replacement agent or one-shot model call; the existing agent can use its normal model turn to resolve the request.
Repeated requests to the same primary generation are deduplicated.
Sailing retries source preparation for up to a minute while the notified agent works, then refuses if stopping is still unconfirmed.
No copy begins on a stop refusal, and the reservation remains available for retry at that destination.
Without a source primary, remaining work must be stopped through its own interface before retrying.

```sh
# Read-only local record and scoped process inventory, including a pending source:
bash bin/fm-anchor.sh status --anchor /absolute/anchor
# Request local stopping without transferring or deleting working state:
bash bin/fm-anchor.sh stop --anchor /absolute/anchor
```

The inventory uses accessible same-user process cwd/environment references to vessel paths and the recorded primary.
This is not certification of every possible writer: a service that clears those references may not be attributable to the vessel.
Unknown processes are reported, not killed by name, and an unverified recorded primary prevents transfer.
Firstmate's existing supervisor lock remains in force.
There is no force-stop fallback, generic process manager or automatic cleanup of work.

## Scuttling a local vessel

Scuttling irreversibly discards the local vessel, including repositories, uncommitted work, tool state and conversation history.
Run it from a separate terminal, not from the primary being stopped.
The personal launcher requires typing `SCUTTLE`; direct core callers must explicitly confirm:

```sh
bash bin/fm-anchor.sh scuttle --anchor /absolute/anchor --confirm
```

Core stops local work first, waiting and retrying when the existing primary has been asked to finish cleanup.
It refuses deletion if cleanup still cannot be confirmed; an accepted request is not treated as proof that work stopped.
It removes only `<anchor>/.firstmate/NAME`, `NAME.json`, any unfinished `NAME.init.json` reservation and `NAME.lock`, not the `.firstmate/` container, other named vessels, outside repositories or other harbors.
Read-only vessel directories gain owner permissions before deletion, and directory symlinks are not followed.
Read-only files themselves need no chmod for unlinking; leaving their modes alone protects hardlinked Git objects shared with outside clones.
The coordination lock is removed last, and competing operations must reject an old unlinked lock inode.
There is no trash, backup or force-stop fallback.

## Verification

Run `bash bin/fm-test-run.sh tests/fm-anchor.test.sh` for the executable anchoring/gate contract.
The core cases also exercise interrupted initialization, unchanged host XDG paths, branch fallback, cancellation and delayed activation, independent names, read-only listing, sibling locks and live work, name/path validation, portable environment defaults, cumulative forwarding, empty incoming lists, nested execution and simulated multi-harbor sailing.
The harbor fixtures execute real core peer commands and real local rsync copies without accessing live vessels or SSH servers.
Interactive Pi/Nono startup and tmux worker launch must be tested in a terminal with PTY access; these checks are not a claim that an interactive session ran.
