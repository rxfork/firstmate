#!/usr/bin/env bash
# Exercise anchoring, simulated harbor transfers and the CLI launch contract.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
if [ "$(uname -s)" != Linux ]; then printf 'skip: local anchoring requires Linux\n'; exit 0; fi
TMP_ROOT=$(fm_test_tmproot fm-anchor)
ANCHOR_CLI="$ROOT/bin/fm-anchor.sh"

configure() {
  export FM_ANCHOR="$1" FM_HOME="$1/.firstmate" FM_BACKEND=tmux
  export FM_CONFIG_OVERRIDE="$FM_HOME/config" FM_DATA_OVERRIDE="$FM_HOME/data"
  export FM_STATE_OVERRIDE="$FM_HOME/state" FM_PROJECTS_OVERRIDE="$FM_HOME/projects"
  export TREEHOUSE_ROOT="$FM_HOME/worktrees" NM_HOME="$FM_HOME/no-mistakes"
  export LAVISH_AXI_STATE_DIR="$FM_HOME/lavish-axi" PI_CODING_AGENT_SESSION_DIR="$FM_HOME/pi/sessions"
  export TMUX_TMPDIR="$FM_HOME/tmux" XDG_CACHE_HOME="$FM_HOME/cache"
}

unanchored_launch_is_refused() (
  local anchor="$TMP_ROOT/not anchored"
  mkdir -p "$anchor"
  configure "$anchor"
  if bash "$ANCHOR_CLI" run --anchor "$anchor" -- touch "$TMP_ROOT/launched" >/dev/null 2>&1; then fail 'unanchored runtime started'; fi
  [ ! -e "$TMP_ROOT/launched" ] && [ ! -e "$FM_HOME" ] || fail 'refused launch created runtime state'
  pass 'an unanchored vessel cannot start work'
)

local_anchor_records_options_and_preserves_argv() (
  local anchor="$TMP_ROOT/local anchor" result rc=0
  mkdir -p "$anchor"
  configure "$anchor"
  result=$(bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi)
  jq -e --arg anchor "$anchor" --arg harbor "$(uname -n | tr '[:upper:]' '[:lower:]')" '.version == 2 and (.vessel_id | length > 0) and .anchored == true and .anchor == $anchor and .harbor == $harbor and .last_known_harbor == $harbor and .known_harbors == [$harbor] and .backend == "tmux"' <<< "$result" >/dev/null || fail 'anchor identity was not recorded'
  [ "$(< "$FM_CONFIG_OVERRIDE/backend")" = tmux ] || fail 'selected backend was not initialized'
  jq -e --arg home "$FM_HOME" '.options.FM_HOME == $home' "$anchor/.firstmate.json" >/dev/null || fail 'boarding paths were not snapshotted'
  bash "$ANCHOR_CLI" run --anchor "$anchor" -- bash -c 'printf "%s\0" "$@" > "$1"; exit 37' bash "$TMP_ROOT/argv" 'a model' '' 'literal ; argument' || rc=$?
  [ "$rc" -eq 37 ] || fail 'runtime status changed'
  printf '%s\0' "$TMP_ROOT/argv" 'a model' '' 'literal ; argument' > "$TMP_ROOT/expected"
  cmp -s "$TMP_ROOT/argv" "$TMP_ROOT/expected" || fail 'runtime arguments changed'
  ln -s "$anchor" "$TMP_ROOT/alias"
  bash "$ANCHOR_CLI" check --anchor "$TMP_ROOT/alias" >/dev/null || fail 'physical anchor alias was rejected'
  pass 'local anchoring records physical configuration and preserves command argv and status'
)

boarding_gate_never_repairs_metadata() (
  local anchor="$TMP_ROOT/gated" mode
  mkdir -p "$anchor"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi > "$TMP_ROOT/registered"
  for mode in raised foreign multiple-records; do
    case "$mode" in
      raised) jq '.anchored=false' "$TMP_ROOT/registered" > "$anchor/.firstmate.json" ;;
      foreign) jq '.harbor="another-harbor"' "$TMP_ROOT/registered" > "$anchor/.firstmate.json" ;;
      multiple-records) { jq '.anchored=false' "$TMP_ROOT/registered"; printf '%s\n' "$(< "$TMP_ROOT/registered")"; } > "$anchor/.firstmate.json" ;;
    esac
    cp "$anchor/.firstmate.json" "$TMP_ROOT/before"
    if bash "$ANCHOR_CLI" run --anchor "$anchor" -- touch "$TMP_ROOT/forbidden" >/dev/null 2>&1; then fail "$mode vessel started work"; fi
    cmp -s "$anchor/.firstmate.json" "$TMP_ROOT/before" || fail 'boarding silently repaired metadata'
  done
  [ ! -e "$TMP_ROOT/forbidden" ] || fail 'gated runtime was invoked'
  pass 'the local startup gate refuses raised or foreign anchors without rewriting state'
)

options_and_existing_work_are_not_converted() (
  local anchor="$TMP_ROOT/options" existing="$TMP_ROOT/existing"
  mkdir -p "$anchor" "$existing/.firstmate"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  cp "$anchor/.firstmate.json" "$TMP_ROOT/options-before"
  if FM_BACKEND=zellij bash "$ANCHOR_CLI" check --anchor "$anchor" >/dev/null 2>&1; then fail 'backend changed'; fi
  if TREEHOUSE_ROOT="$FM_HOME/different" bash "$ANCHOR_CLI" check --anchor "$anchor" >/dev/null 2>&1; then fail 'recorded path changed'; fi
  if NM_HOME="$TMP_ROOT/outside" bash "$ANCHOR_CLI" check --anchor "$anchor" >/dev/null 2>&1; then fail 'selected state escaped the vessel'; fi
  cmp -s "$anchor/.firstmate.json" "$TMP_ROOT/options-before" || fail 'changed options rewrote metadata'
  printf 'keep existing work\n' > "$existing/.firstmate/work"
  configure "$existing"
  if bash "$ANCHOR_CLI" init --anchor "$existing" --agent pi >/dev/null 2>&1; then fail 'existing state was silently adopted'; fi
  [ "$(< "$existing/.firstmate/work")" = 'keep existing work' ] || fail 'existing work changed'
  pass 'backend/path mismatches and unregistered existing work refuse without conversion'
)

anchor_operations_use_one_regular_lock() (
  local anchor="$TMP_ROOT/locked" rc=0
  mkdir -p "$anchor"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  exec 8> "$anchor/.firstmate.lock"
  flock -n 8
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1; then fail 'another operation bypassed the anchor lock'; fi
  if bash "$ANCHOR_CLI" sail --anchor "$anchor" >/dev/null 2>&1; then fail 'sailing bypassed the anchor lock'; fi
  exec 8>&-
  rm "$anchor/.firstmate.lock"
  mkfifo "$anchor/.firstmate.lock"
  timeout 5 bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1 || rc=$?
  [ "$rc" -eq 1 ] || fail 'FIFO lock did not refuse promptly'
  rc=0
  timeout 5 bash "$ANCHOR_CLI" sail --anchor "$anchor" >/dev/null 2>&1 || rc=$?
  [ "$rc" -eq 1 ] || fail 'sailing did not refuse a FIFO lock promptly'
  rm "$anchor/.firstmate.lock"
  printf 'keep\n' > "$TMP_ROOT/unrelated"
  ln -s "$TMP_ROOT/unrelated" "$anchor/.firstmate.lock"
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1; then fail 'symlink was treated as an anchor lock'; fi
  if bash "$ANCHOR_CLI" sail --anchor "$anchor" >/dev/null 2>&1; then fail 'sailing followed a symlink anchor lock'; fi
  [ "$(< "$TMP_ROOT/unrelated")" = keep ] || fail 'anchor lock modified an unrelated file'
  pass 'anchor operations serialize with a regular lock and reject file aliases'
)

