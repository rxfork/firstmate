default:
    @just --justfile "{{ justfile() }}" --list

# Release the current commit as YYYY-MM-DD.N, using today's UTC date.
release:
    #!/usr/bin/env bash
    set -euo pipefail
    day=$(date -u +%F)
    next=$(gh api --paginate "repos/{owner}/{repo}/git/matching-refs/tags/$day." --jq '.[].ref' |
        jq --raw-input --slurp --arg prefix "refs/tags/$day." '
            [split("\n")[] | ltrimstr($prefix) | select(test("^[0-9]+$")) | tonumber]
            | (max // 0) + 1
        ')
    tag="$day.$next"
    gh release create "$tag" --target "$(git rev-parse HEAD)" --title "$tag" --generate-notes
