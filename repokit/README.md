# repokit

Repo listing/filtering and CLI-entrypoint plumbing shared by repo-admin-style
scripts: fetch an account's repos via [`asyncgh`](../asyncgh/README.md), keep
the non-archived/non-fork (by default) ones matching an `only`/`skip` filter,
and run work over them in bounded parallel via
[`reconcilekit`](../reconcilekit/README.md). No config-file loading, no sops,
no fork/exclude policy — those are a caller concern, which is why
`list_repos`'s `include_forks` defaults to none included rather than reading
any file.

Published to PyPI as `hugoh-repokit`; imported as `repokit`.

## API

Full API reference, generated from the docstrings:
[hugoh.github.io/gh-workflows/repokit](https://hugoh.github.io/gh-workflows/repokit/)
(rebuilt on every push that touches this package — see
`.github/workflows/docs.yml`).

## Example

```python
import asyncio

from repokit import DEFAULT_OWNER, list_repos, run_cli


async def main(args) -> int:
    repos = await list_repos(DEFAULT_OWNER, only=args.only, skip=args.skip)
    for repo in repos:
        print(repo.name)
    return 0


def cli(argv: list[str]) -> int:
    return run_cli(main, parse_args(argv))
```

## Consumers

`repo-admin/` (in this repo) uses it for `repo_admin.py`/`activity.py`,
consumed as a uv workspace member there — `repo-admin/lib.py` layers its
own config-file-backed fork/exclude policy, sops-based secrets, and
`console`/`progress_bar`/etc. re-exports from `asyncgh`/`reconcilekit` on
top, kept local since none of that is generic.
[`digest-action`](https://github.com/hugoh/digest-action) (a separate repo,
since GitHub Marketplace only publishes actions from a repository root)
consumes it as a regular PyPI dependency.
