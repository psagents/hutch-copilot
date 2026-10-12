#!/usr/bin/env bash
# Install the optional advisor skills hutch-copilot consults.
#
#   scripts/fetch-advisors.sh            clone/update, then report
#   scripts/fetch-advisors.sh --report   report only, touch nothing
#
# Advisors are consultants, not dependencies: hutch-copilot works with none of
# them installed, so a source that cannot be reached is a warning, never fatal.
#
# Sources are cloned whole into a cache and the skill directories inside them
# are symlinked into place. The layout is read off the filesystem after cloning
# rather than probed over the API -- psagents repos put SKILL.md at the root or
# under claude/skills/<name>/, and the GitHub API rate limit (60/hour for an
# unauthenticated shared IP) does not survive two probes per repo.
set -uo pipefail

DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
CACHE="${HUTCH_ADVISOR_CACHE:-$HOME/.claude/advisor-src}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SELF="hutch-copilot"

# The LCLS-II DAQ diagnostic suite lives in a subdirectory of a large repo, so
# it is cloned sparse and pinned to the branch that carries it.
LCLS2_URL="https://github.com/slac-lcls/lcls2.git"
LCLS2_REF="features/psana-daq-monitor"
LCLS2_DIR="psana/psana/skills"

REPORT_ONLY=0
[[ "${1:-}" == "--report" ]] && REPORT_ONLY=1

info() { echo "[advisors] $*"; }
warn() { echo "[advisors] WARNING: $*" >&2; }

# ── sources ────────────────────────────────────────────────────────────────

# Names echoed one per line so a failed curl yields an empty list, not a hang.
psagents_repos() {
    curl -sf "https://api.github.com/orgs/psagents/repos?per_page=100" \
        | python3 -c 'import json,sys; [print(r["name"]) for r in json.load(sys.stdin)]' \
        2>/dev/null
}

# Clone if absent, pull if present. Prints nothing; returns non-zero on failure.
sync_repo() {
    local url="$1" path="$2"
    if [[ -d "$path/.git" ]]; then
        git -C "$path" pull --quiet --ff-only
    else
        rm -rf "$path"
        git clone --quiet --depth 1 --filter=blob:none "$url" "$path"
    fi
}

sync_lcls2() {
    local path="$1"
    if [[ -d "$path/.git" ]]; then
        git -C "$path" pull --quiet --ff-only
    else
        rm -rf "$path"
        git clone --quiet --depth 1 --filter=blob:none --sparse \
            --branch "$LCLS2_REF" "$LCLS2_URL" "$path" \
            && git -C "$path" sparse-checkout set "$LCLS2_DIR"
    fi
}

# Every skill directory inside a cached checkout: root SKILL.md, or one level
# down under a skills/ parent (claude/skills/<name>, psana/psana/skills/<name>).
#
# Deduplicated by skill name, because several psagents repos ship the same skill
# twice -- claude/skills/<name> and opencode/skills/<name> are identical copies
# kept in step by the repo's own install.sh. Sorting puts claude/ first, so the
# Claude Code copy wins and the choice is not left to directory order.
skill_dirs() {
    local root="$1"
    { [[ -f "$root/SKILL.md" ]] && echo "$root"
      find "$root" -mindepth 2 -maxdepth 5 -name SKILL.md -not -path '*/.git/*' \
          -printf '%h\n' 2>/dev/null | sort
    } | awk -F/ '!seen[$NF]++'
}

# ── link ───────────────────────────────────────────────────────────────────

installed=0 relinked=0 kept=0

link_skill() {
    local src="$1" name link
    name="$(basename "$src")"
    link="$DEST/$name"
    [[ "$name" == "$SELF" ]] && return 0   # never shadow the caller

    if [[ -L "$link" ]]; then
        [[ "$(readlink -f "$link")" == "$(readlink -f "$src")" ]] && return 0
        ln -sfn "$src" "$link" && ((relinked++))
    elif [[ -e "$link" ]]; then
        ((kept++))                          # a real clone someone owns; leave it
    else
        ln -s "$src" "$link" && ((installed++))
    fi
}

# ── run ────────────────────────────────────────────────────────────────────

if (( ! REPORT_ONLY )); then
    mkdir -p "$DEST" "$CACHE"

    repos="$(psagents_repos)"
    [[ -z "$repos" ]] && warn "could not list the psagents org -- skipping it"

    for repo in $repos; do
        case "$repo" in .github|psagents.github.io|"$SELF") continue ;; esac
        if sync_repo "https://github.com/psagents/$repo.git" "$CACHE/$repo"; then
            while read -r d; do [[ -n "$d" ]] && link_skill "$d"; done \
                < <(skill_dirs "$CACHE/$repo")
        else
            warn "could not sync psagents/$repo"
        fi
    done

    if sync_lcls2 "$CACHE/lcls2"; then
        while read -r d; do [[ -n "$d" ]] && link_skill "$d"; done \
            < <(skill_dirs "$CACHE/lcls2/$LCLS2_DIR")
    else
        warn "could not sync the LCLS-II DAQ skills from lcls2@$LCLS2_REF"
    fi

    info "linked: $installed new, $relinked updated, $kept left as local copies"
    echo ""
fi

# ── report ─────────────────────────────────────────────────────────────────
# Two tiers, both derived -- nothing here restates a list kept elsewhere.
# DECLARED_ADVISORS in the test suite says which names may be cited; an `@name`
# citation in a SKILL.md says which of them a judgment actually routes through,
# and those are required. Exits 4 (UNREACHABLE) if one of those is absent.
python3 - "$REPO_ROOT" <<'PY'
import ast, json, pathlib, re, subprocess, sys

root = pathlib.Path(sys.argv[1])
test = root / "tests/unit/test_self_contained.py"
declared = set()
for node in ast.walk(ast.parse(test.read_text())):
    if isinstance(node, ast.Assign) and any(
        getattr(t, "id", "") == "DECLARED_ADVISORS" for t in node.targets
    ):
        declared = set(ast.literal_eval(node.value))

cited: dict[str, list[str]] = {}
for doc in sorted(root.glob("SKILL.md")) + sorted(root.glob("*/SKILL.md")):
    where = "/" if doc.parent == root else doc.parent.name
    for name in set(re.findall(r"@([a-z][a-z0-9-]*)", doc.read_text())):
        if name in declared:
            cited.setdefault(name, []).append(where)

found = json.loads(subprocess.run(
    [str(root / "bin/hutch-skills"), "list"],
    capture_output=True, text=True, check=True).stdout)["value"]

def show(names):
    for name in sorted(names):
        where = f"  (cited by {', '.join(sorted(cited[name]))})" if name in cited else ""
        print(f"  {'ok     ' if name in found else 'MISSING'}  {name}{where}")

print("required -- a judgment routes through these, and is refused without them:")
show(cited)
print("\navailable -- installed if present, absence is not a blocker:")
show(declared - set(cited))

extra = sorted(set(found) - declared - {"hutch-copilot"})
if extra:
    print("\ninstalled but not declared (add to DECLARED_ADVISORS to cite them):")
    print("  " + ", ".join(extra))

absent = sorted(set(cited) - set(found))
if absent:
    print(f"\nREQUIRED ADVISOR MISSING: {', '.join(absent)}")
    sys.exit(4)
PY
# $? is the heredoc's exit status: 4 when a required advisor is absent. Explicit
# because this script runs without `set -e` on purpose -- a failed clone must
# warn and let the loop continue rather than abort the whole run.
exit $?
