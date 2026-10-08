# Copyright 2026 EcoFuture Technology Services LLC and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
The agent that does the work of a command: Claude Code (Claude Agent SDK) in the project
directory, with the MCP server bazis-mcp of the project and the rules of Bazis.
"""

import json
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultError,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)
from claude_agent_sdk.types import CanUseToolShadowedWarning


DEFAULT_MODEL = 'claude-opus-5-5'
DEFAULT_EFFORT = 'high'

#: git commands that change the repository: the user commits, never the agent
GIT_DENY = ['Bash(git commit:*)', 'Bash(git push:*)', 'Bash(git reset:*)', 'Bash(git rebase:*)']
#: tools a review cannot use
WRITE_TOOLS = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash', 'WebFetch', 'WebSearch']
GIT_COMMAND_RE = re.compile(r'(^|[;&|(]\s*)git\s+(commit|push|reset|rebase)\b', re.MULTILINE)

RULES = """\
# Bazis

You work on a Bazis project in the current directory. Bazis builds JSON:API services on
Django, FastAPI and Pydantic; the core is the package `bazis`, every other feature is a
separate package `bazis-<name>`. A project installs only the packages it needs.

- The MCP server `bazis` is the source of truth about Bazis, not your memory of it:
  `list_packages` (what each package solves and requires), `package_guide` (how to use a
  package, also `bazis` for the core), `project_info` (the installed packages, settings,
  models and routes of this project) and `run_doctor` (the system checks). Read the guide
  of every package before you use it and follow it exactly.
- Read `AGENTS.md` (and `CLAUDE.md` if there is one) of the project first.
- The Python environment is `.venv` (`.venv/bin/python`, on Windows
  `.venv\\Scripts\\python.exe`). Add a package to `requirements.txt`, install it with
  `.venv/bin/python -m pip install -r requirements.txt` and list its app in
  `BS_INSTALLED_APPS` of `project.env` when its guide says so.
- After changing models run `.venv/bin/python manage.py makemigrations`. After every change
  run `run_doctor` and fix the errors and the warnings of the packages you used. Run the
  tests with `.venv/bin/python -m pytest` when PostgreSQL and Redis are available (`.env`);
  if they are not, say so instead of skipping the tests silently.
- Write tests for the behavior you add (`tests/`), with `bazis_test_utils` as the guides show.
- Never read, print or copy the values of `.env`. Never run git commands that change the
  repository (commit, push, reset): the user reviews and commits the changes.
- End with a short summary: what you built or changed, the Bazis packages used, how to run
  it, and what is left for the user (for example the database).
"""

#: the rules of a project with a frontend made by bazis-front (Task.extra_rules)
FRONTEND_RULES = """\
# The frontend (bazis-front)

The product has a frontend made by bazis-front: the specs of the product in `spec/`, the
contract generated from the backend in `contract/`, the React app in `frontend/`. The
guides are `package_guide("bazis-front")` and `frontend/AGENTS.md`: read them before you
change the frontend and follow them exactly. All the frontend logic is in bazis-front: use
its commands, do not write generators or frontends of your own.

- The frontend is changed with the subcommands of `.venv/bin/python manage.py bazis_front`
  (`init`, `contract`, `check`, `add`, `design`, `e2e`, `update`). The MCP tools read it: `front_check` (the issues of the specs
  against the contract, with a `hint` each), `front_status` (what is stale and the command
  that updates it) and `front_catalog` (the components, the capabilities and the assets
  they require).
- The specs describe the product and the backend is built to satisfy them. After every
  change of the models, routes, roles, statuses or transits: migrate, export the contract
  (`bazis_front contract`), then fix the spec or the backend until `front_check` reports
  no errors.
- Never edit `contract/`, `frontend/src/bazis/generated/`, `frontend/e2e/generated/`, the
  copies of `frontend/src/bazis/client/`, `frontend/src/bazis/react/`,
  `frontend/e2e/bazis/`, `frontend/bazis-front.lock.json` or `frontend/.bazis/`: generate
  them again with the commands, and wrap the copies in the product code.
