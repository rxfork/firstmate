#!/usr/bin/env bash
# GitHub HTTPS transport for callers that own auth policy.
# fm_git_github_https_configure installs operator-owned, repository-local policy
# in the common config (shared by linked worktrees) and an existing local bare
# no-mistakes remote. No remote URLs, hooks or global configuration are changed.
# Separately created repositories must be configured again after initialization.
# The gh executable must be available in each Git caller's PATH; an already
# running daemon does not inherit the invoking shell's credentials or environment.

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

fm_git_url_is_github() { # <url>
  case "$1" in
    https://github.com/*|git@github.com:*|ssh://git@github.com/*) return 0 ;;
    *) return 1 ;;
  esac
}

fm_git_repo_uses_github() { # <repo>
  local repo=$1 urls url
  urls=$(git -C "$repo" config --get-regexp '^remote\..*\.(url|pushurl)$' 2>/dev/null || true)
  while IFS= read -r url; do
    fm_git_url_is_github "${url#* }" && return 0
  done <<< "$urls"
  return 1
}

fm_git_github_https_configure_repo() { # <repo>
  local repo=$1 url key current expected i
  git -C "$repo" rev-parse --git-dir >/dev/null || return 1
  fm_git_repo_uses_github "$repo" || return 0
  # These helpers are executable operator policy, never copied from project files.
  key=credential.https://github.com.helper
  current=$(git -C "$repo" config --local --get-all "$key" || true)
  expected=$'\n!gh auth git-credential'
  if [ "$current" != "$expected" ]; then
    git -C "$repo" config --local --replace-all "$key" '' || return 1
    git -C "$repo" config --local --add "$key" '!gh auth git-credential' || return 1
  fi
  key=url.https://github.com/.insteadOf
  for i in 2 3; do
    url=${FM_GITHUB_HTTPS_CONFIG_VALUES[$i]}
    current=$(git -C "$repo" config --local --get-all "$key" || true)
    case $'\n'"$current"$'\n' in
      *$'\n'"$url"$'\n'*) ;;
      *) git -C "$repo" config --local --add "$key" "$url" || return 1 ;;
    esac
  done
}

fm_git_github_https_configure() { # <repo>
  local repo=$1 gate
  fm_git_github_https_configure_repo "$repo" || return 1
  fm_git_repo_uses_github "$repo" || return 0
  # Use the raw URL: get-url applies insteadOf, and a gate is a separate repo.
  gate=$(git -C "$repo" config --get remote.no-mistakes.url 2>/dev/null || true)
  case "$gate" in
    '') return 0 ;;
    file:///*) gate=${gate#file://} ;;
    /*) ;;
    *://*|*:*) return 0 ;; # Never traverse or mutate a network remote.
    *) gate="$repo/$gate" ;;
  esac
  [ "$(git -C "$gate" rev-parse --is-bare-repository 2>/dev/null)" = true ] || {
    echo "error: no-mistakes remote is not an accessible local bare repository: $gate" >&2
    return 1
  }
  fm_git_github_https_configure_repo "$gate"
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
  origin=$(git -C "$repo" config --get remote.origin.url 2>/dev/null || true)
  if fm_git_url_is_github "$origin"; then
    fm_git_github_https_git -C "$repo" "$@"
  else
    git -C "$repo" "$@"
  fi
}
