# bazis-cli — guide for AI agents

The command `bazis` creates, extends and audits Bazis projects with Claude: it runs Claude
Code (Claude Agent SDK) in the project with the MCP server bazis-mcp and the rules of
Bazis. It is a tool for the developer's machine, not a dependency of a project.

## Commands

- `bazis new DIR "what the product does"` writes the skeleton of a project (package with
  settings, root router and ASGI app, `project.env`, `.env` with a generated secret key,
  `requirements*.txt`, `tests/`, `.mcp.json`, `AGENTS.md`, `.bazis/ports.json` with free
  ports of this machine for the backend, the dev server of the frontend and that of the
  end-to-end tests), creates `.venv` with Bazis and bazis-mcp (`--no-venv` skips it), raises
  the minimums of the requirements to the installed releases (`X.Y.Z`) and the database
  of the settings of the project (`.env`, the `BS_DATABASES__DEFAULT__*` variables; the
  server is `--database host:port`, else that of the environment, else localhost:5432, resolved by
  `project.database_server` of
  bazis-mcp and written to `.env` before the database is made; a refused `--database`
  stops the command before anything is written) with the extension PostGIS when
  PostgreSQL is reachable and the database is missing (it says when it cannot, when
  PostGIS could not be
  installed, and when an existing database has tables, probably of another project, and
  goes on; the agent is told what it did), then the agent chooses the Bazis packages and builds the apps,
  migrations and tests. By default the product also gets its frontend: bazis-front
  is in `requirements.txt` and `BS_INSTALLED_APPS`, and the agent follows the order of the
  guide of bazis-front (`bazis_front init`, the specs in `spec/`, the backend that
  satisfies them with the roles, statuses and transits declared in `roles.py` and
  `workflow.py` and the test users of `manage.py e2e_data`, migrate, `contract`, `check` until no errors, `add`, the
  screens, `design`, `e2e` run against the backend, the final checks). It needs Node.js
  22.12 or newer with npm (checked before anything is written); `--no-frontend` builds
  only the backend. The product is in the language of the description (the agent sets
  `BS_LANGUAGES` and `BS_LANGUAGE_CODE`) or of `--language CODE` (written by the
  skeleton) alone, unless the description asks for more: English msgids with
  `gettext_lazy`, the catalogs of the project `locale/<language>/LC_MESSAGES/django.po`
  made, filled and compiled by
  `manage.py bazis_messages` (`make`, the translations in `locale/translations.json`,
  `apply`; a msgid of the packages that `bazis.W004` names declared with `gettext_noop` in
  `<project>/translations.py` and translated so too), the texts of the screens through
  `t()` of bazis-front, a `bazis.W005` reported. The `tests/conftest.py` of the
  skeleton gives the cache keys of the tests a prefix of their own: the tests never flush
  the shared Redis; the pytest plugin of bazis-test-utils installs the triggers of the test
  database and `migrate` applies the declared roles and workflows, so it has no other
  fixture.
- `--name` of `bazis new` is normalized to an identifier (`master-na-chas` becomes
  `master_na_chas`).
- `bazis add PACKAGE ["what for"]` installs a Bazis package and its Bazis dependencies and
  sets them up as their guides say. In a project with a frontend
  (`frontend/bazis-front.lock.json`) it then exports the contract, fixes `check`, copies
  the components of the new capability and runs the final checks; `bazis add
  bazis-front` builds the frontend of a backend.
- `bazis audit [--fix]` reviews the project against the guides and checks of its packages
  and, with a frontend, `front_check`, `front_status` and the guides of bazis-front;
  without `--fix` the agent can only read (it lists the frontend commands it could not run).
- `bazis change ["what to change"]` changes the project in the session of Claude Code of
  the last run of bazis in it (`session_id` of `.bazis/run.json`, resumed when Claude Code
  has its transcript on this machine), so the agent keeps what it built and was asked;
  without a request the changes are typed in the terminal one after another, each in the
  same session (an empty line, `exit` or Ctrl-D ends; Ctrl-C stops the running change and
  keeps the session; without a terminal the request is needed). A new session
  (`--new-session`, or none to resume) first reads `project_info` and the guides of the
  packages concerned and, with a frontend, follows the steps of `bazis add`. A resumed
  session gets the rules of the current run, and `--budget` limits each change.

The agent reads the introduction of a guide and its sections when a step needs them, reads
the sources of the packages only for a fact the guides lack, runs the final checks once
(again only those that failed) and ends with a summary in fixed sections: Built, Packages,
Run, Outside the files, Left for you, Guide gaps. The servers of a project use its ports
of `.bazis/ports.json` (`frontend/.env.local` has `BAZIS_API_URL`), and its files go to
`media/` and `static/` of the project (`BS_MEDIA_ROOT`, `BS_STATIC_ROOT` in `.env`).
Every command writes the facts of its run to `.bazis/run.json`: the session, the turns, the
cost (in a resumed session, that of the run, and `session_cost_usd` that of the session),
the duration, the usage of the tokens and the calls of each tool. `.bazis/` ignores itself in
Git.

bazis-cli only orchestrates: the frontend logic (template, contract, specs, components,
design, end-to-end tests) is in bazis-front and its `manage.py bazis_front` commands, the
read-only tools in bazis-mcp (`front_check`, `front_status`, `front_catalog`); the CLI adds
the order of the work, with the skeleton, the prompts and the rules of bazis-mcp
(`bazis.contrib.mcp.scaffold`, `prompts`, `rules`). `BAZIS_FRONT_REQUIREMENT` (a path to a
wheel or a checkout, or a URL) replaces the requirement of bazis-front in a new product, to
try a version that is not on PyPI.

Options: `--project-dir` (`add`, `audit`, `change`; default: the current directory), `--model` (default
`claude-opus-5-5`), `--effort` (default `high`), `--budget USD`, `--yes` (run shell commands
without asking).

## Rules

- Edits of the files of the project are accepted; every shell command and every other tool
  call (edits or reads outside the project, web) is asked in the terminal (`a` allows the
  rest of the run) unless `--yes`; without a terminal and `--yes` they are denied.
- `.env` cannot be read; git cannot commit, push, reset or rebase: the user reviews and
  commits the changes.
- Only the MCP server bazis-mcp and no Claude Code settings of the project or the user are
  loaded. The checks of bazis-mcp run the project code: use the commands in trusted
  projects.
- Credentials are those of Claude Code: `claude login` or `ANTHROPIC_API_KEY`.
