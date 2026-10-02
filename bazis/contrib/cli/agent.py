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

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultError,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)


DEFAULT_MODEL = 'claude-opus-5-5'
DEFAULT_EFFORT = 'high'

#: tools that only read: the MCP server and the files of the project
READ_ONLY_TOOLS = ['Read', 'Grep', 'Glob', 'mcp__bazis']
#: tools that change the project without a shell; edits are accepted in the project directory
WRITE_TOOLS = ['Write', 'Edit', 'MultiEdit']

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
- The Python environment is `.venv`: run `.venv/bin/python` and `.venv/bin/pip`. Add a
  package to `requirements.txt`, install it with `.venv/bin/pip install -r requirements.txt`
  and list its app in `BS_INSTALLED_APPS` of `project.env` when its guide says so.
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


def options(task: Task, ask=None) -> ClaudeAgentOptions:
    """
    The options of the agent for a task. A read-only task cannot change files or run
    commands; otherwise file edits in the project are accepted and every shell command goes
    through `ask` (unless `yes`).
    """
    rules = '\n'.join([RULES, *task.extra_rules])
    common = dict(
        cwd=str(task.project_dir),
        model=task.model,
        effort=task.effort,
        max_budget_usd=task.max_budget_usd,
        mcp_servers={'bazis': mcp_server(task.project_dir)},
        system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': rules},
        setting_sources=['project'],
    )
    if not task.writes:
        return ClaudeAgentOptions(
            allowed_tools=READ_ONLY_TOOLS,
            disallowed_tools=[*WRITE_TOOLS, 'Bash', 'NotebookEdit'],
            permission_mode='dontAsk',
            **common,
        )
    return ClaudeAgentOptions(
        allowed_tools=[*READ_ONLY_TOOLS, *WRITE_TOOLS],
        permission_mode='acceptEdits',
        can_use_tool=ask or ask_in_terminal(task.yes),
        **common,
    )


def ask_in_terminal(yes: bool, stdin: TextIO | None = None, out: TextIO | None = None):
    """
    The permission callback for the tools that are not allowed in advance (shell commands,
    web access): allowed with `yes`, else asked in the terminal (`a` allows the rest of the
    run). Without a terminal they are denied.
    """
    allow_all = yes
    stdin = stdin or sys.stdin
    out = out or sys.stderr

    async def ask(tool_name: str, tool_input: dict, context) -> object:
        nonlocal allow_all
        if allow_all:
            return PermissionResultAllow()
        if not stdin.isatty():
            return PermissionResultDeny(
                message='Not allowed: no terminal to ask the user (run with --yes to allow).'
            )
        out.write(f'\n? {tool_name}: {describe(tool_name, tool_input)}\n  allow [y/N/a=all]: ')
        out.flush()
        answer = stdin.readline().strip().lower()
        if answer == 'a':
            allow_all = True
        if answer in ('y', 'yes', 'a'):
            return PermissionResultAllow()
        return PermissionResultDeny(message='The user did not allow it.')

    return ask


def describe(tool_name: str, tool_input: dict) -> str:
    """
    One line about a tool call.
    """
    for key in ('command', 'file_path', 'path', 'pattern', 'url', 'name'):
        if value := tool_input.get(key):
            return str(value).splitlines()[0][:200]
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
