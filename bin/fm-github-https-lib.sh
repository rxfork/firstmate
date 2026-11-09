#!/usr/bin/env bash
# Command-scoped GitHub SSH-to-HTTPS transport for callers that own auth policy.

FM_GITHUB_HTTPS_CONFIG_KEYS=(
  credential.https://github.com.helper
  credential.https://github.com.helper
  url.https://github.com/.insteadOf
  url.https://github.com/.insteadOf
)
FM_GITHUB_HTTPS_CONFIG_VALUES=(
  ''
  '!gh auth git-credential'
  'git@github.com:'
  'ssh://git@github.com/'
)

fm_git_origin_is_github_ssh() { # <url>
  case "$1" in
    git@github.com:*|ssh://git@github.com/*) return 0 ;;
    *) return 1 ;;
  esac
}

fm_git_github_https_require_auth() {
  command -v gh >/dev/null 2>&1 || {
    echo "error: GitHub SSH origin requires the 'gh' CLI for command-scoped HTTPS authentication" >&2
    return 1
  }
  gh auth status --hostname github.com >/dev/null 2>&1 || {
    echo "error: GitHub SSH origin requires an active 'gh auth login' for github.com; refusing to fall back to SSH" >&2
    return 1
  }
}

fm_git_github_https_git() { # <git args...>
  local args=() i
  for i in "${!FM_GITHUB_HTTPS_CONFIG_KEYS[@]}"; do
    args+=(
      -c "${FM_GITHUB_HTTPS_CONFIG_KEYS[$i]}=${FM_GITHUB_HTTPS_CONFIG_VALUES[$i]}"
    )
  done
  git "${args[@]}" "$@"
}

fm_git_github_https_env() { # <command> [args...]
  local env_args=("GIT_CONFIG_COUNT=${#FM_GITHUB_HTTPS_CONFIG_KEYS[@]}") i
  for i in "${!FM_GITHUB_HTTPS_CONFIG_KEYS[@]}"; do
    env_args+=(
      "GIT_CONFIG_KEY_$i=${FM_GITHUB_HTTPS_CONFIG_KEYS[$i]}"
      "GIT_CONFIG_VALUE_$i=${FM_GITHUB_HTTPS_CONFIG_VALUES[$i]}"
    )
  done
  env "${env_args[@]}" "$@"
}

fm_git_github_https_shell_prefix() {
  local out="env GIT_CONFIG_COUNT=${#FM_GITHUB_HTTPS_CONFIG_KEYS[@]}" i
  for i in "${!FM_GITHUB_HTTPS_CONFIG_KEYS[@]}"; do
    printf -v out '%s GIT_CONFIG_KEY_%d=%q GIT_CONFIG_VALUE_%d=%q' \
      "$out" "$i" "${FM_GITHUB_HTTPS_CONFIG_KEYS[$i]}" \
      "$i" "${FM_GITHUB_HTTPS_CONFIG_VALUES[$i]}"
  done
  printf '%s' "$out"
}

fm_git_github_https_unset_command() {
  local sh_names='GIT_CONFIG_COUNT' fish_commands='' i
  for i in "${!FM_GITHUB_HTTPS_CONFIG_KEYS[@]}"; do
    sh_names+=" GIT_CONFIG_KEY_$i GIT_CONFIG_VALUE_$i"
    fish_commands+="set -e GIT_CONFIG_KEY_$i; set -e GIT_CONFIG_VALUE_$i; "
  done
  printf "test -n \"\$FISH_VERSION\" && eval 'set -e GIT_CONFIG_COUNT; %strue' || eval 'unset %s'" \
    "$fish_commands" "$sh_names"
}

fm_git_with_origin_transport() { # <repo> <git args...>
  local repo=$1 origin
  shift
  origin=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
  if fm_git_origin_is_github_ssh "$origin"; then
    fm_git_github_https_git -C "$repo" "$@"
  else
    git -C "$repo" "$@"
  fi
}
