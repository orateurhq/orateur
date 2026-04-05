# GitHub Pages (MkDocs)

This site is built with [MkDocs](https://www.mkdocs.org/) and [Material for MkDocs](https://squid-funk.github.io/mkdocs-material/), then deployed with [GitHub Actions](https://docs.github.com/en/actions).

## One-time repository settings

1. Open the repository on GitHub.
2. Go to **Settings** → **Pages**.
3. Under **Source**, choose **GitHub Actions** (not the legacy `gh-pages` branch).
4. Save.

The workflow **Deploy Documentation** (`.github/workflows/docs.yml`) builds on every push to `main` and on pull requests targeting `main`. Only pushes to `main` publish to Pages.

## Default URL

After the first successful deployment:

`https://orateurhq.github.io/orateur/`

Custom domains are configured separately under **Settings** → **Pages** and in DNS.

## Local preview

From the repository root (with [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --dev
uv run mkdocs serve
```

Build a static copy:

```bash
uv run mkdocs build
```

Output is written to `site/`.
