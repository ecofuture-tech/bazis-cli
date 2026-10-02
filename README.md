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
# a new project from a description
bazis new shop "An online shop: products with categories, orders of users, \
users see only their own orders"

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
- `bazis add PACKAGE [GOAL]` installs a Bazis package with the Bazis packages it requires
  and sets them up as their guides say.
- `bazis audit [--fix]` reports the problems of the project by severity. Without `--fix`
  the agent can only read.

Options of every command: `--project-dir` (`add`, `audit`; default: the current
directory), `--model` (default `claude-opus-5-5`), `--effort` (`low` ... `max`, default
`high`), `--budget USD`, `--yes`.

## Permissions

The agent edits the files of the project without asking. Every shell command (installing
packages, migrations, tests) is shown and asked in the terminal: `y` allows it, `a` allows
the rest of the run. `--yes` allows all commands; without a terminal and without `--yes`
commands are denied. The agent never commits: review the changes and commit them yourself.

The tests of a project need PostgreSQL with PostGIS and Redis (settings in `.env`); without
them the agent creates the migrations and runs the system checks, and says that the tests
did not run.
