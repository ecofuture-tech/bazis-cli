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
The `bazis` command:

- `bazis new DIR "what the project does"` creates a product and builds its backend and,
  unless `--no-frontend`, its frontend;
- `bazis add PACKAGE [what for]` adds a Bazis package to the project;
- `bazis audit [--fix]` reviews the project against the guides and checks of its packages.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeSDKError, CLIConnectionError

from . import __version__, agent, scaffold


#: the order of the work on a product with a frontend made by bazis-front
FRONTEND_STEPS = """\
1. Read the guides of the core and of bazis-front (`package_guide("bazis-front")`), choose
   the Bazis packages the product needs (`list_packages`; the frontend logs in with
   bazis-users, roles and access need bazis-permit, statuses and transits bazis-statusy,
   files bazis-uploadable), install them and list their apps. Then create the frontend
   with `.venv/bin/python manage.py bazis_front init` (`--preset portal` for a product for
   the public, `workspace` for the staff): it writes `frontend/` and the starters of
   `spec/`.
2. Describe the product in `spec/product.yaml` from {source}: the roles (each with its
   permit role and a `test_user`), the entities with their fields, the workflows of the
   entities with statuses, the access of each role, and scenarios for the main tasks of
   every role, including what a role may not do. Write the screens the scenarios use in
   `spec/screens/<id>.yaml`. Run `front_check` and fix the errors of the shape and of the
   references.
3. Make the backend satisfy the specs: the apps, models, route sets and routers; the
   permit roles with the permissions of the access and the statuses and transits of the
   workflows as data migrations; the management command `e2e_data` with the test users
   and the items the scenarios open; tests; `makemigrations`; `run_doctor` without errors.
4. Migrate the database (`.venv/bin/python manage.py migrate`) and export the contract
   (`.venv/bin/python manage.py bazis_front contract`).
5. Run `front_check` and fix the spec or the backend as each `hint` says until it reports
   no errors; export the contract again after every change of the backend.
6. Choose the components with `front_catalog` and copy those the screens need
   (`.venv/bin/python manage.py bazis_front add ...`), then write the screens in
   `frontend/src/screens/<id>/` from their specs, composed from the components, with
   their routes and navigation in `frontend/src/app/`, as `frontend/AGENTS.md` shows.
7. Write the design in `spec/design/` (the brand, the colors and the fonts that fit
   {source}) and generate the theme (`.venv/bin/python manage.py bazis_front design`).
8. Generate the end-to-end tests (`.venv/bin/python manage.py bazis_front e2e`), install
   the browser once (`npx playwright install chromium` in `frontend/`) and run them
   against the backend with its test data (`e2e_data`); fix the screens, the specs or the
   backend until they pass.
9. Finish with `run_doctor`, the tests of the backend, `front_check` without errors,
   `front_status` with nothing stale, and in `frontend/` `npx tsc --noEmit`,
   `npm run lint`, `npm test` and `npm run build`. Say how to run the backend
   (`.venv/bin/uvicorn {name}.main:app --port 8000`) and the frontend (`npm run dev` in
   `frontend/`).
"""


def new_prompt(description: str, name: str, frontend: bool = False) -> str:
    """
    The task of the agent of `bazis new` (also used by the evals): the backend, and with a
    `frontend` the whole product with its frontend made by bazis-front.
    """
    skeleton = f"""\
Build this Bazis project: {description}

The skeleton is ready: the project package `{name}` (settings, root router, ASGI app in
`{name}/main.py`), `project.env`, `.env`, `requirements.txt`, `tests/`, and `.venv` with
Bazis installed."""
    if not frontend:
        return skeleton + f""" Read the guide of the core (`package_guide("bazis")`), choose the
Bazis packages the project needs with `list_packages`, read their guides, then create the
apps with their models, routes and tests, and register the routers in `{name}/router.py`.
"""
    return skeleton + f""" bazis-front is in `requirements.txt` and `BS_INSTALLED_APPS`.

Build the whole product, the backend and its frontend, in this order; register the
routers of the apps in `{name}/router.py`.

{FRONTEND_STEPS.format(source='the description', name=name)}"""


