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
import shutil
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


#: the real `scaffold.create_database`, which the fixture `environment` replaces
create_database = scaffold.create_database
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


def manage(directory: Path, *args: str) -> subprocess.CompletedProcess:
    """
    Runs `manage.py` of a project with its own settings only, but the libraries of this
    machine (GDAL, GEOS).
    """
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(('BS_', 'DJANGO_')) or k.endswith('_LIBRARY_PATH')
    }
    return subprocess.run(
        [sys.executable, 'manage.py', *args],
        cwd=directory, env=env, capture_output=True, text=True, timeout=300,
    )


def doctor(directory: Path) -> list:
    """
    The warnings and errors of `bazis_doctor` of a project. Its infos are left out: without
    the database, bazis 2.13 says that the database checks were skipped (`bazis.database`).
    """
    done = manage(directory, 'bazis_doctor', '--json')
    assert done.returncode == 0, done.stderr[-3000:]
    return [it for it in json.loads(done.stdout) if it['level'] not in ('info', 'debug')]


def test_scaffold_is_a_working_project(tmp_path):
    """
    The skeleton loads and passes the system checks without a database.
    """
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop')

    assert doctor(directory) == []
    assert (directory / '.gitignore').read_text().startswith('.env\n')
    # the temporary files of the agent
    assert '.scratch/' in (directory / '.gitignore').read_text().splitlines()
    assert '`.scratch/`' in (directory / 'AGENTS.md').read_text()
    assert 'BS_SECRET_KEY=' in (directory / '.env').read_text()
    assert json.loads((directory / '.mcp.json').read_text())['mcpServers']['bazis']


def test_product_language():
    """
    A code is matched by itself, else by its base code, as the core matches the language of
    a request; the name is the own name of the language.
    """
    for code in ('ru', 'ru-RU', 'RU', 'ru_RU', ' ru '):
        assert scaffold.product_language(code) == ('ru', 'Русский')
    assert scaffold.product_language('pt_BR') == ('pt-br', 'Português Brasileiro')
    assert scaffold.product_language('zh-cn')[0] == 'zh-hans'
    assert scaffold.product_language('en-US') == ('en', 'English')
    with pytest.raises(scaffold.ScaffoldError, match='not a language of Django'):
        scaffold.product_language('xx')

    assert scaffold.languages('ru') == [['ru', 'Русский'], ['en', 'English']]
    assert scaffold.languages('en') == [['en', 'English']]


def test_scaffold_in_a_language(tmp_path):
    """
    The skeleton of a product in Russian: Russian by default and English, and the checks of
    the translations (bazis.W005) pass.
    """
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop', language='ru')

    env = (directory / 'project.env').read_text(encoding='utf-8')
    assert """BS_LANGUAGES='[["ru", "Русский"], ["en", "English"]]'""" in env
    assert 'BS_LANGUAGE_CODE=ru\n' in env
    assert doctor(directory) == []

    english = tmp_path / 'en'
    scaffold.write_files(english, 'shop')
    env = (english / 'project.env').read_text(encoding='utf-8')
    assert """BS_LANGUAGES='[["en", "English"]]'""" in env and 'BS_LANGUAGE_CODE=en\n' in env


def test_makemessages_writes_into_the_catalog_of_the_project(tmp_path):
    """
    The skeleton has `locale/`, the first of LOCALE_PATHS, where `makemessages` writes the
    msgids of the project; without it, the first is the catalog of an installed package.
    """
    if shutil.which('xgettext') is None:
        pytest.skip('gettext is not installed')
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop', language='ru')
    assert (directory / 'locale' / '.gitkeep').is_file()

    # checked before makemessages, which would otherwise change an installed package
    done = manage(
        directory, 'shell', '-c', 'from django.conf import settings; print(settings.LOCALE_PATHS[0])'
    )
    assert done.returncode == 0, done.stderr[-3000:]
    assert os.path.realpath(done.stdout.split()[-1]) == os.path.realpath(directory / 'locale')

    (directory / 'shop' / 'texts.py').write_text(
        "from django.utils.translation import gettext_lazy as _\n\nTITLE = _('A text of the shop')\n"
    )
    done = manage(directory, 'makemessages', '-l', 'ru')
    assert done.returncode == 0, done.stderr[-3000:]
    catalog = directory / 'locale' / 'ru' / 'LC_MESSAGES' / 'django.po'
    assert 'msgid "A text of the shop"' in catalog.read_text(encoding='utf-8')


