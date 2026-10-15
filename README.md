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

# a product in Russian (by default in the language of the description), always also in English
bazis new shop "..." --language ru

# a Bazis package for an existing project (run in the directory of manage.py)
bazis add bazis-statusy "orders go through new -> paid -> shipped"

# a review against the guides and checks of the packages; --fix also fixes
bazis audit
bazis audit --fix

# go on changing the project, in the session of the last run of bazis in it
bazis change "loans of rare books need an approval of a librarian"
bazis change          # the changes typed one after another; an empty line ends
```

- `bazis new DIR DESCRIPTION` writes the skeleton of a project: the project package
  (settings, root router, ASGI app), `project.env`, `.env` with a generated secret key,
  `requirements.txt`, `requirements-dev.txt`, `tests/`, `.mcp.json` and `AGENTS.md`, and
  chooses free ports for the servers of the project (`.bazis/ports.json`). It creates
  `.venv` with Bazis and bazis-mcp (with uv if it is installed; `--no-venv` skips it),
  raises the minimums of the requirements to the installed releases, and the database of `.env` with PostGIS when PostgreSQL is reachable (else it says
  so and goes on; a database of that name with the data of another project is left as it
  is, and the first free `<name>_N` is created and set in `.env` instead). Then the agent chooses the Bazis packages, creates the apps, models,
  routes, migrations and tests, and runs the checks.
- By default `bazis new` builds the whole product with its frontend, made by
  [bazis-front](https://github.com/ecofuture-tech/bazis-front): the specs of the product
  (`spec/`: roles, entities, workflows, access, scenarios, screens, design), the backend
  that satisfies them, the contract exported from it (`contract/`), a React + TypeScript
  frontend (`frontend/`) with the screens composed from the components of bazis-front,
  and the end-to-end tests of the scenarios run against the backend. It needs Node.js
  22.12 or newer with npm, and PostgreSQL for the contract and the end-to-end tests;
  `--no-frontend` builds only the backend.
- The product is in the language of its description and in English, the language of the
  msgids of the code (`BS_LANGUAGES`, `BS_LANGUAGE_CODE`); `--language CODE` (a code of
  Django such as `ru` or `pt-br`) chooses it instead. The texts of the backend are
  translated in its gettext catalog and those of the screens in the dictionaries of the
  frontend.
- `bazis add PACKAGE [GOAL]` installs a Bazis package with the Bazis packages it requires
  and sets them up as their guides say, in the frontend too when the project has one.
  `bazis add bazis-front` builds the frontend of a backend.
- `bazis audit [--fix]` reports the problems of the project by severity, with the checks
  of the frontend when it has one. Without `--fix` the agent can only read.
- `bazis change [REQUEST]` changes the project as the request says, in the session of
  Claude Code of the last run of bazis in the project (`bazis new` prints the command):
  the agent remembers what it built and what was asked before. Without a request the
  changes are typed in the terminal one after another, each in the same session; an
  empty line, `exit` or Ctrl-D ends, Ctrl-C stops the change that runs. `--new-session`
  (or a session whose transcript is not on this machine) starts a new session, in which
  the agent first learns the project. `--budget` limits every change.

Every command writes the facts of its run (session, turns, cost, duration, tokens, calls
of each tool) to `.bazis/run.json` of the project; `.bazis/` is never committed.

Options of every command: `--project-dir` (`add`, `audit`, `change`; default: the current
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
- The agent calls only the tools of bazis-mcp that read (the catalog, the guides, the facts
  and checks of the project, its files), without asking. Those for a client without a shell
  (`run_command`, `start_server`, `stop_server`, `add_packages`, `write_file`,
  `delete_file`, `create_project`, `use_project`, `install_tools`) are not given to it: its commands and
  edits are those above. A tool of a newer bazis-mcp is asked like any other.
- `bazis audit` without `--fix` can only read the project and call the tools of bazis-mcp
  that read. bazis-mcp runs the system checks of the project, that is the project code with
  its `.venv`: run the commands only in projects you trust.

The tests of a project need PostgreSQL with PostGIS and Redis (settings in `.env`); without
them the agent creates the migrations and runs the system checks, and says that the tests
did not run.

The roles of a product are declared in `<app>/roles.py` and its statuses and transits in
`<app>/workflow.py`; `migrate` applies them. The end-to-end tests of
the frontend run against the backend with the test data of `python manage.py e2e_data`
(the e2e data command recommended by bazis-front): the test users of the roles, with the
password of `E2E_PASSWORD`, and the records the scenarios need, on the ports of
`.bazis/ports.json` (`frontend/.env.local` sends `/api` to the backend with `BAZIS_API_URL`):

```bash
E2E_PASSWORD=... .venv/bin/python manage.py e2e_data
.venv/bin/uvicorn shop.main:app --port <backend> &
cd frontend && E2E_PORT=<e2e> E2E_PASSWORD=... npm run e2e
```

To try a bazis-front that is not on PyPI yet, point `BAZIS_FRONT_REQUIREMENT` to a wheel,
a checkout or a URL: it replaces the requirement of bazis-front in `requirements.txt` of
the new product, and uv builds it again instead of reusing the build of an older commit.