def new_task(args) -> agent.Task:
    directory = args.directory.resolve()
    name = args.name or scaffold.package_name(directory)
    frontend = not args.no_frontend
    if frontend and (problem := scaffold.node_problem()):
        raise scaffold.ScaffoldError(
            f'{problem}. Install it, or pass --no-frontend to build only the backend.'
        )
    scaffold.write_files(directory, name, frontend=frontend)
    print(f'Created the project {name} in {directory}')
    if not args.no_venv:
        print('Installing the dependencies into .venv ...')
        try:
            scaffold.create_venv(directory)
        except scaffold.ScaffoldError as err:
            raise scaffold.ScaffoldError(
                f'{err}\nThe files of the project are written: create .venv and install '
                f'requirements-dev.txt by hand, or delete {directory} and run bazis new again.'
            ) from err
    prompt = new_prompt(args.description, name, frontend)
    return _task(args, prompt, directory, frontend=frontend)


def has_frontend(project_dir: Path) -> bool:
    """
    Whether the project has a frontend made by `bazis_front init` (its lock).
    """
    return (project_dir / 'frontend' / 'bazis-front.lock.json').is_file()


def add_task(args) -> agent.Task:
    project_dir = args.project_dir.resolve()
    goal = f' The project needs it for: {args.goal}' if args.goal else ''
    prompt = f"""\
Add the Bazis package {args.package} to this project and use it.{goal}

Read its guide with `package_guide("{args.package}")` and the guides of the Bazis packages
it requires that are not installed (`list_packages`), install them, follow the setup of the
guides, change the models and routes they concern, create the migrations, add tests, and
make `run_doctor` pass without the warnings of the new packages.
"""
    frontend = has_frontend(project_dir)
    if frontend:
        prompt += """
The product has a frontend (bazis-front). Describe in `spec/` what the package changes in
the product (fields, workflows, access, scenarios, screens). After the backend: migrate,
export the contract (`.venv/bin/python manage.py bazis_front contract`) and fix the spec
or the backend until `front_check` reports no errors. With `front_catalog` find the
components that need the capability of the package and copy those the screens need
(`bazis_front add`), use them in the screens, generate the end-to-end tests again
(`bazis_front e2e`), extend `e2e_data`, and run `npx tsc --noEmit`, `npm run lint`,
`npm test` and `npm run e2e` in `frontend/`.
"""
    elif args.package == 'bazis-front':
        frontend = True
        source = 'the backend' + (' and what the project needs it for' if args.goal else '')
        steps = FRONTEND_STEPS.format(source=source, name='<project package>')
        prompt += f'\nThen build the frontend of the product in this order:\n\n{steps}'
    return _task(args, prompt, project_dir, frontend=frontend)


def audit_task(args) -> agent.Task:
    project_dir = args.project_dir.resolve()
    frontend = has_frontend(project_dir)
    if args.fix:
        action = 'Then fix the problems you are sure about and run the checks and the tests again.'
    else:
        action = 'Change nothing: this is a review.'
    steps = ''
    if frontend:
        if args.fix:
            run = (
                'run `bazis_front contract --check`, `design --check` and `e2e --check`, and '
                'in `frontend/` `npx tsc --noEmit`, `npm run lint`, `npm test` and '
                '`npm run e2e` against the backend with its test data'
            )
        else:
            run = (
                'list the commands that this review cannot run: `npx tsc --noEmit`, '
                '`npm run lint`, `npm test` and `npm run e2e` in `frontend/`'
            )
        steps = f"""\
3. The frontend (bazis-front): run `front_check` and `front_status`, and compare `spec/`
   and `frontend/` with the guides of bazis-front (`package_guide("bazis-front")`,
   `frontend/AGENTS.md`): edited copies or generated files, roles or permission rules in
   the screens, literal colors, scenarios that miss a role or what it may not do, screens
   that are not composed from the components; {run}.
"""
    prompt = f"""\
Audit this Bazis project.

1. Run `run_doctor` with `deploy=true` and `project_info`.
2. For every installed Bazis package read `package_guide` and compare the project with its
   rules and pitfalls: routes that bypass permissions or the update checks, settings that
   are unsafe in production, packages that are installed but not used, needs that a Bazis
   package solves but the project implements by hand, missing tests and migrations.
{steps}{4 if frontend else 3}. Report the problems ordered by severity, each with the file, the reason and the fix.

{action}
"""
    return _task(args, prompt, project_dir, writes=args.fix, frontend=frontend)