def test_tests_of_a_scaffold_keep_their_cache_keys_apart(tmp_path):
    """
    The conftest of the skeleton gives the cache keys of the tests a prefix of their own, new
    at every run, in every thread (the endpoints run in a thread pool); it works without
    Redis (here an address where nothing listens).
    """
    pytest.importorskip('pytest_django')
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop')
    (directory / 'tests' / 'test_cache.py').write_text("""\
import threading

from django.core.cache import cache


def test_prefix():
    keys = [cache.make_key('x')]
    thread = threading.Thread(target=lambda: keys.append(cache.make_key('x')))
    thread.start()
    thread.join()
    prefix = keys[0].split(':')[0]
    assert prefix.startswith('shop-tests-') and len(prefix) == len('shop-tests-') + 8
    assert keys[1] == keys[0]
""")
    env = {
        **{k: v for k, v in os.environ.items()
           if not k.startswith(('BS_', 'DJANGO_')) or k.endswith('_LIBRARY_PATH')},
        'BS_CACHES__DEFAULT__LOCATION': 'redis://127.0.0.1:1/0',
    }
    done = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'],
        cwd=directory, env=env, capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-3000:]
    assert '1 passed' in done.stdout


def test_scaffold_with_a_frontend(tmp_path):
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

    assert doctor(directory) == []


def node_version(stdout: str, returncode: int = 0):
    def run(command, **kwargs):
        assert command == ['/usr/bin/node', '--version']
        return subprocess.CompletedProcess(command, returncode, stdout, '')

    return run


def test_node_problem(monkeypatch):
    found = {'node': '/usr/bin/node', 'npm': '/usr/bin/npm'}
    monkeypatch.setattr(scaffold.shutil, 'which', found.get)
    assert node_problem(node_version('v22.12.0\n')) is None
    assert node_problem(node_version('v24.1.3\n')) is None

    too_old = node_problem(node_version('v22.11.9\n'))
    assert too_old.startswith('Node.js v22.11.9 is too old') and '22.12 or newer' in too_old
    assert node_problem(node_version('v18.20.0\n')).startswith('Node.js v18.20.0 is too old')
    assert node_problem(node_version('', 1)).startswith('`node --version` failed')

    del found['npm']
    assert node_problem().startswith('npm not found')
    found.clear()
    assert node_problem().startswith('node and npm not found')


def test_node_version_is_that_of_the_template():
    """
    NODE_VERSION follows `engines.node` of the template of bazis-front.
    """
    pytest.importorskip('bazis.contrib.front')
    from importlib.resources import files

    package = json.loads(
        files('bazis.contrib.front').joinpath('assets/template/package.json').read_text()
    )
    assert package['engines']['node'] == '>=' + '.'.join(map(str, scaffold.NODE_VERSION))


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


def test_create_venv_with_uv(tmp_path, monkeypatch):
    """
    A bazis-front from FRONTEND_REQUIREMENT_ENV (a checkout) is built again, not taken from
    the cache of uv, which keeps the build of an older commit; the released one is not.
    """
    monkeypatch.setattr(scaffold.shutil, 'which', lambda name: '/usr/bin/uv')
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.delenv(scaffold.FRONTEND_REQUIREMENT_ENV, raising=False)
    python = scaffold.create_venv(tmp_path, run=run)
    assert commands[0][:3] == ['/usr/bin/uv', 'venv', '--quiet']
    assert commands[1][:3] == ['/usr/bin/uv', 'pip', 'install']
    assert commands[1][-2:] == ['-r', str(tmp_path / 'requirements-dev.txt')]
    assert str(python) in commands[1]
    assert '--refresh-package' not in commands[1]

    commands.clear()
    monkeypatch.setenv(scaffold.FRONTEND_REQUIREMENT_ENV, '/src/bazis-front')
    scaffold.create_venv(tmp_path, run=run)
    refresh = commands[1].index('--refresh-package')
    assert commands[1][refresh + 1] == 'bazis-front'


