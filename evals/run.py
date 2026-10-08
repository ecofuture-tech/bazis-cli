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
Runs the evals: for every task of tasks.toml, `bazis new` builds the project from its
description (the real agent: it spends credits, limited by --budget per task), then the
grader checks the result. Writes a JSON file per task and summary.md to the results
directory. A task with a frontend installs bazis-front (BAZIS_FRONT_REQUIREMENT points
to a build of it that is not on PyPI) and needs Node.js.

    python -m evals.run --budget 5 [--task library] [--model claude-opus-5-5]
    python -m evals.run --grade-only RESULTS_DIR      # grade the projects again
"""

import argparse
import asyncio
import json
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from bazis.contrib.cli import agent, main, scaffold

from . import grade


TASKS = Path(__file__).resolve().parent / 'tasks.toml'


def load_tasks(names: list[str] | None = None) -> list[dict]:
    tasks = tomllib.loads(TASKS.read_text(encoding='utf-8'))['task']
    if names:
        unknown = set(names) - {t['id'] for t in tasks}
        if unknown:
            raise SystemExit(f'unknown tasks: {", ".join(sorted(unknown))}')
        tasks = [t for t in tasks if t['id'] in names]
    return tasks


async def build(task: dict, project: Path, args) -> dict:
    """
    Builds the project of a task with the agent of `bazis new`; returns the run facts.
    """
    name = scaffold.package_name(project)
    frontend = task.get('frontend', False)
    # without --language: the agent finds the `language` of a task in its description
    scaffold.write_files(project, name, frontend=frontend)
    scaffold.create_venv(project)
    started = time.monotonic()
    with (project.parent / f'{task["id"]}.log').open('w', encoding='utf-8') as log:
        result = await agent.run(
            agent.Task(
                prompt=main.new_prompt(task['description'], name, frontend),
                project_dir=project,
                model=args.model,
                effort=args.effort,
                max_budget_usd=args.budget,
                yes=True,  # no terminal: the agent runs commands in its own project only
                extra_rules=[agent.FRONTEND_RULES] if frontend else [],
            ),
            out=log,
        )
    return {
        'seconds': round(time.monotonic() - started),
        'cost_usd': result.total_cost_usd if result else None,
        'turns': result.num_turns if result else None,
        'agent_error': agent.error_text(result) if result and result.is_error else None,
    }


def write_summary(results_dir: Path, results: list[dict]) -> Path:
    lines = [
        f'# Evals of bazis-cli — {results_dir.name}', '',
        '| Task | Score | Cost, USD | Turns | Minutes | Failed checks |',
        '|---|---|---|---|---|---|',
    ]
    for r in results:
        failed = '; '.join(c['name'] for c in r['checks'] if not c['passed']) or '—'
        cost = f'{r["cost_usd"]:.2f}' if r.get('cost_usd') is not None else '—'
        minutes = f'{r["seconds"] / 60:.0f}' if r.get('seconds') is not None else '—'
        lines.append(
            f'| {r["task"]} | {r["score"]:.0%} | {cost} | {r.get("turns") or "—"} | {minutes} | {failed} |'
        )
    total = sum(r.get('cost_usd') or 0 for r in results)
    mean = sum(r['score'] for r in results) / len(results) if results else 0
    lines += ['', f'Mean score {mean:.0%}, total cost ${total:.2f}.', '']
    for r in results:
        lines += [f'## {r["task"]}', '']
        if r.get('agent_error'):
            lines += [f'Agent: {r["agent_error"]}', '']
        lines += [f'- {"pass" if c["passed"] else "FAIL"}: {c["name"]}' + (
            f' — {c["detail"].splitlines()[-1][:200]}' if c['detail'] else ''
        ) for c in r['checks']]
        lines.append('')
    path = results_dir / 'summary.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def grade_task(task: dict, project: Path, facts: dict) -> dict:
    checks = grade.grade(project, task) if (project / 'manage.py').is_file() else [
        grade.Check('project created', False, 'no manage.py')
    ]
    return {'task': task['id'], **facts, 'score': grade.score(checks), 'checks': grade.as_dicts(checks)}


def main_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog='python -m evals.run')
    parser.add_argument('--task', action='append', help='Run only this task (repeatable).')
    parser.add_argument('--budget', type=float, help='USD per task (required to run the agent).')
    parser.add_argument('--model', default=agent.DEFAULT_MODEL)
    parser.add_argument('--effort', default=agent.DEFAULT_EFFORT)
    parser.add_argument('--results', type=Path, default=Path('eval-results'))
    parser.add_argument('--grade-only', type=Path, help='Grade the projects of a results directory.')
    args = parser.parse_args(argv)

    tasks = load_tasks(args.task)
    if args.grade_only:
        results_dir = args.grade_only.resolve()
        results = []
        for task in tasks:
            facts_file = results_dir / f'{task["id"]}.json'
            facts = json.loads(facts_file.read_text()) if facts_file.is_file() else {}
            facts = {k: facts.get(k) for k in ('seconds', 'cost_usd', 'turns', 'agent_error')}
            result = grade_task(task, results_dir / task['id'], facts)
            facts_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            results.append(result)
    else:
        if args.budget is None:
            raise SystemExit('--budget is required: the agent spends credits')
        stamp = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
        results_dir = (args.results / stamp).resolve()
        results_dir.mkdir(parents=True)
        results = []
        for task in tasks:
            project = results_dir / task['id']
            print(f'{task["id"]}: building ...', flush=True)
            try:
                facts = asyncio.run(build(task, project, args))
            except Exception as err:  # a failed run is a result too
                facts = {'seconds': None, 'cost_usd': None, 'turns': None, 'agent_error': repr(err)}
            print(f'{task["id"]}: grading ...', flush=True)
            result = grade_task(task, project, facts)
            (results_dir / f'{task["id"]}.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
            print(f'{task["id"]}: {result["score"]:.0%}, ${result.get("cost_usd") or 0:.2f}', flush=True)
            results.append(result)
    print(write_summary(results_dir, results))


if __name__ == '__main__':
    main_cli()
