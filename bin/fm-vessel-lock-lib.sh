#!/usr/bin/env bash
# Shared vessel side of the anchor lock for task lifecycle mutations.
# The exclusive owner passes the original descriptor to its lifecycle children.
fm_vessel_has_exclusive_lock() {  # <lock path>
  local fd=${FM_VESSEL_RECOVERY_FD:-}
  [[ "$fd" =~ ^[0-9]+$ ]] && [ "$fd" -gt 2 ] && [ ! -L "$1" ] &&
    [ "$1" -ef "/proc/self/fd/$fd" ] && flock -nx "$fd"
}

fm_vessel_shared_lock_caller() {  # <lock path>; print the caller behind the flock wrapper
  local fd
  [ "/proc/$PPID/exe" -ef "$(type -P flock)" ] || return 1
  for fd in /proc/"$PPID"/fd/*; do
    [ "$1" -ef "$fd" ] || continue
    if awk -v pid="$PPID" '$1 == "lock:" && $3 == "FLOCK" && $4 == "ADVISORY" && $5 == "READ" && $6 == pid { held=1 } END { exit !held }' \
        "/proc/$PPID/fdinfo/${fd##*/}"; then
      awk '$1 == "PPid:" && $2 ~ /^[1-9][0-9]*$/ { print $2; found=1 } END { exit !found }' "/proc/$PPID/status"
      return
    fi
  done
  return 1
}

fm_vessel_guard_mutation() {  # <script> [args...]; re-exec once under a shared lock
  FM_VESSEL_MUTATION_CALLER=$PPID
  [ -n "${FM_ANCHOR:-}" ] || return 0
  local name=${FM_VESSEL:-flagship} script=$1 lock receipt
  shift
  [[ "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || return 1
  lock="$FM_ANCHOR/.firstmate/$name.lock"
  fm_vessel_has_exclusive_lock "$lock" && return 0
  unset FM_VESSEL_RELAUNCH_TX
  if [ -L "$lock" ] || [ ! -f "$lock" ]; then
    printf 'error: vessel anchor lock is missing or not a regular file: %s\n' "$lock" >&2
    return 1
  fi
  if [ "${FM_VESSEL_MUTATOR:-}" = "$script" ]; then
    unset FM_VESSEL_MUTATOR
    # A marker bounds re-exec; only the immediate parent's kernel lock proves it happened.
    # shellcheck disable=SC2034 # Consumed by fm-spawn.sh after the guard.
    FM_VESSEL_MUTATION_CALLER=$(fm_vessel_shared_lock_caller "$lock") || {
      printf 'error: cannot verify shared vessel lock ownership: %s\n' "$lock" >&2
      return 1
    }
    receipt="${FM_STATE_OVERRIDE:-$FM_ANCHOR/.firstmate/$name/state}/.vessel-recovery.json"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
      if [ -L "$receipt" ] || ! jq -e '.body.phase == "complete"' "$receipt" >/dev/null; then
        printf 'error: vessel recovery is pending; finish allstop/aheadfull before mutating tasks\n' >&2
        return 1
      fi
    fi
    return 0
  fi
  # --close keeps persistent descendants (tmux, helpers) from pinning the lock.
  exec flock -s -n -E 75 --close "$lock" env FM_VESSEL_MUTATOR="$script" bash "$script" "$@"
}
