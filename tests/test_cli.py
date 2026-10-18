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

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from bazis.contrib.cli import agent, main
from bazis.contrib.mcp import prompts, scaffold
from bazis.contrib.mcp.rules import FRONTEND_RULES
from bazis.core.introspect import validate_manifest


def result_message(**kwargs) -> ResultMessage:
    values = dict(
        subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=3,
        session_id='s', total_cost_usd=0.5, result='done',
    )
    return ResultMessage(**{**values, **kwargs})


@pytest.fixture
def fake_agent(monkeypatch):
    """
    Replaces the Claude Agent SDK: records the prompt and the options, yields messages.
    """
    calls = []

    async def query(prompt, options):
        calls.append((prompt, options))
        yield AssistantMessage(
            content=[
                TextBlock('Reading the guide.'),
                ToolUseBlock('1', 'mcp__bazis__package_guide', {'name': 'bazis'}),
                ToolUseBlock('2', 'Bash', {'command': '.venv/bin/python manage.py check'}),
            ],
            model=agent.DEFAULT_MODEL,
        )
        yield result_message()

    monkeypatch.setattr(agent, 'query', query)
    return calls


def test_manifest_is_valid():
    assert validate_manifest('bazis.contrib.cli') == []


#: what the fake `scaffold.create_database` of the tests did
DATABASE_CREATED = 'Created the database `library` on PostgreSQL localhost:5432.'


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    """
    Node.js is found unless a test says otherwise, bazis-front is that of PyPI, and
    `bazis new` creates no database (the calls of `create_database` are recorded).
    """
    monkeypatch.setattr(scaffold, 'node_problem', lambda: None)
    monkeypatch.delenv(scaffold.FRONTEND_REQUIREMENT_ENV, raising=False)
    calls = []

    def fake_create_database(directory, name, python=sys.executable):
        calls.append((directory, name, python))
        return DATABASE_CREATED

    monkeypatch.setattr(scaffold, 'create_database', fake_create_database)
    return calls


#: tools of bazis-mcp that read: allowed in advance
MCP_READ = ['mcp__bazis__package_guide', 'mcp__bazis__run_doctor', 'mcp__bazis__read_file']
#: tools of bazis-mcp for a client without a shell: the agent never gets them
MCP_WORK = [
    'mcp__bazis__run_command', 'mcp__bazis__write_file', 'mcp__bazis__delete_file',
    'mcp__bazis__create_project',
]


@pytest.mark.anyio
async def test_options_of_a_task_that_writes(tmp_path):
    options = await agent.options(
        agent.Task('build', tmp_path, model='claude-sonnet-5-5', session_id='s')
    )

    assert options.cwd == str(tmp_path)
    assert options.model == 'claude-sonnet-5-5'
    assert options.permission_mode == 'acceptEdits'
    # only the MCP tools that read are allowed in advance: edits outside the project, reads
    # outside it and everything else reach can_use_tool, shell commands the Bash hook
    assert set(MCP_READ) <= set(options.allowed_tools)
    assert all(name.startswith('mcp__bazis__') for name in options.allowed_tools)
    assert options.can_use_tool is not None
    assert options.hooks['PreToolUse'][0].matcher == 'Bash'
    assert f'Read(/{tmp_path.resolve()}/.env)' in options.disallowed_tools
    assert 'Bash(git push:*)' in options.disallowed_tools
    # the agent runs the commands and changes the files itself, never through bazis-mcp
    assert set(MCP_WORK) <= set(options.disallowed_tools)
    assert agent.MCP_RULE in options.system_prompt['append']
    # no settings or MCP servers of the project
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    server = options.mcp_servers['bazis']
    assert server['args'][-2:] == ['--project-dir', str(tmp_path)]
    assert 'package_guide' in options.system_prompt['append']
    # a resumed session gets the rules of this run, not those it recorded
    assert options.resume == 's'
    assert options.system_prompt['snapshot'] is False


