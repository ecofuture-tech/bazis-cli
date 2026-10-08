# bazis-cli

The `bazis` command creates, extends and audits [Bazis](https://github.com/ecofuture-tech/bazis)
projects with Claude. It runs Claude Code (the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk))
in the project with the MCP server [bazis-mcp](https://github.com/ecofuture-tech/bazis-mcp),
so the agent chooses the Bazis packages from their catalog, follows their guides and checks
the result with the system checks of the project instead of relying on its memory.

## Installation

```bash
pip install bazis-cli      # or: uv tool install bazis-cli
claude login               # or set ANTHROPIC_API_KEY
```

The agent uses the account of Claude Code and spends its credits; limit a run with
`--budget`.

## Commands

```bash
# a new product, backend and frontend, from a description
bazis new shop "An online shop: products with categories, orders of users, \
users see only their own orders"

# only the backend
bazis new shop "..." --no-frontend

# a Bazis package for an existing project (run in the directory of manage.py)
bazis add bazis-statusy "orders go through new -> paid -> shipped"

# a review against the guides and checks of the packages; --fix also fixes
bazis audit
bazis audit --fix
```

- `bazis new DIR DESCRIPTION` writes the skeleton of a project: the project package
  (settings, root router, ASGI app), `project.env`, `.env` with a generated secret key,
  `requirements.txt`, `requirements-dev.txt`, `tests/`, `.mcp.json` and `AGENTS.md`. It
  creates `.venv` with Bazis and bazis-mcp (with uv if it is installed; `--no-venv` skips
  it). Then the agent chooses the Bazis packages, creates the apps, models, routes,
  migrations and tests, and runs the checks.
- By default `bazis new` builds the whole product with its frontend, made by
  [bazis-front](https://github.com/ecofuture-tech/bazis-front): the specs of the product
  (`spec/`: roles, entities, workflows, access, scenarios, screens, design), the backend
  that satisfies them, the contract exported from it (`contract/`), a React + TypeScript
  frontend (`frontend/`) with the screens composed from the components of bazis-front,
  and the end-to-end tests of the scenarios run against the backend. It needs Node.js
  22.12 or newer with npm, and PostgreSQL for the contract and the end-to-end tests;
  `--no-frontend` builds only the backend.
- `bazis add PACKAGE [GOAL]` installs a Bazis package with the Bazis packages it requires
  and sets them up as their guides say, in the frontend too when the project has one.
  `bazis add bazis-front` builds the frontend of a backend.
- `bazis audit [--fix]` reports the problems of the project by severity, with the checks
  of the frontend when it has one. Without `--fix` the agent can only read.

Options of every command: `--project-dir` (`add`, `audit`; default: the current
directory), `--model` (default `claude-opus-5-5`), `--effort` (`low` ... `max`, default
`high`), `--budget USD`, `--yes`.

## Permissions

- The agent edits the files of the project directory without asking.
- Everything else is shown in full and asked in the terminal: every shell command
  (installing packages, migrations, tests), edits and reads outside the project, web access.
  `y` allows it, `a` allows the rest of the run. `--yes` allows all of it; without a
  terminal and without `--yes` it is denied.
- The agent cannot read `.env` and cannot commit, push or reset with git: review the changes
  and commit them yourself.
- Only the settings of bazis-cli apply: the Claude Code settings, hooks and MCP servers of
  the project and of the user are not loaded.
- `bazis audit` without `--fix` can only read the project and call bazis-mcp. bazis-mcp
  runs the system checks of the project, that is the project code with its `.venv`: run
  the commands only in projects you trust.

The tests of a project need PostgreSQL with PostGIS and Redis (settings in `.env`); without
them the agent creates the migrations and runs the system checks, and says that the tests
did not run.

The roles, statuses and transits of a product are data migrations. The end-to-end tests of
the frontend run against the backend with the test data of `python manage.py e2e_data`
(the e2e data command recommended by bazis-front): the test users of the roles, with the
password of `E2E_PASSWORD`, and the records the scenarios need:

```bash
E2E_PASSWORD=... .venv/bin/python manage.py e2e_data
.venv/bin/uvicorn shop.main:app --port 8000 &
cd frontend && E2E_PASSWORD=... npm run e2e
```

To try a bazis-front that is not on PyPI yet, point `BAZIS_FRONT_REQUIREMENT` to a wheel,
a checkout or a URL: it replaces the requirement of bazis-front in `requirements.txt` of
the new product.
