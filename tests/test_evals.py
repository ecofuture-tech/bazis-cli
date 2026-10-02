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
