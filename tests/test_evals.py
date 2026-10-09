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

import json
import os
import subprocess
import time

from bazis.contrib.cli import scaffold
from bazis.contrib.mcp import catalog

from evals import grade, run


def test_tasks_are_valid():
    tasks = run.load_tasks()
    ids = [t['id'] for t in tasks]
    assert len(ids) == len(set(ids))
    known = set(catalog.catalog())
    for task in tasks:
        assert task['description'].strip()
        assert set(task['expected_packages']) <= known, task['id']
        assert task['min_models'] >= 1 and task['min_routes'] >= 1 and task['min_tests'] >= 1
        assert task.get('min_scenarios', 1) >= 1
        if 'language' in task:
            assert task['language'] != 'en'
            assert scaffold.product_language(task['language'])[0] == task['language']
    assert any(t.get('frontend') for t in tasks)
    assert any(t.get('language') for t in tasks)


def test_language_check(tmp_path, monkeypatch):
    status = {'ru': {'catalogs': ['locale/ru/LC_MESSAGES/django.po'], 'total': 2, 'translated': 2,
                     'untranslated': [], 'fuzzy': []}}
    commands = []

    def run_status(project, args, env):
        commands.append(args)
        incomplete = status['ru']['untranslated'] or status['ru']['fuzzy']
        return subprocess.CompletedProcess(args, 1 if incomplete else 0, json.dumps(status), '')

    monkeypatch.setattr(grade, 'run', run_status)
    info = {'settings': [
        {'name': 'LANGUAGES', 'value': [['ru', 'Русский'], ['en', 'English']]},
        {'name': 'LANGUAGE_CODE', 'value': 'ru'},
    ]}
    check = grade.language_check(tmp_path, info, 'ru', {})
    assert not check.passed and check.detail == 'no locale/ru/LC_MESSAGES/django.mo'

    catalog = tmp_path / 'shop' / 'locale' / 'ru' / 'LC_MESSAGES'
    catalog.mkdir(parents=True)
    (catalog / 'django.mo').write_bytes(b'')
    assert grade.language_check(tmp_path, info, 'ru', {}).passed
    assert commands[-1] == ['manage.py', 'bazis_messages', 'status', '--check']

    # an entry left untranslated or fuzzy by the agent
    status['ru']['untranslated'] = [{'msgid': 'Open'}]
    status['ru']['fuzzy'] = [{'msgid': 'Close', 'msgstr': 'Закрыть'}]
    check = grade.language_check(tmp_path, info, 'ru', {})
    assert not check.passed and check.detail == 'catalogs incomplete: ru 2'

    info = {'settings': [{'name': 'LANGUAGES', 'value': [['en', 'English']]},
                         {'name': 'LANGUAGE_CODE', 'value': 'en'}]}
    check = grade.language_check(tmp_path, info, 'ru', {})
    assert check.detail == "LANGUAGE_CODE is 'en'; LANGUAGES are ['en']; catalogs incomplete: ru 2"


def test_json_in_output_with_other_lines():
    assert grade.json_in('Loading...\n{"models": []}\n') == {'models': []}
    assert grade.json_in('Traceback: no json') is None


def test_local_apps(tmp_path):
    for app in ('shop', 'tests', 'shop_project'):
        (tmp_path / app).mkdir()
    (tmp_path / 'shop' / 'models.py').write_text('')
    (tmp_path / 'tests' / 'models.py').write_text('')
    assert grade.local_apps(tmp_path) == {'shop'}


def test_project_env_isolates_the_database(monkeypatch):
    monkeypatch.setenv('BS_DEBUG', 'false')
    monkeypatch.setenv('DJANGO_SETTINGS_MODULE', 'other.settings')
    env = grade.project_env('eval_x')
    assert env['BS_DATABASES__DEFAULT__NAME'] == 'eval_x'
    assert 'BS_DEBUG' not in env and 'DJANGO_SETTINGS_MODULE' not in env


def test_summary(tmp_path):
    checks = [grade.Check('project loads', True), grade.Check('tests pass', False, 'boom\n1 failed')]
    result = {'task': 'tracker', 'seconds': 120, 'cost_usd': 1.5, 'turns': 30,
              'agent_error': None, 'score': grade.score(checks), 'checks': grade.as_dicts(checks)}
    text = run.write_summary(tmp_path, [result]).read_text()
    assert '| tracker | 50% | 1.50 | 30 | 2 | tests pass |' in text
    assert '- FAIL: tests pass — 1 failed' in text


def test_running_needs_a_budget():
    import pytest

    with pytest.raises(SystemExit, match='budget'):
        run.main_cli(['--task', 'tracker'])


def done(returncode: int = 0, stdout: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, '')


def product(tmp_path, scenarios: int):
    scaffold.write_files(tmp_path, 'desk', frontend=True)
    generated = tmp_path / 'frontend' / 'e2e' / 'generated'
    generated.mkdir(parents=True)
    (tmp_path / 'frontend' / 'bazis-front.lock.json').write_text('{}')
    for index in range(scenarios):
        (generated / f'scenario-{index}.spec.ts').write_text('')
    (generated / 'product.ts').write_text('')
    return tmp_path


