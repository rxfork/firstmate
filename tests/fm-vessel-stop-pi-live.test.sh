#!/usr/bin/env bash
# Token-free real-Pi stop guard for bin/fm-vessel.py via bin/fm-anchor.sh.
# Uses only a disposable PTY and vessel; no model prompt.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
if [ "$(uname -s)" != Linux ]; then printf 'skip: vessel stopping requires Linux\n'; exit 0; fi
fm_live_gate default-on FM_VESSEL_PI_STOP_LIVE pi python3
TMP_ROOT=$(fm_test_tmproot fm-vessel-stop-pi)
if ! python3 -c 'import os, pty; a, b = pty.openpty(); os.close(a); os.close(b)' 2>"$TMP_ROOT/pty-error"; then
  if [ "${FM_VESSEL_PI_STOP_LIVE:-${FM_LIVE:-0}}" = 1 ]; then fail 'interactive Pi stop guard was requested but PTY allocation is unavailable'; fi
  printf 'skip: live: PTY allocation unavailable; interactive Pi shutdown is unverified\n'
  exit 0
fi

pi_stops_after_source_coordination() {
  python3 "$ROOT/tests/fixtures/fm-vessel-stop-pi.py" "$TMP_ROOT/native"
  pass 'real Pi stops without a model call, preserves history, requests unknown-work cleanup once and leaves unrelated work alone'
}

fm_test_run pi_stops_after_source_coordination
