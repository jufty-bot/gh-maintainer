# Installation and releases

The [0.2.0 release](https://github.com/jufty-bot/gh-maintainer/releases/tag/v0.2.0)
contains a Python wheel, source distribution, a versioned skill ZIP,
and SHA-256 checksums. The wheel includes all skill resources and migrations.
No checkout or model subscription is needed for installed CLI use.

Download the wheel from [GitHub releases](https://github.com/jufty-bot/gh-maintainer/releases)
and install it with uv:

```bash
uv tool install ./gh_maintainer-0.2.0-py3-none-any.whl
gh-maintainer --version
gh-maintainer skills --install "$HOME/.codex/skills"
```

The last command copies each complete bundled skill directory and refuses any existing
destination. Choose a fresh destination when upgrading, inspect customized copies, and
replace them deliberately. `gh-maintainer skills --format json` lists available skills.
The skill ZIP offers the same directories plus a manifest with CLI version/file hashes.
Reload your agent's discovery or explicitly load an installed `SKILL.md` by absolute path.
See [skill usage](skills.md) for inputs and supported workflows.

Python 3.10–3.14 is supported. Sync uses environment tokens or existing `gh` authentication;
technical context additionally requires the `gh` executable. Local index operations need
neither credentials nor network. See [usage](usage.md) for database location and backup.
No PyPI publication is assumed by these instructions.

## Build and validate a release

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy src
npm ci
npx playwright install --with-deps chromium
npm run test:ui
uv run --locked --group docs mkdocs build --strict
uv build
uv run --locked python scripts/build_release.py
uv run --locked python scripts/check_install.py
```

The installation check creates a temporary environment, installs only the built wheel,
and runs outside this checkout with Python isolated mode. It exercises migrations,
demo/query/summary/processing, policy, bundled-skill installation, and a legacy database
upgrade retaining its note. Tests additionally cover source races, missing coverage,
pagination and malformed inputs. A build passing inside an editable checkout alone is
not evidence that package resources are correct.

CI runs the Python matrix, desktop/mobile browser tests and quality/docs/build/install checks for PRs **and** direct
pushes to main. Release publication is a separate step after those checks succeed.
The existing manually dispatched semantic-release workflow creates subsequent versions
using the project's Gitmoji conventions and attaches wheel/source/skills/checksums.
It requires its configured publishing credential. The initial 0.2.0 release was published
from a validated main commit with matching version/tag and those four artifacts; see
[publication evidence](evaluations/published-0.2.0.json).
No workflow publishes model credentials or invokes a maintenance agent automatically.
