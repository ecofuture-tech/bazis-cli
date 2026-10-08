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
import os
import subprocess
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from bazis.contrib.cli import agent, main, scaffold
from bazis.contrib.cli.scaffold import node_problem
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


def test_package_name(tmp_path):
    assert scaffold.package_name(tmp_path / 'My Shop-API') == 'my_shop_api'
    assert scaffold.package_name(tmp_path / '42') == 'project_42'
    # names that would hide a module, a keyword or the tests
    assert scaffold.package_name(tmp_path / 'django') == 'django_project'
    assert scaffold.package_name(tmp_path / 'class') == 'class_project'
    assert scaffold.package_name(tmp_path / 'tests') == 'tests_project'


def test_scaffold_refuses_a_bad_package_name(tmp_path):
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.write_files(tmp_path / 'x', 'json')


@pytest.fixture(autouse=True)
def node(monkeypatch):
    """
    Node.js is found unless a test says otherwise.
    """
    monkeypatch.setattr(scaffold, 'node_problem', lambda: None)


def doctor(directory: Path) -> list:
    """
    The messages of `bazis_doctor` of a project: its own settings only, but the libraries
    of this machine (GDAL, GEOS).
    """
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(('BS_', 'DJANGO_')) or k.endswith('_LIBRARY_PATH')
    }
    done = subprocess.run(
        [sys.executable, 'manage.py', 'bazis_doctor', '--json'],
        cwd=directory, env=env, capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout)


def test_scaffold_is_a_working_project(tmp_path):
    """
    The skeleton loads and passes the system checks without a database.
    """
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop')

    assert doctor(directory) == []
    assert (directory / '.gitignore').read_text().startswith('.env\n')
    assert 'BS_SECRET_KEY=' in (directory / '.env').read_text()
    assert json.loads((directory / '.mcp.json').read_text())['mcpServers']['bazis']


def test_scaffold_with_a_frontend(tmp_path, monkeypatch):
    monkeypatch.delenv(scaffold.FRONTEND_REQUIREMENT_ENV, raising=False)
    backend, product = tmp_path / 'backend', tmp_path / 'product'
    scaffold.write_files(backend, 'shop')
    scaffold.write_files(product, 'shop', frontend=True)

    assert 'bazis-front' not in (backend / 'requirements.txt').read_text()
    assert "BS_INSTALLED_APPS='[]'" in (backend / 'project.env').read_text()
    assert '## Frontend' not in (backend / 'AGENTS.md').read_text()

    assert (product / 'requirements.txt').read_text().splitlines() == [
        *scaffold.REQUIREMENTS, 'bazis-front>=0.1.0',
    ]
    assert """BS_INSTALLED_APPS='["bazis.contrib.front"]'""" in (product / 'project.env').read_text()
    agents_md = (product / 'AGENTS.md').read_text()
    assert '## Frontend' in agents_md and 'frontend/AGENTS.md' in agents_md
    # the agent creates the frontend with `bazis_front init`, after choosing the packages
    assert not (product / 'frontend').exists() and not (product / 'spec').exists()


def test_frontend_requirement_override(tmp_path, monkeypatch):
    """
    A bazis-front that is not on PyPI: a path or a URL instead of the requirement.
    """
    monkeypatch.setenv(scaffold.FRONTEND_REQUIREMENT_ENV, ' /wheels/bazis_front-0.1.0-py3-none-any.whl ')
    scaffold.write_files(tmp_path, 'shop', frontend=True)
    requirements = (tmp_path / 'requirements.txt').read_text().splitlines()
    assert requirements[-1] == '/wheels/bazis_front-0.1.0-py3-none-any.whl'
    assert not any(line.startswith('bazis-front') for line in requirements)

    monkeypatch.setenv(scaffold.FRONTEND_REQUIREMENT_ENV, '')
    assert scaffold.frontend_requirement() == scaffold.FRONTEND_REQUIREMENT


def test_scaffold_with_a_frontend_is_a_working_project(tmp_path):
    """
    With bazis-front installed, the skeleton of a product passes the system checks before
    its frontend exists.
    """
    pytest.importorskip('bazis.contrib.front')
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop', frontend=True)

    assert [m for m in doctor(directory) if m['level'] in ('error', 'critical', 'warning')] == []