def test_create_database(tmp_path):
    """
    The script runs in the directory of the project with its Python; its outcome, printed
    as JSON, is what `bazis new` says to the user and the agent.
    """
    commands = []

    def runner(stdout, stderr=''):
        def run(command, **kwargs):
            commands.append((command, kwargs['cwd']))
            return subprocess.CompletedProcess(command, 0, stdout, stderr)

        return run

    def outcome(outcome, **extra):
        result = {'name': 'shop', 'server': 'db:5432', 'outcome': outcome, **extra}
        return create_database(tmp_path, 'shop', '/p/python', run=runner(json.dumps(result)))

    assert outcome('created') == 'Created the database `shop` on PostgreSQL db:5432.'
    command, cwd = commands[0]
    assert command == ['/p/python', '-c', scaffold.CREATE_DATABASE_PY, 'shop'] and cwd == tmp_path
    assert outcome('exists') == 'The database `shop` exists on PostgreSQL db:5432 without tables.'
    # the database and PostGIS are separate outcomes
    assert outcome('created', postgis='extension "postgis" is not available') == (
        'Created the database `shop` on PostgreSQL db:5432, but PostGIS is not installed in '
        'it: extension "postgis" is not available'
    )
    assert outcome('exists', postgis='permission denied') == (
        'The database `shop` exists on PostgreSQL db:5432 without tables, but PostGIS is not '
        'installed in it: permission denied'
    )
    # tables of another project: not the database of this one
    has_data = outcome('has_data')
    assert has_data.startswith('The database `shop` on PostgreSQL db:5432 already has data')
    assert '`BS_DATABASES__DEFAULT__NAME` in `.env`' in has_data
    unreachable = outcome('unreachable', detail='connection refused')
    assert 'cannot be reached (connection refused)' in unreachable and 'not created' in unreachable
    assert outcome('failed', detail='permission denied') == (
        'The database `shop` was not created on PostgreSQL db:5432: permission denied'
    )
    assert 'not on PostgreSQL' in outcome('other')
    # the script failed: its last error line
    failed = create_database(tmp_path, 'shop', run=runner('', 'Traceback\nImportError: x'))
    assert failed == 'The database of the project was not created: ImportError: x'

    def broken(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 120)

    assert 'was not created' in create_database(tmp_path, 'shop', run=broken)


#: a PostgreSQL server with PostGIS for the test of the database of a project, `host:port`
#: with the user `postgres` and the password `postgres`; without it the test is skipped
TEST_POSTGRES = os.environ.get('BAZIS_CLI_TEST_POSTGRES', '')


def project_database_env(monkeypatch, host: str, port: str, name: str) -> None:
    """The settings of the database of a project in the environment, as a user may set them."""
    for key in [k for k in os.environ if k.startswith('BS_DATABASES__')]:
        monkeypatch.delenv(key)
    for key, value in dict(HOST=host, PORT=port, NAME=name, USER='postgres',
                           PASSWORD='postgres').items():
        monkeypatch.setenv(f'BS_DATABASES__DEFAULT__{key}', value)


def test_create_database_of_a_project_without_postgres(tmp_path, monkeypatch):
    """
    The real script with the settings of a project: a server that cannot be reached is
    skipped with a message, `bazis new` goes on.
    """
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop')
    project_database_env(monkeypatch, '127.0.0.1', '9', 'shop')

    message = create_database(directory, 'shop')
    assert message.startswith('PostgreSQL 127.0.0.1:9 cannot be reached ('), message
    assert message.endswith('the database `shop` is not created.')