@pytest.mark.anyio
async def test_options_of_a_read_only_task(tmp_path):
    options = await agent.options(agent.Task('audit', tmp_path, writes=False))

    assert options.permission_mode == 'dontAsk'
    assert options.can_use_tool is None
    assert not options.hooks
    assert set(MCP_READ) <= set(options.allowed_tools)
    assert all(name.startswith('mcp__bazis__') for name in options.allowed_tools)
    assert {'Write', 'Edit', 'Bash', 'WebFetch'} <= set(options.disallowed_tools)
    assert set(MCP_WORK) <= set(options.disallowed_tools)
    assert f'Read(/{tmp_path.resolve()}/.env)' in options.disallowed_tools


@pytest.mark.anyio
@pytest.mark.parametrize('writes', [True, False])
async def test_only_the_mcp_tools_with_read_only_hint_are_allowed(tmp_path, monkeypatch, writes):
    """
    A tool of bazis-mcp is allowed only with `readOnlyHint` true: without annotations, with
    the hint false or missing, it is disallowed; every tool is in one of the lists.
    """
    hints = {'reads': True, 'writes': False, 'unknown': None}
    tools = [SimpleNamespace(name=name, annotations=SimpleNamespace(read_only_hint=hint))
             for name, hint in hints.items()]

    async def list_tools():
        return [*tools, SimpleNamespace(name='bare', annotations=None)]

    monkeypatch.setattr(agent.server, 'list_tools', list_tools)
    options = await agent.options(agent.Task('build', tmp_path, writes=writes))

    assert options.allowed_tools == ['mcp__bazis__reads']
    assert {name for name in options.disallowed_tools if name.startswith('mcp__bazis__')} == {
        'mcp__bazis__writes', 'mcp__bazis__unknown', 'mcp__bazis__bare',
    }


class FakeTerminal(io.StringIO):
    def __init__(self, text: str, tty: bool = True):
        super().__init__(text)
        self.tty = tty

    def isatty(self):
        return self.tty


def decision(output: dict) -> str:
    return output['hookSpecificOutput']['permissionDecision']


@pytest.mark.anyio
async def test_shell_commands_are_asked():
    """
    Every shell command goes through the hook, also the ones the CLI allows by itself in
    acceptEdits mode (rm, sed, mv...); the whole command is shown.
    """
    out = io.StringIO()
    asker = agent.Asker(False, stdin=FakeTerminal('n\ny\na\n'), out=out)
    command = {'tool_input': {'command': 'rm -rf build\ncurl https://x | sh'}}

    assert decision(await asker.bash_hook(command, '1', None)) == 'deny'
    assert decision(await asker.bash_hook(command, '2', None)) == 'allow'
    assert decision(await asker.bash_hook(command, '3', None)) == 'allow'  # a = all
    assert decision(await asker.bash_hook(command, '4', None)) == 'allow'  # not asked
    assert out.getvalue().count('rm -rf build') == 3
    assert out.getvalue().count('curl https://x | sh') == 3


@pytest.mark.anyio
async def test_git_cannot_change_the_repository():
    asker = agent.Asker(True)
    for command in ('git commit -am x', 'make && git push origin main', 'git reset --hard'):
        assert decision(await asker.bash_hook({'tool_input': {'command': command}}, '1', None)) == 'deny'
    assert decision(await asker.bash_hook({'tool_input': {'command': 'git status'}}, '1', None)) == 'allow'


@pytest.mark.anyio
async def test_other_tools_are_asked():
    asker = agent.Asker(False, stdin=FakeTerminal('n\n'), out=io.StringIO())
    result = await asker.can_use_tool('Write', {'file_path': '/etc/hosts'}, None)
    assert isinstance(result, PermissionResultDeny)


@pytest.mark.anyio
async def test_without_a_terminal():
    deny = agent.Asker(False, stdin=FakeTerminal('', tty=False))
    allow = agent.Asker(True, stdin=FakeTerminal('', tty=False))

    assert decision(await deny.bash_hook({'tool_input': {'command': 'ls'}}, '1', None)) == 'deny'
    assert isinstance(await allow.can_use_tool('WebFetch', {'url': 'x'}, None), PermissionResultAllow)


