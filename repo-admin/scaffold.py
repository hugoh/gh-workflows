"""Render a new fleet repo's baseline files from ``gh-workflows/templates/``.

One-shot scaffold, not an ongoing sync: it writes the shared dev-tooling files
(hk.pkl, mise.toml, .renovaterc.json, editor/lint config) plus the workflow
callers the feature flags ask for, then leaves Renovate to keep the version
lines current. It never runs ``copier update``.

Idempotent: an existing directory is fine, existing files are left alone
(``--force`` to overwrite), and an existing git/jj repo is not re-initialised.

Usage:
  repo_admin.py repo scaffold PATH [--name N] [--release] [--pages] [--action]
      [--rerun-transient] [--tests {none,python}] [--shell]
      [--apt-packages "pkg ..."] [--pre-hk CMD] [--default-branch B]
      [--gh-workflows-ref REF] [--pages-dir DIR] [--pages-build-cmd CMD]
      [--watch-workflows "[CI, hk]"] [--force] [--no-pin] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import tomllib
from pathlib import Path

import jinja2

GH_WORKFLOWS = Path(__file__).resolve().parent.parent
TEMPLATES = GH_WORKFLOWS / "templates"

# Which of gh-workflows' OWN mise.toml [tools] each hk-config group needs.
# Versions are never hardcoded here — they come from gh-workflows/mise.toml,
# which Renovate keeps current as this repo's own toolchain. A scaffolded repo
# then inherits `github>hugoh/renovate-config`, whose mise + hk-config regex
# managers keep the copy current from then on.
BASE_TOOLS = (
    "hk",
    "uv",
    "actionlint",
    "pinact",
    "zizmor",
    "ghalint",
    "gitleaks",
    "typos",
    "rumdl",
    "biome",
    "tombi",
    "github:owenlamont/ryl",
)
SHELL_TOOLS = ("shellcheck", "shfmt", "aqua:anordal/shellharden")
PYTHON_TOOLS = ("ruff", "ty")


def _canonical_tools() -> dict[str, str]:
    data = tomllib.loads((GH_WORKFLOWS / "mise.toml").read_text(encoding="utf-8"))
    return {k: v for k, v in data.get("tools", {}).items() if isinstance(v, str)}


def _canonical_hk_versions() -> tuple[str, str]:
    text = (GH_WORKFLOWS / "hk.pkl").read_text(encoding="utf-8")
    hk = re.search(r"jdx/hk/releases/download/v([^/]+)/hk@", text)
    cfg = re.search(r"hugoh/hk-config/releases/download/v([^/]+)/hk-config@", text)
    if not (hk and cfg):
        raise RuntimeError(
            "could not read hk / hk-config versions from gh-workflows/hk.pkl"
        )
    return hk.group(1), cfg.group(1)


def _tools_block(
    tools: dict[str, str], groups: list[str], *, python_extra: bool
) -> str:
    wanted = list(BASE_TOOLS)
    if "shell" in groups:
        wanted += SHELL_TOOLS
    if "python" in groups or python_extra:
        wanted += PYTHON_TOOLS
    lines = []
    for key in wanted:
        if key not in tools:
            continue
        rendered_key = f'"{key}"' if not key.isidentifier() else key
        lines.append(f'{rendered_key} = "{tools[key]}"')
    return "\n".join(lines)


# Overlay dir -> the flag that enables it ("" means always).
OVERLAYS: tuple[tuple[str, str], ...] = (
    ("base", ""),
    ("release", "release"),
    ("pages", "pages"),
    ("action", "action"),
    ("rerun-transient", "rerun_transient"),
)


def _jinja_env() -> jinja2.Environment:
    # << var >> / <% block %> so GitHub's ${{ ... }} passes through untouched.
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        variable_start_string="<<",
        variable_end_string=">>",
        block_start_string="<%",
        block_end_string="%>",
        comment_start_string="<#",
        comment_end_string="#>",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )


def _dest_relpath(overlay: str, template_path: Path) -> Path:
    """templates/<overlay>/dot-foo.j2         -> .foo
    templates/<overlay>/github/workflows/x.j2 -> .github/workflows/x
    """
    rel = template_path.relative_to(TEMPLATES / overlay)
    parts = list(rel.parts)
    if parts and parts[0] == "github":
        parts[0] = ".github"
    name = parts[-1]
    name = name.removesuffix(".j2")
    if name.startswith("dot-"):
        name = "." + name[len("dot-") :]
    parts[-1] = name
    return Path(*parts)


def _resolve_gh_workflows_ref(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        tag = subprocess.run(
            [
                "gh",
                "release",
                "view",
                "-R",
                "hugoh/gh-workflows",
                "--json",
                "tagName",
                "--jq",
                ".tagName",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        sha = subprocess.run(
            [
                "gh",
                "api",
                f"repos/hugoh/gh-workflows/git/ref/tags/{tag}",
                "--jq",
                ".object.sha",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if tag and sha:
            return f"{sha} # {tag}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    print(
        "  ! could not resolve latest hugoh/gh-workflows tag — using @main; "
        "pin it before merging"
    )
    return "main"


def _context(args: argparse.Namespace, name: str) -> dict[str, object]:
    hk_groups: list[str] = []
    if args.shell:
        hk_groups.append("shell")
    if args.tests == "python":
        hk_groups.append("python")
    hk_version, hk_config_version = _canonical_hk_versions()
    tools_block = _tools_block(
        _canonical_tools(), hk_groups, python_extra=args.tests == "python"
    )
    return {
        "repo_name": name,
        "default_branch": args.default_branch,
        "gh_workflows_ref": _resolve_gh_workflows_ref(args.gh_workflows_ref),
        "hk_groups": hk_groups,
        "tests": args.tests,
        "action": bool(args.action),
        "apt_packages": args.apt_packages or "",
        "pre_hk": args.pre_hk or "",
        "pages_dir": args.pages_dir,
        "pages_build_cmd": args.pages_build_cmd,
        "watch_workflows": args.watch_workflows,
        "hk_version": hk_version,
        "hk_config_version": hk_config_version,
        "tools_block": tools_block,
        "year": datetime.datetime.now(tz=datetime.UTC).year,
    }


def _enabled_overlays(args: argparse.Namespace) -> list[str]:
    return [name for name, flag in OVERLAYS if not flag or getattr(args, flag)]


def _render_overlay(
    env: jinja2.Environment,
    overlay: str,
    ctx: dict[str, object],
    target: Path,
    *,
    force: bool,
    dry_run: bool,
) -> tuple[list[Path], list[Path]]:
    written: list[Path] = []
    skipped: list[Path] = []
    root = TEMPLATES / overlay
    for tpl in sorted(root.rglob("*.j2")):
        dest_rel = _dest_relpath(overlay, tpl)
        dest = target / dest_rel
        if dest.exists() and not force:
            skipped.append(dest_rel)
            continue
        template = env.get_template(str(tpl.relative_to(TEMPLATES)))
        content = template.render(**ctx)
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        written.append(dest_rel)
    return written, skipped


def _init_vcs(target: Path, *, dry_run: bool) -> str:
    if (target / ".jj").exists():
        return "jj repo already present — left as-is"
    if (target / ".git").exists():
        return (
            "git repo present, no .jj — run `jj git init --colocate` yourself if wanted"
        )
    if dry_run:
        return "would run `jj git init`"
    try:
        subprocess.run(
            ["jj", "git", "init"], cwd=target, check=True, capture_output=True
        )
    except FileNotFoundError:
        return "jj not on PATH — run `jj git init` yourself"
    except subprocess.CalledProcessError as exc:
        return f"`jj git init` failed ({exc}) — run it yourself"
    return "ran `jj git init`"


def _pin_actions(target: Path, *, dry_run: bool, no_pin: bool) -> str:
    if no_pin:
        return "skipped (--no-pin)"
    if dry_run:
        return "would run `pinact run` to pin action refs"
    try:
        subprocess.run(
            ["mise", "exec", "--", "pinact", "run"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        )
        return "ran `pinact run` — action refs pinned"
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return f"pinact run failed ({exc}); pin action refs before merging"


async def run(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    name = args.name or target.name
    target.mkdir(parents=True, exist_ok=True)

    env = _jinja_env()
    ctx = _context(args, name)
    overlays = _enabled_overlays(args)

    all_written: list[Path] = []
    all_skipped: list[Path] = []
    for overlay in overlays:
        written, skipped = _render_overlay(
            env, overlay, ctx, target, force=args.force, dry_run=args.dry_run
        )
        all_written += written
        all_skipped += skipped

    label = "would write" if args.dry_run else "wrote"
    print(f"{name}  ({', '.join(overlays)})")
    for path in sorted(all_written):
        print(f"  {label:>11}  {path}")
    for path in sorted(all_skipped):
        print(f"  {'exists, kept':>11}  {path}")

    if all_written and not args.dry_run:
        print(f"  {'vcs':>11}  {_init_vcs(target, dry_run=args.dry_run)}")
        print(
            f"  {'pin':>11}  {_pin_actions(target, dry_run=args.dry_run, no_pin=args.no_pin)}"
        )

    print("\nnext:")
    print("  - /project-setup            (jj policy + raw-git block in CLAUDE.md)")
    print("  - review, then `jj commit`")
    print("  - gh repo create hugoh/" + name + " --private --source . --push")
    print("  - (from gh-workflows/) ./repo-admin.sh sync " + name)
    return 0


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="target directory (created if missing)")
    p.add_argument("--name", help="repo name (default: basename of PATH)")
    p.add_argument("--release", action="store_true", help="add release.yml")
    p.add_argument("--pages", action="store_true", help="add pages.yml")
    p.add_argument(
        "--action",
        action="store_true",
        help="add action.yml stub + major-tag move in release.yml",
    )
    p.add_argument(
        "--rerun-transient",
        action="store_true",
        dest="rerun_transient",
        help="add the rerun-transient-failures workflow",
    )
    p.add_argument(
        "--tests",
        choices=("none", "python"),
        default="none",
        help="test wiring in hk.pkl/mise.toml (default: none)",
    )
    p.add_argument(
        "--shell",
        action="store_true",
        help="include shell linters (shellcheck/shfmt/shellharden)",
    )
    p.add_argument("--apt-packages", help="system packages the lint job needs")
    p.add_argument("--pre-hk", help="command to run before hk check")
    p.add_argument("--default-branch", default="main")
    p.add_argument(
        "--gh-workflows-ref",
        help="pin for hugoh/gh-workflows (default: latest release)",
    )
    p.add_argument("--pages-dir", default="site", help="dir to publish (with --pages)")
    p.add_argument(
        "--pages-build-cmd",
        default="mise run pages-build",
        help="build command (with --pages)",
    )
    p.add_argument(
        "--watch-workflows",
        default="[ci, hk]",
        help="workflow_run watch list (with --rerun-transient)",
    )
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.add_argument("--no-pin", action="store_true", help="don't run pinact afterwards")
    p.add_argument("--dry-run", action="store_true", help="show what would be written")
