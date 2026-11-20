#!/usr/bin/env bash
# Shared vessel side of the anchor lock for task lifecycle mutations.
# The exclusive owner passes the original descriptor to its lifecycle children.
fm_vessel_has_exclusive_lock() {  # <lock path>
  local fd=${FM_VESSEL_RECOVERY_FD:-}
  [[ "$fd" =~ ^[0-9]+$ ]] && [ "$fd" -gt 2 ] && [ ! -L "$1" ] &&
    [ "$1" -ef "/proc/self/fd/$fd" ] && flock -nx "$fd"
}

fm_vessel_guard_mutation() {  # <script> [args...]; re-exec once under a shared lock
  [ -n "${FM_ANCHOR:-}" ] || return 0
  local name=${FM_VESSEL:-flagship} script=$1 lock receipt
  shift
  [[ "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || return 1
  lock="$FM_ANCHOR/.firstmate/$name.lock"
  fm_vessel_has_exclusive_lock "$lock" && return 0
  unset FM_VESSEL_RELAUNCH_TX
  if [ "${FM_VESSEL_MUTATOR:-}" = "$script" ]; then
    unset FM_VESSEL_MUTATOR
    receipt="${FM_STATE_OVERRIDE:-$FM_ANCHOR/.firstmate/$name/state}/.vessel-recovery.json"
    if [ -e "$receipt" ] || [ -L "$receipt" ]; then
      if [ -L "$receipt" ] || ! jq -e '.body.phase == "complete"' "$receipt" >/dev/null; then
        printf 'error: vessel recovery is pending; finish fullstop/resume before mutating tasks\n' >&2
        return 1
      fi
    fi
    return 0
  fi
  if [ -L "$lock" ] || [ ! -f "$lock" ]; then
    printf 'error: vessel anchor lock is missing or not a regular file: %s\n' "$lock" >&2
    return 1
  fi
  # --close keeps persistent descendants (tmux, helpers) from pinning the lock.
  exec flock -s -n -E 75 --close "$lock" env FM_VESSEL_MUTATOR="$script" bash "$script" "$@"
}
