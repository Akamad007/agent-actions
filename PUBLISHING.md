# Publishing

Releases are published to PyPI automatically via GitHub Actions using
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC).
No API tokens or secrets are required.

## How it works

1. A GitHub Release is created (or `workflow_dispatch` is triggered manually).
2. The `build` job lints, builds source + wheel distributions, and uploads them as a workflow artifact.
3. The `publish` job downloads the artifact and pushes to PyPI using OIDC — no password or token needed.

## One-time setup on PyPI

Before the workflow can publish, configure a Trusted Publisher on PyPI **once**:

1. Go to [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/)
   (or the project page → *Publishing* if the project already exists).
2. Add a new **GitHub** trusted publisher with **exactly** these values:

   | Field | Value |
   |---|---|
   | PyPI project name | `django-agent-actions` |
   | GitHub owner | `akamad007` |
   | Repository name | `agent-actions` |
   | Workflow filename | `python-publish.yml` |
   | Environment name | `pypi` |

   > The environment name, workflow filename, owner, and repo must match exactly
   > or PyPI will reject the OIDC token.

3. Create a **`pypi` environment** in the GitHub repository settings
   (*Settings → Environments → New environment*). No secrets needed — the
   environment name just has to match what is declared in the workflow.

## Releasing a new version

1. Bump `__version__` in `django_agent_actions/__init__.py`.
2. Commit and push to `main`.
3. On GitHub, create a new Release (*Releases → Draft a new release*):
   - Tag: `v<version>` (e.g. `v0.3.0`)
   - Target: `main`
   - Click **Publish release**.
4. The publish workflow triggers automatically. Monitor it under *Actions*.

## Important caveats

- **Reusable workflows** (`workflow_call`) cannot be used as the trusted workflow
  for PyPI Trusted Publishing — the workflow file must live directly in this repo.
- An **environment mismatch** (e.g. environment name on PyPI says `pypi` but the
  workflow omits it, or vice-versa) will cause OIDC authentication to fail.
- The `id-token: write` permission is scoped to the `publish` job only.
  The `build` job runs with read-only permissions.
