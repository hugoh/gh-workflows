# templates/

Source for `repo-admin.sh repo scaffold` — the baseline files a new fleet repo
gets. See [`../docs/repo-setup.md`](../docs/repo-setup.md).

## Layout

```text
base/               always rendered
release/            --release
pages/              --pages
action/             --action
rerun-transient/    --rerun-transient
```

Within an overlay:

- `dot-foo.j2` → `.foo`
- `github/…` → `.github/…`
- everything is a `.j2` (Jinja), even the near-literal files — the suffix
  keeps this repo's own linters off them.

## Conventions

- **Delimiters:** `<< var >>` / `<% block %>` / `<# comment #>`, so GitHub
  Actions `${{ … }}` passes through untouched.
- **No version numbers.** Tool versions in the rendered `mise.toml` / `hk.pkl`
  are read from *this repo's own* `mise.toml` / `hk.pkl` by `scaffold.py` at
  render time. Renovate keeps those canonical files current here; the
  templates never drift.
- Third-party action refs are floating-major seeds; `scaffold.py` runs
  `pinact run` in the new repo to pin them, and that repo's Renovate takes
  over.

Context variables are assembled in
[`../repo-admin/scaffold.py`](../repo-admin/scaffold.py) (`_context`).