def _task(
    args, prompt: str, project_dir: Path, writes: bool = True, frontend: bool = False
) -> agent.Task:
    if frontend and writes and (problem := scaffold.node_problem()):
        print(f'bazis: warning: {problem}; the agent skips the steps that need it.', file=sys.stderr)
    return agent.Task(
        prompt=prompt,
        project_dir=project_dir,
        writes=writes,
        model=args.model,
        effort=args.effort,
        max_budget_usd=args.budget,
        yes=args.yes,
        extra_rules=[agent.FRONTEND_RULES] if frontend else [],
    )


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--model', default=agent.DEFAULT_MODEL, help='Default: %(default)s.')
    common.add_argument(
        '--effort', default=agent.DEFAULT_EFFORT,
        choices=['low', 'medium', 'high', 'xhigh', 'max'], help='Default: %(default)s.',
    )
    common.add_argument('--budget', type=float, help='Stop after spending this many USD.')
    common.add_argument(
        '--yes', '-y', action='store_true',
        help='Allow shell commands and the other tools without asking (edits of the files of '
        'the project are always allowed; git cannot commit, push or reset).',
    )

    project = argparse.ArgumentParser(add_help=False)
    project.add_argument(
        '--project-dir', type=Path, default=Path.cwd(),
        help='The directory of manage.py (default: the current directory).',
    )

    root = argparse.ArgumentParser(
        prog='bazis', description='Builds and audits Bazis projects with Claude.'
    )
    root.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    commands = root.add_subparsers(dest='command', required=True)

    new = commands.add_parser('new', parents=[common], help='Create and build a project.')
    new.add_argument('directory', type=Path, help='A new or empty directory.')
    new.add_argument('description', help='What the project does, in your words.')
    new.add_argument('--name', help='The project package (default: from the directory name).')
    new.add_argument('--no-venv', action='store_true', help='Do not create .venv.')
    new.add_argument(
        '--no-frontend', action='store_true',
        help='Build only the backend (by default the product also gets its frontend, made by '
        'bazis-front: Node.js with npm is needed).',
    )
    new.set_defaults(make_task=new_task)

    add = commands.add_parser('add', parents=[common, project], help='Add a Bazis package.')
    add.add_argument('package', help='The package, e.g. bazis-permit.')
    add.add_argument('goal', nargs='?', help='What the project needs it for.')
    add.set_defaults(make_task=add_task)

    audit = commands.add_parser('audit', parents=[common, project], help='Review the project.')
    audit.add_argument('--fix', action='store_true', help='Also fix the problems.')
    audit.set_defaults(make_task=audit_task)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        task = args.make_task(args)
    except scaffold.ScaffoldError as err:
        print(f'bazis: {err}', file=sys.stderr)
        return 1
    try:
        result = asyncio.run(agent.run(task))
    except ClaudeSDKError as err:
        print(f'bazis: {err}', file=sys.stderr)
        if isinstance(err, CLIConnectionError) or 'auth' in str(err).lower():
            print(
                'The agent runs Claude Code: sign in with `claude login` or set '
                'ANTHROPIC_API_KEY.',
                file=sys.stderr,
            )
        return 1
    except KeyboardInterrupt:
        return 130
    if result is None:
        print('bazis: the agent ended without a result', file=sys.stderr)
        return 1
    cost = f', ${result.total_cost_usd:.2f}' if result.total_cost_usd is not None else ''
    print(f'\n[{result.num_turns} turns{cost}]', file=sys.stderr)
    if result.is_error:
        print(f'bazis: {agent.error_text(result)}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
