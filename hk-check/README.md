# hk-check

Runs `hk check --no-progress --profile ci --all --no-fail-fast` — every check
runs even after one fails, so all failures surface in one pass. Expects `hk`
(and the repo's tools) already on `PATH`; run [`setup`](../setup/) first.

No inputs, no outputs.

## Usage

```yaml
steps:
  - uses: hugoh/gh-workflows/setup@<pinned-sha>
  - run: npm ci && npm run build   # optional extra steps
  - uses: hugoh/gh-workflows/hk-check@<pinned-sha>
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->
No inputs.
<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->
No outputs.
<!-- AUTO-DOC-OUTPUT:END -->
