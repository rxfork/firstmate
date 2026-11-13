#!/usr/bin/env bash
# Exercise just release with real Just/Git/JQ and a recording GitHub CLI stub.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
for tool in just jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'skip: release tests require %s\n' "$tool"
    exit 0
  fi
done
TMP_ROOT=$(fm_test_tmproot fm-release)
mkdir -p "$TMP_ROOT/bin"
export FM_RELEASE_LOG="$TMP_ROOT/release.args" FM_RELEASE_API_LOG="$TMP_ROOT/api.args"
export FM_RELEASE_REFS='' FM_RELEASE_API_STATUS=0
cat > "$TMP_ROOT/bin/gh" <<'SH'
#!/usr/bin/env bash
set -eu
case "$1" in
  api)
    printf '%s\n' "$@" > "$FM_RELEASE_API_LOG"
    [ "$FM_RELEASE_API_STATUS" -eq 0 ] || exit "$FM_RELEASE_API_STATUS"
    printf '%s\n' "$FM_RELEASE_REFS"
    ;;
  release) printf '%s\n' "$@" > "$FM_RELEASE_LOG" ;;
  *) exit 99 ;;
esac
SH
cat > "$TMP_ROOT/bin/date" <<'SH'
#!/usr/bin/env bash
set -eu
[ "$#" -eq 2 ] && [ "$1" = -u ] && [ "$2" = +%F ]
printf '2026-11-23\n'
SH
chmod +x "$TMP_ROOT/bin/gh" "$TMP_ROOT/bin/date"
git init -qb main "$TMP_ROOT/repo"
git -C "$TMP_ROOT/repo" -c core.hooksPath=/dev/null -c user.name=Fixture -c user.email=fixture@example.invalid commit --allow-empty -qm fixture
HEAD_SHA=$(git -C "$TMP_ROOT/repo" rev-parse HEAD)

run_just() {
  PATH="$TMP_ROOT/bin:$PATH" just --tempdir "$TMP_ROOT" --justfile "$ROOT/justfile" --working-directory "$TMP_ROOT/repo" "$@"
}

assert_release() {
  local tag=$1
  printf 'release\ncreate\n%s\n--target\n%s\n--title\n%s\n--generate-notes\n' "$tag" "$HEAD_SHA" "$tag" > "$TMP_ROOT/expected.args"
  cmp -s "$TMP_ROOT/expected.args" "$FM_RELEASE_LOG" || fail 'release did not use the expected tag, current commit, title and generated notes'
  assert_grep --paginate "$FM_RELEASE_API_LOG" 'release did not request all tag pages'
  assert_grep 'repos/{owner}/{repo}/git/matching-refs/tags/2026-11-23.' "$FM_RELEASE_API_LOG" 'release did not look up the current UTC date'
}

bare_just_only_lists_recipes() {
  run_just > "$TMP_ROOT/default-output"
  [ ! -e "$FM_RELEASE_LOG" ] && [ ! -e "$FM_RELEASE_API_LOG" ] || fail 'bare just contacted GitHub or created a release'
  assert_grep release "$TMP_ROOT/default-output" 'bare just did not list the release recipe'
  pass 'bare just lists recipes without publishing'
}

first_release_starts_at_one() {
  run_just release
  assert_release 2026-11-23.1
  pass 'the first daily release uses .1 and targets the current commit'
}

release_uses_numeric_maximum_not_count_or_sort_order() {
  FM_RELEASE_REFS=$'refs/tags/2026-11-23.10\nrefs/tags/2026-11-23.2\nrefs/tags/2026-11-23.1\nrefs/tags/2026-11-23.99-preview\nrefs/tags/2026-11-23.5.nested.99'
  run_just release
  assert_release 2026-11-23.11
  pass 'daily numbering increments the largest numeric suffix and ignores other tag formats'
}

api_failure_never_publishes() {
  FM_RELEASE_API_STATUS=42
  rm "$FM_RELEASE_LOG"
  if run_just release >"$TMP_ROOT/failure-output" 2>&1; then fail 'a failed GitHub lookup reported success'; fi
  [ ! -e "$FM_RELEASE_LOG" ] || fail 'a failed GitHub lookup still published a release'
  pass 'a failed tag lookup stops before publication'
}

fm_test_run bare_just_only_lists_recipes first_release_starts_at_one release_uses_numeric_maximum_not_count_or_sort_order api_failure_never_publishes
