#!/usr/bin/env bash
# Install the advisor skills hutch-copilot consults. bin/hutch-setup runs it, then links
# every advisor into each Claude config.
#
#   scripts/fetch-advisors.sh            clone/update, then report
#   scripts/fetch-advisors.sh --report   report only, touch nothing
#
# A source that cannot be reached is a warning, never fatal: the advisors a SKILL.md cites
# are required (their judgments are refused without them, exit 4), the rest are optional.
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
# A root SKILL.md makes the whole repo one skill: ask-lute's bayfai/, smalldata/
# and cctbx/ are its sub-skills, not skills to link beside it.
#
# Deduplicated by skill name, because several psagents repos ship the same skill
# twice -- claude/skills/<name> and opencode/skills/<name> are identical copies
# kept in step by the repo's own install.sh. Sorting puts claude/ first, so the
# Claude Code copy wins and the choice is not left to directory order.
skill_dirs() {
    local root="$1"
    if [[ -f "$root/SKILL.md" ]]; then echo "$root"; return; fi
    find "$root" -mindepth 2 -maxdepth 5 -name SKILL.md -not -path '*/.git/*' \
        -printf '%h\n' 2>/dev/null | sort | awk -F/ '!seen[$NF]++'
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
# Two tiers, both derived -- nothing here restates a list kept elsewhere. ADVISORS
# (src/hutch_copilot/tools/skills.py) says which names may be cited; an `@name`
# citation in a SKILL.md says which of them a judgment actually routes through,
# and those are required. Exits 4 (UNREACHABLE) if one of those is absent.
python3 - "$REPO_ROOT" <<'PY'
import pathlib, sys

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from hutch_copilot.tools.skills import ADVISORS, required, skills

cited = required()
found = skills()["value"]
ours = {"hutch-copilot", *(p.name for p in (root / "skills").iterdir())}

def show(names):
    for name in sorted(names):
        where = f"  (cited by {', '.join(cited[name])})" if name in cited else ""
        print(f"  {'ok     ' if name in found else 'MISSING'}  {name}{where}")

print("required -- a judgment routes through these, and is refused without them:")
show(cited)
print("\navailable -- installed if present, absence is not a blocker:")
show(ADVISORS - set(cited))

extra = sorted(set(found) - ADVISORS - ours)
if extra:
    print("\ninstalled but not declared (add to ADVISORS in tools/skills.py to cite them):")
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