core_environment_composes_forwarding_layers() (
  local anchor="$TMP_ROOT/environment" output forwarded nested
  env -i PATH="$PATH" HOME="$HOME" FM_ANCHOR="$TMP_ROOT/not-a-live-vessel" \
    bash -es -- "$ROOT/tests/lib.sh" <<'SH' || fail 'test helpers retained a live vessel identity'
. "$1"
test -z "${FM_ANCHOR+x}"
SH
  mkdir -p "$anchor"
  unset FM_ANCHOR FM_HOME FM_BACKEND FM_CONFIG_OVERRIDE FM_DATA_OVERRIDE FM_STATE_OVERRIDE FM_PROJECTS_OVERRIDE \
    TREEHOUSE_ROOT NM_HOME LAVISH_AXI_STATE_DIR PI_CODING_AGENT_SESSION_DIR NM_TEST_START_DAEMON FM_WORKER_ENV_FORWARD
  export FM_WORKER_ENV_FORWARD='PERSONAL_SETTING' PERSONAL_SETTING='personal value'
  export TMUX='/ambient/socket,1,0' TMUX_PANE='%1'
  if bash "$ANCHOR_CLI" init --anchor "$anchor" >/dev/null 2>&1; then fail 'initialization accepted a missing agent'; fi
  [ ! -e "$anchor/.firstmate" ] || fail 'missing agent created vessel state'
  output=$(bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi)
  [ "$(< "$anchor/.firstmate/no-mistakes/config.yaml")" = 'agent: "pi"' ] || fail 'core did not record the selected agent'
  printf 'agent: codex\n' > "$anchor/.firstmate/no-mistakes/config.yaml"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  [ "$(< "$anchor/.firstmate/no-mistakes/config.yaml")" = 'agent: codex' ] || fail 'initialization rewrote existing preferences'
  jq -e --arg home "$anchor/.firstmate" '.options.FM_HOME == $home and .options.TREEHOUSE_ROOT == ($home + "/worktrees")' <<< "$output" >/dev/null || fail 'core did not supply portable defaults'
  bash "$ANCHOR_CLI" run --anchor "$anchor" -- bash -c '
    [ "$FM_ANCHOR/.firstmate" = "$FM_HOME" ] && [ "$FM_BACKEND" = tmux ] &&
    [ "$NM_HOME" = "$FM_HOME/no-mistakes" ] && [ "$NM_TEST_START_DAEMON" = 1 ] &&
    [ -z "${TMUX+x}" ] && [ -z "${TMUX_PANE+x}" ] &&
    [[ " $FM_WORKER_ENV_FORWARD " = *" TREEHOUSE_ROOT "* ]] &&
    [[ " $FM_WORKER_ENV_FORWARD " = *" PERSONAL_SETTING "* ]] && [ "$PERSONAL_SETTING" = "personal value" ]
  ' || fail 'core and caller forwarding defaults did not compose'
  forwarded=$(bash "$ANCHOR_CLI" run --anchor "$anchor" -- printenv FM_WORKER_ENV_FORWARD | tr '[:space:]' '\n' | sort -u)
  nested=$(bash "$ANCHOR_CLI" run --anchor "$anchor" -- bash "$ANCHOR_CLI" run --anchor "$anchor" -- printenv FM_WORKER_ENV_FORWARD | tr '[:space:]' '\n' | sort -u)
  [ "$nested" = "$forwarded" ] || fail 'nested execution lost forwarding names'
  for output in '' PERSONAL_SETTING; do
    FM_WORKER_ENV_FORWARD="$output" bash "$ANCHOR_CLI" run --anchor "$anchor" -- bash -c '
      [[ " $FM_WORKER_ENV_FORWARD " = *" TREEHOUSE_ROOT "* ]] &&
      { [ -z "$1" ] || [[ " $FM_WORKER_ENV_FORWARD " = *" $1 "* ]]; }
    ' bash "$output" || fail 'core did not append its names while retaining caller additions'
  done
  if FM_ANCHOR="$TMP_ROOT/other" bash "$ANCHOR_CLI" check --anchor "$anchor" >/dev/null 2>&1; then fail 'conflicting inherited anchor was accepted'; fi
  pass 'core exports portable defaults and appends forwarding names without losing caller additions'
)

sailing_stops_registered_work_without_discarding_state() (
  setup_harbors known-work
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  mkdir -p "$FM_FIXTURE_WORLD/source-core/bin" "$TMUX_TMPDIR/tmux-$(id -u)"
  cp "$ROOT/bin/fm-anchor.sh" "$ROOT/bin/fm-vessel.py" "$FM_FIXTURE_WORLD/source-core/bin/"
  for entry in 'fm-control.sh control' 'fm-watch-arm.sh watch'; do
    read -r name operation <<< "$entry"
    printf '#!/bin/sh\nexec python3 "%s/tests/fixtures/fm-vessel-harbors.py" %s "$@"\n' "$ROOT" "$operation" > "$FM_FIXTURE_WORLD/source-core/bin/$name"
    chmod +x "$FM_FIXTURE_WORLD/source-core/bin/$name"
  done
  rm "$FM_FIXTURE_WORLD/hosts/desktop/home/.local/share/firstmate"
  ln -s "$FM_FIXTURE_WORLD/source-core" "$FM_FIXTURE_WORLD/hosts/desktop/home/.local/share/firstmate"
  for entry in 'tmux tmux' 'no-mistakes daemon'; do
    read -r name operation <<< "$entry"
    printf '#!/bin/sh\nexec python3 "%s/tests/fixtures/fm-vessel-harbors.py" %s "$@"\n' "$ROOT" "$operation" > "$FM_FIXTURE_WORLD/fakebin/$name"
    chmod +x "$FM_FIXTURE_WORLD/fakebin/$name"
  done
  sleep 120 &
  local worker=$! server
  printf '%s\n' "$worker" > "$FM_FIXTURE_WORLD/worker.pid"
  python3 -c 'import os, socket, time; s = socket.socket(socket.AF_UNIX); s.bind(os.environ["TMUX_TMPDIR"] + "/tmux-" + str(os.getuid()) + "/default"); time.sleep(120)' &
  server=$!
  printf '%s\n' "$server" > "$FM_FIXTURE_WORLD/tmux.pid"
  trap 'kill "$worker" "$server" 2>/dev/null || true; wait "$worker" "$server" 2>/dev/null || true' EXIT
  for _ in $(seq 1 100); do [ ! -S "$TMUX_TMPDIR/tmux-$(id -u)/default" ] || break; sleep 0.02; done
  [ -S "$TMUX_TMPDIR/tmux-$(id -u)/default" ] || fail 'private socket fixture did not start'
  printf 'kind=ship\nwindow=firstmate:fixture-worker\n' > "$FM_STATE_OVERRIDE/fixture-worker.meta"
  printf 'uncommitted work\n' > "$FM_PROJECTS_OVERRIDE/dirty"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null || fail 'registered work did not stop before transfer'
  wait "$worker" 2>/dev/null || true
  wait "$server" 2>/dev/null || true
  trap - EXIT
  jq -se 'any(.operation == "control" and .args == ["fixture-worker", "exit"]) and any(.operation == "watch" and .args == ["--stop"]) and any(.operation == "daemon" and .args == ["daemon", "stop"]) and any(.operation == "tmux" and .args[2] == "kill-server")' "$FM_FIXTURE_WORLD/stop-calls" >/dev/null || fail 'stop did not use the existing scoped tool interfaces'
  [ "$(< "$FM_PROJECTS_OVERRIDE/dirty")" = 'uncommitted work' ] && [ -f "$FM_STATE_OVERRIDE/fixture-worker.meta" ] || fail 'stopping discarded work or task records'
  pass 'sailing stops registered workers, monitoring, validation and its private tmux server without cleanup of working state'
)

