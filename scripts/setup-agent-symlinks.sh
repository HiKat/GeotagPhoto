#!/usr/bin/env bash
set -euo pipefail

force=0

usage() {
    cat <<'USAGE'
Usage: scripts/setup-agent-symlinks.sh [--force]

Creates these symbolic links:
  .agents/skills -> ../.github/skills
  AGENTS.md      -> .github/copilot-instructions.md

Options:
  -f, --force   Replace an existing non-directory file or symlink.
  -h, --help    Show this help.
USAGE
}

while (($#)); do
    case "$1" in
        -f|--force)
            force=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

same_link_target() {
    local link_path="$1"
    local expected_target="$2"
    local expected_source="$3"
    local actual_target
    local candidate_target
    local resolved_target
    local resolved_source

    [[ -L "$link_path" ]] || return 1

    actual_target="$(readlink -- "$link_path")"
    [[ "$actual_target" == "$expected_target" ]] && return 0

    if [[ "$actual_target" = /* ]]; then
        candidate_target="$actual_target"
    else
        candidate_target="$(dirname -- "$link_path")/$actual_target"
    fi

    resolved_target="$(readlink -f -- "$candidate_target" 2>/dev/null || true)"
    resolved_source="$(readlink -f -- "$repo_root/$expected_source" 2>/dev/null || true)"

    [[ -n "$resolved_target" && "$resolved_target" == "$resolved_source" ]]
}

create_repo_symlink() {
    local link_path="$1"
    local source_path="$2"
    local link_target="$3"
    local kind="$4"

    local absolute_link="$repo_root/$link_path"
    local absolute_source="$repo_root/$source_path"
    local parent

    parent="$(dirname -- "$absolute_link")"

    case "$kind" in
        directory)
            [[ -d "$absolute_source" ]] || {
                echo "Source does not exist or is not a directory: $source_path" >&2
                exit 1
            }
            ;;
        file)
            [[ -f "$absolute_source" ]] || {
                echo "Source does not exist or is not a file: $source_path" >&2
                exit 1
            }
            ;;
        *)
            echo "Unsupported link kind: $kind" >&2
            exit 2
            ;;
    esac

    mkdir -p -- "$parent"

    if [[ -e "$absolute_link" || -L "$absolute_link" ]]; then
        if same_link_target "$absolute_link" "$link_target" "$source_path"; then
            echo "Already linked: $link_path -> $link_target"
            return
        fi

        if ((force == 0)); then
            echo "Target already exists and is not the expected symbolic link: $link_path. Re-run with --force to replace it." >&2
            exit 1
        fi

        if [[ -d "$absolute_link" && ! -L "$absolute_link" ]]; then
            echo "Refusing to remove an existing directory: $link_path. Remove or rename it manually, then re-run this script." >&2
            exit 1
        fi

        rm -f -- "$absolute_link"
    fi

    ln -s -- "$link_target" "$absolute_link"
    echo "Linked: $link_path -> $link_target"
}

create_repo_symlink ".agents/skills" ".github/skills" "../.github/skills" "directory"
create_repo_symlink "AGENTS.md" ".github/copilot-instructions.md" ".github/copilot-instructions.md" "file"