def test_frontend_checks(tmp_path, monkeypatch):
    issues = [{'code': 'P019', 'file': 'spec/product.yaml', 'path': '/roles/0', 'severity': 'error'},
              {'code': 'P020', 'file': 'spec/product.yaml', 'path': '/x', 'severity': 'warning'}]
    commands = []

    def fake_run(project, args, env):
        commands.append(args)
        if args[2] == 'check':
            return done(1, json.dumps({'contract': True, 'errors': 1, 'warnings': 1, 'issues': issues}))
        return done(1 if args[2] == 'e2e' else 0)

    monkeypatch.setattr(grade, 'run', fake_run)
    monkeypatch.setattr(grade, 'npm', lambda frontend, args, env: done(int(args == ['test'])))

    checks = {c.name: c for c in grade.frontend_checks(product(tmp_path, 2), {}, 3)}

    assert checks['frontend created'].passed
    assert not checks['specs valid against the contract'].passed
    assert checks['specs valid against the contract'].detail == 'P019 spec/product.yaml#/roles/0'
    assert not checks['at least 3 scenarios'].passed
    assert checks['at least 3 scenarios'].detail == 'scenario-0.spec, scenario-1.spec'
    assert not checks['theme and end-to-end tests generated'].passed
    assert checks['frontend builds'].passed
    assert not checks['frontend lint and component tests pass'].passed
    assert ['manage.py', 'bazis_front', 'design', '--check'] in commands


def test_frontend_checks_without_a_frontend(tmp_path):
    checks = grade.frontend_checks(tmp_path, {}, 1)
    assert [(c.name, c.passed) for c in checks] == [('frontend created', False)]
    assert grade.frontend_database_checks(tmp_path, {}, True) == []


def test_frontend_database_checks_without_a_database(tmp_path):
    checks = grade.frontend_database_checks(product(tmp_path, 1), {}, False, 'no PostgreSQL')
    assert [(c.name, c.passed, c.detail) for c in checks] == [
        ('contract fresh', False, 'no PostgreSQL'),
        ('end-to-end tests pass', False, 'no PostgreSQL'),
    ]


def test_end_to_end_needs_the_test_data(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, 'run', lambda project, args, env: done(1, 'Unknown command: e2e_data'))
    check = grade.end_to_end(product(tmp_path, 1), {})
    assert not check.passed and check.detail.startswith('manage.py e2e_data: ')


def test_project_package(tmp_path):
    scaffold.write_files(tmp_path / 'x', 'desk')
    assert grade.project_package(tmp_path / 'x') == 'desk'


def test_a_command_that_hangs(tmp_path, monkeypatch):
    """
    A command over the timeout fails its check and its processes are killed; the grading
    goes on.
    """
    monkeypatch.setattr(grade, 'TIMEOUT', 1)
    started = time.monotonic()
    done = grade.execute(['sh', '-c', 'sleep 60 & echo $!; wait'], tmp_path, dict(os.environ))

    assert done.returncode == 124 and 'timed out' in done.stderr
    assert time.monotonic() - started < 30
    child = int(done.stdout.split()[0])
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError('the child of the command is alive')


def test_a_command_that_is_missing(tmp_path):
    assert grade.execute(['no-such-command-x'], tmp_path, {}).returncode == 127


def test_end_to_end_uses_servers_of_its_own(tmp_path, monkeypatch):
    """
    The backend and the dev server of the frontend run on free ports of their own, and the
    tests get the dev server in E2E_BASE_URL: they never start or reuse another one.
    """
    project = product(tmp_path, 1)
    calls, started, stopped = [], [], []
    monkeypatch.setattr(grade, 'run', lambda project, args, env: done())
    monkeypatch.setattr(grade, 'npm', lambda frontend, args, env: calls.append((args, env)) or done())
    ports = iter([8001, 5174])
    monkeypatch.setattr(grade, 'free_port', lambda: next(ports))

    def serve(command, cwd, env, port):
        started.append((command, env, port))
        return f'server-{port}'

    monkeypatch.setattr(grade, 'serve', serve)
    monkeypatch.setattr(grade, 'stop', stopped.append)

    assert grade.end_to_end(project, {}).passed

    (backend, _, api), (dev, dev_env, web) = started
    assert 'desk.main:app' in backend and str(api) in backend
    assert dev[1:3] == ['run', 'dev'] and dev[-2:] == [str(web), '--strictPort']
    assert dev_env['BAZIS_API_URL'] == 'http://127.0.0.1:8001'
    args, env = calls[-1]
    assert args == ['run', 'e2e']
    assert env['E2E_BASE_URL'] == 'http://127.0.0.1:5174'
    assert env['E2E_PASSWORD'] == dev_env['E2E_PASSWORD']
    assert stopped == ['server-8001', 'server-5174']


def test_end_to_end_stops_the_backend_when_the_frontend_does_not_start(tmp_path, monkeypatch):
    stopped = []
    monkeypatch.setattr(grade, 'run', lambda project, args, env: done())
    monkeypatch.setattr(grade, 'npm', lambda frontend, args, env: done())
    monkeypatch.setattr(grade, 'serve', lambda command, cwd, env, port: None if 'dev' in command else 'api')
    monkeypatch.setattr(grade, 'stop', stopped.append)

    check = grade.end_to_end(product(tmp_path, 1), {})
    assert not check.passed and 'npm run dev' in check.detail
    assert stopped == ['api']