unavailable_sources_report_the_payload_reason() (
  setup_harbors unavailable-source
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'changed\n' > "$FM_CONFIG_OVERRIDE/backend"
  activate_harbor laptop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'an unavailable payload was copied'; fi
  rg -q 'vessel backend configuration changed' "$FM_FIXTURE_WORLD/error" || fail 'payload refusal reason was hidden'
  jq -e '.anchored' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'payload refusal changed source ownership'
  pass 'discovery explains an unavailable payload instead of hiding the peer validation error'
)

source_stop_errors_preserve_the_refusal_reason() (
  setup_harbors stop-refusal
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf '#!/bin/sh\nprintf "active validation must stop first\\n" >&2\nexit 77\n' > "$FM_FIXTURE_WORLD/fakebin/no-mistakes"
  chmod +x "$FM_FIXTURE_WORLD/fakebin/no-mistakes"
  activate_harbor laptop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'refused daemon stop copied data'; fi
  rg -q 'active validation must stop first' "$FM_FIXTURE_WORLD/error" || fail 'daemon refusal stderr was hidden'
  rg -q 'exit 77' "$FM_FIXTURE_WORLD/error" || fail 'daemon refusal exit code was hidden'
  [ ! -e "$FM_ANCHOR/.firstmate.json" ] || fail 'refused daemon stop activated a destination'
  pass 'a refused service stop blocks copying and retains its actual diagnostic and exit code'
)

unknown_source_work_blocks_copy_without_killing_it() (
  setup_harbors unknown-work
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  (cd "$FM_HOME/projects"; exec sleep 120) &
  local writer=$!
  trap 'kill "$writer" 2>/dev/null || true; wait "$writer" 2>/dev/null || true' EXIT
  bash "$ANCHOR_CLI" status --anchor "$FM_ANCHOR" | jq -e --argjson pid "$writer" '.processes | any(.pid == $pid)' >/dev/null || fail 'status missed source work'
  activate_harbor laptop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'live unknown source work was copied'; fi
  kill -0 "$writer" || fail 'sailing killed an unknown process'
  rg -q 'source work has not stopped' "$FM_FIXTURE_WORLD/error" || fail 'remaining source work was hidden'
  rg -q "\"pid\": $writer" "$FM_FIXTURE_WORLD/error" || fail 'remaining source process was not identified'
  [ ! -e "$FM_ANCHOR/.firstmate.json" ] || fail 'failed source stop activated a destination'
  jq -e '.anchored == false and .sailing_to == "laptop"' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'stop did not reserve and fence the source'
  kill "$writer"
  wait "$writer" 2>/dev/null || true
  trap - EXIT
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null || fail 'stopped source could not retry sailing'
  pass 'unknown source processes block copying without being killed and the reserved transfer can retry'
)

vessel_projects_clone_origins_only_when_missing() (
  local anchor="$TMP_ROOT/discovered projects" origin="$TMP_ROOT/project-origin" project object expected
  mkdir -p "$anchor/team" "$anchor/other"
  git init -qb main "$origin"
  printf 'from origin\n' > "$origin/file"
  git -C "$origin" add file
  git -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid -C "$origin" commit -qm initial
  git clone -q "$origin" "$anchor/team/repo"
  git clone -q "$origin" "$anchor/other/repo"
  git clone -q "$origin" "$anchor/team/repo/nested"
  ln -s "$anchor/other" "$anchor/linked"
  git -C "$anchor/team/repo" branch other
  mv "$anchor/team/repo/.git/objects" "$TMP_ROOT/borrowed-objects"
  mkdir -p "$anchor/team/repo/.git/objects/info"
  printf '%s\n' "$TMP_ROOT/borrowed-objects" > "$anchor/team/repo/.git/objects/info/alternates"
  printf 'from local main\n' > "$anchor/team/repo/file"
  git -C "$anchor/team/repo" add file
  git -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid -C "$anchor/team/repo" commit -qm local-main
  git -c core.hooksPath=/dev/null -C "$anchor/team/repo" checkout -q other
  printf 'host-only changes\n' > "$anchor/team/repo/file"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  for project in team/repo other/repo; do
    expected='from origin'
    [ "$project" != team/repo ] || expected='from local main'
    [ "$(< "$FM_PROJECTS_OVERRIDE/$project/file")" = "$expected" ] || fail 'vessel project did not clone local main'
    [ "$(git -C "$FM_PROJECTS_OVERRIDE/$project" remote get-url local)" = "$anchor/$project" ] || fail 'local remote does not point at the discovered source'
    [ "$(git -C "$FM_PROJECTS_OVERRIDE/$project" remote get-url origin)" = "$origin" ] || fail 'original origin URL was not preserved'
    [ "$(git -C "$FM_PROJECTS_OVERRIDE/$project" config branch.main.remote)" = origin ] || fail 'main tracks the local source rather than origin'
    [ "$(git -C "$FM_PROJECTS_OVERRIDE/$project" config remote.origin.fetch)" = '+refs/heads/main:refs/remotes/origin/main' ] || fail 'origin fetch is not limited to main'
    if git -C "$FM_PROJECTS_OVERRIDE/$project" show-ref --verify --quiet refs/remotes/local/other; then fail 'single-branch clone copied another branch'; fi
    [ "$(git -C "$FM_PROJECTS_OVERRIDE/$project" rev-parse --path-format=absolute --git-common-dir)" = "$FM_PROJECTS_OVERRIDE/$project/.git" ] || fail 'vessel clone borrowed external Git storage'
    [ ! -e "$FM_PROJECTS_OVERRIDE/$project/.git/objects/info/alternates" ] || fail 'vessel clone borrowed external objects'
  done
  object=$(git -C "$anchor/other/repo" rev-parse 'main^{tree}')
  [ "$(stat -c %i "$anchor/other/repo/.git/objects/${object:0:2}/${object:2}")" = "$(stat -c %i "$FM_PROJECTS_OVERRIDE/other/repo/.git/objects/${object:0:2}/${object:2}")" ] || fail 'ordinary local clone did not reuse immutable objects'
  [ ! -e "$FM_PROJECTS_OVERRIDE/team/repo/nested" ] && [ ! -e "$FM_PROJECTS_OVERRIDE/linked" ] || fail 'discovery entered a repository or followed a directory symlink'
  printf 'travelling changes\n' > "$FM_PROJECTS_OVERRIDE/team/repo/file"
  git -C "$anchor/team/repo" remote remove origin
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  [ "$(< "$FM_PROJECTS_OVERRIDE/team/repo/file")" = 'travelling changes' ] || fail 'initialization overwrote an existing vessel clone'
  git clone -q "$origin" "$anchor/new-repo"
  bash "$ANCHOR_CLI" run --anchor "$anchor" -- test -d "$FM_PROJECTS_OVERRIDE/new-repo/.git" || fail 'launch did not provision a newly discovered repo'
  git -c core.hooksPath=/dev/null -C "$FM_PROJECTS_OVERRIDE/team/repo" worktree add -qb worker "$TREEHOUSE_ROOT/worker"
  rm -rf "$origin" "$anchor/team" "$anchor/other" "$anchor/new-repo" "$TMP_ROOT/borrowed-objects"
  [ "$(git -C "$TREEHOUSE_ROOT/worker" show HEAD:file)" = 'from local main' ] || fail 'worker still depended on outside Git storage'
  pass 'missing projects clone only local main with hardlinks where possible, separate remotes and no borrowed Git storage'
)

