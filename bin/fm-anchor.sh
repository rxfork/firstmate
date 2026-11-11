#!/usr/bin/env bash
# Anchor one local vessel; no transfer, process manager or configuration audit.
# Usage: fm-anchor.sh init|check --anchor DIRECTORY
#        fm-anchor.sh run --anchor DIRECTORY -- COMMAND [ARG...]
# Core supplies the portable environment; callers choose the runtime command.
# .firstmate.json records this harbor, physical anchor and selected paths.
# run checks that exact configuration and anchored=true before exec. The short
# .firstmate.lock serializes initialization/checks, not the lifetime of writers;
# ordinary Firstmate retains its existing session lock. Stopping work is manual.
set -eu
fail() { printf 'fm-anchor: %s\n' "$*" >&2; exit 1; }
usage() { printf 'Usage: fm-anchor.sh init|check --anchor DIRECTORY\n       fm-anchor.sh run --anchor DIRECTORY -- COMMAND [ARG...]\n'; }
case "${1:-}" in -h|--help) usage; exit 0 ;; esac
[ "$#" -ge 3 ] || { usage >&2; exit 2; }
mode=$1; shift
[ "$1" = --anchor ] || { usage >&2; exit 2; }
anchor=$(CDPATH='' cd -P -- "$2" && pwd -P) || fail 'anchor must be an existing directory'
shift 2
case "$mode" in
  init|check) [ "$#" -eq 0 ] || { usage >&2; exit 2; } ;;
  run) [ "$#" -ge 2 ] && [ "$1" = -- ] || { usage >&2; exit 2; }; shift ;;
  *) usage >&2; exit 2 ;;
esac
[ "$(uname -s)" = Linux ] || fail 'local anchoring currently requires Linux'
[ "${FM_BACKEND:-tmux}" = tmux ] || fail 'local vessels require FM_BACKEND=tmux'
vessel="$anchor/.firstmate"
if [ -n "${FM_ANCHOR:-}" ] && [ "$(realpath -m -- "$FM_ANCHOR")" != "$anchor" ]; then
  fail 'inherited FM_ANCHOR conflicts with this anchor'
fi
export FM_ANCHOR="$anchor" FM_HOME="${FM_HOME:-$vessel}" FM_BACKEND=tmux
export FM_CONFIG_OVERRIDE="${FM_CONFIG_OVERRIDE:-$vessel/config}"
export FM_DATA_OVERRIDE="${FM_DATA_OVERRIDE:-$vessel/data}"
export FM_STATE_OVERRIDE="${FM_STATE_OVERRIDE:-$vessel/state}"
export FM_PROJECTS_OVERRIDE="${FM_PROJECTS_OVERRIDE:-$vessel/projects}"
export TREEHOUSE_ROOT="${TREEHOUSE_ROOT:-$vessel/worktrees}"
export NM_HOME="${NM_HOME:-$vessel/no-mistakes}"
export LAVISH_AXI_STATE_DIR="${LAVISH_AXI_STATE_DIR:-$vessel/lavish-axi}"
export PI_CODING_AGENT_SESSION_DIR="${PI_CODING_AGENT_SESSION_DIR:-$vessel/pi/sessions}"
export TMUX_TMPDIR="$vessel/tmux" XDG_CACHE_HOME="$vessel/cache"
export NM_TEST_START_DAEMON="${NM_TEST_START_DAEMON-1}"
if [ "${FM_WORKER_ENV_FORWARD+x}" != x ]; then
  FM_WORKER_ENV_FORWARD='PATH TREEHOUSE_ROOT NM_HOME LAVISH_AXI_STATE_DIR PI_CODING_AGENT_SESSION_DIR TMUX_TMPDIR XDG_CACHE_HOME NM_TEST_START_DAEMON'
  if [ -n "${FM_WORKER_ENV_FORWARD_DEFAULTS:-}" ]; then
    FM_WORKER_ENV_FORWARD="$FM_WORKER_ENV_FORWARD $FM_WORKER_ENV_FORWARD_DEFAULTS"
  fi
fi
export FM_WORKER_ENV_FORWARD
unset TMUX TMUX_PANE ZELLIJ ZELLIJ_SESSION_NAME ZELLIJ_PANE_ID
metadata="$anchor/.firstmate.json"
[ ! -L "$vessel" ] && [ ! -L "$metadata" ] || fail 'vessel and metadata must not be symlinks'
args=(--arg anchor "$anchor" --arg harbor "$(uname -n)")
directories=()
for name in FM_HOME FM_CONFIG_OVERRIDE FM_DATA_OVERRIDE FM_STATE_OVERRIDE FM_PROJECTS_OVERRIDE \
  TREEHOUSE_ROOT NM_HOME LAVISH_AXI_STATE_DIR PI_CODING_AGENT_SESSION_DIR TMUX_TMPDIR XDG_CACHE_HOME; do
  value=${!name:-}
  [ -n "$value" ] && [[ "$value" = /* ]] || fail "$name must name an absolute vessel directory"
  value=$(realpath -m -- "$value")
  case "$value" in "$vessel"|"$vessel"/*) ;; *) fail "$name is outside the vessel" ;; esac
  args+=(--arg "$name" "$value")
  directories+=("$value")
done
[ "$(realpath -m -- "$FM_HOME")" = "$vessel" ] || fail 'FM_HOME must be the vessel directory'
snapshot=$(jq -n "${args[@]}" '{version:1, anchored:true, anchor:$anchor, harbor:$harbor, backend:"tmux", options:($ARGS.named | del(.anchor,.harbor))}')
umask 077
lock="$anchor/.firstmate.lock"
if [ -L "$lock" ] || { [ -e "$lock" ] && [ ! -f "$lock" ]; }; then
  fail 'anchor lock must be a regular file'
fi
exec 9>> "$lock"
flock -n 9 || fail 'another anchor operation is in progress'
if [ -e "$metadata" ]; then
  if [ ! -f "$metadata" ] || ! jq -se --argjson expected "$snapshot" '. == [$expected]' "$metadata" >/dev/null; then
    fail 'vessel is not anchored here with these options; metadata was not changed'
  fi
  if [ ! -f "$FM_CONFIG_OVERRIDE/backend" ] || [ "$(< "$FM_CONFIG_OVERRIDE/backend")" != tmux ]; then
    fail 'vessel backend configuration changed'
  fi
elif [ "$mode" = init ]; then
  [ ! -e "$vessel" ] || [ -d "$vessel" ] || fail 'vessel must be a directory'
  shopt -s nullglob dotglob
  existing=("$vessel"/*)
  [ "${#existing[@]}" -eq 0 ] || fail 'refusing to adopt nonempty unregistered vessel state'
  mkdir -p -- "${directories[@]}"
  printf 'tmux\n' > "$FM_CONFIG_OVERRIDE/backend"
  temporary=$(mktemp "$anchor/.firstmate.json.XXXXXX")
  trap 'rm -f -- "$temporary"' EXIT
  printf '%s\n' "$snapshot" > "$temporary"
  mv -- "$temporary" "$metadata"
  trap - EXIT
else
  fail 'vessel is not anchored; initialize it explicitly first'
fi
if [ "$mode" != run ]; then printf '%s\n' "$snapshot"; exit 0; fi
exec 9>&-
exec "$@"
