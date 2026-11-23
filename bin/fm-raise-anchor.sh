#!/usr/bin/env bash
# Stop vessel work without transferring or deleting working state.
# Usage: fm-raise-anchor.sh --anchor DIRECTORY [--vessel NAME]
# Registered workers/services stop first. Remaining scoped processes require
# terminal confirmation: SIGTERM [Y/n], then SIGKILL [y/N] after a bounded wait.
# A refused no-mistakes stop offers daemon stop --force [y/N] before signals.
# This may abandon reviews; no repository files or review records are deleted.
# EOF/decline never authorizes escalation; noninteractive calls do not prompt.
# Each approval covers only the displayed PID/start-time pairs, not names or
# process groups. Repositories, dirty files and durable task records remain.
# JSON on stdout reports stopped, remaining, errors and requested; a nonzero
# exit means work is not confirmed stopped. Prompts/diagnostics go to stderr.
# fm_raise_anchor.py owns shutdown shared with fm-anchor.sh sail/allstop/scuttle.
set -eu
case "${1:-}" in
  -h|--help)
    printf 'Usage: fm-raise-anchor.sh --anchor DIRECTORY [--vessel NAME]\nStop work without moving or deleting it.\nRefused no-mistakes stop: force-stop [y/N] (may abandon reviews; preserves files/records).\nRemaining scoped processes: SIGTERM [Y/n], then SIGKILL [y/N].\nNoninteractive calls never assume consent; remaining work returns nonzero.\n'
    exit 0
    ;;
esac
exec bash "$(dirname "${BASH_SOURCE[0]}")/fm-anchor.sh" allstop "$@"