def test_node_problem(monkeypatch):
    found = {'node': '/usr/bin/node', 'npm': '/usr/bin/npm'}
    monkeypatch.setattr(scaffold.shutil, 'which', found.get)
    assert node_problem() is None

    del found['npm']
    assert node_problem().startswith('npm not found')
    found.clear()
    assert node_problem().startswith('node and npm not found')


def test_scaffold_refuses_a_directory_with_files(tmp_path):
    (tmp_path / 'file.txt').write_text('x')
    with pytest.raises(scaffold.ScaffoldError):
        scaffold.write_files(tmp_path, 'x')


def test_create_venv_with_pip(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold.shutil, 'which', lambda name: None)
    monkeypatch.chdir(tmp_path.parent)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs['cwd'] == tmp_path
        return subprocess.CompletedProcess(command, 0, '', '')

    # a relative directory: the commands run in it, so their paths are absolute
    python = scaffold.create_venv(Path(tmp_path.name), run=run)

    assert commands[0] == [sys.executable, '-m', 'venv', str(tmp_path / '.venv')]
    assert commands[1][:3] == [str(python), '-m', 'pip']
    assert commands[1][-1] == str(tmp_path / 'requirements-dev.txt')


def test_options_of_a_task_that_writes(tmp_path):
    options = agent.options(agent.Task('build', tmp_path, model='claude-sonnet-5-5'))

    assert options.cwd == str(tmp_path)
    assert options.model == 'claude-sonnet-5-5'
    assert options.permission_mode == 'acceptEdits'
    # only the MCP tools are allowed in advance: edits outside the project, reads outside
    # it and everything else reach can_use_tool, shell commands the Bash hook
    assert options.allowed_tools == ['mcp__bazis']
    assert options.can_use_tool is not None
    assert options.hooks['PreToolUse'][0].matcher == 'Bash'
    assert f'Read(/{tmp_path.resolve()}/.env)' in options.disallowed_tools
    assert 'Bash(git push:*)' in options.disallowed_tools
    # no settings or MCP servers of the project
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    server = options.mcp_servers['bazis']
    assert server['args'][-2:] == ['--project-dir', str(tmp_path)]
    assert 'package_guide' in options.system_prompt['append']


def test_options_of_a_read_only_task(tmp_path):
    options = agent.options(agent.Task('audit', tmp_path, writes=False))

    assert options.permission_mode == 'dontAsk'
    assert options.can_use_tool is None
    assert not options.hooks
    assert options.allowed_tools == ['mcp__bazis']
    assert {'Write', 'Edit', 'Bash', 'WebFetch'} <= set(options.disallowed_tools)
    assert f'Read(/{tmp_path.resolve()}/.env)' in options.disallowed_tools


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


def test_new(tmp_path, fake_agent, capsys):
    directory = tmp_path / 'library'

    code = main.main(['new', str(directory), 'A library catalog with loans', '--no-venv',
                      '--budget', '5'])

    assert code == 0
    assert (directory / 'library' / 'settings.py').is_file()
    prompt, options = fake_agent[0]
    assert 'A library catalog with loans' in prompt
    assert options.cwd == str(directory.resolve())
    assert options.max_budget_usd == 5
    # a product with its frontend by default
    assert 'bazis-front' in (directory / 'requirements.txt').read_text()
    assert agent.FRONTEND_RULES in options.system_prompt['append']
    out = capsys.readouterr()
    assert 'Reading the guide.' in out.out
    assert '> package_guide bazis' in out.out
    assert '> Bash .venv/bin/python manage.py check' in out.out
    assert '[3 turns, $0.50]' in out.err


