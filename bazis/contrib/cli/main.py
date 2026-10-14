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
- `bazis audit [--fix]` reviews the project against the guides and checks of its packages;
- `bazis change ["what to change"]` changes the project in the session of the last run of
  bazis in it, with one request or, without it, the requests typed one after another.
"""

import argparse
import asyncio
import shlex
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeSDKError, CLIConnectionError, ResultMessage

from bazis.contrib.mcp import prompts, scaffold
from bazis.contrib.mcp.rules import FRONTEND_RULES

from . import __version__, agent


def new_task(args) -> agent.Task:
    directory = args.directory.resolve()
    name = args.name or scaffold.package_name(directory)
    frontend = not args.no_frontend
    # the Django of the CLI (bazis), before anything is written
    language = scaffold.product_language(args.language) if args.language else None
    if frontend and (problem := scaffold.node_problem()):
        raise scaffold.ScaffoldError(
            f'{problem}. Install it, or pass --no-frontend to build only the backend.'
        )
    scaffold.write_files(directory, name, frontend=frontend)
    if language:
        scaffold.set_language(directory, language)
    print(f'Created the project {name} in {directory}')
    python = sys.executable  # the settings of the project need only Bazis
    if not args.no_venv:
        print('Installing the dependencies into .venv ...')
        try:
            python = scaffold.create_venv(directory)
        except scaffold.ScaffoldError as err:
            raise scaffold.ScaffoldError(
                f'{err}\nThe files of the project are written: create .venv and install '
                f'requirements-dev.txt by hand, or delete {directory} and run bazis new again.'
            ) from err
    database = scaffold.create_database(directory, name, python)
    print(database)
    ports = scaffold.project_ports(directory)
    prompt = prompts.new_prompt(args.description, name, frontend, language, database, ports)
    return _task(args, prompt, directory, frontend=frontend)


def add_task(args) -> agent.Task:
    project_dir = args.project_dir.resolve()
    built = prompts.has_frontend(project_dir)
    # bazis-front added to a backend builds its frontend
    frontend = built or args.package == 'bazis-front'
    ports = scaffold.project_ports(project_dir) if frontend else None
    prompt = prompts.add_prompt(args.package, args.goal, built, ports)
    return _task(args, prompt, project_dir, frontend=frontend)


def audit_task(args) -> agent.Task:
    project_dir = args.project_dir.resolve()
    frontend = prompts.has_frontend(project_dir)
    if args.fix:
        action = 'Then fix the problems you are sure about and run the final checks of the rules once.'
    else:
        action = 'Change nothing: this is a review.'
    steps = ''
    if frontend:
        cannot_run = '' if args.fix else (
            '; list the checks of the frontend that this review cannot run: `npx tsc --noEmit`, '
            '`npm run lint`, `npm test`, `npm run build` and `npm run e2e` in `frontend/`'
        )
        steps = f"""\
3. The frontend (bazis-front): run `front_check` and `front_status`, and compare `spec/`
   and `frontend/` with the guide of bazis-front (`package_guide("bazis-front")`;
   `frontend/AGENTS.md` only points to its sections): edited copies or generated files, roles or permission rules in
   the screens, literal colors, scenarios that miss a role or what it may not do, screens
   that are not composed from the components{cannot_run}.
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
    if frontend and args.fix:
        prompt += f'\n{prompts.ports_text(scaffold.project_ports(project_dir))}\n'
    return _task(args, prompt, project_dir, writes=args.fix, frontend=frontend)


def change_task(args, request: str, session: str | None) -> agent.Task:
    """
    A request of `bazis change`: in the `session` that it continues, as it is (the session
    has the project and the steps already); in a new session, with the preparation.
    """
    project_dir = args.project_dir.resolve()
    frontend = prompts.has_frontend(project_dir)
    ports = scaffold.project_ports(project_dir) if frontend and not session else None
    prompt = prompts.change_prompt(request, resumed=bool(session), ports=ports)
    return _task(args, prompt, project_dir, frontend=frontend, session_id=session)