vessel_project_failures_block_launch_and_can_retry() (
  local anchor="$TMP_ROOT/missing origin" origin="$TMP_ROOT/retry-origin"
  mkdir -p "$anchor"
  git init -qb main "$anchor/repo"
  configure "$anchor"
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >"$TMP_ROOT/no-origin" 2>&1; then fail 'a missing origin was ignored'; fi
  rg -q 'origin' "$TMP_ROOT/no-origin" || fail 'missing origin was not explained'
  if bash "$ANCHOR_CLI" run --anchor "$anchor" -- touch "$TMP_ROOT/missing-origin-launched" >/dev/null 2>&1; then fail 'provisioning failure started Pi'; fi
  [ ! -e "$TMP_ROOT/missing-origin-launched" ] || fail 'failed provisioning executed the launch command'
  git -C "$anchor/repo" remote add origin "$origin"
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1; then fail 'failed clone was ignored'; fi
  [ ! -e "$FM_PROJECTS_OVERRIDE/repo" ] || fail 'failed clone was left as an existing destination'
  printf 'available locally\n' > "$anchor/repo/file"
  git -C "$anchor/repo" add file
  git -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid -C "$anchor/repo" commit -qm main
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  [ -d "$FM_PROJECTS_OVERRIDE/repo/.git" ] || fail 'failed provisioning could not be retried'
  [ ! -e "$origin" ] || fail 'fixture unexpectedly made origin available'
  pass 'missing origins or local main block launch, then retry clones locally without contacting the unavailable origin'
)

pushing_anchors_the_destination_without_reverse_ssh() (
  setup_harbors push-offline
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'portable work\n' > "$FM_PROJECTS_OVERRIDE/work"
  git init -q "$FM_PROJECTS_OVERRIDE/repo"
  printf 'tracked\n' > "$FM_PROJECTS_OVERRIDE/repo/file"
  git -C "$FM_PROJECTS_OVERRIDE/repo" add file
  git -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid -C "$FM_PROJECTS_OVERRIDE/repo" commit -qm initial
  git -c core.hooksPath=/dev/null -C "$FM_PROJECTS_OVERRIDE/repo" worktree add -qb push-worker "$TREEHOUSE_ROOT/push-worker"
  printf 'uncommitted\n' > "$TREEHOUSE_ROOT/push-worker/file"
  mkdir -p "$FM_HOME/pi/primary"
  printf 'history\n' > "$FM_HOME/pi/primary/session.jsonl"
  printf 'old runtime lock\n' > "$FM_STATE_OVERRIDE/.lock"
  touch "$FM_FIXTURE_WORLD/desktop.no-inbound"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to LAPTOP >/dev/null
  jq -e '.anchored == false and .last_known_harbor == "laptop" and (has("sailing_to") | not)' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'push left the source anchored or uncommitted'
  jq -se 'all(.from == "desktop" and .to == "laptop")' "$FM_FIXTURE_WORLD/ssh-operations" >/dev/null || fail 'push attempted reverse SSH'
  activate_harbor laptop
  [ "$(< "$FM_PROJECTS_OVERRIDE/work")" = 'portable work' ] && [ "$(< "$FM_HOME/pi/primary/session.jsonl")" = history ] || fail 'push lost working data or history'
  [ ! -e "$FM_STATE_OVERRIDE/.lock" ] || fail 'push copied the source session lock'
  [ "$(git -C "$TREEHOUSE_ROOT/push-worker" status --porcelain)" = ' M file' ] && [ "$(< "$TREEHOUSE_ROOT/push-worker/file")" = uncommitted ] || fail 'push lost linked Git storage or uncommitted work'
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'pushed destination cannot launch with source offline'
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null || fail 'local launch tried to contact the offline source'
  printf 'delete me\n' > "$FM_PROJECTS_OVERRIDE/obsolete"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to third >/dev/null
  activate_harbor third
  rm "$FM_PROJECTS_OVERRIDE/obsolete"
  printf 'updated work\n' > "$FM_PROJECTS_OVERRIDE/work"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null
  activate_harbor laptop
  [ ! -e "$FM_PROJECTS_OVERRIDE/obsolete" ] && [ "$(< "$FM_PROJECTS_OVERRIDE/work")" = 'updated work' ] || fail 'push did not mirror into an inactive cache'
  pass 'outbound-only pushing anchors the destination before the source goes offline and mirrors returning caches'
)

pushing_retries_copy_and_activation_failures() (
  setup_harbors push-retries
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'keep\n' > "$FM_PROJECTS_OVERRIDE/work"
  touch "$FM_FIXTURE_WORLD/laptop.fail-receive-after"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'lost receive acknowledgement was ignored'; fi
  jq -e '.anchored' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'source stopped before receiver acknowledgement'
  rm "$FM_FIXTURE_WORLD/laptop.fail-receive-after"
  touch "$FM_FIXTURE_WORLD/fail-copy"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'failed outbound copy activated a destination'; fi
  jq -e '.anchored == false and .sailing_to == "laptop"' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'failed push did not retain its source reservation'
  rm "$FM_FIXTURE_WORLD/fail-copy"
  touch "$FM_FIXTURE_WORLD/laptop.fail-activate"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'failed receiver activation was ignored'; fi
  jq -e '.anchored == false and .last_known_harbor == "laptop" and (has("sailing_to") | not)' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'source did not commit before activating its destination'
  jq -e '.anchored == false and .sailing_to == "laptop"' "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json" >/dev/null || fail 'failed activation enabled the receiver'
  rm "$FM_FIXTURE_WORLD/laptop.fail-activate"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null || fail 'pending pushed arrival could not retry'
  pass 'receive acknowledgement, copy and activation failures keep work intact and permit retry to the same destination'
)

