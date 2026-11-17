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

repo="$TMP_ROOT/project"
gate="$TMP_ROOT/gate.git"
linked="$TMP_ROOT/linked"
git init -q "$repo"
git -C "$repo" -c user.name=Test -c user.email=test@example.com commit -qm seed --allow-empty
git init -q --bare "$gate"
git -C "$repo" remote add origin git@github.com:example/project.git
git -C "$repo" remote add no-mistakes "$gate"
git -C "$gate" remote add origin ssh://git@github.com/example/project.git
git -C "$repo" config core.hooksPath /unchanged/hooks
git -C "$repo" config credential.https://example.com.helper other-helper
git -C "$repo" config --add url.https://github.com/.insteadOf preserved-prefix:
git -C "$repo" worktree add -q "$linked" -b linked
git -C "$repo" config extensions.worktreeConfig true
git -C "$linked" config --worktree core.hooksPath /unchanged/worktree-hooks
bash "$ROOT/bin/fm-github-https.sh" "$linked" || fail "linked-worktree repair failed"
for dir in "$repo" "$linked" "$gate"; do
  helpers=$(git -C "$dir" config --get-all credential.https://github.com.helper)
  [ "$helpers" = $'\n!gh auth git-credential' ] || fail "helper missing from $dir"
  [ "$(git -C "$dir" remote get-url origin)" = https://github.com/example/project.git ] || fail "SSH URL not rewritten in $dir"
done
[ "$(git -C "$repo" config --get remote.origin.url)" = git@github.com:example/project.git ] || fail "raw origin changed"
[ "$(git -C "$repo" config core.hooksPath)" = /unchanged/hooks ] || fail "hooks changed"
[ "$(git -C "$linked" config core.hooksPath)" = /unchanged/worktree-hooks ] || fail "worktree hooks changed"
[ "$(git -C "$repo" config credential.https://example.com.helper)" = other-helper ] || fail "other host helper changed"
before=$(git -C "$repo" config --local --list)
bash "$ROOT/bin/fm-github-https.sh" "$repo" || fail "second repair failed"
[ "$(git -C "$repo" config --local --list)" = "$before" ] || fail "repair is not idempotent"
pass "repair configures primary, linked and separate bare repositories without changing URLs or hooks"

# A fresh Git process, without acquisition env, must select HTTPS for reads and
# pushes; a fake HTTPS remote helper prevents any network or credential access.
cat > "$FAKEBIN/git-remote-https" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FM_TEST_TRANSPORT_LOG"
exit 1
SH
cat > "$FAKEBIN/deny-ssh" <<'SH'
#!/usr/bin/env bash
printf 'SSH invoked\n' >> "$FM_TEST_TRANSPORT_LOG"
exit 1
SH
chmod +x "$FAKEBIN/git-remote-https" "$FAKEBIN/deny-ssh"
export FM_TEST_TRANSPORT_LOG="$TMP_ROOT/transport.log"
: > "$FM_TEST_TRANSPORT_LOG"
for dir in "$repo" "$linked"; do
  PATH="$FAKEBIN:$PATH" GIT_EXEC_PATH="$FAKEBIN" GIT_SSH_COMMAND="$FAKEBIN/deny-ssh" \
    git -C "$dir" ls-remote origin >/dev/null 2>&1 && fail "fake transport unexpectedly succeeded"
done
PATH="$FAKEBIN:$PATH" GIT_EXEC_PATH="$FAKEBIN" GIT_SSH_COMMAND="$FAKEBIN/deny-ssh" \
  git --git-dir="$gate" fetch --no-tags --no-write-fetch-head origin \
  +refs/heads/main:refs/no-mistakes/pi-profile/fixture >/dev/null 2>&1 && fail "fake trusted-branch fetch unexpectedly succeeded"
git -C "$repo" config remote.origin.pushurl ssh://git@github.com/example/push.git
PATH="$FAKEBIN:$PATH" GIT_EXEC_PATH="$FAKEBIN" GIT_SSH_COMMAND="$FAKEBIN/deny-ssh" \
  git -C "$repo" push origin HEAD >/dev/null 2>&1 && fail "fake push unexpectedly succeeded"
assert_not_contains "$(< "$FM_TEST_TRANSPORT_LOG")" 'SSH invoked' "repair still selected SSH"
[ "$(wc -l < "$FM_TEST_TRANSPORT_LOG")" -eq 4 ] || fail "not every read/push selected fake HTTPS transport"
assert_contains "$(< "$FM_TEST_TRANSPORT_LOG")" 'https://github.com/example/push.git' "push URL did not use HTTPS"
pass "fresh Git readers and pushes use HTTPS without shell transport overrides"

git -C "$repo" remote set-url origin https://github.com/example/project.git
fm_git_with_origin_transport "$repo" config --get-all credential.https://github.com.helper >/dev/null || fail "HTTPS origin wrapper failed"
other="$TMP_ROOT/other"
git init -q "$other"
git -C "$other" remote add origin ssh://git@example.com/example/project.git
before=$(git -C "$other" config --local --list)
bash "$ROOT/bin/fm-github-https.sh" "$other" || fail "non-GitHub repair failed"
[ "$(git -C "$other" config --local --list)" = "$before" ] || fail "non-GitHub config changed"
pass "non-GitHub repositories keep their existing configuration"

# Credential lookup runs in the independent bare repository, not its caller shell.
cat > "$FAKEBIN/gh" <<'SH'
#!/usr/bin/env bash
[ "$1 $2" = "auth git-credential" ] || exit 1
printf '%s\n' "$3" >> "$FM_TEST_CREDENTIAL_LOG"
printf 'username=fixture\npassword=fixture\n'
SH
export FM_TEST_CREDENTIAL_LOG="$TMP_ROOT/credentials.log"
out=$(printf 'protocol=https\nhost=github.com\n\n' | PATH="$FAKEBIN:$PATH" GIT_TERMINAL_PROMPT=0 git -C "$gate" credential fill)
[ "$(< "$FM_TEST_CREDENTIAL_LOG")" = get ] || fail "bare repository did not call gh credential helper"
assert_contains "$out" username=fixture "credential result did not come from gh"
scopes=$(fm_git_with_origin_transport "$repo" config --show-scope --get-all credential.https://github.com.helper)
assert_contains "$scopes" $'command\t!gh auth git-credential' "HTTPS origin bypassed command-scoped helper"
pass "bare repository credentials and HTTPS-origin command authentication use gh"
