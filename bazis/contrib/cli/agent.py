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
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    get_session_info,
    query,
)
from claude_agent_sdk.types import CanUseToolShadowedWarning

from bazis.contrib.mcp import scaffold
from bazis.contrib.mcp.rules import RULES
from bazis.contrib.mcp.server import server


DEFAULT_MODEL = 'claude-opus-5-5'
DEFAULT_EFFORT = 'high'

#: git commands that change the repository: the user commits, never the agent
GIT_DENY = ['Bash(git commit:*)', 'Bash(git push:*)', 'Bash(git reset:*)', 'Bash(git rebase:*)']
#: tools a review cannot use
WRITE_TOOLS = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash', 'WebFetch', 'WebSearch']
#: the instructions of bazis-mcp tell a client to run commands and change files with the
#: tools of the server: not for this agent
MCP_RULE = """\
- The MCP server `bazis` gives you only its tools that read: the instructions of the server
  about its tools that run commands, start servers, install or change files do not apply
  here. You have a shell: run the commands of these rules and change the files with your
  own tools, as far as the task allows.
"""
#: the facts of the last run, in the local directory of the project (`scaffold.local_dir`),
#: for the user, the measurements of the runs and the session that `bazis change` continues
RUN_FILE = 'run.json'
GIT_COMMAND_RE = re.compile(r'(^|[;&|(]\s*)git\s+(commit|push|reset|rebase)\b', re.MULTILINE)


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
    #: the session of Claude Code that the task continues (`last_session`), else a new one
    session_id: str | None = None


def mcp_server(project_dir: Path) -> dict:
    """
    The stdio MCP server bazis-mcp of the project, run with the Python of the CLI (it reads
    the installed packages and runs the project tools with `.venv` of the project).
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


async def options(task: Task, asker: 'Asker | None' = None) -> ClaudeAgentOptions:
    """
    The options of the agent for a task. Only the MCP server bazis-mcp of the CLI is used
    (`strict_mcp_config`) and no settings of the project or the user are loaded, so the
    permissions below are the only ones.

    The tools of bazis-mcp that read (`readOnlyHint`) are allowed in advance; the others,
    for a client without a shell (a new one too), are never given to the agent, which has
    the shell and the files of its project. A read-only task can only read the project and
    call those MCP tools. A task that writes gets the edits of files in the project accepted
    (`acceptEdits`); everything else is asked: shell commands through a PreToolUse hook
    (the CLI would otherwise allow commands such as `rm` or `sed` in `acceptEdits` mode),
    the other tools through `can_use_tool`. In both, `.env` cannot be read and git cannot change the repository.

    A task with a `session_id` resumes that session with these options: the rules of this
    run replace those the session recorded (`snapshot` off), and the budget counts only
    this run.
    """
    rules = '\n'.join([RULES, MCP_RULE, *task.extra_rules])
    tools = {f'mcp__bazis__{tool.name}': tool.annotations for tool in await server.list_tools()}
    reads = [name for name, hints in tools.items() if hints and hints.read_only_hint]
    denied = [
        *(_path_rule(tool, task.project_dir / '.env') for tool in ('Read', 'Edit')),
        *(name for name in tools if name not in reads),
    ]
    common = dict(
        cwd=str(task.project_dir),
        model=task.model,
        effort=task.effort,
        max_budget_usd=task.max_budget_usd,
        resume=task.session_id,
        mcp_servers={'bazis': mcp_server(task.project_dir)},
        strict_mcp_config=True,
        setting_sources=[],
        system_prompt={
            'type': 'preset', 'preset': 'claude_code', 'append': rules, 'snapshot': False,
        },
        allowed_tools=reads,
    )
    if not task.writes:
        return ClaudeAgentOptions(
            disallowed_tools=[*WRITE_TOOLS, *denied],
            permission_mode='dontAsk',
            **common,
        )
    asker = asker or Asker(task.yes)
    return ClaudeAgentOptions(
        disallowed_tools=[*GIT_DENY, *denied],
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


def render(message, out: TextIO | None = None, tools: Counter | None = None) -> ResultMessage | None:
    """
    Prints the text of the agent and one line per tool call, counted in `tools` by name;
    returns the final result.
    """
    out = out or sys.stdout
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                out.write(block.text.rstrip() + '\n')
            elif isinstance(block, ToolUseBlock):
                name = block.name.removeprefix('mcp__bazis__')
                if tools is not None:
                    tools[name] += 1
                out.write(f'  > {name} {describe(block.name, block.input)}'.rstrip() + '\n')
        out.flush()
    elif isinstance(message, ResultMessage):
        return message
    return None


def last_run(project_dir: Path) -> dict:
    """
    The facts of the last run in the project (RUN_FILE), empty without them.
    """
    try:
        return json.loads((project_dir / scaffold.LOCAL_DIR / RUN_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def last_session(project_dir: Path) -> str | None:
    """
    The session of the last run in the project when Claude Code can resume it (its
    transcript is on this machine), else None.
    """
    session = last_run(project_dir).get('session_id')
    if session and get_session_info(session, directory=str(project_dir)) is not None:
        return session
    return None


def write_run(
    task: Task, result: ResultMessage, tools: Counter, started: datetime, before: dict
) -> None:
    """
    Writes the facts of a run as JSON to RUN_FILE of the project: the session, the turns,
    the cost, the duration, the usage of the tokens and the calls of each tool. A resumed
    session reports the cost of the whole session: the cost of the run is what it adds to
    the cost of the session in the facts of the run `before`.
    """
    cost = result.total_cost_usd
    if task.session_id and before.get('session_id') == task.session_id:
        spent = before.get('session_cost_usd')
        # a session whose transcript has no total starts again from zero
        if cost is not None and spent is not None and spent <= cost:
            cost -= spent
    facts = {
        'started': started.isoformat(timespec='seconds'),
        'model': task.model,
        'effort': task.effort,
        'session_id': result.session_id,
        'subtype': result.subtype,
        'error': error_text(result) if result.is_error else None,
        'turns': result.num_turns,
        'cost_usd': cost,
        'session_cost_usd': result.total_cost_usd,
        'duration_ms': result.duration_ms,
        'duration_api_ms': result.duration_api_ms,
        'usage': result.usage,
        'model_usage': result.model_usage,
        'tools': dict(tools.most_common()),
    }
    path = scaffold.local_dir(task.project_dir) / RUN_FILE
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


async def run(task: Task, out: TextIO | None = None) -> ResultMessage | None:
    """
    Runs the task and prints the progress; returns the final result of the agent, whose
    facts are written to RUN_FILE.
    """
    result = None
    tools = Counter()
    started = datetime.now(UTC)
    before = last_run(task.project_dir)
    # the read-only MCP tools of bazis-mcp are allowed in advance on purpose
    warnings.filterwarnings('ignore', category=CanUseToolShadowedWarning)
    try:
        async for message in query(prompt=task.prompt, options=await options(task)):
            result = render(message, out, tools) or result
    except ResultError:
        # the CLI exits with an error after a failed result (e.g. the budget is spent):
        # the result says why
        if result is None:
            raise
    if result is not None:
        write_run(task, result, tools, started, before)
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
