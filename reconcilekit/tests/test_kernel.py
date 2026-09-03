import asyncio
from dataclasses import dataclass

import pytest

from reconcilekit import (
    ReconcileError,
    Result,
    Status,
    run_parallel,
    run_reconcile,
)


@dataclass
class Fake:
    name: str


def _targets(n: int) -> list[Fake]:
    return [Fake(name=f"t{i}") for i in range(n)]


def test_result_defaults_to_ok_status():
    assert Result(Fake("t"), "line").status == Status.OK


async def test_run_reconcile_dry_run_reports_plan_without_applying():
    applied = False

    async def fetch():
        return "state"

    def plan_result(state):
        assert state == "state"
        return "planned"

    async def apply_result(state):
        nonlocal applied
        applied = True
        return "applied"

    result = await run_reconcile(
        dry_run=True, fetch=fetch, plan_result=plan_result, apply_result=apply_result
    )
    assert result == "planned"
    assert not applied


async def test_run_reconcile_applies_with_fetched_state():
    fetch_calls = 0

    async def fetch():
        nonlocal fetch_calls
        fetch_calls += 1
        return "before"

    def plan_result(state):
        raise AssertionError("plan_result should not run when not dry_run")

    async def apply_result(state):
        assert state == "before"
        return "applied"

    result = await run_reconcile(
        dry_run=False, fetch=fetch, plan_result=plan_result, apply_result=apply_result
    )
    assert result == "applied"
    assert fetch_calls == 1


async def test_run_parallel_returns_worker_results_for_every_target():
    targets = _targets(5)

    async def worker(target):
        return Result(target, f"{target.name} done")

    results = await run_parallel(targets, worker, jobs=3)
    assert sorted(r.target.name for r in results) == sorted(t.name for t in targets)


async def test_run_parallel_prints_each_worker_result_line(capsys):
    async def worker(target):
        return Result(target, f"{target.name} line")

    await run_parallel([Fake("t-a")], worker, jobs=1)
    assert "t-a line" in capsys.readouterr().out


async def test_run_parallel_runs_workers_concurrently():
    targets = _targets(4)
    barrier = asyncio.Barrier(4)

    async def worker(target):
        await barrier.wait()
        return Result(target, target.name)

    # If run_parallel executed workers serially, the barrier would never
    # release with only 1 worker present at a time and this would time out.
    await asyncio.wait_for(run_parallel(targets, worker, jobs=4), timeout=2)


async def test_run_parallel_one_failure_does_not_block_others():
    targets = _targets(3)
    completed = []

    async def worker(target):
        if target.name == "t1":
            raise RuntimeError("boom")
        await asyncio.sleep(0.05)
        completed.append(target.name)
        return Result(target, target.name)

    with pytest.raises(ReconcileError):
        await run_parallel(targets, worker, jobs=3)

    assert sorted(completed) == ["t0", "t2"]


async def test_run_parallel_reports_failed_target_names_in_error(capsys):
    async def worker(target):
        raise RuntimeError("network error")

    with pytest.raises(ReconcileError, match="bad-target"):
        await run_parallel([Fake("bad-target")], worker, jobs=1)
    assert "bad-target" in capsys.readouterr().out


async def test_run_parallel_raises_the_given_error_class():
    class MyError(RuntimeError):
        pass

    async def worker(target):
        raise RuntimeError("boom")

    with pytest.raises(MyError):
        await run_parallel([Fake("t")], worker, jobs=1, error_cls=MyError)


async def test_run_parallel_hides_unchanged_lines_by_default(capsys):
    async def worker(target):
        return Result(target, f"{target.name} line", status=Status.UNCHANGED)

    await run_parallel([Fake("t-a")], worker, jobs=1)
    out = capsys.readouterr().out
    assert "t-a line" not in out
    assert "1 unchanged" in out


async def test_run_parallel_verbose_shows_unchanged_lines_without_summary(capsys):
    async def worker(target):
        return Result(target, f"{target.name} line", status=Status.UNCHANGED)

    await run_parallel([Fake("t-a")], worker, jobs=1, verbose=True)
    out = capsys.readouterr().out
    assert "t-a line" in out
    assert "unchanged" not in out.replace("t-a line", "")


@pytest.mark.parametrize("status", [Status.OK, Status.FAILED, Status.LIMITED])
async def test_run_parallel_always_shows_non_unchanged_lines(capsys, status):
    async def worker(target):
        if status is Status.FAILED:
            raise RuntimeError("boom")
        return Result(target, f"{target.name} line", status=status)

    if status is Status.FAILED:
        with pytest.raises(ReconcileError):
            await run_parallel([Fake("t-a")], worker, jobs=1)
        out = capsys.readouterr().out
        assert "t-a" in out
    else:
        await run_parallel([Fake("t-a")], worker, jobs=1)
        out = capsys.readouterr().out
        assert "t-a line" in out
    assert "unchanged" not in out


async def test_run_parallel_hides_limited_unchanged_lines_by_default(capsys):
    async def worker(target):
        return Result(target, f"{target.name} line", status=Status.LIMITED_UNCHANGED)

    await run_parallel([Fake("t-a")], worker, jobs=1)
    out = capsys.readouterr().out
    assert "t-a line" not in out
    assert "1 unchanged" in out
