# bazis-cli

The `bazis` command (`bazis new`, `bazis add`, `bazis audit`, `bazis change`): Claude Code through the
Claude Agent SDK, run in a Bazis project with the MCP server bazis-mcp and the rules of
Bazis (`bazis.contrib.mcp.rules.RULES`).

- `bazis/contrib/cli/main.py` — the commands, with the tasks of `bazis.contrib.mcp.prompts`
  (and the prompt of `bazis audit`);
- `bazis/contrib/cli/agent.py` — the options of the agent (tools, permissions, MCP server),
  the permission prompt in the terminal and the output. Of the tools of bazis-mcp only
  those with `readOnlyHint` (read from the server it imports) are allowed in advance; the
  others, for a client without a shell, a new one too, are never given to the agent, which
  runs commands and edits files itself (`MCP_RULE` tells it that the instructions of the
  server about them do not apply).

The skeleton of a new project and its `.venv` (bazis-front in its requirements and apps
unless `--no-frontend`, its language: `--language`, else English until the agent sets the
language of the description (the product has that language alone), the check of Node.js), the rules and the tasks of the agent
are in bazis-mcp (`bazis.contrib.mcp.scaffold`, `rules`, `prompts`), with their tests.
`--language` is checked by the Django of the Python of the CLI (bazis is its dependency),
before anything is written.

The frontend of a product is made by bazis-front (`manage.py bazis_front ...`) and read by
the tools of bazis-mcp (`front_check`, `front_status`, `front_catalog`): bazis-cli only
orders the work with the prompts (`prompts.FRONTEND_STEPS`) and rules
(`rules.FRONTEND_RULES`) of bazis-mcp, it has no frontend code of its own.

Commits, tags and pull requests are authored by the maintainer, Ilya Kharyn
<ilya.tt07@gmail.com>, never by an AI assistant (see the CLAUDE.md of the core).

## Running the tests

The tests replace the Claude Agent SDK with a fake: they spend no credits and need no
account; neither PostgreSQL nor Redis is needed:

```bash
python -m pytest tests -p no:cacheprovider
```

`evals/` builds projects with the real agent (`python -m evals.run --budget N`); its task
with a frontend also needs Node.js and, before the release of bazis-front,
`BAZIS_FRONT_REQUIREMENT`.

Lint: `ruff check bazis tests evals`. A real run (`bazis audit --budget 0.5` in a project) spends
credits of the Claude Code account: do it only when asked.

## Releasing

A release is the tag `vX.Y.Z` on `main`: the Build and Publish workflow builds the package
(the version comes from the tag through setuptools-scm) and publishes it to PyPI
(pre-releases `-alphaN`/`-betaN`/`-rcN` go to Test PyPI) and creates the GitHub release.

Claude Code sessions cannot push tags. Release through the **Release** workflow instead:

1. Make sure the changes are merged into `main` and the Tests workflow is green on the
   `main` head commit (the Release workflow checks this and refuses otherwise).
2. Add the release notes as `docs/releases/X.Y.Z.md` in the change being released.
3. Start the workflow `release.yml` on `ref: main` with the input `version: X.Y.Z`
   (GitHub API: `POST /repos/ecofuture-tech/bazis-cli/actions/workflows/release.yml/dispatches`;
   with the GitHub MCP tools: `actions_run_trigger`, method `run_workflow`).
4. The Release run creates the annotated tag and starts Build and Publish on it. Check
   that both runs succeed and that the version appears on https://pypi.org/project/bazis-cli/.

bazis-cli is released after bazis-mcp (and bazis-mcp after bazis-front). Pick the version by semver: breaking changes
(commands or options renamed or removed) bump the minor version while the project is
below 3.0.
