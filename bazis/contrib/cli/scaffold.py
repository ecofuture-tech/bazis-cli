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
The skeleton of a new Bazis project: the files every project has, written without the
model, and its virtual environment. The agent then adds the apps the project needs and,
with bazis-front, its frontend.
"""

import importlib.util
import json
import keyword
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path


#: the packages of every project; the agent adds the Bazis packages the project needs. The
#: minimums are the latest releases, not older than those of the catalog of bazis-mcp
#: (tests/test_cli.py compares them), so that the guides describe the installed versions
REQUIREMENTS = ['bazis>=2.13.0']
REQUIREMENTS_DEV = ['bazis-mcp>=2.4.5', 'bazis-test-utils>=2.5.0']

#: the frontend layer of a product (`bazis new` without `--no-frontend`)
FRONTEND_REQUIREMENT = 'bazis-front>=0.1.0'
FRONTEND_APP = 'bazis.contrib.front'
#: replaces FRONTEND_REQUIREMENT in requirements.txt: a pip requirement such as a path to a
#: wheel or a checkout, or `bazis-front @ <url>`, to try a bazis-front that is not on PyPI
FRONTEND_REQUIREMENT_ENV = 'BAZIS_FRONT_REQUIREMENT'
#: the oldest Node.js of the frontend: `engines.node` of the template of bazis-front, which
#: is a dependency of the project, not of the CLI (tests/test_cli.py compares them)
NODE_VERSION = (22, 12)


MANAGE_PY = '''#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
'''

SETTINGS_PY = '''# Settings are environment variables with the BS_ prefix (project.env, .env):
# bazis.core.configure builds the Django settings from them.
import bazis.core.configure  # noqa: F401
'''

MAIN_PY = '''import os


os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')

from bazis.core.app import app  # noqa: E402, F401
'''

ROUTER_PY = '''from bazis.core.routing import BazisRouter


router = BazisRouter(prefix='/api/v1')
# register the routers of the apps: router.register('<app>.router')
'''

URLS_PY = '''from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path
from django.views.static import serve


urlpatterns = [
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, dict(document_root=settings.MEDIA_ROOT)),
        re_path(r'^static/(?P<path>.*)$', serve, dict(document_root=settings.STATIC_ROOT)),
    ]
'''

WSGI_PY = '''import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')

application = get_wsgi_application()
'''

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ['__BASE_DIR__/templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ]
        },
    }
]

PROJECT_ENV = '''# Settings of the project shared by all environments (secrets go to .env)
BS_INSTALLED_APPS='{installed_apps}'
BS_ROOT_URLCONF={name}.urls
BS_BAZIS_ROUTER_MODULE={name}.router
BS_WSGI_APPLICATION={name}.wsgi.application
BS_DEFAULT_AUTO_FIELD=django.db.models.BigAutoField
BS_LANGUAGES='{languages}'
BS_LANGUAGE_CODE={language}
BS_TEMPLATES='{templates}'
'''

DOT_ENV = '''# Local settings and secrets of this environment; not committed
BS_DEBUG=true
BS_SECRET_KEY={secret_key}
BS_DATABASES__DEFAULT__HOST=localhost
BS_DATABASES__DEFAULT__PORT=5432
BS_DATABASES__DEFAULT__NAME={name}
BS_DATABASES__DEFAULT__USER=postgres
BS_DATABASES__DEFAULT__PASSWORD=postgres
BS_CACHES__DEFAULT__LOCATION=redis://localhost:6379/0
'''

GITIGNORE = '''.env
.venv/
.scratch/
__pycache__/
*.pyc
.pytest_cache/
media/
static/
'''

PYTEST_INI = '''[pytest]
DJANGO_SETTINGS_MODULE = {name}.settings
'''

CONFTEST_PY = '''"""
The fixtures of the tests. The pytest plugin of bazis-test-utils installs the triggers of
pgtrigger in the test database, and `migrate` applies the roles and the workflows declared
in `<app>/roles.py` and `<app>/workflow.py`, also again after the flush of a test with
`transaction=True`: no fixture installs the triggers or creates the roles, statuses or
transits. A test that changes the declared rows itself takes the fixture `bazis_declared`.
"""

import uuid

import pytest
from django.test import override_settings


@pytest.fixture(scope='session', autouse=True)
def cache_of_the_tests():
    """
    The tests share Redis (BS_CACHES__DEFAULT__LOCATION) with the backend and the other
    projects that use it: their cache keys have a prefix of their own, new at every run, and
    only those keys are deleted. Never flush the Redis database or call cache.clear().
    """
    from django.conf import settings
    from django.core.cache import cache

    default = {{**settings.CACHES['default'], 'KEY_PREFIX': f'{name}-tests-{{uuid.uuid4().hex[:8]}}'}}
    # the cache connections of all the threads, also of the endpoints, are made again
    with override_settings(CACHES={{**settings.CACHES, 'default': default}}):
        yield
        cache.delete_pattern('*')


@pytest.fixture
def app():
    from {name}.main import app

    return app
'''

WINDOWS = sys.platform == 'win32'
VENV_BIN = '.venv/Scripts' if WINDOWS else '.venv/bin'
EXE = '.exe' if WINDOWS else ''

AGENTS_MD = '''# {name}

A [Bazis](https://github.com/ecofuture-tech/bazis) project: JSON:API services on Django,
FastAPI and Pydantic.

- The Python environment is `.venv`; the dependencies are in `requirements.txt` and
  `requirements-dev.txt` (install them with `.venv/bin/python -m pip install -r ...`).
- Settings are `BS_*` variables: shared ones in `project.env`, local ones and secrets in
  `.env` (not committed). Apps, including Bazis packages, are listed in `BS_INSTALLED_APPS`.
- The MCP server `bazis` (`.mcp.json`) gives the catalog and the guides of the Bazis
  packages and the facts and checks of this project. Read the guide of a package before
  using it, and run `python manage.py bazis_doctor` and the tests after every change.
- Temporary files (scripts, outputs, screenshots) go to `.scratch/` (ignored by Git), not
  to `/tmp`.
- PostgreSQL with PostGIS and Redis are required to run the project and the tests
  (`.env`); the checks and `makemigrations` work without them. The tests share Redis: they
  never flush it, their cache keys have a prefix of their own (`tests/conftest.py`).
- The languages are `BS_LANGUAGES` and `BS_LANGUAGE_CODE` (`project.env`), English always
  among them: the texts of the code are English msgids (`gettext_lazy`), translated in
  `locale/<language>/LC_MESSAGES/django.po` and compiled (`compilemessages`). Keep
  `locale/`, even empty: `makemessages` writes into the first directory of `LOCALE_PATHS`,
  which without it is the catalog of a package.
'''

AGENTS_MD_FRONTEND = '''
## Frontend

The product has a frontend made by [bazis-front](https://github.com/ecofuture-tech/bazis-front)
(`package_guide("bazis-front")` of the MCP server): the specs of the product in `spec/`,
the contract generated from the backend in `contract/`, the React app in `frontend/` (its
guide is `frontend/AGENTS.md`). Node.js 22.12 or newer with npm is needed for the frontend.

- The backend is built to satisfy the specs. After every change of the models, routes,
  roles, statuses or transits: migrate, `python manage.py bazis_front contract`, then
  `python manage.py bazis_front check` (the MCP tool `front_check`) until it has no errors.
- `contract/`, `frontend/src/bazis/generated/` and `frontend/e2e/generated/` are only
  generated (`bazis_front contract`, `design`, `e2e`), never edited; the MCP tool
  `front_status` says what is stale.
- The permit roles are declared in `<app>/roles.py` and the statuses and transits in
  `<app>/workflow.py`: `migrate` applies them. The test data of the end-to-end tests is `python manage.py e2e_data` (the e2e data command recommended by
  bazis-front): the test users of the roles of the specs, with the password of
  `E2E_PASSWORD`, and the records the scenarios need.
- In `frontend/`: `npx tsc --noEmit`, `npm run lint`, `npm test`, and `npm run e2e` against
  the running backend with that test data.
'''


class ScaffoldError(Exception):
    pass


def package_name(directory: Path) -> str:
    """
    The name of the project package: the directory name as a Python identifier that does
    not hide a module (`django`, `json`) or the tests.
    """
    name = re.sub(r'\W+', '_', directory.name.lower()).strip('_')
    if not name or name[0].isdigit():
        name = f'project_{name}'.rstrip('_')
    if not valid_package_name(name):
        name = f'{name}_project'
    return name


def valid_package_name(name: str) -> bool:
    return (
        name.isidentifier()
        and not keyword.iskeyword(name)
        and name not in ('tests', 'test')
        and importlib.util.find_spec(name) is None
    )


def frontend_requirement() -> str:
    """
    The requirement of bazis-front in requirements.txt (FRONTEND_REQUIREMENT_ENV overrides it).
    """
    return os.environ.get(FRONTEND_REQUIREMENT_ENV, '').strip() or FRONTEND_REQUIREMENT


def node_problem(run=subprocess.run) -> str | None:
    """
    Why the frontend cannot be built on this machine (Node.js or npm are missing, or Node.js
    is older than NODE_VERSION), or None.
    """
    need = '.'.join(map(str, NODE_VERSION))
    missing = [tool for tool in ('node', 'npm') if shutil.which(tool) is None]
    if missing:
        return (
            f'{" and ".join(missing)} not found: the frontend needs Node.js {need} or newer '
            'with npm (https://nodejs.org)'
        )
    try:
        found = run([shutil.which('node'), '--version'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as err:
        return f'`node --version` failed ({err}): the frontend needs Node.js {need} or newer'
    version = re.match(r'v?(\d+)\.(\d+)', found.stdout.strip())
    if found.returncode or not version:
        return f'`node --version` failed: the frontend needs Node.js {need} or newer'
    if tuple(map(int, version.groups())) < NODE_VERSION:
        return (
            f'Node.js {found.stdout.strip()} is too old: the frontend needs Node.js {need} or '
            'newer (https://nodejs.org)'
        )
    return None


def product_language(code: str) -> tuple[str, str]:
    """
    The language of a product, as a code of Django and its own name, from a code such as
    `ru`, `ru-RU`, `RU` or `pt_BR` (matched by itself, else by its base code, as the core
    matches the language of a request).
    """
    from django.conf.locale import LANG_INFO

    wanted = code.strip().lower().replace('_', '-')
    for candidate in (wanted, wanted.split('-')[0]):
        info = LANG_INFO.get(candidate, {})
        info = LANG_INFO.get(info['fallback'][0], {}) if 'fallback' in info else info
        if 'name_local' in info:
            return info['code'], info['name_local']
    raise ScaffoldError(f'{code!r} is not a language of Django: pass a code such as ru, de or pt-br')


def languages(code: str) -> list[list[str]]:
    """
    The LANGUAGES of a product in a language: that language, then English, the language of
    the msgids, which every product has.
    """
    result = [list(product_language(code))]
    if result[0][0] != 'en':
        result.append(['en', 'English'])
    return result


def write_files(
    directory: Path, name: str, frontend: bool = False, language: str = 'en'
) -> list[Path]:
    """
    Writes the skeleton of the project in a `language` (`LANGUAGE_CODE`, with English in
    `LANGUAGES`), with bazis-front in its requirements and apps when it has a `frontend`
    (the agent creates the frontend itself with `bazis_front init`). Fails if the directory
    has files already.
    """
    if directory.exists() and any(directory.iterdir()):
        raise ScaffoldError(f'{directory} is not empty')
    if not valid_package_name(name):
        raise ScaffoldError(
            f'{name!r} cannot be the project package: it must be an identifier that is not '
            'a keyword, "tests" or the name of an installed module'
        )
    product_languages = languages(language)
    requirements = [*REQUIREMENTS, *([frontend_requirement()] if frontend else [])]
    installed_apps = [FRONTEND_APP] if frontend else []
    files = {
        'manage.py': MANAGE_PY.format(name=name),
        f'{name}/__init__.py': '',
        f'{name}/settings.py': SETTINGS_PY,
        f'{name}/main.py': MAIN_PY.format(name=name),
        f'{name}/router.py': ROUTER_PY,
        f'{name}/urls.py': URLS_PY,
        f'{name}/wsgi.py': WSGI_PY.format(name=name),
        'project.env': PROJECT_ENV.format(
            name=name, installed_apps=json.dumps(installed_apps), templates=json.dumps(TEMPLATES),
            languages=json.dumps(product_languages, ensure_ascii=False),
            language=product_languages[0][0],
        ),
        '.env': DOT_ENV.format(name=name, secret_key=secrets.token_urlsafe(48)),
        '.gitignore': GITIGNORE,
        'requirements.txt': '\n'.join(requirements) + '\n',
        'requirements-dev.txt': '-r requirements.txt\n' + '\n'.join(REQUIREMENTS_DEV) + '\n',
        'pytest.ini': PYTEST_INI.format(name=name),
        'tests/__init__.py': '',
        'tests/conftest.py': CONFTEST_PY.format(name=name),
        # the catalogs of the project: the first of LOCALE_PATHS (the core lists it only when
        # it exists), where `makemessages` writes; without it, into the catalog of a package
        'locale/.gitkeep': '',
        '.mcp.json': json.dumps(
            {'mcpServers': {'bazis': {'command': f'{VENV_BIN}/bazis-mcp{EXE}', 'args': []}}},
            indent=2,
        ) + '\n',
        'AGENTS.md': AGENTS_MD.format(name=name) + (AGENTS_MD_FRONTEND if frontend else ''),
    }
    written = []
    for relative, content in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        written.append(path)
    return written


def create_venv(directory: Path, run=subprocess.run) -> Path:
    """
    Creates `.venv` with the dependencies of the project (with uv if it is installed) and
    returns its Python.
    """
    directory = directory.resolve()  # the commands run in it
    venv = directory / '.venv'
    python = venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    uv = shutil.which('uv')
    if uv:
        # a bazis-front from FRONTEND_REQUIREMENT_ENV, such as a checkout, is built again:
        # uv keeps the build of a directory until its pyproject.toml changes, so it would
        # install an older commit (pip builds a directory at every install)
        refresh = (
            ['--refresh-package', 'bazis-front']
            if frontend_requirement() != FRONTEND_REQUIREMENT else []
        )
        commands = [
            # --seed installs pip, which the agent uses to add packages
            [uv, 'venv', '--quiet', '--seed', '--python', sys.executable, str(venv)],
            [uv, 'pip', 'install', '--quiet', *refresh, '--python', str(python),
             '-r', str(directory / 'requirements-dev.txt')],
        ]
    else:
        commands = [
            [sys.executable, '-m', 'venv', str(venv)],
            [str(python), '-m', 'pip', 'install', '--quiet',
             '-r', str(directory / 'requirements-dev.txt')],
        ]
    for command in commands:
        done = run(command, cwd=directory, capture_output=True, text=True)
        if done.returncode:
            raise ScaffoldError(f'{" ".join(command)} failed:\n{done.stderr.strip()[-2000:]}')
    return python
