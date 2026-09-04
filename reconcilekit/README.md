# reconcilekit

[![PyPI](https://img.shields.io/pypi/v/reconcilekit)](https://pypi.org/project/reconcilekit/)

A tiny, stateless fetch-diff-apply reconciliation kernel — the pattern a
fleet-wide config tool repeats for every resource it manages, with none of the
domain specifics.

There is no state file, no backend, no daemon. Each run reads live state,
compares it to what you want, and either reports the plan or applies it.

## The pattern

1. **Enumerate targets** — anything with a `name`.
2. **Fetch** each target's current state.
3. **Compare** to the desired state: already there? partly blocked?
4. **Dry-run** → report the plan, or **apply** → mutate and report the outcome.
5. **Classify** into `Status` (`OK`, `UNCHANGED`, `LIMITED`, `LIMITED_UNCHANGED`,
   `FAILED`).
6. **Run the fleet in bounded parallel**, isolating per-target failures and
   raising them together at the end.

## API

Full API reference, generated from the docstrings:
[hugoh.github.io/gh-workflows/reconcilekit](https://hugoh.github.io/gh-workflows/reconcilekit/)
(rebuilt on every push that touches this package -- see
`.github/workflows/docs.yml`).

## Example

```python
import asyncio
from dataclasses import dataclass

from reconcilekit import (
    Result,
    Status,
    classify_status,
    result_line,
    run_parallel,
    run_reconcile,
)


@dataclass
class File:
    name: str
    path: str
    want: str


def make_worker(dry_run: bool):
    async def worker(f: File) -> Result[File]:
        async def fetch() -> str:
            return f.path_text()  # read current contents

        def plan(current: str) -> Result[File]:
            status = classify_status(
                at_target=current == f.want, changed=current != f.want
            )
            return Result(
                f,
                result_line(
                    f.name, "would rewrite" if current != f.want else "ok", status
                ),
                status,
            )

        async def apply(current: str) -> Result[File]:
            if current != f.want:
                f.write(f.want)
            status = classify_status(at_target=True, changed=current != f.want)
            return Result(
                f,
                result_line(f.name, "written" if current != f.want else "ok", status),
                status,
            )

        return await run_reconcile(
            dry_run=dry_run, fetch=fetch, plan_result=plan, apply_result=apply
        )

    return worker


asyncio.run(run_parallel(files, make_worker(dry_run=True)))
```

## Consumers

`repo-admin/` (in this repo) uses it for every account-wide GitHub `sync`
command, consumed as a uv workspace member there.
