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
The grader of the evals: deterministic checks of a project built by the agent, run with the
Python of the project (`.venv`) against PostgreSQL and Redis of `.env`. It does not depend
on the names the agent chose: it checks that the project loads and passes its checks, has
the expected Bazis packages, complete migrations that apply, models and routes of its own,
and tests that pass.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


#: the prefixes of the system check ids of the Bazis packages (the Django `security.*`
#: deployment warnings are not graded: projects run with DEBUG in development)
BAZIS_CHECK_PREFIXES = (
    'bazis.', 'users.', 'permit.', 'author.', 'authing.', 'statusy.', 'uploadable.',
    'async_request.',
)
TIMEOUT = 900


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ''


def python_of(project: Path) -> str:
    for path in (project / '.venv' / 'bin' / 'python', project / '.venv' / 'Scripts' / 'python.exe'):
        if path.is_file():
            return str(path)
    return sys.executable


def run(project: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python_of(project), *args], cwd=project, env=env, capture_output=True, text=True,
        timeout=TIMEOUT, stdin=subprocess.DEVNULL,
    )


def json_in(text: str):
    """
    The first JSON document in the output (the project may print lines before it).
    """
    for match in re.finditer(r'^[\[{]', text, re.MULTILINE):
        try:
            return json.JSONDecoder().raw_decode(text, match.start())[0]
        except ValueError:
            continue
    return None


def tail(done: subprocess.CompletedProcess, lines: int = 12) -> str:
    return '\n'.join((done.stdout + done.stderr).strip().splitlines()[-lines:])


def local_apps(project: Path) -> set[str]:
    """
    The app labels of the project: the directories with a models.py (outside .venv).
    """
    return {
        path.parent.name
        for path in project.glob('*/models.py')
        if path.parent.name not in ('.venv', 'tests')
    }


def project_env(database: str) -> dict:
    """
    The environment of the project: its own .env and project.env, a database of its own.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith(('BS_', 'DJANGO_'))}
    env['BS_DATABASES__DEFAULT__NAME'] = database
    return env


def grade(project: Path, task: dict) -> list[Check]:
    project = project.resolve()
    database = f'eval_{task["id"]}_{uuid.uuid4().hex[:8]}'
    env = project_env(database)
    checks: list[Check] = []

    doctor = run(project, ['manage.py', 'bazis_doctor', '--json'], env)
    messages = json_in(doctor.stdout)
    if messages is None:
        checks.append(Check('project loads', False, tail(doctor)))
        return checks
    checks.append(Check('project loads', True))
    errors = [m for m in messages if m['level'] in ('error', 'critical')]
    checks.append(Check('no check errors', not errors, '; '.join(m['id'] for m in errors)))
    warnings = [m['id'] for m in messages if m['id'].startswith(BAZIS_CHECK_PREFIXES)]
    checks.append(Check('no warnings of Bazis packages', not warnings, ', '.join(warnings)))

    info = json_in(run(project, ['manage.py', 'bazis_introspect'], env).stdout) or {}
    installed = {p['name'] for p in info.get('packages', [])}
    missing = sorted(set(task.get('expected_packages', [])) - installed)
    checks.append(Check('expected Bazis packages', not missing, 'missing: ' + ', '.join(missing) if missing else ''))

    apps = local_apps(project)
    models = [m['model'] for m in info.get('models', []) if m['model'].split('.')[0] in apps]
    checks.append(Check(
        f'at least {task.get("min_models", 1)} models', len(models) >= task.get('min_models', 1),
        ', '.join(models),
    ))
    routes = [
        r['route_set'] for r in info.get('routes') or []
        if not r['route_set'].startswith('bazis.')
    ]
    checks.append(Check(
        f'at least {task.get("min_routes", 1)} route sets', len(routes) >= task.get('min_routes', 1),
        ', '.join(routes),
    ))

    migrations = run(project, ['manage.py', 'makemigrations', '--check', '--dry-run'], env)
    checks.append(Check('migrations complete', migrations.returncode == 0, tail(migrations, 6)))

    if shutil.which('createdb'):
        created = subprocess.run(
            ['createdb', '-h', 'localhost', '-U', 'postgres', database],
            env={**env, 'PGPASSWORD': 'postgres'}, capture_output=True, text=True,
        )
        migrate = run(project, ['manage.py', 'migrate', '--noinput'], env)
        checks.append(Check(
            'migrations apply', created.returncode == 0 and migrate.returncode == 0,
            created.stderr.strip() or tail(migrate, 6),
        ))
        subprocess.run(
            ['dropdb', '-h', 'localhost', '-U', 'postgres', '--if-exists', database],
            env={**env, 'PGPASSWORD': 'postgres'}, capture_output=True,
        )

    collected = run(project, ['-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider'], env)
    count = re.search(r'(\d+) tests? collected', collected.stdout)
    count = int(count.group(1)) if count else 0
    checks.append(Check(f'at least {task.get("min_tests", 1)} tests', count >= task.get('min_tests', 1), f'{count} collected'))
    if count:
        tests = run(project, ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--create-db'], env)
        checks.append(Check('tests pass', tests.returncode == 0, tail(tests, 8)))
    else:
        checks.append(Check('tests pass', False, 'no tests'))
    return checks


def score(checks: list[Check]) -> float:
    return sum(c.passed for c in checks) / len(checks) if checks else 0.0


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