@pytest.mark.skipif(not TEST_POSTGRES, reason='BAZIS_CLI_TEST_POSTGRES is not set')
def test_create_database_of_a_project(tmp_path, monkeypatch):
    """
    The database of the settings of the project is created with PostGIS, once: the second
    run finds it, and one with tables is reported as the data of another project.
    """
    import psycopg

    host, port = TEST_POSTGRES.rsplit(':', 1)
    name = f'bazis_cli_test_{os.getpid()}'
    directory = tmp_path / 'shop'
    scaffold.write_files(directory, 'shop')
    project_database_env(monkeypatch, host, port, name)
    server = dict(host=host, port=port, user='postgres', password='postgres', autocommit=True)
    try:
        assert create_database(directory, 'shop') == (
            f'Created the database `{name}` on PostgreSQL {host}:{port}.'
        )
        # PostGIS, whose table is no data of a project
        assert create_database(directory, 'shop') == (
            f'The database `{name}` exists on PostgreSQL {host}:{port} without tables.'
        )
        with psycopg.connect(dbname=name, **server) as database:
            query = "SELECT 1 FROM pg_extension WHERE extname = 'postgis'"
            assert database.execute(query).fetchone()
            # the data of a project
            database.execute('CREATE TABLE django_migrations (id serial)')
        assert 'already has data, probably of another project' in create_database(directory, 'shop')
    finally:
        with psycopg.connect(dbname='postgres', **server) as database:
            database.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_requirements_are_the_latest_releases():
    """
    The skeleton requires at least the releases of the catalog of bazis-mcp, which its
    guides describe: an agent never starts from an older Bazis.
    """
    from bazis.contrib.mcp import catalog

    def version(text):
        return tuple(int(part) for part in text.split('.')[:3])

    released = catalog.catalog()
    for requirement in [*scaffold.REQUIREMENTS, *scaffold.REQUIREMENTS_DEV]:
        name, minimum = requirement.split('>=')
        if name in released:
            assert version(minimum) >= version(released[name]['version']), requirement


def test_bazis_is_required_as_bazis_front_requires_it():
    """
    The minimum of bazis of the skeleton is not older than that of bazis-front, which every
    product installs: the agent never starts from a Bazis that bazis-front replaces.
    """
    pytest.importorskip('bazis.contrib.front')
    import re
    from importlib import metadata

    def version(text):
        return tuple(int(part) for part in text.split('.')[:3])

    front = next(
        match.group(1) for it in metadata.requires('bazis-front')
        if (match := re.fullmatch(r'bazis\s*>=\s*([\d.]+)', it))
    )
    ours = next(it.split('>=')[1] for it in scaffold.REQUIREMENTS if it.startswith('bazis>='))
    assert version(ours) >= version(front)


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
    assert 'A library catalog with loans' in prompt
    assert options.cwd == str(directory.resolve())
    assert options.max_budget_usd == 5
    # a product with its frontend by default
    assert 'bazis-front' in (directory / 'requirements.txt').read_text()
    assert agent.FRONTEND_RULES in options.system_prompt['append']
    out = capsys.readouterr()
    # the language of the product is that of the description
    assert 'the language of the description' in prompt and 'BS_LANGUAGE_CODE=<code>' in prompt
    assert 'BS_LANGUAGE_CODE=en\n' in (directory / 'project.env').read_text()
    assert DATABASE_CREATED in out.out
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


def test_rules_and_steps_agree_on_the_test_data():
    """
    Roles, statuses and transits are declared in `roles.py` and `workflow.py` (the contract
    is exported after migrate); `e2e_data` only adds the test users and the records of the
    scenarios.
    """
    rules = ' '.join(agent.FRONTEND_RULES.split())
    steps = ' '.join(main.FRONTEND_STEPS.split())
    for text in (rules, steps):
        assert 'statuses and transits of the workflows' in text
        assert 'declared in `roles.py` and `workflow.py`' in text and 'data migration' not in text
        assert 'the e2e data command recommended by bazis-front' in text
    assert 'it creates only a user per `test_user`' in rules
    assert '`e2e_data`, with only the test users and the records the scenarios need' in steps


