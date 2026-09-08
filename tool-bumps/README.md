# tool-bumps

Emits a `tools` output: a JSON object mapping each `mise.toml` `[tools]` key to
whether it changed since a base git ref, for gating downstream jobs on a
specific tool's version bump. Requires the repo checked out with full history
(`fetch-depth: 0`).

When `base-ref` is empty it is derived from the event: the PR base sha, the
push `before` sha, or `HEAD~1`.

## Usage

```yaml
jobs:
  changes:
    runs-on: ubuntu-latest
    outputs:
      copier: ${{ fromJSON(steps.bumps.outputs.tools).copier == true }}
    steps:
      - uses: hugoh/gh-workflows/setup@<pinned-sha>
        with:
          fetch-depth: 0
      - uses: hugoh/gh-workflows/tool-bumps@<pinned-sha>
        id: bumps
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|  INPUT   | REQUIRED |    DEFAULT    |                                                        DESCRIPTION                                                         |
|----------|----------|---------------|----------------------------------------------------------------------------------------------------------------------------|
| base-ref |  false   |               | Base git ref to diff against. When empty, it is derived from the event: the PR base sha, the push "before" sha, or HEAD~1. |
| manifest |  false   | `"mise.toml"` |                                                  Manifest file to inspect                                                  |

<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->

| OUTPUT |                       DESCRIPTION                       |
|--------|---------------------------------------------------------|
| tools  | JSON object mapping each tool key to whether it changed |

<!-- AUTO-DOC-OUTPUT:END -->