pushing_lost_activation_ack_never_overwrites_active_work() (
  setup_harbors push-lost-ack
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'original\n' > "$FM_PROJECTS_OVERRIDE/work"
  touch "$FM_FIXTURE_WORLD/laptop.fail-activate-after"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'lost activation acknowledgement was reported as received'; fi
  activate_harbor laptop
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'ack-loss fixture did not activate the receiver'
  printf 'new destination work\n' > "$FM_PROJECTS_OVERRIDE/work"
  activate_harbor desktop
  rm "$FM_FIXTURE_WORLD/laptop.fail-activate-after"
  local before
  before=$(wc -l < "$FM_FIXTURE_WORLD/copies")
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null || fail 'completed push was not recognized on retry'
  [ "$(wc -l < "$FM_FIXTURE_WORLD/copies")" = "$before" ] || fail 'retry overwrote the active destination with stale source cache'
  [ "$(< "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate/projects/work")" = 'new destination work' ] || fail 'ack retry lost authoritative work'
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to third >/dev/null 2>&1; then fail 'inactive cache was exported to an unrelated destination'; fi
  pass 'lost push acknowledgement is recovered without recopying over active work or exporting inactive caches elsewhere'
)

pushing_rejects_conflicts_and_its_own_primary() (
  setup_harbors push-refusals
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  activate_harbor laptop
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  activate_harbor desktop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'push replaced a foreign anchored vessel'; fi
  jq -e '.anchored' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'conflicting destination stopped the source'
  jq '.anchored = false' "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json" > "$FM_FIXTURE_WORLD/cache"
  mv "$FM_FIXTURE_WORLD/cache" "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null 2>&1; then fail 'push adopted a foreign inactive cache'; fi
  mkdir -p "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate"
  printf 'keep\n' > "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate/unregistered"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to third >/dev/null 2>&1; then fail 'push overwrote unregistered destination data'; fi
  [ "$(< "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate/unregistered")" = keep ] || fail 'receiver refusal changed unregistered data'
  jq -e '.anchored' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'receiver preflight stopped the source'
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to desktop >/dev/null 2>&1; then fail 'push sailed to its own harbor'; fi
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from laptop --to third >/dev/null 2>&1; then fail 'ambiguous sailing direction was accepted'; fi
  printf '%s\n' "$$" > "$FM_STATE_OVERRIDE/.lock"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to third >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'push could shut down its calling primary'; fi
  rg -q 'separate terminal' "$FM_FIXTURE_WORLD/error" || fail 'source-primary restriction was not explained'
  [ ! -e "$FM_FIXTURE_WORLD/copies" ] || fail 'refused push copied data'
  pass 'push refuses occupied destinations, ambiguous flags and execution inside its source primary before copying'
)

pushing_unknown_work_refuses_without_killing_it() (
  setup_harbors push-unknown
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  (cd "$FM_PROJECTS_OVERRIDE"; exec sleep 120) &
  local writer=$!
  trap 'kill "$writer" 2>/dev/null || true; wait "$writer" 2>/dev/null || true' EXIT
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'push copied unknown live source work'; fi
  kill -0 "$writer" || fail 'push killed an unfamiliar process'
  [ ! -e "$FM_FIXTURE_WORLD/copies" ] || fail 'push copied before stopping source work'
  jq -e '.anchored == false' "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json" >/dev/null || fail 'push launched a receiver before cleanup'
  kill "$writer"
  wait "$writer" 2>/dev/null || true
  trap - EXIT
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null || fail 'push could not resume after cleanup'
  pass 'push uses the existing source-stop refusal and resumes without killing unknown work'
)

scuttling_requires_confirmation_and_preserves_outside_files() (
  local anchor="$TMP_ROOT/scuttle" outside="$TMP_ROOT/scuttle-outside"
  mkdir -p "$anchor" "$outside"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  printf 'keep\n' > "$outside/shared"
  chmod 444 "$outside/shared"
  chmod 500 "$outside"
  mkdir -p "$FM_STATE_OVERRIDE/task.git-hooks"
  printf 'hook\n' > "$FM_STATE_OVERRIDE/task.git-hooks/pre-commit"
  chmod 500 "$FM_STATE_OVERRIDE/task.git-hooks" "$FM_STATE_OVERRIDE/task.git-hooks/pre-commit"
  ln "$outside/shared" "$FM_HOME/cache/hardlinked"
  ln -s "$outside" "$FM_HOME/cache/outside-link"
  printf 'anchor sibling\n' > "$anchor/keep"
  if bash "$ANCHOR_CLI" scuttle --anchor "$anchor" >/dev/null 2>&1; then fail 'scuttle accepted missing confirmation'; fi
  [ -f "$FM_HOME/cache/hardlinked" ] && [ -f "$anchor/.firstmate.json" ] || fail 'unconfirmed scuttle deleted data'
  bash "$ANCHOR_CLI" scuttle --anchor "$anchor" --confirm >/dev/null || fail 'confirmed scuttle could not remove read-only hook directories'
  [ ! -e "$FM_HOME" ] && [ ! -e "$anchor/.firstmate.json" ] && [ ! -e "$anchor/.firstmate.lock" ] || fail 'scuttle left vessel or metadata behind'
  bash "$ANCHOR_CLI" scuttle --anchor "$anchor" --confirm >/dev/null || fail 'scuttle could not handle an already empty anchor'
  [ ! -e "$FM_HOME" ] && [ ! -e "$anchor/.firstmate.lock" ] || fail 'empty scuttle recreated lasting state'
  [ "$(< "$outside/shared")" = keep ] && [ "$(stat -c %a "$outside/shared")" = 444 ] && [ "$(stat -c %a "$outside")" = 500 ] || fail 'scuttle followed an outside link or changed shared-file permissions'
  [ "$(< "$anchor/keep")" = 'anchor sibling' ] || fail 'scuttle deleted unrelated anchor contents'
  chmod 700 "$outside"
  pass 'confirmed scuttle removes read-only vessel directories and metadata without chmodding hardlinked files or following symlinks'
)

scuttling_refuses_live_work_and_its_own_primary() (
  local anchor="$TMP_ROOT/scuttle-refusal" writer
  mkdir -p "$anchor"
  configure "$anchor"
  bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null
  printf 'uncommitted\n' > "$FM_PROJECTS_OVERRIDE/work"
  printf '%s\n' "$$" > "$FM_STATE_OVERRIDE/.lock"
  if bash "$ANCHOR_CLI" scuttle --anchor "$anchor" --confirm >"$TMP_ROOT/scuttle-primary-error" 2>&1; then fail 'scuttle could stop its own calling primary'; fi
  rg -q 'separate terminal' "$TMP_ROOT/scuttle-primary-error" || fail 'scuttle did not explain the calling-primary refusal'
  rm "$FM_STATE_OVERRIDE/.lock"
  (cd "$FM_PROJECTS_OVERRIDE"; exec sleep 120) &
  writer=$!
  trap 'kill "$writer" 2>/dev/null || true; wait "$writer" 2>/dev/null || true' EXIT
  if bash "$ANCHOR_CLI" scuttle --anchor "$anchor" --confirm >/dev/null 2>&1; then fail 'scuttle deleted live unknown work'; fi
  kill -0 "$writer" || fail 'scuttle killed an unknown process'
  [ "$(< "$FM_PROJECTS_OVERRIDE/work")" = uncommitted ] && [ -f "$anchor/.firstmate.json" ] || fail 'refused scuttle deleted working state'
  kill "$writer"
  wait "$writer" 2>/dev/null || true
  trap - EXIT
  bash "$ANCHOR_CLI" scuttle --anchor "$anchor" --confirm >/dev/null
  pass 'scuttle refuses live unknown work and its calling primary without deleting data, then permits retry'
)

