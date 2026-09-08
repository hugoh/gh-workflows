# `.github/workflows/semantic-pr.yml`

`workflow_call` Conventional-Commit lint of the **pull request title**, via
[`amannn/action-semantic-pull-request`](https://github.com/amannn/action-semantic-pull-request).
Repos configured to squash-merge with the PR title as the commit subject get a
conventional history on `main` without every contributor hand-formatting the
merge box.

Enforced: a valid Conventional-Commit `type` (the action's default Angular set
— `feat fix docs style refactor perf test build ci chore revert`), an optional
scope, and a subject that starts lowercase (it becomes the squash commit
subject).

Runs on `opened`, `edited`, `reopened` and `synchronize` so the check
re-evaluates when the title is fixed without a new push, and clears on the
merge commit's status.

## Usage

```yaml
name: semantic-pr
on:
  pull_request:
    types: [opened, edited, reopened, synchronize]
permissions: {}
jobs:
  semantic-pr:
    uses: hugoh/gh-workflows/.github/workflows/semantic-pr.yml@<pinned-sha>
```

Then, per repo (outside this workflow):

- Set the squash-merge commit **title** to the PR title
  (`squash_merge_commit_title: PR_TITLE`, `squash_merge_commit_message: BLANK`
  or `COMMIT_MESSAGES`).
- Add the `semantic-pr` check to the branch protection rule's required checks.

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->
No inputs.
<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