def change(args) -> int:
    """
    `bazis change`: the request, or without it the requests typed in the terminal, each
    run in the session of the run before it, from the session of the last run of bazis in
    the project (a new one with `--new-session` or when it cannot be resumed).
    """
    session = None if args.new_session else agent.last_session(args.project_dir.resolve())
    if args.request:
        return run(change_task(args, args.request, session))[0]
    if not sys.stdin.isatty():
        print(
            'bazis: no terminal to type the changes in: pass the change, bazis change "..."',
            file=sys.stderr,
        )
        return 1
    print('Type a change of the project; an empty line, exit or Ctrl-D ends.', file=sys.stderr)
    while True:
        try:
            request = input('\nchange> ').strip()
        except EOFError:
            return 0
        except KeyboardInterrupt:
            return 130
        if request in ('', 'exit'):
            return 0
        code, result = run(change_task(args, request, session))
        if code == 130:
            print('\nbazis: interrupted', file=sys.stderr)
        elif result is not None:
            session = result.session_id


def _task(
    args,
    prompt: str,
    project_dir: Path,
    writes: bool = True,
    frontend: bool = False,
    session_id: str | None = None,
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
        extra_rules=[FRONTEND_RULES] if frontend else [],
        session_id=session_id,
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
        '--project-dir', type=Path, default=Path('.'),
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
    new.add_argument(
        '--language',
        help='The language of the product, a code such as ru, de or pt-br; English is always '
        'one of its languages (default: the language of the description).',
    )
    new.add_argument('--no-venv', action='store_true', help='Do not create .venv.')
    new.add_argument(
        '--no-frontend', action='store_true',
        help='Build only the backend (by default the product also gets its frontend, made by '
        'bazis-front: Node.js 22.12 or newer with npm is needed).',
    )
    new.set_defaults(make_task=new_task)

    add = commands.add_parser('add', parents=[common, project], help='Add a Bazis package.')
    add.add_argument('package', help='The package, e.g. bazis-permit.')
    add.add_argument('goal', nargs='?', help='What the project needs it for.')
    add.set_defaults(make_task=add_task)

    audit = commands.add_parser('audit', parents=[common, project], help='Review the project.')
    audit.add_argument('--fix', action='store_true', help='Also fix the problems.')
    audit.set_defaults(make_task=audit_task)

    change = commands.add_parser(
        'change', parents=[common, project],
        help='Change the project, in the session of the last run of bazis in it.',
    )
    change.add_argument(
        'request', nargs='?',
        help='What to change, in your words (without it, the changes are typed in the '
        'terminal one after another).',
    )
    change.add_argument(
        '--new-session', action='store_true',
        help='Start a new session instead of continuing that of the last run.',
    )
    return root


def run(task: agent.Task) -> tuple[int, ResultMessage | None]:
    """
    Runs the agent of a task and reports the end of the run: the exit code (130 when
    interrupted) and the result of the agent.
    """
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
        return 1, None
    except KeyboardInterrupt:
        return 130, None
    if result is None:
        print('bazis: the agent ended without a result', file=sys.stderr)
        return 1, None
    cost = agent.last_run(task.project_dir)['cost_usd']
    cost = f', ${cost:.2f}' if cost is not None else ''
    print(f'\n[{result.num_turns} turns{cost}]', file=sys.stderr)
    if result.is_error:
        print(f'bazis: {agent.error_text(result)}', file=sys.stderr)
        return 1, result
    return 0, result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == 'change':
        return change(args)
    try:
        task = args.make_task(args)
    except scaffold.ScaffoldError as err:
        print(f'bazis: {err}', file=sys.stderr)
        return 1
    code, result = run(task)
    if result is not None and args.command in ('new', 'add'):
        directory = args.directory if args.command == 'new' else args.project_dir
        cd = '' if directory == Path('.') else f'cd {shlex.quote(str(directory))} && '
        print(f'To change the project: {cd}bazis change', file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
