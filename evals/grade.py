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
and tests that pass. A product with a frontend (`frontend = true`) also needs specs valid
against a fresh contract, generated files that are not stale, a frontend that builds and
passes its lint and component tests, and end-to-end tests of its scenarios that pass
against the backend with the test data of `manage.py e2e_data` (Node.js is needed).
"""

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
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

    if task.get('frontend'):
        checks += frontend_checks(project, env, task.get('min_scenarios', 1))
    if shutil.which('createdb'):
        created = subprocess.run(
            ['createdb', '-h', 'localhost', '-U', 'postgres', database],
            env={**env, 'PGPASSWORD': 'postgres'}, capture_output=True, text=True,
        )
        try:
            migrate = run(project, ['manage.py', 'migrate', '--noinput'], env)
            migrated = created.returncode == 0 and migrate.returncode == 0
            checks.append(Check(
                'migrations apply', migrated, created.stderr.strip() or tail(migrate, 6),
            ))
            if task.get('frontend'):
                checks += frontend_database_checks(project, env, migrated)
        finally:
            subprocess.run(
                ['dropdb', '-h', 'localhost', '-U', 'postgres', '--if-exists', '--force', database],
                env={**env, 'PGPASSWORD': 'postgres'}, capture_output=True,
            )
    elif task.get('frontend'):
        checks += frontend_database_checks(project, env, False, 'no PostgreSQL (createdb)')

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


def npm(frontend: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    command = [shutil.which('npm') or 'npm', *args]
    try:
        return subprocess.run(
            command, cwd=frontend, env=env, capture_output=True, text=True, timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, '', 'npm not found')


def frontend_checks(project: Path, env: dict, min_scenarios: int) -> list[Check]:
    """
    The checks of the frontend that need no database: the specs against the contract of
    the project, the generated files, the build, the lint and the component tests.
    """
    frontend = project / 'frontend'
    if not (frontend / 'bazis-front.lock.json').is_file():
        return [Check('frontend created', False, 'no frontend/bazis-front.lock.json')]
    checks = [Check('frontend created', True)]

    spec = run(project, ['manage.py', 'bazis_front', 'check', '--json'], env)
    result = json_in(spec.stdout)
    if result is None:
        checks.append(Check('specs valid against the contract', False, tail(spec)))
    else:
        errors = [f'{i["code"]} {i["file"]}#{i["path"]}' for i in result['issues'] if i['severity'] == 'error']
        checks.append(Check(
            'specs valid against the contract', result['contract'] and not errors,
            '; '.join(errors) if result['contract'] else 'no contract/contract.json',
        ))

    scenarios = sorted(p.stem for p in (frontend / 'e2e' / 'generated').glob('*.spec.ts'))
    checks.append(Check(
        f'at least {min_scenarios} scenarios', len(scenarios) >= min_scenarios, ', '.join(scenarios),
    ))
    stale = [
        done for done in (
            run(project, ['manage.py', 'bazis_front', command, '--check'], env)
            for command in ('design', 'e2e')
        ) if done.returncode
    ]
    checks.append(Check(
        'theme and end-to-end tests generated', not stale, '\n'.join(tail(d, 4) for d in stale),
    ))

    build = npm(frontend, ['run', 'build'], env)
    checks.append(Check('frontend builds', build.returncode == 0, tail(build, 8)))
    failed = next(
        (done for done in (npm(frontend, ['run', 'lint'], env), npm(frontend, ['test'], env))
         if done.returncode),
        None,
    )
    checks.append(Check(
        'frontend lint and component tests pass', failed is None, tail(failed, 8) if failed else '',
    ))
    return checks


def project_package(project: Path) -> str:
    """
    The project package, from DJANGO_SETTINGS_MODULE of manage.py.
    """
    match = re.search(r"DJANGO_SETTINGS_MODULE', '(\w+)\.settings'", (project / 'manage.py').read_text())
    return match.group(1) if match else project.name


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, server: subprocess.Popen, seconds: int = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and server.poll() is None:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.5)
    return False


def end_to_end(project: Path, env: dict) -> Check:
    """
    The end-to-end tests of the frontend against the backend (uvicorn on a free port) with
    the test data of `manage.py e2e_data`.
    """
    name = 'end-to-end tests pass'
    password = secrets.token_urlsafe(12)
    env = {**env, 'E2E_PASSWORD': password}
    data = run(project, ['manage.py', 'e2e_data'], env)
    if data.returncode:
        return Check(name, False, 'manage.py e2e_data: ' + tail(data, 6))
    frontend = project / 'frontend'
    browser = npm(frontend, ['exec', '--', 'playwright', 'install', 'chromium'], env)
    if browser.returncode:
        return Check(name, False, 'playwright install: ' + tail(browser, 6))
    port = free_port()
    server = subprocess.Popen(
        [python_of(project), '-m', 'uvicorn', f'{project_package(project)}.main:app',
         '--host', '127.0.0.1', '--port', str(port)],
        cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        if not wait_for_port(port, server):
            return Check(name, False, 'the backend did not start (uvicorn)')
        tests = npm(frontend, ['run', 'e2e'], {**env, 'BAZIS_API_URL': f'http://127.0.0.1:{port}'})
        return Check(name, tests.returncode == 0, tail(tests, 10))
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()


def frontend_database_checks(project: Path, env: dict, migrated: bool, reason: str = '') -> list[Check]:
    """
    The checks of the frontend that need the migrated database: the contract is that of the
    backend, and the end-to-end tests pass.
    """
    if not (project / 'frontend' / 'bazis-front.lock.json').is_file():
        return []  # `frontend created` failed already
    if not migrated:
        reason = reason or 'the migrations did not apply'
        return [Check('contract fresh', False, reason), Check('end-to-end tests pass', False, reason)]
    contract = run(project, ['manage.py', 'bazis_front', 'contract', '--check'], env)
    return [Check('contract fresh', contract.returncode == 0, tail(contract, 6)), end_to_end(project, env)]


def score(checks: list[Check]) -> float:
    return sum(c.passed for c in checks) / len(checks) if checks else 0.0


def as_dicts(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