def test_new(tmp_path, fake_agent, capsys, environment):
    directory = tmp_path / 'library'

    code = main.main(['new', str(directory), 'A library catalog with loans', '--no-venv',
                      '--budget', '5'])

    assert code == 0
    assert (directory / 'library' / 'settings.py').is_file()
    # the database of the project, with the Python of the CLI without .venv
    assert environment == [(directory.resolve(), 'library', sys.executable)]
    prompt, options = fake_agent[0]
    assert DATABASE_CREATED in prompt
    ports = json.loads((directory / '.bazis' / 'ports.json').read_text())
    assert prompts.ports_text(ports) in prompt
    assert 'A library catalog with loans' in prompt
    assert options.cwd == str(directory.resolve())
    assert options.max_budget_usd == 5
    # a product with its frontend by default
    assert 'bazis-front' in (directory / 'requirements.txt').read_text()
    assert FRONTEND_RULES in options.system_prompt['append']
    out = capsys.readouterr()
    # the language of the product is that of the description
    assert 'the language of the description' in prompt and 'BS_LANGUAGE_CODE=<code>' in prompt
    assert 'BS_LANGUAGE_CODE=en\n' in (directory / 'project.env').read_text()
    assert DATABASE_CREATED in out.out
    assert 'Reading the guide.' in out.out
    assert '> package_guide bazis' in out.out
    assert '> Bash .venv/bin/python manage.py check' in out.out
    assert '[3 turns, $0.50]' in out.err
    # the facts of the run, for the measurements
    facts = json.loads((directory / scaffold.LOCAL_DIR / agent.RUN_FILE).read_text())
    assert facts['turns'] == 3 and facts['cost_usd'] == 0.5 and facts['duration_ms'] == 1
    assert facts['tools'] == {'package_guide': 1, 'Bash': 1}
    assert facts['model'] == agent.DEFAULT_MODEL and facts['error'] is None


def test_new_without_a_frontend(tmp_path, fake_agent):
    directory = tmp_path / 'library'
    assert main.main(['new', str(directory), 'A library', '--no-venv', '--no-frontend']) == 0

    prompt, options = fake_agent[0]
    assert 'bazis_front' not in prompt
    assert 'bazis-front' not in (directory / 'requirements.txt').read_text()
    assert FRONTEND_RULES not in options.system_prompt['append']


def test_new_without_node(tmp_path, fake_agent, monkeypatch, capsys):
    """
    Without Node.js the product cannot get its frontend: nothing is written.
    """
    monkeypatch.setattr(scaffold, 'node_problem', lambda: 'node and npm not found: ...')
    directory = tmp_path / 'library'

    assert main.main(['new', str(directory), 'A library', '--no-venv']) == 1
    err = capsys.readouterr().err
    assert 'node and npm not found' in err and '--no-frontend' in err
    assert not directory.exists()
    assert fake_agent == []

    # the backend alone needs no Node
    assert main.main(['new', str(directory), 'A library', '--no-venv', '--no-frontend']) == 0


def test_new_with_a_language(tmp_path, fake_agent):
    directory = tmp_path / 'library'
    args = ['new', str(directory), 'Каталог библиотеки', '--language', 'ru-RU', '--no-venv']
    assert main.main(args) == 0

    assert 'BS_LANGUAGE_CODE=ru\n' in (directory / 'project.env').read_text(encoding='utf-8')
    prompt, _ = fake_agent[0]
    assert 'The product is in Русский (`ru`) and English' in prompt
    assert 'the language of the description' not in prompt


def test_new_with_an_unknown_language(tmp_path, fake_agent, capsys):
    directory = tmp_path / 'library'
    assert main.main(['new', str(directory), 'A library', '--language', 'xx', '--no-venv']) == 1
    assert 'not a language of Django' in capsys.readouterr().err
    assert not directory.exists()
    assert fake_agent == []


def test_new_with_a_name(tmp_path, fake_agent):
    args = ['new', str(tmp_path / 'x'), 'anything', '--name', 'catalog', '--no-venv']
    assert main.main(args) == 0
    assert (tmp_path / 'x' / 'catalog' / 'settings.py').is_file()


def test_add(tmp_path, fake_agent):
    assert main.main(['add', 'bazis-permit', 'users see only their orders',
                      '--project-dir', str(tmp_path), '--yes']) == 0

    prompt, options = fake_agent[0]
    assert 'package_guide("bazis-permit")' in prompt
    assert 'users see only their orders' in prompt
    assert options.permission_mode == 'acceptEdits'
    assert 'bazis_front' not in prompt
    assert FRONTEND_RULES not in options.system_prompt['append']


