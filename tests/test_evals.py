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
import subprocess

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
    assert any(t.get('frontend') for t in tasks)


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
