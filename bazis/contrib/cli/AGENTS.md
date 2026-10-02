# bazis-cli — guide for AI agents

The command `bazis` creates, extends and audits Bazis projects with Claude: it runs Claude
Code (Claude Agent SDK) in the project with the MCP server bazis-mcp and the rules of
Bazis. It is a tool for the developer's machine, not a dependency of a project.

## Commands

- `bazis new DIR "what the project does"` writes the skeleton of a project (package with
  settings, root router and ASGI app, `project.env`, `.env` with a generated secret key,
  `requirements*.txt`, `tests/`, `.mcp.json`, `AGENTS.md`), creates `.venv` with Bazis and
  bazis-mcp (`--no-venv` skips it), then the agent chooses the Bazis packages and builds the
  apps, migrations and tests.
- `bazis add PACKAGE ["what for"]` installs a Bazis package and its Bazis dependencies and
  sets them up as their guides say.
- `bazis audit [--fix]` reviews the project against the guides and checks of its packages;
  without `--fix` the agent can only read.

Options: `--project-dir` (default: the current directory), `--model` (default
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
