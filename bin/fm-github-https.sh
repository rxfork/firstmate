#!/usr/bin/env bash
# Install/repair repository-local GitHub HTTPS routing and gh authentication.
# Usage: bash bin/fm-github-https.sh <repository>
# Includes an existing local bare no-mistakes remote. Run again after no-mistakes
# init creates/replaces it. Safe to repeat; does not fetch, restart services,
# change remote URLs, modify hooks, or write global authentication configuration.
# GitHub-specific credential helpers are replaced with a reset and gh's helper.
# Other hosts and existing URL rewrites are preserved. Only use on trusted repos.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bin/fm-github-https-lib.sh
. "$SCRIPT_DIR/fm-github-https-lib.sh"
if [ "$#" != 1 ] || [ "$1" = --help ]; then
  echo "usage: bash $0 <repository>"
  [ "${1:-}" = --help ] && exit 0
  exit 2
fi
fm_git_github_https_configure "$1"