scuttling_a_cache_does_not_touch_other_harbors() (
  setup_harbors scuttle-cache
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'authoritative work\n' > "$FM_PROJECTS_OVERRIDE/work"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --to laptop >/dev/null
  local calls
  calls=$(wc -l < "$FM_FIXTURE_WORLD/ssh-operations")
  bash "$ANCHOR_CLI" scuttle --anchor "$FM_ANCHOR" --confirm >/dev/null
  [ "$(wc -l < "$FM_FIXTURE_WORLD/ssh-operations")" = "$calls" ] || fail 'scuttle contacted a remote harbor'
  activate_harbor laptop
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'scuttle changed the remote anchor'
  [ "$(< "$FM_PROJECTS_OVERRIDE/work")" = 'authoritative work' ] || fail 'scuttle deleted another harbor payload'
  pass 'scuttling removes only the local cache and leaves its active remote vessel untouched'
)

replaced_anchor_locks_are_rejected() (
  local anchor="$TMP_ROOT/replaced-lock" fakebin="$TMP_ROOT/lock-fakebin"
  mkdir -p "$anchor" "$fakebin"
  configure "$anchor"
  export FM_TEST_REAL_FLOCK FM_TEST_REPLACED_LOCK="$anchor/.firstmate.lock"
  FM_TEST_REAL_FLOCK=$(command -v flock)
  cat > "$fakebin/flock" <<'SH'
#!/bin/sh
"$FM_TEST_REAL_FLOCK" "$@" || exit
rm -- "$FM_TEST_REPLACED_LOCK"
: > "$FM_TEST_REPLACED_LOCK"
SH
  chmod +x "$fakebin/flock"
  if PATH="$fakebin:$PATH" bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >"$TMP_ROOT/replaced-lock-error" 2>&1; then fail 'operation accepted an unlinked old lock inode'; fi
  rg -q 'anchor lock changed' "$TMP_ROOT/replaced-lock-error" || fail 'stale lock refusal was not explained'
  [ ! -e "$anchor/.firstmate.json" ] && [ ! -e "$FM_HOME" ] || fail 'stale lock holder initialized state'
  pass 'an operation that acquired a replaced lock inode refuses before mutating vessel state'
)

setup_harbors() {
  export FM_FIXTURE_WORLD="$TMP_ROOT/$1" FM_FIXTURE_HARBOR=''
  export FM_REAL_RSYNC
  FM_REAL_RSYNC=$(command -v rsync)
  mkdir -p "$FM_FIXTURE_WORLD/fakebin"
  for host in desktop laptop third; do
    mkdir -p "$FM_FIXTURE_WORLD/hosts/$host/anchor" "$FM_FIXTURE_WORLD/hosts/$host/home/.local/share"
    ln -s "$ROOT" "$FM_FIXTURE_WORLD/hosts/$host/home/.local/share/firstmate"
  done
  for tool in ssh rsync uname; do
    printf '#!/bin/sh\nexec python3 "%s" %s "$@"\n' "$ROOT/tests/fixtures/fm-vessel-harbors.py" "$tool" > "$FM_FIXTURE_WORLD/fakebin/$tool"
    chmod +x "$FM_FIXTURE_WORLD/fakebin/$tool"
  done
  export PATH="$FM_FIXTURE_WORLD/fakebin:$PATH"
  configure "$FM_FIXTURE_WORLD/anchor with spaces"
  activate_harbor desktop
}

activate_harbor() {
  if [ -n "$FM_FIXTURE_HARBOR" ]; then
    mv "$FM_ANCHOR" "$FM_FIXTURE_WORLD/hosts/$FM_FIXTURE_HARBOR/anchor"
  fi
  mv "$FM_FIXTURE_WORLD/hosts/$1/anchor" "$FM_ANCHOR"
  export FM_FIXTURE_HARBOR=$1
}

sailing_copies_and_redirects_origin() (
  setup_harbors sailing
  local identity
  identity=$(bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi | jq -r .vessel_id)
  printf 'uncommitted work\n' > "$FM_HOME/projects/work"
  mkdir -p "$FM_HOME/pi/primary"
  printf 'conversation\n' > "$FM_HOME/pi/primary/session.jsonl"
  printf 'old socket\n' > "$FM_HOME/tmux/socket"
  mkdir -p "$FM_HOME/state/task.git-hooks"
  printf '#!/bin/sh\nexit 0\n' > "$FM_HOME/state/task.git-hooks/pre-commit"
  chmod 500 "$FM_HOME/state/task.git-hooks/pre-commit" "$FM_HOME/state/task.git-hooks"
  git init -q "$FM_HOME/projects/repo"
  printf 'tracked\n' > "$FM_HOME/projects/repo/file"
  git -C "$FM_HOME/projects/repo" add file
  git -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid -C "$FM_HOME/projects/repo" commit -qm initial
  mv "$FM_HOME/projects/repo/.git/objects" "$XDG_CACHE_HOME/git-objects"
  mkdir -p "$FM_HOME/projects/repo/.git/objects/info"
  printf '%s\n' "$XDG_CACHE_HOME/git-objects" > "$FM_HOME/projects/repo/.git/objects/info/alternates"
  git -c core.hooksPath=/dev/null -C "$FM_HOME/projects/repo" worktree add -qb task "$TREEHOUSE_ROOT/task"
  printf 'uncommitted\n' > "$TREEHOUSE_ROOT/task/file"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null
  [ "$(< "$FM_HOME/projects/work")" = 'uncommitted work' ] || fail 'sailing lost working data'
  [ "$(< "$FM_HOME/pi/primary/session.jsonl")" = conversation ] || fail 'sailing lost primary history'
  [ ! -e "$FM_HOME/tmux/socket" ] || fail 'sailing copied host runtime sockets'
  [ "$(git -C "$TREEHOUSE_ROOT/task" status --porcelain)" = ' M file' ] && [ "$(< "$TREEHOUSE_ROOT/task/file")" = uncommitted ] || fail 'linked Git worktree or uncommitted changes were lost'
  [ -x "$FM_HOME/state/task.git-hooks/pre-commit" ] && [ "$(stat -c %a "$FM_HOME/state/task.git-hooks")" = 500 ] || fail 'managed Git hook permissions changed'
  jq -e --arg id "$identity" '.vessel_id == $id and .anchored and .harbor == "laptop" and .last_known_harbor == "laptop" and (.known_harbors | sort) == ["desktop","laptop"]' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'arrival did not record identity and harbors'
  jq -e '.anchored == false and .last_known_harbor == "laptop"' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'origin still permits local launch'
  [ -f "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate/projects/work" ] || fail 'sailing deleted origin data'
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'arrived vessel cannot launch'
  chmod u+w "$FM_HOME/state/task.git-hooks" "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate/state/task.git-hooks"
  pass 'core copies a stopped vessel at the same path and redirects its origin before arrival'
)

