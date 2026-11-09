#!/usr/bin/env bash
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-github-https-lib.sh
. "$ROOT/bin/fm-github-https-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-github-https-lib)
FAKEBIN="$TMP_ROOT/fakebin"
mkdir -p "$FAKEBIN"

fail() { printf 'not ok - %s\n' "$1" >&2; exit 1; }
pass() { printf 'ok - %s\n' "$1"; }

fm_git_origin_is_github_ssh git@github.com:owner/repo.git || fail "scp-style GitHub SSH URL was not recognized"
fm_git_origin_is_github_ssh ssh://git@github.com/owner/repo.git || fail "URL-style GitHub SSH URL was not recognized"
if fm_git_origin_is_github_ssh ssh://git@example.com/owner/repo.git; then
  fail "non-GitHub SSH URL was recognized as GitHub"
fi
pass "GitHub SSH origin matching is host-scoped"

cat > "$FAKEBIN/probe-env" <<'SH'
#!/usr/bin/env bash
for i in 0 1 2 3; do
  eval "printf 'key%s=%s\\n' \"\$i\" \"\${GIT_CONFIG_KEY_$i-}\""
  eval "printf 'value%s=%s\\n' \"\$i\" \"\${GIT_CONFIG_VALUE_$i-}\""
done
printf 'count=%s\n' "${GIT_CONFIG_COUNT-}"
SH
chmod +x "$FAKEBIN/probe-env"
expected=$'key0=credential.https://github.com.helper\nvalue0=\nkey1=credential.https://github.com.helper\nvalue1=!gh auth git-credential\nkey2=url.https://github.com/.insteadOf\nvalue2=git@github.com:\nkey3=url.https://github.com/.insteadOf\nvalue3=ssh://git@github.com/\ncount=4'
out=$(PATH="$FAKEBIN:$PATH" fm_git_github_https_env probe-env)
[ "$out" = "$expected" ] || fail "command environment did not contain the bounded GitHub HTTPS config"
prefix=$(fm_git_github_https_shell_prefix)
out=$(PATH="$FAKEBIN:$PATH" /bin/bash -c "$prefix probe-env")
[ "$out" = "$expected" ] || fail "shell prefix did not reproduce the bounded GitHub HTTPS config"
helpers=$(fm_git_github_https_env git config --get-all credential.https://github.com.helper)
[ "$helpers" = $'\n!gh auth git-credential' ] || fail "Git did not parse both command-scoped credential helpers"
rewrites=$(fm_git_github_https_env git config --get-all url.https://github.com/.insteadOf)
[ "$rewrites" = $'git@github.com:\nssh://git@github.com/' ] || fail "Git did not parse both command-scoped URL rewrites"
pass "direct and pane-shell transport configuration agree and Git consumes every entry"

unset_line=$(fm_git_github_https_unset_command)
out=$(env GIT_CONFIG_COUNT=4 GIT_CONFIG_KEY_0=x GIT_CONFIG_VALUE_0=y \
  /bin/bash -c "$unset_line; printf '%s:%s:%s' \"\${GIT_CONFIG_COUNT-unset}\" \"\${GIT_CONFIG_KEY_0-unset}\" \"\${GIT_CONFIG_VALUE_0-unset}\"")
[ "$out" = unset:unset:unset ] || fail "acquisition-only Git configuration was not cleared in bash"
if command -v fish >/dev/null 2>&1; then
  out=$(env GIT_CONFIG_COUNT=4 GIT_CONFIG_KEY_0=x GIT_CONFIG_VALUE_0=y \
    fish --no-config -c "$unset_line; if set -q GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0; printf set; else; printf unset; end")
  [ "$out" = unset ] || fail "acquisition-only Git configuration was not cleared in fish"
fi
pass "acquisition-only Git configuration has a bash/fish-safe unset command"

cat > "$FAKEBIN/gh" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = auth ] && [ "${2:-}" = status ] && exit "${FM_FAKE_GH_STATUS:-0}"
exit 0
SH
chmod +x "$FAKEBIN/gh"
PATH="$FAKEBIN:$PATH" fm_git_github_https_require_auth || fail "valid gh auth was refused"
out=$(PATH="$FAKEBIN:$PATH" FM_FAKE_GH_STATUS=1 fm_git_github_https_require_auth 2>&1)
status=$?
[ "$status" -ne 0 ] || fail "missing gh auth was accepted"
assert_contains "$out" "refusing to fall back to SSH" "missing-auth refusal did not rule out SSH fallback"
pass "GitHub SSH transport requires an active gh login"
