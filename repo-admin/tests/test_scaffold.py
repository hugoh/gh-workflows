import argparse
import subprocess
from pathlib import Path

import pytest
import scaffold


def _args(path: Path, **overrides: object) -> argparse.Namespace:
    base = {
        "path": str(path),
        "name": None,
        "release": False,
        "pages": False,
        "action": False,
        "rerun_transient": False,
        "tests": "none",
        "shell": False,
        "apt_packages": None,
        "pre_hk": None,
        "default_branch": "main",
        "gh_workflows_ref": "deadbeef1234 # v1.99.0",
        "pages_dir": "site",
        "pages_build_cmd": "mise run pages-build",
        "watch_workflows": "[ci, hk]",
        "force": False,
        "no_pin": True,
        "dry_run": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


async def _run(path: Path, **overrides: object) -> int:
    return await scaffold.run(_args(path, **overrides))


async def test_minimal_scaffold_writes_baseline(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    assert await _run(target) == 0

    for name in (
        ".editorconfig",
        ".gitignore",
        ".yamllint",
        "biome.json",
        "hk.pkl",
        "mise.toml",
        ".renovaterc.json",
        ".github/workflows/hk.yml",
    ):
        assert (target / name).is_file(), name
    assert not (target / ".github/workflows/release.yml").exists()
    assert not (target / "action.yml").exists()


async def test_no_template_markers_leak(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    await _run(
        target,
        release=True,
        pages=True,
        action=True,
        rerun_transient=True,
        tests="python",
        shell=True,
    )
    for f in target.rglob("*"):
        if not f.is_file() or {".git", ".jj"} & set(f.parts):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert "<<" not in text and "<%" not in text, f


async def test_tool_versions_come_from_gh_workflows_mise_toml(tmp_path: Path) -> None:
    canonical = scaffold._canonical_tools()
    target = tmp_path / "widget"
    await _run(target, shell=True, tests="python")
    mise = (target / "mise.toml").read_text()
    assert f'hk = "{canonical["hk"]}"' in mise
    assert f'shellcheck = "{canonical["shellcheck"]}"' in mise
    assert f'ruff = "{canonical["ruff"]}"' in mise
    # gh-workflows-only tools are not propagated
    assert "sops" not in mise


async def test_hk_groups_track_flags(tmp_path: Path) -> None:
    plain = tmp_path / "a"
    await _run(plain)
    assert "...Base.shell" not in (plain / "hk.pkl").read_text()
    assert "...Base.python" not in (plain / "hk.pkl").read_text()

    full = tmp_path / "b"
    await _run(full, shell=True, tests="python")
    hk = (full / "hk.pkl").read_text()
    assert "...Base.shell" in hk
    assert "...Base.python" in hk
    assert "unittest discover" in hk


async def test_action_flag_adds_renovate_rule_and_major_tag(tmp_path: Path) -> None:
    target = tmp_path / "act"
    await _run(target, action=True, release=True)
    assert (
        '"matchFileNames": ["action.yml"]' in (target / ".renovaterc.json").read_text()
    )
    assert "major-tag: true" in (target / ".github/workflows/release.yml").read_text()
    assert (target / "action.yml").is_file()


async def test_idempotent_keeps_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    await _run(target)
    (target / "hk.pkl").write_text("# hand-edited\n")
    await _run(target)
    assert (target / "hk.pkl").read_text() == "# hand-edited\n"

    await _run(target, force=True)
    assert (target / "hk.pkl").read_text() != "# hand-edited\n"


async def test_existing_git_repo_is_not_reinitialised(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    target.mkdir()
    (target / ".git").mkdir()
    await _run(target)
    assert not (target / ".jj").exists()


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    await _run(target, dry_run=True)
    assert not target.exists() or not any(target.iterdir())


async def test_default_branch_flows_into_workflows(tmp_path: Path) -> None:
    target = tmp_path / "widget"
    await _run(target, default_branch="trunk", release=True)
    assert "- trunk" in (target / ".github/workflows/hk.yml").read_text()
    assert "- trunk" in (target / ".github/workflows/release.yml").read_text()


async def test_rendered_workflows_are_valid_yaml(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    target = tmp_path / "widget"
    await _run(target, release=True, pages=True, rerun_transient=True)
    for wf in (target / ".github/workflows").glob("*.yml"):
        yaml.safe_load(wf.read_text())


def test_scaffolded_repo_passes_actionlint(tmp_path: Path) -> None:
    if not _have("actionlint"):
        pytest.skip("actionlint not on PATH")
    import asyncio

    target = tmp_path / "widget"
    asyncio.run(
        _run(target, release=True, pages=True, rerun_transient=True, action=True)
    )
    proc = subprocess.run(
        ["actionlint", *map(str, (target / ".github/workflows").glob("*.yml"))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _have(tool: str) -> bool:
    from shutil import which

    return which(tool) is not None