- The backend decides the permissions (bazis-permit): never encode roles or permission
  rules in the frontend. The screens use the tokens of the design, never literal colors.
- The permit roles with their permissions and the statuses and transits of the workflows
  are data migrations: the contract is exported from the migrated database. The test
  data of the end-to-end tests is the e2e data command recommended by bazis-front,
  `.venv/bin/python manage.py e2e_data`: on top of the migrations it creates only a user
  per `test_user` of the roles, with its role and the password of the environment
  variable `E2E_PASSWORD`, and the records the scenarios need (the items they open). It
  can run again (it sets the passwords again).
- After changing the frontend run in `frontend/` `npx tsc --noEmit`, `npm run lint` and
  `npm test`; after changing the specs or the screens also `bazis_front e2e` and
  `npm run e2e` against the running backend: migrate, run `e2e_data`, start
  `.venv/bin/uvicorn <project package>.main:app --port 8000` in the background, run
  `E2E_PASSWORD=<the same password> npm run e2e`, then stop the backend.
- Node.js with npm is needed by `npm install`, the TypeScript of the contract
  (`schema.d.ts`), the build and the tests of the frontend, and the contract by a migrated
  database. Without them do the other steps and say which ones did not run.
"""


@dataclass
class Task:
    """
    What the agent is asked to do in a project.
    """

    prompt: str
    project_dir: Path
    #: the agent may change files and run commands
    writes: bool = True
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    max_budget_usd: float | None = None
    #: run shell commands without asking
    yes: bool = False
    extra_rules: list[str] = field(default_factory=list)


def mcp_server(project_dir: Path) -> dict:
    """
    The stdio MCP server bazis-mcp of the project, run with the Python of the CLI (it uses
    `.venv` of the project for the project tools).
    """
    return {
        'type': 'stdio',
        'command': sys.executable,
        'args': ['-m', 'bazis.contrib.mcp.server', '--project-dir', str(project_dir)],
    }


def _path_rule(tool: str, path: Path) -> str:
    """
    A permission rule for an absolute path (`//` starts an absolute path in the rules).
    """
    return f'{tool}(//{path.resolve().as_posix().lstrip("/")})'


def options(task: Task, asker: 'Asker | None' = None) -> ClaudeAgentOptions:
    """
    The options of the agent for a task. Only the MCP server bazis-mcp of the CLI is used
    (`strict_mcp_config`) and no settings of the project or the user are loaded, so the
    permissions below are the only ones.

    A read-only task can only read the project and call the MCP tools. A task that writes
    gets the edits of files in the project accepted (`acceptEdits`); everything else is
    asked: shell commands through a PreToolUse hook (the CLI would otherwise allow commands
    such as `rm` or `sed` in `acceptEdits` mode), the other tools through `can_use_tool`.
    In both, `.env` cannot be read and git cannot change the repository.
    """
    rules = '\n'.join([RULES, *task.extra_rules])
    secrets = [_path_rule(tool, task.project_dir / '.env') for tool in ('Read', 'Edit')]
    common = dict(
        cwd=str(task.project_dir),
        model=task.model,
        effort=task.effort,
        max_budget_usd=task.max_budget_usd,
        mcp_servers={'bazis': mcp_server(task.project_dir)},
        strict_mcp_config=True,
        setting_sources=[],
        system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': rules},
        allowed_tools=['mcp__bazis'],
    )
    if not task.writes:
        return ClaudeAgentOptions(
            disallowed_tools=[*WRITE_TOOLS, *secrets],
            permission_mode='dontAsk',
            **common,
        )
    asker = asker or Asker(task.yes)
    return ClaudeAgentOptions(
        disallowed_tools=[*GIT_DENY, *secrets],
        permission_mode='acceptEdits',
        can_use_tool=asker.can_use_tool,
        hooks={'PreToolUse': [HookMatcher(matcher='Bash', hooks=[asker.bash_hook])]},
        **common,
    )


class Asker:
    """
    Asks the user in the terminal before the tools that are not allowed in advance: `y`
    allows the call, `a` allows the rest of the run. With `yes` everything is allowed;
    without a terminal everything is denied. Commands that change the git repository are
    always denied.
    """

    def __init__(self, yes: bool, stdin: TextIO | None = None, out: TextIO | None = None):
        self.allow_all = yes
        self.stdin = stdin or sys.stdin
        self.out = out or sys.stderr

    def decide(self, tool_name: str, tool_input: dict) -> str | None:
        """
        None if allowed, else the reason of the denial.
        """
        command = tool_input.get('command', '') if tool_name == 'Bash' else ''
        if GIT_COMMAND_RE.search(command):
            return 'The user commits the changes: git commands that change the repository are not allowed.'
        if self.allow_all:
            return None
        if not self.stdin.isatty():
            return 'Not allowed: no terminal to ask the user (run bazis with --yes to allow).'
        details = command or json.dumps(tool_input, ensure_ascii=False, indent=2)
        why = tool_input.get('description')
        self.out.write(f'\n? {tool_name}' + (f' — {why}' if why else '') + '\n')
        self.out.write(''.join(f'    {line}\n' for line in details.splitlines()))
        self.out.write('  allow [y/N/a=all]: ')
        self.out.flush()
        answer = self.stdin.readline().strip().lower()
        if answer == 'a':
            self.allow_all = True
        if answer in ('y', 'yes', 'a'):
            return None
        return 'The user did not allow it.'

    async def can_use_tool(self, tool_name: str, tool_input: dict, context) -> object:
        reason = self.decide(tool_name, tool_input)
        if reason is None:
            return PermissionResultAllow()
        return PermissionResultDeny(message=reason)

    async def bash_hook(self, hook_input: dict, tool_use_id, context) -> dict:
        reason = self.decide('Bash', hook_input.get('tool_input', {}))
        return {
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'allow' if reason is None else 'deny',
                'permissionDecisionReason': reason or 'Allowed by the user.',
            }
        }


def describe(tool_name: str, tool_input: dict) -> str:
    """
    One line about a tool call, for the progress output.
    """
    for key in ('command', 'file_path', 'path', 'pattern', 'url', 'name'):
        if value := tool_input.get(key):
            lines = str(value).splitlines()
            more = ' ...' if len(lines) > 1 else ''
            return lines[0][:200] + more
    return ''


def render(message, out: TextIO | None = None) -> ResultMessage | None:
    """
    Prints the text of the agent and one line per tool call; returns the final result.
    """
    out = out or sys.stdout
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                out.write(block.text.rstrip() + '\n')
            elif isinstance(block, ToolUseBlock):
                name = block.name.removeprefix('mcp__bazis__')
                out.write(f'  > {name} {describe(block.name, block.input)}'.rstrip() + '\n')
        out.flush()
    elif isinstance(message, ResultMessage):
        return message
    return None


async def run(task: Task, out: TextIO | None = None) -> ResultMessage | None:
    """
    Runs the task and prints the progress; returns the final result of the agent.
    """
    result = None
    # the read-only MCP tools of bazis-mcp are allowed in advance on purpose
    warnings.filterwarnings('ignore', category=CanUseToolShadowedWarning)
    try:
        async for message in query(prompt=task.prompt, options=options(task)):
            result = render(message, out) or result
    except ResultError:
        # the CLI exits with an error after a failed result (e.g. the budget is spent):
        # the result says why
        if result is None:
            raise
    return result


def error_text(result: ResultMessage) -> str:
    """
    Why a run failed.
    """
    reasons = {
        'error_max_budget_usd': 'the budget (--budget) is spent',
        'error_max_turns': 'the agent reached the maximum number of turns',
    }
    return (
        '; '.join(result.errors or [])
        or reasons.get(result.subtype)
        or result.result
        or result.subtype
    )
