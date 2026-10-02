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
model, and its virtual environment. The agent then adds the apps the project needs.
"""

import importlib.util
import json
import keyword
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path


#: the packages of every project; the agent adds the Bazis packages the project needs
REQUIREMENTS = ['bazis>=2.4.1']
REQUIREMENTS_DEV = ['bazis-mcp>=2.4.1', 'bazis-test-utils>=2.4.0']


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
BS_INSTALLED_APPS='[]'
BS_ROOT_URLCONF={name}.urls
BS_BAZIS_ROUTER_MODULE={name}.router
BS_WSGI_APPLICATION={name}.wsgi.application
BS_DEFAULT_AUTO_FIELD=django.db.models.BigAutoField
BS_LANGUAGE_CODE=en
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
__pycache__/
*.pyc
.pytest_cache/
media/
static/
'''

PYTEST_INI = '''[pytest]
DJANGO_SETTINGS_MODULE = {name}.settings
'''

CONFTEST_PY = '''import pytest


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
- PostgreSQL with PostGIS and Redis are required to run the project and the tests
  (`.env`); the checks and `makemigrations` work without them.
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


def write_files(directory: Path, name: str) -> list[Path]:
    """
    Writes the skeleton of the project. Fails if the directory has files already.
    """
    if directory.exists() and any(directory.iterdir()):
        raise ScaffoldError(f'{directory} is not empty')
    if not valid_package_name(name):
        raise ScaffoldError(
            f'{name!r} cannot be the project package: it must be an identifier that is not '
            'a keyword, "tests" or the name of an installed module'
        )
    files = {
        'manage.py': MANAGE_PY.format(name=name),
        f'{name}/__init__.py': '',
        f'{name}/settings.py': SETTINGS_PY,
        f'{name}/main.py': MAIN_PY.format(name=name),
        f'{name}/router.py': ROUTER_PY,
        f'{name}/urls.py': URLS_PY,
        f'{name}/wsgi.py': WSGI_PY.format(name=name),
        'project.env': PROJECT_ENV.format(name=name, templates=json.dumps(TEMPLATES)),
        '.env': DOT_ENV.format(name=name, secret_key=secrets.token_urlsafe(48)),
        '.gitignore': GITIGNORE,
        'requirements.txt': '\n'.join(REQUIREMENTS) + '\n',
        'requirements-dev.txt': '-r requirements.txt\n' + '\n'.join(REQUIREMENTS_DEV) + '\n',
        'pytest.ini': PYTEST_INI.format(name=name),
        'tests/__init__.py': '',
        'tests/conftest.py': CONFTEST_PY.format(name=name),
        '.mcp.json': json.dumps(
            {'mcpServers': {'bazis': {'command': f'{VENV_BIN}/bazis-mcp{EXE}', 'args': []}}},
            indent=2,
        ) + '\n',
        'AGENTS.md': AGENTS_MD.format(name=name),
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
    venv = directory / '.venv'
    python = venv / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    uv = shutil.which('uv')
    if uv:
        commands = [
            # --seed installs pip, which the agent uses to add packages
            [uv, 'venv', '--quiet', '--seed', '--python', sys.executable, str(venv)],
            [uv, 'pip', 'install', '--quiet', '--python', str(python),
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
