#!/usr/bin/env bash
set -u

# shellcheck source=tests/fixtures.sh
. "$(dirname "${BASH_SOURCE[0]}")/fixtures.sh"

TMP_ROOT=$(fm_test_tmproot fm-spawn-zellij-abort)
HOME_DIR="$TMP_ROOT/home"
PROJ_DIR="$TMP_ROOT/project"
WT_DIR="$TMP_ROOT/wt"
FAKEBIN_DIR=$(fm_test_make_spawn_fakebin "$TMP_ROOT/fake")
ID=zellij-acquisition-failure
LOG="$TMP_ROOT/zellij.log"
ZSTATE="$TMP_ROOT/zellij-state"

fm_test_spawn_home "$HOME_DIR" codex
fm_test_spawn_brief "$HOME_DIR" "$ID"
fm_git_worktree "$PROJ_DIR" "$WT_DIR" "wt-$ID"

cat > "$FAKEBIN_DIR/zellij" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${FM_ZELLIJ_TEST_LOG:?}"
case "${1:-}" in
  --version) printf 'zellij 0.45.1\n'; exit 0 ;;
  list-sessions) printf 'live-session\n'; exit 0 ;;
esac

if printf '%s\n' "$*" | grep -q 'action list-tabs --json'; then
  if [ -f "${FM_ZELLIJ_TEST_STATE:?}/title" ]; then
    title=$(cat "$FM_ZELLIJ_TEST_STATE/title")
    printf '[{"tab_id":3,"name":"%s","active":false}]\n' "$title"
  else
    printf '[]\n'
  fi
  exit 0
fi
if printf '%s\n' "$*" | grep -q 'action new-tab '; then
  previous=
  title=
  for arg in "$@"; do
    [ "$previous" = --name ] && title=$arg
    previous=$arg
  done
  mkdir -p "$FM_ZELLIJ_TEST_STATE"
  printf '%s\n' "$title" > "$FM_ZELLIJ_TEST_STATE/title"
  printf '3\n'
  exit 0
fi
if printf '%s\n' "$*" | grep -q 'action list-panes --json'; then
  printf '[{"id":7,"tab_id":3,"is_plugin":false}]\n'
  exit 0
fi
if printf '%s\n' "$*" | grep -q 'action paste '; then
  exit 1
fi
if printf '%s\n' "$*" | grep -q 'action close-tab-by-id 3'; then
  rm -f "$FM_ZELLIJ_TEST_STATE/title"
  exit 0
fi
exit 0
SH
chmod +x "$FAKEBIN_DIR/zellij"

out=$(FM_ROOT_OVERRIDE='' FM_HOME="$HOME_DIR" HOME="$HOME_DIR/user-home" \
  FM_STATE_OVERRIDE="$HOME_DIR/state" FM_DATA_OVERRIDE="$HOME_DIR/data" \
  FM_PROJECTS_OVERRIDE="$HOME_DIR/projects" FM_CONFIG_OVERRIDE="$HOME_DIR/config" \
  FM_SPAWN_NO_GUARD=1 FM_BACKEND=zellij FM_ZELLIJ_SESSION=live-session \
  FM_ZELLIJ_TEST_LOG="$LOG" FM_ZELLIJ_TEST_STATE="$ZSTATE" \
  PATH="$FAKEBIN_DIR:$PATH" \
  "$ROOT/bin/fm-spawn.sh" "$ID" "$PROJ_DIR" --mode no-mistakes --yolo off 2>&1)
status=$?
expect_code 1 "$status" "failed treehouse delivery should refuse the spawn: $out"
assert_grep 'action close-tab-by-id 3' "$LOG" \
  "failed pre-worker acquisition did not close the exact new Zellij tab"
[ ! -e "$ZSTATE/title" ] || fail "failed pre-worker acquisition left the Zellij tab live"
[ ! -e "$HOME_DIR/state/$ID.meta" ] || fail "failed pre-worker acquisition published task metadata"
printf 'ok - failed pre-worker acquisition closes its exact fresh Zellij tab and leaves no task record\n'