def with_frontend(directory: Path) -> Path:
    (directory / 'frontend').mkdir()
    (directory / 'frontend' / 'bazis-front.lock.json').write_text('{}')
    return directory


def test_add_to_a_product_with_a_frontend(tmp_path, fake_agent, capsys):
    assert main.main(['add', 'bazis-statusy', '--project-dir', str(with_frontend(tmp_path))]) == 0

    prompt, options = fake_agent[0]
    backend, frontend = prompt.split('The product has a frontend')
    assert 'package_guide("bazis-statusy")' in backend
    # a project made before the ports gets them
    assert prompts.ports_text(scaffold.project_ports(tmp_path)) in frontend
    # the screens generated again, then the whole round of the final checks (bazis-mcp 2.6)
    frontend = ' '.join(frontend.split())
    order = ['spec/', 'bazis_front contract', 'front_check', 'bazis_front screens',
             'bazis_front e2e', 'e2e_data', 'the whole round of the final checks of the rules',
             'npm run build', 'bazis_front screens --check']
    positions = [frontend.index(text) for text in order]
    assert positions == sorted(positions)
    assert FRONTEND_RULES in options.system_prompt['append']
    assert 'warning' not in capsys.readouterr().err
    assert 'Install bazis-front from the requirement' not in prompt


def test_add_the_frontend(tmp_path, fake_agent):
    """
    `bazis add bazis-front` to a backend: the frontend is built as by `bazis new`.
    """
    assert main.main(['add', 'bazis-front', 'a portal for the clients',
                      '--project-dir', str(tmp_path)]) == 0

    prompt, options = fake_agent[0]
    assert 'package_guide("bazis-front")' in prompt
    assert 'Then build the frontend of the product' in prompt
    assert '"Building the frontend of a product"' in prompt
    assert 'the final checks of the rules' in prompt
    assert 'the specs describe the backend and what the project needs it for' in prompt
    assert f'requirement `{scaffold.FRONTEND_REQUIREMENT}`' in prompt
    assert FRONTEND_RULES in options.system_prompt['append']


def test_add_the_frontend_from_a_build(tmp_path, fake_agent, monkeypatch):
    """
    `bazis add bazis-front` installs the bazis-front of BAZIS_FRONT_REQUIREMENT too.
    """
    monkeypatch.setenv(scaffold.FRONTEND_REQUIREMENT_ENV, '/wheels/bazis_front-0.1.0.whl')
    assert main.main(['add', 'bazis-front', '--project-dir', str(tmp_path)]) == 0

    prompt, _ = fake_agent[0]
    assert 'requirement `/wheels/bazis_front-0.1.0.whl`' in prompt
    assert scaffold.FRONTEND_REQUIREMENT not in prompt


def test_add_without_node(tmp_path, fake_agent, monkeypatch, capsys):
    """
    In a product with a frontend, the agent runs without Node.js and says what did not run.
    """
    monkeypatch.setattr(scaffold, 'node_problem', lambda: 'node and npm not found: ...')
    assert main.main(['add', 'bazis-statusy', '--project-dir', str(with_frontend(tmp_path))]) == 0
    assert 'warning: node and npm not found' in capsys.readouterr().err
    assert len(fake_agent) == 1


@pytest.mark.parametrize('fix', [False, True])
def test_audit(tmp_path, fake_agent, fix):
    args = ['audit', '--project-dir', str(tmp_path)] + (['--fix'] if fix else [])
    assert main.main(args) == 0

    prompt, options = fake_agent[0]
    assert ('Change nothing' in prompt) is not fix
    assert (options.permission_mode == 'acceptEdits') is fix
    assert 'front_check' not in prompt
    assert '3. Report the problems' in prompt


@pytest.mark.parametrize('fix', [False, True])
def test_audit_of_a_product_with_a_frontend(tmp_path, fake_agent, fix):
    args = ['audit', '--project-dir', str(with_frontend(tmp_path))] + (['--fix'] if fix else [])
    assert main.main(args) == 0

    prompt, options = fake_agent[0]
    for text in ('front_check', 'front_status', 'package_guide("bazis-front")'):
        assert text in prompt
    assert '4. Report the problems' in prompt
    # a review cannot run commands: it lists them; a fix runs the final checks of the rules
    assert ('cannot run' in prompt) is not fix
    assert ('npm run build' in prompt) is not fix
    assert ('`.bazis/ports.json`' in prompt) is fix
    assert ('the final checks of the rules' in prompt) is fix
    assert FRONTEND_RULES in options.system_prompt['append']


