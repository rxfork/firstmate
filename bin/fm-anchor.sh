#!/usr/bin/env bash
# Anchor, stop or sail a vessel; no generic process manager or writer certification.
# Usage: fm-anchor.sh init --anchor DIRECTORY [--vessel NAME] --agent NAME
#        fm-anchor.sh check --anchor DIRECTORY [--vessel NAME]
#        fm-anchor.sh run --anchor DIRECTORY [--vessel NAME] -- COMMAND [ARG...]
#        fm-anchor.sh open --anchor DIRECTORY [--vessel NAME] --agent NAME -- COMMAND [ARG...]
#        fm-anchor.sh sail --anchor DIRECTORY [--vessel NAME] [--from HARBOR | --to HARBOR]
#        fm-anchor.sh stop --anchor DIRECTORY [--vessel NAME]
#        fm-anchor.sh status --anchor DIRECTORY [--vessel NAME]
#        fm-anchor.sh scuttle --anchor DIRECTORY [--vessel NAME] (--confirm | --interactive)
# open prompts to initialize or sail an unregistered vessel, otherwise follows
# its recorded harbor, then runs COMMAND through the ordinary run gate.
# EOF or declined initialization never starts COMMAND. Prompts go to stderr.
# scuttle --interactive requires typing SCUTTLE before the ordinary --confirm
# path; noninteractive callers must still supply --confirm explicitly.
# Sailing uses SSH/rsync at identical physical paths. Source preparation stops
# known work and asks the source primary to resolve unfamiliar processes.
# Host tooling/auth remain local. fm-vessel.py owns routing, stopping and the
# prepare/copy/commit sequence; status lists the local scoped process facts.
# init/run discover repositories under the anchor, skipping .firstmate and
# repository contents, and clone local main into the selected projects path
# only when missing. local points at the source; origin retains its origin URL.
# Existing vessel clones are never refreshed or replaced.
# Core supplies the portable environment; callers choose the runtime command.
# --vessel NAME selects .firstmate/NAME (default flagship); NAME.json records
# this harbor, physical anchor and selected paths beside that payload.
# run checks that exact configuration and anchored=true before exec.
# NAME.lock serializes that vessel's operations, not the lifetime of writers;
# pulling holds the destination lock, pushing the source lock, until arrival.
# Push from a separate terminal, not from inside the primary being stopped.
# Ordinary Firstmate retains
# its existing session lock. Unknown or unconfirmed stops prevent copying.
# scuttle --confirm discards only the selected .firstmate/NAME directory, record
# and lock after stopping work. It makes directories owner-writable for
# deletion, not files that may share hardlinks with outside repositories.
set -eu
fail() { printf 'fm-anchor: %s\n' "$*" >&2; exit 1; }
usage() { printf 'Usage: fm-anchor.sh init --anchor DIRECTORY [--vessel NAME] --agent NAME\n       fm-anchor.sh check --anchor DIRECTORY [--vessel NAME]\n       fm-anchor.sh run --anchor DIRECTORY [--vessel NAME] -- COMMAND [ARG...]\n       fm-anchor.sh open --anchor DIRECTORY [--vessel NAME] --agent NAME -- COMMAND [ARG...]\n       fm-anchor.sh sail --anchor DIRECTORY [--vessel NAME] [--from HARBOR | --to HARBOR]\n       fm-anchor.sh stop --anchor DIRECTORY [--vessel NAME]\n       fm-anchor.sh status --anchor DIRECTORY [--vessel NAME]\n       fm-anchor.sh scuttle --anchor DIRECTORY [--vessel NAME] (--confirm | --interactive)\nAll operations accept --vessel NAME (default flagship). list --anchor DIRECTORY [--vessel NAME] prints registered names without SSH or writes.\nVessels live at .firstmate/NAME with sibling NAME.json and NAME.lock.\nSailing requires identical physical anchor paths and SSH/rsync; unconfirmed source stops prevent copying.\nRun --to from a separate terminal and keep the source online until destination acknowledgement.\nOpen prompts to initialize or sail before running the supplied command.\nScuttle irreversibly discards all local vessel work and history; --confirm or typed --interactive confirmation is required.\n'; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  _peer)
    exec python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" "$@"
    ;;
esac
[ "$#" -ge 3 ] || { usage >&2; exit 2; }
mode=$1; shift
[ "$1" = --anchor ] || { usage >&2; exit 2; }
anchor=$(CDPATH='' cd -P -- "$2" && pwd -P) || fail 'anchor must be an existing directory'
shift 2
vessel_name=flagship
vessel_seen=0
arguments=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --) arguments+=("$@"); break ;;
    --vessel)
      [ "$#" -ge 2 ] && [ "$vessel_seen" -eq 0 ] || { usage >&2; exit 2; }
      vessel_name=$2; vessel_seen=1; shift 2 ;;
    *) arguments+=("$1"); shift ;;
  esac
done
set -- "${arguments[@]}"
[[ "$vessel_name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || fail 'invalid vessel name: use 1-64 lowercase letters, digits, underscores or hyphens'
case "$mode" in
  init|open)
    if [ "${1:-}" != --agent ] || [[ ! "${2:-}" =~ ^[a-z][a-z0-9-]*$ ]] ||
      { [ "$mode" = init ] && [ "$#" -ne 2 ]; } ||
      { [ "$mode" = open ] && { [ "$#" -lt 4 ] || [ "${3:-}" != -- ]; }; }; then
      usage >&2
      exit 2
    fi
    agent=$2
    if [ "$mode" = open ]; then shift 3; fi
    ;;
  check|stop|status|list) [ "$#" -eq 0 ] || { usage >&2; exit 2; } ;;
  scuttle)
    if [ "$#" -ne 1 ] || { [ "$1" != --confirm ] && [ "$1" != --interactive ]; }; then
      usage >&2
      exit 2
    fi
    ;;
  sail)
    if [ "$#" -ne 0 ] && { [ "$#" -ne 2 ] || { [ "${1:-}" != --from ] && [ "${1:-}" != --to ]; } || [ -z "${2:-}" ]; }; then
      usage >&2
      exit 2
    fi
    ;;
  run) [ "$#" -ge 2 ] && [ "$1" = -- ] || { usage >&2; exit 2; }; shift ;;
  *) usage >&2; exit 2 ;;