sailing_follows_hints_and_syncs_inactive_cache() (
  setup_harbors hints
  local cache_inode unchanged_inode
  local -a leftovers
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'original\n' > "$FM_HOME/projects/work"
  printf 'delete me\n' > "$FM_HOME/projects/obsolete"
  printf 'unchanged\n' > "$FM_HOME/projects/unchanged"
  printf 'old socket\n' > "$FM_HOME/tmux/socket"
  printf 'old pid\n' > "$FM_HOME/state/.lock"
  cache_inode=$(stat -c %i "$FM_HOME")
  unchanged_inode=$(stat -c %i "$FM_HOME/projects/unchanged")
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null
  activate_harbor third
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from laptop >/dev/null
  printf 'latest\n' > "$FM_HOME/projects/work"
  rm "$FM_HOME/projects/obsolete"
  activate_harbor desktop
  : > "$FM_FIXTURE_WORLD/calls"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  [ "$(head -n 1 "$FM_FIXTURE_WORLD/calls")" = laptop ] || fail 'last-known harbor was not queried first'
  [ "$(< "$FM_HOME/projects/work")" = latest ] || fail 'newer harbor hints did not locate the vessel'
  [ "$(stat -c %i "$FM_HOME")" = "$cache_inode" ] || fail 'sailing replaced the cache rather than syncing the same target'
  [ "$(stat -c %i "$FM_HOME/projects/unchanged")" = "$unchanged_inode" ] || fail 'incremental sync rewrote an unchanged file'
  [ ! -e "$FM_HOME/projects/obsolete" ] || fail 'sync did not delete a file removed at the docked source'
  [ ! -e "$FM_HOME/tmux/socket" ] && [ ! -e "$FM_HOME/state/.lock" ] || fail 'sync retained excluded stale runtime files'
  shopt -s nullglob
  leftovers=("$FM_ANCHOR"/.firstmate.previous.* "$FM_ANCHOR"/.firstmate.sailing.*)
  [ "${#leftovers[@]}" -eq 0 ] || fail 'sync created staging or archive copies'
  jq -e '(.known_harbors | sort) == ["desktop","laptop","third"] and .anchored' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'known harbors did not converge'
  pass 'sailing follows hints and incrementally mirrors the same inactive cache including deletions'
)

failed_copy_can_retry_without_launching() (
  setup_harbors copy-failure
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'keep\n' > "$FM_HOME/projects/work"
  activate_harbor laptop
  touch "$FM_FIXTURE_WORLD/fail-copy"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null 2>&1; then fail 'failed rsync reported success'; fi
  jq -e '.anchored == false' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'failed copy activated the destination'
  jq -e '.anchored == false and .sailing_to == "laptop"' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'source was not reserved for retry'
  if bash "$ANCHOR_CLI" run --anchor "$FM_ANCHOR" -- touch "$FM_FIXTURE_WORLD/launched" >/dev/null 2>&1; then fail 'failed arrival launched work'; fi
  [ ! -e "$FM_FIXTURE_WORLD/launched" ] || fail 'failed arrival ran its command'
  rm "$FM_FIXTURE_WORLD/fail-copy"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  [ "$(< "$FM_HOME/projects/work")" = keep ] || fail 'retry did not recover source data'
  pass 'failed copies leave both ends inactive and retry at the same destination'
)

lost_origin_ack_does_not_activate_destination() (
  setup_harbors ack-failure
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  activate_harbor laptop
  touch "$FM_FIXTURE_WORLD/desktop.fail-commit-after"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null 2>&1; then fail 'lost origin acknowledgement reported success'; fi
  jq -e '.anchored == false' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'destination activated without origin acknowledgement'
  jq -e '.anchored == false and .last_known_harbor == "laptop" and (has("sailing_to") | not)' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'fixture did not commit before losing its acknowledgement'
  rm "$FM_FIXTURE_WORLD/desktop.fail-commit-after"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'committed origin could not be retried'
  pass 'lost origin acknowledgement keeps arrival inactive and permits a safe retry'
)

sailing_falls_back_when_last_known_is_unreachable() (
  setup_harbors fallback
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  jq '.harbor="laptop" | .anchored=false | .last_known_harbor="offline" | .known_harbors=["desktop","laptop","offline"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  [ "$(head -n 1 "$FM_FIXTURE_WORLD/calls")" = offline ] || fail 'last-known harbor was not attempted'
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'known harbor fallback did not arrive'
  pass 'an unreachable last-known harbor falls back to another known harbor'
)

sailing_never_adopts_an_unrelated_vessel() (
  setup_harbors wrong-vessel
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  jq '.harbor="laptop" | .anchored=false | .known_harbors=["desktop","laptop"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json"
  jq '.vessel_id="12345678-1234-4234-8234-123456789abc"' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/changed"
  mv "$FM_FIXTURE_WORLD/changed" "$FM_ANCHOR/.firstmate.json"
  activate_harbor laptop
  mkdir -p "$FM_HOME"
  printf 'keep local work\n' > "$FM_HOME/local-work"
  cp "$FM_ANCHOR/.firstmate.json" "$FM_FIXTURE_WORLD/before"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null 2>&1; then fail 'another vessel was adopted at the same path'; fi
  cmp -s "$FM_ANCHOR/.firstmate.json" "$FM_FIXTURE_WORLD/before" || fail 'failed lookup changed local hints'
  [ "$(< "$FM_HOME/local-work")" = 'keep local work' ] || fail 'failed lookup overwrote local work'
  pass 'failed identity lookup preserves local work and never creates a replacement vessel'
)

sailing_refuses_conflicting_anchored_copies() (
  setup_harbors conflict
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  jq '.harbor="laptop" | .anchored=false | .last_known_harbor="offline" | .known_harbors=["desktop","laptop","third","offline"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json"
  jq '.harbor="third" | .last_known_harbor="third" | .known_harbors=["desktop","laptop","third"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate.json"
  "$FM_REAL_RSYNC" -a "$FM_HOME/" "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate/"
  activate_harbor laptop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null 2>&1; then fail 'conflicting anchored copies were chosen arbitrarily'; fi
  jq -e '.anchored' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'conflict deactivated an origin'
  jq -e '.anchored' "$FM_FIXTURE_WORLD/hosts/third/anchor/.firstmate.json" >/dev/null || fail 'conflict deactivated another origin'
  [ ! -e "$FM_HOME" ] || fail 'conflict copied a payload'
  pass 'conflicting anchored copies stop discovery without choosing or deactivating either'
)

sailing_refuses_source_changes_before_copy() (
  setup_harbors changed-source
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  activate_harbor laptop
  touch "$FM_FIXTURE_WORLD/desktop.change-source"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >"$FM_FIXTURE_WORLD/error" 2>&1; then fail 'changed origin was copied'; fi
  rg -q 'source changed; rediscover before sailing' "$FM_FIXTURE_WORLD/error" || fail 'origin refusal reason was hidden'
  rg -q 'desktop.*prepare.*exit 1' "$FM_FIXTURE_WORLD/error" || fail 'origin refusal lost its harbor, operation or exit status'
  jq -e '.anchored and (.known_harbors | index("unseen") != null)' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'source-change fixture did not run or source was deactivated'
  [ ! -e "$FM_ANCHOR/.firstmate.json" ] && [ ! -e "$FM_HOME" ] || fail 'changed source created local vessel state'
  pass 'a changed source is rejected before copying or deactivating it'
)

