# `.github/workflows/release.yml`

`workflow_call` tag + GitHub Release via
[`hugoh/cog-bump`](https://github.com/hugoh/cog-bump). With no `tag` input it
runs `cog bump` to compute and push the next tag, then cuts the Release; pass an
existing `tag` to skip the bump and just create the Release.

`package-command` runs after the release is cut with `$TAG` (e.g. `v1.2.3`) and
`$VERSION` (without the leading `v`) exported and the repo's mise tools on
`PATH`; the files it produces (`package-artifacts` glob) are uploaded to the
Release. `major-tag: true` also force-moves the `vN` major tag — for Marketplace
actions.

The `tag` output is the released tag, or empty when there was nothing to
release.

## Usage

```yaml
jobs:
  release:
    uses: hugoh/gh-workflows/.github/workflows/release.yml@<pinned-sha>
    permissions:
      contents: write
```

## Inputs

<!-- AUTO-DOC-INPUT:START - Do not remove or modify this section -->

|       INPUT       | REQUIRED | DEFAULT |                                                                                                DESCRIPTION                                                                                                 |
|-------------------|----------|---------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|     major-tag     |  false   | `false` |                                                               Also force-move the vN major tag to the new release (for Marketplace actions)                                                                |
|       notes       |  false   |         |                                                                         Release notes body (default: GitHub auto-generated notes)                                                                          |
| package-artifacts |  false   |         |                                            Glob(s) of files produced by package-command to attach to the GitHub Release. Required when package-command is set.                                             |
|  package-command  |  false   |         | Shell command that builds release artifacts, run after checkout with the repo's mise tools on PATH and with $TAG (e.g. v1.2.3) and $VERSION (the same without the leading v) exported. Skipped when empty. |
|        tag        |  false   |         |                                                                        Existing tag to create a GitHub Release for; skips the bump                                                                         |

<!-- AUTO-DOC-INPUT:END -->

## Outputs

<!-- AUTO-DOC-OUTPUT:START - Do not remove or modify this section -->

| OUTPUT |                              DESCRIPTION                              |
|--------|-----------------------------------------------------------------------|
|  tag   | The tag that was released, or empty when there was nothing to release |

<!-- AUTO-DOC-OUTPUT:END -->