esac
[ "$(uname -s)" = Linux ] || fail 'local anchoring currently requires Linux'
namespace="$anchor/.firstmate"
vessel="$namespace/$vessel_name"
metadata="$namespace/$vessel_name.json"
lock="$namespace/$vessel_name.lock"
[ ! -L "$namespace" ] && [ ! -L "$vessel" ] && [ ! -L "$metadata" ] || fail 'vessel container, payload and metadata must not be symlinks'
if [ "$mode" = list ]; then
  exec python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" list --anchor "$anchor" --vessel "$vessel_name"
fi
[ "${FM_BACKEND:-tmux}" = tmux ] || fail 'local vessels require FM_BACKEND=tmux'
if [ -n "${FM_ANCHOR:-}" ] && [ "$(realpath -m -- "$FM_ANCHOR")" != "$anchor" ]; then
  fail 'inherited FM_ANCHOR conflicts with this anchor'
fi
export FM_ANCHOR="$anchor" FM_VESSEL="$vessel_name" FM_HOME="${FM_HOME:-$vessel}" FM_BACKEND=tmux
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
export FM_WORKER_ENV_FORWARD="${FM_WORKER_ENV_FORWARD:+$FM_WORKER_ENV_FORWARD }PATH TREEHOUSE_ROOT NM_HOME LAVISH_AXI_STATE_DIR PI_CODING_AGENT_SESSION_DIR TMUX_TMPDIR XDG_CACHE_HOME NM_TEST_START_DAEMON"
unset TMUX TMUX_PANE ZELLIJ ZELLIJ_SESSION_NAME ZELLIJ_PANE_ID
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
snapshot=$(jq -n "${args[@]}" '{version:2, anchored:true, anchor:$anchor, harbor:$harbor, backend:"tmux", options:($ARGS.named | del(.anchor,.harbor))}')
umask 077
if [ "$mode" = open ]; then
  target=(--anchor "$anchor" --vessel "$vessel_name")
  if [ ! -e "$metadata" ]; then
    printf 'Sail vessel %s here from another harbor? [y/N] ' "$vessel_name" >&2
    IFS= read -r answer || exit 1
    case "$answer" in
      y|Y|yes|YES)
        printf 'Source harbor: ' >&2
        IFS= read -r source_harbor || exit 1
        bash "${BASH_SOURCE[0]}" sail "${target[@]}" --from "$source_harbor" >/dev/null
        ;;
      *)
        printf 'Anchor a new vessel named %s here? [Y/n] ' "$vessel_name" >&2
        IFS= read -r answer || exit 1
        case "$answer" in
          ''|y|Y|yes|YES) bash "${BASH_SOURCE[0]}" init "${target[@]}" --agent "$agent" >/dev/null ;;
          *) exit 1 ;;
        esac
        ;;
    esac
  else
    bash "${BASH_SOURCE[0]}" sail "${target[@]}" >/dev/null
  fi
  exec bash "${BASH_SOURCE[0]}" run "${target[@]}" -- "$@"
fi
if [ "$mode" = scuttle ] && [ "$1" = --interactive ]; then
  printf 'Permanently delete %s, its metadata, ALL vessel repositories, uncommitted work and session history?\nType SCUTTLE to confirm: ' "$vessel" >&2
  IFS= read -r answer || exit 1
  [ "$answer" = SCUTTLE ] || fail 'scuttle cancelled'
  set -- --confirm
fi
if [ "$mode" = sail ] || [ "$mode" = stop ] || [ "$mode" = status ] || [ "$mode" = scuttle ]; then
  printf '%s\n' "$snapshot" | python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" "$mode" --anchor "$anchor" --vessel "$vessel_name" "$@"
  exit $?
fi
mkdir -p -- "$namespace"
if [ -L "$lock" ] || { [ -e "$lock" ] && [ ! -f "$lock" ]; }; then
  fail 'anchor lock must be a regular file'
fi
exec 9>> "$lock"
flock -n 9 || fail 'another anchor operation is in progress'
[ ! -L "$lock" ] && [ "$lock" -ef /proc/self/fd/9 ] || fail 'anchor lock changed; retry the operation'
if [ -e "$metadata" ]; then
  record=$(printf '%s\n' "$snapshot" | python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" check --anchor "$anchor" --vessel "$vessel_name") || exit 1
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
  printf 'agent: "%s"\n' "$agent" > "$NM_HOME/config.yaml"
  record=$(printf '%s\n' "$snapshot" | python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" init --anchor "$anchor" --vessel "$vessel_name")
else
  fail 'vessel is not anchored; initialize it explicitly first'
fi
if [ "$mode" = init ] || [ "$mode" = run ]; then
  printf '%s\n' "$snapshot" | python3 "$(dirname "${BASH_SOURCE[0]}")/fm-vessel.py" _projects --anchor "$anchor" --vessel "$vessel_name" >/dev/null
fi
if [ "$mode" != run ]; then printf '%s\n' "$record"; exit 0; fi
exec 9>&-
exec "$@"