legacy_local_anchors_can_sail_without_rewriting_preferences() (
  setup_harbors legacy
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'agent: codex\n' > "$NM_HOME/config.yaml"
  jq 'del(.vessel_id,.last_known_harbor,.known_harbors) | .version=1' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/legacy"
  cp "$FM_FIXTURE_WORLD/legacy" "$FM_ANCHOR/.firstmate.json"
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null
  cmp -s "$FM_ANCHOR/.firstmate.json" "$FM_FIXTURE_WORLD/legacy" || fail 'ordinary checking migrated a legacy record'
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null
  jq -e '.version == 2 and (.vessel_id | length > 0)' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'explicit sailing did not add routing identity'
  [ "$(< "$NM_HOME/config.yaml")" = 'agent: codex' ] || fail 'sailing rewrote existing agent preferences'
  pass 'existing local records upgrade only on explicit sailing and preserve tool preferences'
)

sailing_does_not_confuse_ssh_aliases_with_multiple_holders() (
  setup_harbors aliases
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  jq '.harbor="laptop" | .anchored=false | .last_known_harbor="offline" | .known_harbors=["desktop","work","laptop","offline"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/hosts/laptop/anchor/.firstmate.json"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'SSH aliases were treated as conflicting holders'
  pass 'multiple SSH names for one harbor do not create a false conflict'
)

pending_arrival_never_exports_its_cache() (
  setup_harbors pending-arrival
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'old copy\n' > "$FM_HOME/projects/work"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null
  activate_harbor desktop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  printf 'latest work\n' > "$FM_HOME/projects/work"
  activate_harbor laptop
  touch "$FM_FIXTURE_WORLD/desktop.fail-commit-after"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null 2>&1; then fail 'lost acknowledgement reported arrival'; fi
  activate_harbor desktop
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null 2>&1; then fail 'pending destination exported its cache before becoming anchored'; fi
  [ "$(< "$FM_HOME/projects/work")" = 'latest work' ] || fail 'latest origin data was replaced by an old destination copy'
  activate_harbor laptop
  rm "$FM_FIXTURE_WORLD/desktop.fail-commit-after"
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  [ "$(< "$FM_HOME/projects/work")" = 'latest work' ] || fail 'pending arrival did not recover the latest data'
  pass 'an unfinished arrival cannot export its cache as a docked vessel'
)

harbor_names_are_lowercase_and_unique() (
  setup_harbors lowercase
  printf '%s\n' '{"desktop":"DeSkToP","laptop":"LaPtOp"}' > "$FM_FIXTURE_WORLD/hostnames.json"
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi > "$FM_FIXTURE_WORLD/initialized"
  jq -e '.harbor == "desktop" and .last_known_harbor == "desktop" and .known_harbors == ["desktop"]' "$FM_FIXTURE_WORLD/initialized" >/dev/null || fail 'initial hostname was not lowercase'
  jq '.harbor="DeSkToP" | .last_known_harbor="DESKTOP" | .known_harbors=["DESKTOP","desktop","LAPTOP","laptop","CaseUser@DESKTOP","CaseUser@desktop"]' "$FM_ANCHOR/.firstmate.json" > "$FM_FIXTURE_WORLD/old-case"
  cp "$FM_FIXTURE_WORLD/old-case" "$FM_ANCHOR/.firstmate.json"
  bash "$ANCHOR_CLI" check --anchor "$FM_ANCHOR" >/dev/null || fail 'mixed-case existing record was refused'
  cmp -s "$FM_ANCHOR/.firstmate.json" "$FM_FIXTURE_WORLD/old-case" || fail 'read-only checking rewrote metadata'
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" >/dev/null
  jq -e '.harbor == "desktop" and .last_known_harbor == "desktop" and .known_harbors == ["desktop","laptop","CaseUser@desktop"]' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'local sailing did not normalize and deduplicate existing records'
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from DESKTOP >/dev/null
  [ "$(head -n 1 "$FM_FIXTURE_WORLD/calls")" = desktop ] || fail 'SSH destination was not normalized'
  jq -e '.harbor == "laptop" and .last_known_harbor == "laptop" and .known_harbors == ["desktop","laptop","CaseUser@desktop"]' "$FM_ANCHOR/.firstmate.json" >/dev/null || fail 'arrival routing names are not lowercase and unique'
  jq -e '.last_known_harbor == "laptop"' "$FM_FIXTURE_WORLD/hosts/desktop/anchor/.firstmate.json" >/dev/null || fail 'origin redirect was not normalized'
  pass 'harbor names normalize to lowercase and deduplicate without changing SSH user names'
)

inactive_cache_is_not_an_authoritative_source() (
  setup_harbors inactive-source
  bash "$ANCHOR_CLI" init --anchor "$FM_ANCHOR" --agent pi >/dev/null
  printf 'cached old work\n' > "$FM_HOME/projects/work"
  activate_harbor laptop
  bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null
  printf 'current work\n' > "$FM_HOME/projects/work"
  mv "$FM_ANCHOR" "$FM_FIXTURE_WORLD/parked-current-copy"
  mkdir "$FM_ANCHOR"
  if bash "$ANCHOR_CLI" sail --anchor "$FM_ANCHOR" --from desktop >/dev/null 2>&1; then fail 'undocked origin cache was treated as an anchored source'; fi
  [ ! -e "$FM_HOME" ] || fail 'failed discovery copied an undocked cache'
  [ "$(< "$FM_FIXTURE_WORLD/parked-current-copy/.firstmate/projects/work")" = 'current work' ] || fail 'current work was modified'
  pass 'an undocked cache is not a source without a matching pending arrival'
)

fm_test_run scuttling_requires_confirmation_and_preserves_outside_files scuttling_refuses_live_work_and_its_own_primary scuttling_a_cache_does_not_touch_other_harbors replaced_anchor_locks_are_rejected pushing_anchors_the_destination_without_reverse_ssh pushing_retries_copy_and_activation_failures pushing_lost_activation_ack_never_overwrites_active_work pushing_rejects_conflicts_and_its_own_primary pushing_unknown_work_refuses_without_killing_it sailing_stops_registered_work_without_discarding_state unavailable_sources_report_the_payload_reason source_stop_errors_preserve_the_refusal_reason unknown_source_work_blocks_copy_without_killing_it vessel_projects_clone_origins_only_when_missing vessel_project_failures_block_launch_and_can_retry inactive_cache_is_not_an_authoritative_source harbor_names_are_lowercase_and_unique pending_arrival_never_exports_its_cache sailing_does_not_confuse_ssh_aliases_with_multiple_holders sailing_copies_and_redirects_origin sailing_follows_hints_and_syncs_inactive_cache failed_copy_can_retry_without_launching lost_origin_ack_does_not_activate_destination sailing_falls_back_when_last_known_is_unreachable sailing_never_adopts_an_unrelated_vessel sailing_refuses_conflicting_anchored_copies sailing_refuses_source_changes_before_copy legacy_local_anchors_can_sail_without_rewriting_preferences core_environment_composes_forwarding_layers unanchored_launch_is_refused local_anchor_records_options_and_preserves_argv boarding_gate_never_repairs_metadata options_and_existing_work_are_not_converted anchor_operations_use_one_regular_lock
