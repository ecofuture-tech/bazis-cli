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

- `bazis new DIR "what the project does"` creates a project and builds it;
- `bazis add PACKAGE [what for]` adds a Bazis package to the project;
- `bazis audit [--fix]` reviews the project against the guides and checks of its packages.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeSDKError, CLIConnectionError

from . import __version__, agent, scaffold


def new_task(args) -> agent.Task:
    directory = args.directory.resolve()
    name = scaffold.package_name(directory)
    scaffold.write_files(directory, name)
    print(f'Created the project {name} in {directory}')
    if not args.no_venv:
        print('Installing the dependencies into .venv ...')
        scaffold.create_venv(directory)
    prompt = f"""\
Build this Bazis project: {args.description}

The skeleton is ready: the project package `{name}` (settings, root router, ASGI app in
`{name}/main.py`), `project.env`, `.env`, `requirements.txt`, `tests/`, and `.venv` with
Bazis installed. Read the guide of the core (`package_guide("bazis")`), choose the Bazis
packages the project needs with `list_packages`, read their guides, then create the apps
with their models, routes and tests, and register the routers in `{name}/router.py`.
"""
    return _task(args, prompt, directory)


def add_task(args) -> agent.Task:
    goal = f' The project needs it for: {args.goal}' if args.goal else ''
    prompt = f"""\
Add the Bazis package {args.package} to this project and use it.{goal}

Read its guide with `package_guide("{args.package}")` and the guides of the Bazis packages
it requires that are not installed (`list_packages`), install them, follow the setup of the
guides, change the models and routes they concern, create the migrations, add tests, and
make `run_doctor` pass without the warnings of the new packages.
"""
    return _task(args, prompt, args.project_dir.resolve())


def audit_task(args) -> agent.Task:
    if args.fix:
        action = 'Then fix the problems you are sure about and run the checks and the tests again.'
    else:
        action = 'Change nothing: this is a review.'
    prompt = f"""\
Audit this Bazis project.

1. Run `run_doctor` with `deploy=true` and `project_info`.
2. For every installed Bazis package read `package_guide` and compare the project with its
   rules and pitfalls: routes that bypass permissions or the update checks, settings that
   are unsafe in production, packages that are installed but not used, needs that a Bazis
   package solves but the project implements by hand, missing tests and migrations.
3. Report the problems ordered by severity, each with the file, the reason and the fix.

{action}
"""
    return _task(args, prompt, args.project_dir.resolve(), writes=args.fix)


def _task(args, prompt: str, project_dir: Path, writes: bool = True) -> agent.Task:
    return agent.Task(
        prompt=prompt,
        project_dir=project_dir,
        writes=writes,
        model=args.model,
        effort=args.effort,
        max_budget_usd=args.budget,
        yes=args.yes,
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
        help='Run shell commands without asking (file edits in the project are always allowed).',
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
    new.add_argument('--no-venv', action='store_true', help='Do not create .venv.')
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
