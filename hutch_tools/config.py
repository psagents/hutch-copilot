"""LUTE config rendering with overrides and validation -- Categories B and C.

Replaces the campaign's ``render_iter.py``: 250 lines of per-campaign YAML text
surgery that existed only because ``setup-cctbx-pipeline/render.py`` has no way
to override a single parameter. One tested implementation instead of a copy per
campaign.

Three things it does that hand-editing a YAML cannot:

  1. **Reports what each override actually did** -- ``replaced`` or ``inserted``.
     An override you believed was changing a value but which silently inserted a
     new key means you misspelled the key. Without this report that is invisible.
  2. **Refuses keys the phil template would silently drop.** ``phil_parameters``
     is pydantic ``extra='allow'``, so LUTE accepts anything and the fixed jinja
     template then ignores what it does not reference. The legal key set is
     discovered from the template the config itself names in ``phil_file`` --
     not from a hardcoded list that would go stale.
  3. **Runs the incident register** over the result, and re-parses it, so a
     surgery bug fails here rather than ten minutes into a job.

Comments in the rendered YAML are load-bearing (they record why step_list
excludes 'modify', why the radial-average two-theta range is what it is), so
overrides are applied as line edits rather than by round-tripping through a
YAML parser, which would drop them.

Requires jinja2 and PyYAML; both are in the psana conda environment. The rest
of hutch_tools is stdlib-only.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from ._cli import UNREACHABLE, fail, out
from .paths import skill_root
from .traps import check as check_traps
from .traps import template_whitelist

#: Sentinel: remove the key entirely rather than setting a value.
DELETE = "__DELETE__"


class ConfigError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# YAML block surgery
# --------------------------------------------------------------------------
def _task_span(lines: list[str], task: str) -> tuple[int, int]:
    """[start, end) of a top-level ``task:`` block."""
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{task}:")), None)
    if start is None:
        raise ConfigError(
            f"task block {task!r} not found in the rendered YAML. "
            f"Present at top level: {sorted(_top_level_keys(lines))}"
        )
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t")):
            return start, i
    return start, len(lines)


def _top_level_keys(lines: list[str]) -> set[str]:
    return {
        m.group(1)
        for m in (re.match(r"^([A-Za-z_]\w*):", ln) for ln in lines)
        if m
    }


def _sub_span(lines: list[str], lo: int, hi: int, sub: str) -> tuple[int, int, int]:
    """[start, end) of a nested block, plus the indent its children use."""
    start = next((i for i in range(lo, hi) if lines[i].strip() == f"{sub}:"), None)
    if start is None:
        raise ConfigError(f"sub-block {sub!r} not found in lines {lo}..{hi}")
    parent_indent = len(lines[start]) - len(lines[start].lstrip())
    child_indent = parent_indent + 2
    for i in range(start + 1, hi):
        if not lines[i].strip():
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= parent_indent:
            return start, i, child_indent
        child_indent = min(child_indent, indent) if i == start + 1 else child_indent
    return start, hi, child_indent


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _find_key(lines: list[str], lo: int, hi: int, key: str, indent: int) -> tuple[int, int] | None:
    """Locate ``key:`` at ``indent``, swallowing any block-list continuation."""
    pat = re.compile(rf"^{' ' * indent}{re.escape(key)}:")
    for i in range(lo, hi):
        if pat.match(lines[i]):
            end = i + 1
            while end < hi and lines[end].strip():
                if len(lines[end]) - len(lines[end].lstrip()) > indent:
                    end += 1
                else:
                    break
            return i, end
    return None


def apply_override(lines: list[str], dotted: str, value: Any) -> tuple[list[str], str]:
    """Apply ``TASK.SUBBLOCK.key = value``. Returns (lines, action).

    ``action`` is ``replaced``, ``inserted``, ``deleted`` or ``absent``. Callers
    must surface it: an ``inserted`` where you expected ``replaced`` is how a
    misspelled key announces itself.
    """
    try:
        task, sub, key = dotted.split(".", 2)
    except ValueError:
        raise ConfigError(
            f"override {dotted!r} must be TASK.SUBBLOCK.key (e.g. "
            f"IndexCCTBXXFEL.phil_parameters.dispatch_index)"
        ) from None

    t_lo, t_hi = _task_span(lines, task)
    s_lo, s_hi, indent = _sub_span(lines, t_lo, t_hi, sub)
    found = _find_key(lines, s_lo + 1, s_hi, key, indent)

    if value == DELETE:
        if found is None:
            return lines, "absent"
        a, b = found
        return lines[:a] + lines[b:], "deleted"

    pad = " " * indent
    if isinstance(value, list):
        new = [f"{pad}{key}:\n"] + [f"{pad}  - {v}\n" for v in value]
    else:
        new = [f"{pad}{key}: {_fmt(value)}\n"]

    if found is not None:
        a, b = found
        return lines[:a] + new + lines[b:], "replaced"

    ins = s_hi
    while ins > s_lo + 1 and not lines[ins - 1].strip():
        ins -= 1
    return lines[:ins] + new + lines[ins:], "inserted"


# --------------------------------------------------------------------------
# Whitelist discovery
# --------------------------------------------------------------------------
def phil_templates(yaml_text: str) -> dict[str, str]:
    """Map each task block to the phil template basename it names.

    Read from the config's own ``phil_file``, so there is no hardcoded
    task -> template table to go stale.
    """
    out: dict[str, str] = {}
    for m in re.finditer(r"^([A-Za-z_]\w*):$", yaml_text, re.M):
        task = m.group(1)
        rest = yaml_text[m.end():]
        nxt = re.search(r"^[A-Za-z_]\w*:$", rest, re.M)
        block = rest[: nxt.start()] if nxt else rest
        pf = re.search(r"phil_file:\s*\"([^\"]+)\"", block)
        if pf:
            out[task] = pathlib.PurePosixPath(pf.group(1)).name
    return out


def whitelist_for(yaml_text: str, phil_dir: str | pathlib.Path) -> dict[str, set[str]]:
    """Legal phil keys per task, from the LUTE templates on disk."""
    phil_dir = pathlib.Path(phil_dir)
    if not phil_dir.is_dir():
        raise ConfigError(
            f"phil template directory not found: {phil_dir}. "
            f"Point --whitelist-dir at {{LUTE_PATH}}/config/templates, or pass "
            f"--no-whitelist to skip the check and accept that an unknown key "
            f"will be silently ignored."
        )
    out: dict[str, set[str]] = {}
    for task, basename in phil_templates(yaml_text).items():
        path = phil_dir / basename
        if not path.exists():
            raise ConfigError(f"{task} names phil_file {basename!r}, absent from {phil_dir}")
        out[task] = template_whitelist(path.read_text())
    return out


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
#: Delimiter schemes.
#:
#: ``lute`` uses ``${{ }}`` for variables and ``$% %`` for blocks, so that
#: LUTE's own native ``{{ work_dir }}`` and ``{{ run:04d }}`` placeholders pass
#: through the render untouched and are substituted later at task runtime. This
#: is the default, and it is what the vendored templates use: rendering a LUTE
#: config with standard delimiters fails on ``{{ run:04d }}``, which is not valid
#: jinja.
DELIMITERS = {
    "lute": {
        "variable_start_string": "${{",
        "variable_end_string": "}}",
        "block_start_string": "$%",
        "block_end_string": "%",
        "comment_start_string": "{##",
        "comment_end_string": "##}",
    },
    "standard": {},
}

#: Templates ship with this repo, so rendering a config needs nothing outside it.
#: They previously lived in a separate `setup-cctbx-pipeline` skill; when that was
#: removed, `hutch-config` had no template to render at all.
TEMPLATE_DIR_NAME = "templates"
DEFAULT_TEMPLATE = "cctbx_pipeline"


def template_dir() -> pathlib.Path:
    return skill_root() / TEMPLATE_DIR_NAME


def available_templates() -> list[str]:
    d = template_dir()
    return sorted({p.name.split(".")[0] for p in d.glob("*.j2")}) if d.is_dir() else []


def resolve_template(name_or_path: str | pathlib.Path) -> pathlib.Path:
    """Accept a filesystem path, or a bare name resolved against templates/.

    A bare name keeps callers from hardcoding a path that can move -- the same
    reason ``paths.skill_root()`` resolves itself rather than being written out.
    """
    text = str(name_or_path)
    p = pathlib.Path(text)
    if p.suffix or "/" in text:
        if not p.exists():
            raise ConfigError(f"template not found: {p}")
        return p
    for candidate in (template_dir() / f"{text}.yaml.j2", template_dir() / f"{text}.j2"):
        if candidate.exists():
            return candidate
    raise ConfigError(
        f"no template named {text!r} in {template_dir()}. "
        f"Available: {', '.join(available_templates()) or '(none)'}"
    )


def render_template(
    template_path: str | pathlib.Path,
    context: dict,
    delims: str = "lute",
) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "jinja2 is required to render a template; source psconda.sh first"
        ) from exc
    if delims not in DELIMITERS:
        raise ConfigError(f"delims must be one of {sorted(DELIMITERS)}, got {delims!r}")
    path = pathlib.Path(template_path)
    if not path.exists():
        raise ConfigError(f"template not found: {path}")
    env = Environment(
        loader=FileSystemLoader(str(path.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        **DELIMITERS[delims],
    )
    try:
        return env.get_template(path.name).render(**context)
    except Exception as exc:
        hint = ""
        if delims == "lute" and "{{" in path.read_text():
            hint = (
                "\n  hint: with --delims lute, template variables are written ${{ x }}; "
                "plain {{ x }} is passed through for LUTE to substitute at runtime."
            )
        raise ConfigError(f"template render failed: {exc}{hint}") from None


def parse_documents(yaml_text: str) -> list[dict]:
    """Parse the rendered config, tolerating the %YAML directive.

    LUTE's runtime placeholders (``{{ work_dir }}``, ``{{ run:04d }}``) are
    inside quotes, so they survive a parse unharmed.
    """
    try:
        import yaml as _yaml
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("PyYAML is required; source psconda.sh first") from exc
    stripped = re.sub(r"^%YAML [\d.]+\n", "", yaml_text)
    try:
        return [d for d in _yaml.safe_load_all(stripped) if d is not None]
    except Exception as exc:
        raise ConfigError(f"rendered config is not valid YAML: {exc}") from None


def build(
    *,
    template: str | pathlib.Path,
    context: dict,
    overrides: dict[str, Any] | None = None,
    whitelist_dir: str | pathlib.Path | None = None,
    delims: str = "lute",
) -> dict:
    """Render, override, validate. Returns a report; raises on any violation.

    The report lists every override with what it did, so a misspelled key that
    quietly became a new entry is visible instead of looking like a null result.
    """
    overrides = overrides or {}
    text = render_template(resolve_template(template), context, delims=delims)

    lines = text.splitlines(keepends=True)
    actions: dict[str, str] = {}
    for dotted, value in overrides.items():
        lines, action = apply_override(lines, dotted, value)
        actions[dotted] = action
    text = "".join(lines)

    docs = parse_documents(text)

    violations: list[dict] = list(check_traps("lute-yaml", {"yaml_text": text}))

    if whitelist_dir is not None:
        allowed = whitelist_for(text, whitelist_dir)
        for task, keys in allowed.items():
            scoped = {
                k: v for k, v in overrides.items()
                if k.startswith(f"{task}.") and v != DELETE
            }
            violations += check_traps("phil-overrides", {"whitelist": keys, "overrides": scoped})
        # Also check what the template itself emitted, not only our overrides:
        # a context key that no template references is equally a silent no-op.
        for task, keys in allowed.items():
            block = next((d[task] for d in docs if isinstance(d, dict) and task in d), None)
            if isinstance(block, dict) and isinstance(block.get("phil_parameters"), dict):
                unknown = sorted(set(block["phil_parameters"]) - keys)
                if unknown:
                    violations.append({
                        "id": "phil-key-silently-dropped",
                        "problem": f"{task}.phil_parameters contains keys the template "
                                   f"does not reference: {', '.join(unknown)}",
                        "evidence": "rendered config vs the phil template it names in phil_file",
                    })

    if violations:
        detail = "\n".join(f"  [{v['id']}] {v['problem']}" for v in violations)
        raise ConfigError(f"config failed validation:\n{detail}")

    inserted = [k for k, a in actions.items() if a == "inserted"]
    return {
        "text": text,
        "documents": len(docs),
        "overrides": actions,
        "inserted_keys": inserted,
        "tasks": sorted(phil_templates(text)),
        "whitelist_checked": whitelist_dir is not None,
        "delims": delims,
    }


def write(report: dict, out_path: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(report["text"])
    return p


def parse_set(pairs: list[str]) -> dict[str, Any]:
    """Parse ``--set K=V`` pairs. V is JSON when parseable, else a string."""
    out: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ConfigError(f"--set expects KEY=VALUE, got {pair!r}")
        key, _, raw = pair.partition("=")
        if raw == DELETE:
            out[key.strip()] = DELETE
            continue
        try:
            out[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            out[key.strip()] = raw
    return out


# --------------------------------------------------------------------------
# CLI
#
# The parser lives in lute.py (`hutch-lute render`), not here. This module is
# the only one with third-party dependencies -- jinja2 and PyYAML -- so lute
# imports it lazily, inside the one branch that needs it, and `hutch-lute
# sbatch` stays stdlib-only. Keeping a parser here too would mean two places to
# add a flag; `run` takes the namespace lute already parsed.
# --------------------------------------------------------------------------
def add_arguments(p: "argparse.ArgumentParser") -> None:
    """Declare `hutch-lute render`'s flags on an existing parser.

    Callable without importing jinja2 or PyYAML: every default that would need
    a constant from this module is left as None and resolved in ``run``, so
    lute's parser construction stays free of the heavy imports.
    """
    p.add_argument("--template", default=None,
                   help=f"template name from this repo's templates/ (default: "
                        f"{DEFAULT_TEMPLATE}) or a path to one")
    p.add_argument("--list-templates", action="store_true",
                   help="print the templates shipped with this repo and exit")
    p.add_argument("--context", default=None, help="JSON file of template context")
    # action="extend", not the default "store": with plain nargs="*" a second
    # --set silently REPLACED the first, so `--set a=1 --set b=2` applied only
    # b=2 and dropped a=1 with no diagnostic. That is the exact silent-drop
    # failure --whitelist-dir exists to catch, occurring inside the tool meant
    # to catch it. extend accumulates every occurrence instead.
    p.add_argument("--set-context", nargs="*", action="extend", default=[], metavar="K=V")
    p.add_argument("--set", nargs="*", action="extend", default=[], dest="sets",
                   metavar="TASK.BLOCK.KEY=V",
                   help=f"override; value {DELETE} removes the key")
    p.add_argument("--overrides", default=None,
                   help="JSON file of overrides; a top-level 'overrides' key is unwrapped")
    p.add_argument("--whitelist-dir", default=None,
                   help="{LUTE_PATH}/config/templates -- enables the silent-drop check")
    p.add_argument("--no-whitelist", action="store_true",
                   help="skip the silent-drop check (an unknown key will be ignored)")
    p.add_argument("--delims", default="lute", choices=sorted(DELIMITERS),
                   help="lute: ${{ }} for template vars so LUTE's own {{ }} survives")
    p.add_argument("--out", default=None, help="write here; omit to print the config")


def run(args) -> int:
    """Execute `hutch-lute render` from an already-parsed namespace."""
    try:
        if args.list_templates:
            return out({"template_dir": str(template_dir()),
                        "templates": available_templates()})

        ctx = json.load(open(args.context)) if args.context else {}
        ctx.update(parse_set(args.set_context))

        ovr = json.load(open(args.overrides)) if args.overrides else {}
        if isinstance(ovr, dict) and "overrides" in ovr:
            ovr = ovr["overrides"]      # accept the campaign's params/*.json shape
        ovr.update(parse_set(args.sets))

        if not args.no_whitelist and not args.whitelist_dir:
            raise ConfigError(
                "pass --whitelist-dir {LUTE_PATH}/config/templates so unknown phil keys "
                "are caught, or --no-whitelist to accept that they are dropped silently."
            )

        report = build(
            template=args.template or DEFAULT_TEMPLATE, context=ctx, overrides=ovr,
            whitelist_dir=None if args.no_whitelist else args.whitelist_dir,
            delims=args.delims,
        )
        if not args.out:
            return out(report["text"], plain=True)
        path = write(report, args.out)
        summary = {k: v for k, v in report.items() if k != "text"}
        summary["written"] = str(path)
        return out(summary)
    except (ConfigError, OSError) as exc:
        return fail(exc, UNREACHABLE)