def test_the_rules_follow_the_released_stack():
    """
    The rules of every task and the skeleton use the released stack: the declarations of
    bazis-permit and bazis-statusy applied by migrate, the pytest plugin of bazis-test-utils
    (no hand-written fixture of the triggers or of the declared data), `notify()` of
    bazis-ws, and the database checks of `run_doctor`.
    """
    rules = ' '.join(agent.RULES.split())
    for text in ('`<app>/roles.py`', '`<app>/workflow.py`', 'never created by data migrations',
                 'keeps those migrations as history without effect (`operations = []`',
                 '`django_db_setup`', 'notify(users, lambda user: notification(...))',
                 'publish_changed(item)', "router.register('bazis.contrib.ws.router')",
                 '`permit.W005`', '`statusy.W003`', '`bazis.database`'):
        assert text in rules, text
    assert 'clear_cache' not in rules and 'data migrations set' not in rules

    conftest = scaffold.CONFTEST_PY
    assert 'def django_db_setup' not in conftest and 'pgtrigger install' not in conftest
    assert 'plugin of bazis-test-utils' in conftest and '`bazis_declared`' in conftest
    assert 'data migration' not in scaffold.AGENTS_MD_FRONTEND


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


def test_rules_of_the_packages_and_of_what_the_agent_leaves():
    """
    The minimum of a package is its latest release; temporary files stay in the project,
    secrets stay out of the messages, the database of `bazis new` is kept (and one the
    agent creates is reported).
    """
    rules = ' '.join(agent.RULES.split())
    for text in ('`<name>>=<catalog_version>` of `list_packages`',
                 'a package without a `catalog_version`, such as bazis-front, without a minimum',
                 '`.scratch/`', '`/tmp`',
                 'also of the test users', '`E2E_PASSWORD`',
                 'The database of `.env` is created by `bazis new`', 'Never drop or recreate it',
                 'never delete or generate again the applied ones', 'CREATE EXTENSION postgis',
                 'already has data of another project', 'never migrate it',
                 'what you created outside the files (such as the database)'):
        assert text in rules, text
    assert '.scratch/' in scaffold.GITIGNORE.splitlines()


def test_rules_of_the_languages_and_the_tests():
    rules = ' '.join(agent.RULES.split())
    for text in ('gettext_lazy', 'English msgids', 'locale/<language>/LC_MESSAGES/django.po',
                 'makemessages', 'compilemessages', 'bazis.W004', 'bazis.W005',
                 'never flush', 'cache.clear()', "cache.delete_pattern('*')",
                 'the flush applies the declarations again', 'bazis.contrib.users.token',
                 'users.E002', 'UserLanguageMixin'):
        assert text in rules, text
    frontend = ' '.join(agent.FRONTEND_RULES.split())
    for text in ('t()', 'frontend/src/i18n/<language>.ts', '`languages` and `language`'):
        assert text in frontend, text


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
    assert 'bazis_front init' in prompt and 'npm run build' in prompt
    assert 'from the backend and what the project needs it for' in prompt
    assert 'requirement `bazis-front>=0.1.0`' in prompt
    assert agent.FRONTEND_RULES in options.system_prompt['append']


def test_add_the_frontend_from_a_build(tmp_path, fake_agent, monkeypatch):
    """
    `bazis add bazis-front` installs the bazis-front of BAZIS_FRONT_REQUIREMENT too.
    """
    monkeypatch.setenv(scaffold.FRONTEND_REQUIREMENT_ENV, '/wheels/bazis_front-0.1.0.whl')
    assert main.main(['add', 'bazis-front', '--project-dir', str(tmp_path)]) == 0

    prompt, _ = fake_agent[0]
    assert 'requirement `/wheels/bazis_front-0.1.0.whl`' in prompt
    assert 'bazis-front>=0.1.0' not in prompt


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