def test_new_prompt_of_a_product():
    """
    The prompt of a product with a frontend gives the order of the work of the guide of
    bazis-front, with its commands and the MCP tools.
    """
    prompt = main.new_prompt('A help desk', 'helpdesk', frontend=True)

    order = [
        'package_guide("bazis-front")', 'bazis_front init', 'spec/product.yaml',
        'spec/screens/', 'Make the backend satisfy the specs', 'e2e_data', 'manage.py migrate',
        'bazis_front contract', 'as each `hint` says', 'front_catalog', 'bazis_front add',
        'frontend/src/screens/', 'spec/design/', 'bazis_front design', 'bazis_front e2e',
        'npx playwright install chromium', 'front_status', 'npx tsc --noEmit', 'npm run lint',
        'npm test', 'npm run build', 'uvicorn helpdesk.main:app',
    ]
    positions = [prompt.index(text) for text in order]
    assert positions == sorted(positions)
    for text in ('front_check', 'test_user', 'access', 'scenarios', 'workflows', 'statuses'):
        assert text in prompt
    assert 'A help desk' in prompt
    assert 'the description' in prompt


def test_new_prompt_of_a_backend():
    prompt = main.new_prompt('A help desk', 'helpdesk')
    assert 'helpdesk/router.py' in prompt
    assert 'bazis_front' not in prompt and 'front_check' not in prompt


def test_frontend_rules():
    for text in ('front_check', 'front_status', 'front_catalog', 'package_guide("bazis-front")',
                 'frontend/AGENTS.md', 'e2e_data', 'E2E_PASSWORD', 'npm run e2e',
                 'frontend/src/bazis/generated/', 'Node.js'):
        assert text in agent.FRONTEND_RULES


def test_new_without_a_frontend(tmp_path, fake_agent):
    directory = tmp_path / 'library'
    assert main.main(['new', str(directory), 'A library', '--no-venv', '--no-frontend']) == 0

    prompt, options = fake_agent[0]
    assert 'bazis_front' not in prompt
    assert 'bazis-front' not in (directory / 'requirements.txt').read_text()
    assert agent.FRONTEND_RULES not in options.system_prompt['append']


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
    assert agent.FRONTEND_RULES not in options.system_prompt['append']


def with_frontend(directory: Path) -> Path:
    (directory / 'frontend').mkdir()
    (directory / 'frontend' / 'bazis-front.lock.json').write_text('{}')
    return directory


def test_add_to_a_product_with_a_frontend(tmp_path, fake_agent, capsys):
    assert main.main(['add', 'bazis-statusy', '--project-dir', str(with_frontend(tmp_path))]) == 0

    prompt, options = fake_agent[0]
    backend, frontend = prompt.split('The product has a frontend')
    assert 'package_guide("bazis-statusy")' in backend
    order = ['spec/', 'bazis_front contract', 'front_check', 'front_catalog', 'bazis_front add',
             'bazis_front e2e', 'e2e_data', 'npx tsc --noEmit', 'npm test', 'npm run e2e']
    positions = [frontend.index(text) for text in order]
    assert positions == sorted(positions)
    assert agent.FRONTEND_RULES in options.system_prompt['append']
    assert 'warning' not in capsys.readouterr().err


def test_add_the_frontend(tmp_path, fake_agent):
    """
    `bazis add bazis-front` to a backend: the frontend is built as by `bazis new`.
    """
    assert main.main(['add', 'bazis-front', 'a portal for the clients',
                      '--project-dir', str(tmp_path)]) == 0

    prompt, options = fake_agent[0]
    assert 'package_guide("bazis-front")' in prompt
    assert 'Then build the frontend of the product' in prompt
    assert 'bazis_front init' in prompt and 'npm run build' in prompt
    assert 'from the backend and what the project needs it for' in prompt
    assert agent.FRONTEND_RULES in options.system_prompt['append']


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
    for text in ('front_check', 'front_status', 'package_guide("bazis-front")',
                 'npx tsc --noEmit', 'npm run lint', 'npm test', 'npm run e2e'):
        assert text in prompt
    assert '4. Report the problems' in prompt
    # a review cannot run commands: it lists them
    assert ('cannot run' in prompt) is not fix
    assert ('bazis_front contract --check' in prompt) is fix
    assert agent.FRONTEND_RULES in options.system_prompt['append']


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


def test_new_in_a_directory_with_files(tmp_path, fake_agent, capsys):
    (tmp_path / 'x.txt').write_text('x')

    assert main.main(['new', str(tmp_path), 'anything', '--no-venv']) == 1
    assert 'is not empty' in capsys.readouterr().err
    assert fake_agent == []