def test_failed_run(tmp_path, monkeypatch, capsys):
    """
    The CLI reports a failed result, then exits with an error: the result says why.
    """
    from claude_agent_sdk import ResultError

    async def query(prompt, options):
        yield result_message(is_error=True, subtype='error_max_budget_usd', result=None)
        raise ResultError('Claude Code returned an error result', {'subtype': 'error_max_budget_usd'})

    monkeypatch.setattr(agent, 'query', query)

    assert main.main(['audit', '--project-dir', str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert 'the budget (--budget) is spent' in err
    assert '[3 turns, $0.50]' in err
    assert 'claude login' not in err
    facts = json.loads((tmp_path / scaffold.LOCAL_DIR / agent.RUN_FILE).read_text())
    assert facts['subtype'] == 'error_max_budget_usd'
    assert facts['error'] == 'the budget (--budget) is spent'


def test_new_in_a_directory_with_files(tmp_path, fake_agent, capsys):
    (tmp_path / 'x.txt').write_text('x')

    assert main.main(['new', str(tmp_path), 'anything', '--no-venv']) == 1
    assert 'is not empty' in capsys.readouterr().err
    assert fake_agent == []


@pytest.mark.parametrize('args, directory, hint', [
    (['new', 'my library', 'A library', '--no-venv', '--no-frontend'], 'my library',
     "cd 'my library' && bazis change"),
    (['add', 'bazis-permit', '--project-dir', 'my library'], 'my library',
     "cd 'my library' && bazis change"),
    (['add', 'bazis-permit'], '.', 'bazis change'),
])
def test_how_to_change_the_project(
    tmp_path, fake_agent, capsys, monkeypatch, args, directory, hint
):
    """
    After `bazis new` and `bazis add` the user is told how to go on changing the project,
    with the directory he gave.
    """
    monkeypatch.chdir(tmp_path)
    assert main.main(args) == 0
    assert f'To change the project: {hint}\n' in capsys.readouterr().err
    assert fake_agent[0][1].cwd == str((tmp_path / directory).resolve())


@pytest.fixture
def sessions(monkeypatch):
    """
    The sessions whose transcripts Claude Code has on this machine.
    """
    known = set()
    monkeypatch.setattr(
        agent, 'get_session_info',
        lambda session_id, directory: object() if session_id in known else None,
    )
    return known


def test_change(tmp_path, fake_agent, sessions, capsys):
    """
    A change continues the session of the last run of bazis in the project when Claude Code
    can resume it: the request goes as it is. In a new session (none, one that cannot be
    resumed, `--new-session`) the agent first learns the project.
    """
    project = ['--project-dir', str(tmp_path), '--yes']

    # no run yet: a new session with the preparation
    assert main.main(['change', 'Loans last two weeks', *project]) == 0
    prompt, options = fake_agent[-1]
    assert options.resume is None
    assert 'Change this Bazis project: Loans last two weeks' in prompt
    assert 'project_info' in prompt and 'Finish with the final checks of the rules' in prompt
    assert options.permission_mode == 'acceptEdits'  # the options of a task that writes
    assert FRONTEND_RULES not in options.system_prompt['append']
    facts = json.loads((tmp_path / scaffold.LOCAL_DIR / agent.RUN_FILE).read_text())
    assert facts['session_id'] == 's'

    # the session of the last run, but its transcript is not on this machine
    assert main.main(['change', 'Add the authors', *project]) == 0
    assert fake_agent[-1][1].resume is None
    assert 'Change this Bazis project' in fake_agent[-1][0]

    sessions.add('s')
    assert main.main(['change', 'Add the authors', *project, '--budget', '2']) == 0
    prompt, options = fake_agent[-1]
    assert options.resume == 's' and options.max_budget_usd == 2
    assert prompt == 'Add the authors\n\nAfter the change, run the final checks of the rules once.\n'

    assert main.main(['change', 'Add the authors', *project, '--new-session']) == 0
    assert fake_agent[-1][1].resume is None
    assert 'Change this Bazis project' in fake_agent[-1][0]
    assert 'To change the project' not in capsys.readouterr().err


def test_change_of_a_product_with_a_frontend(tmp_path, fake_agent, sessions):
    """
    The preparation of a change of a product with a frontend has the steps of `bazis add`
    and the ports; the rules of the frontend apply in every session.
    """
    assert main.main(['change', 'Loans of rare books need an approval',
                      '--project-dir', str(with_frontend(tmp_path))]) == 0

    prompt, options = fake_agent[-1]
    assert prompts.frontend_change('the change') in prompt
    assert prompts.ports_text(scaffold.project_ports(tmp_path)) in prompt
    assert 'Finish with the final checks' not in prompt
    assert FRONTEND_RULES in options.system_prompt['append']

    sessions.add('s')
    assert main.main(['change', 'Show the approvals', '--project-dir', str(tmp_path)]) == 0
    prompt, options = fake_agent[-1]
    assert options.resume == 's' and 'bazis_front' not in prompt
    assert FRONTEND_RULES in options.system_prompt['append']


def session_agent(monkeypatch, results: list):
    """
    Replaces the Claude Agent SDK with an agent whose runs end with `results`, in turn: a
    ResultMessage, or an exception it raises. Returns the recorded calls.
    """
    calls = []

    async def query(prompt, options):
        calls.append((prompt, options))
        outcome = results.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        yield outcome

    monkeypatch.setattr(agent, 'query', query)
    return calls


def test_cost_of_a_run_in_a_resumed_session(tmp_path, monkeypatch, sessions, capsys):
    """
    Claude Code reports the cost of the whole resumed session: the run costs what it adds.
    """
    session_agent(monkeypatch, [result_message(total_cost_usd=0.5),
                                result_message(total_cost_usd=0.8, num_turns=2)])
    sessions.add('s')
    project = ['--project-dir', str(tmp_path)]

    assert main.main(['change', 'Loans last two weeks', *project]) == 0
    assert main.main(['change', 'Add the authors', *project]) == 0

    assert '[2 turns, $0.30]' in capsys.readouterr().err
    facts = json.loads((tmp_path / scaffold.LOCAL_DIR / agent.RUN_FILE).read_text())
    assert facts['cost_usd'] == pytest.approx(0.3) and facts['session_cost_usd'] == 0.8


def test_change_in_the_terminal(tmp_path, monkeypatch, sessions, capsys):
    """
    Without a request the changes are typed one after another, each in the session of the
    run before it; a failed or interrupted run does not end the work, an empty line does.
    """
    calls = session_agent(monkeypatch, [
        result_message(session_id='a', is_error=True, subtype='error_max_budget_usd', result=None),
        KeyboardInterrupt(),
        result_message(session_id='a', total_cost_usd=0.75),
    ])
    monkeypatch.setattr(sys, 'stdin', FakeTerminal('Loans last two weeks\nAdd the authors\n'
                                                   '  Add the reviews  \n\nnot asked\n'))

    assert main.main(['change', '--project-dir', str(tmp_path), '--new-session']) == 0

    assert [prompt.split('\n')[0] for prompt, _ in calls] == [
        'Change this Bazis project: Loans last two weeks', 'Add the authors', 'Add the reviews',
    ]
    assert [options.resume for _, options in calls] == [None, 'a', 'a']
    out = capsys.readouterr()
    assert out.out.count('change> ') == 4
    assert 'the budget (--budget) is spent' in out.err
    assert 'interrupted' in out.err
    assert '[3 turns, $0.25]' in out.err


@pytest.mark.parametrize('typed, code', [('exit\n', 0), ('', 0)])
def test_change_in_the_terminal_ends(tmp_path, fake_agent, monkeypatch, typed, code):
    monkeypatch.setattr(sys, 'stdin', FakeTerminal(typed))
    assert main.main(['change', '--project-dir', str(tmp_path)]) == code
    assert fake_agent == []


def test_change_without_a_terminal(tmp_path, fake_agent, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', FakeTerminal('Add the authors\n', tty=False))
    assert main.main(['change', '--project-dir', str(tmp_path)]) == 1
    assert 'bazis change "..."' in capsys.readouterr().err
    assert fake_agent == []

