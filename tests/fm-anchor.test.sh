#!/usr/bin/env bash
# Local anchoring only: exercise the CLI, its metadata and the launch boundary.
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
  jq -e --arg anchor "$anchor" --arg harbor "$(uname -n)" '.version == 1 and .anchored == true and .anchor == $anchor and .harbor == $harbor and .backend == "tmux"' <<< "$result" >/dev/null || fail 'anchor identity was not recorded'
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
  exec 8> "$anchor/.firstmate.lock"
  flock -n 8
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1; then fail 'another operation bypassed the anchor lock'; fi
  exec 8>&-
  rm "$anchor/.firstmate.lock"
  mkfifo "$anchor/.firstmate.lock"
  timeout 5 bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1 || rc=$?
  [ "$rc" -eq 1 ] || fail 'FIFO lock did not refuse promptly'
  rm "$anchor/.firstmate.lock"
  printf 'keep\n' > "$TMP_ROOT/unrelated"
  ln -s "$TMP_ROOT/unrelated" "$anchor/.firstmate.lock"
  if bash "$ANCHOR_CLI" init --anchor "$anchor" --agent pi >/dev/null 2>&1; then fail 'symlink was treated as an anchor lock'; fi
  [ "$(< "$TMP_ROOT/unrelated")" = keep ] || fail 'anchor lock modified an unrelated file'
  pass 'anchor operations serialize with a regular lock and reject file aliases'
)

core_environment_composes_forwarding_layers() (
  local anchor="$TMP_ROOT/environment" output forwarded nested
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

fm_test_run core_environment_composes_forwarding_layers unanchored_launch_is_refused local_anchor_records_options_and_preserves_argv boarding_gate_never_repairs_metadata options_and_existing_work_are_not_converted anchor_operations_use_one_regular_lock
