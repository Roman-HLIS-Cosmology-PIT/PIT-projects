"""Permanent numbers and atomic writes to the accepted project registry."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error

from project_workflow.project_registry import Invalid, load_context, parse_yaml, utc_timestamp
from project_workflow.submission import project_yaml, record_path

NUMBER_REGISTRY = 'projects/number_registry.yaml'


def timestamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def ledger(read, paths, projects):
    value = parse_yaml(read(NUMBER_REGISTRY)) if NUMBER_REGISTRY in paths else {'schema_version': 1, 'allocations': []}
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'allocations'}
            or value['schema_version'] != 1 or not isinstance(value['allocations'], list)):
        raise Invalid('Invalid project number registry')
    used, keys = set(), set()
    for item in value['allocations']:
        if not isinstance(item, dict) or set(item) != {'project_id', 'project_number', 'working_group', 'submitted_at', 'assigned_at'}:
            raise Invalid('Invalid project number allocation')
        key, display, group = item['project_id'], item['project_number'], item['working_group']
        if (not all(isinstance(x, str) for x in (key, display, group))
                or not re.fullmatch(r'P[0-9]{3,}', key)
                or not re.fullmatch(r'[A-Z][A-Z0-9_-]*', group)
                or not re.fullmatch(re.escape(group) + r'[1-9][0-9]*', display)
                or display in used or key in keys):
            raise Invalid('Project number allocations must be unique and match their group')
        utc_timestamp(item['submitted_at'])
        utc_timestamp(item['assigned_at'])
        used.add(display)
        keys.add(key)
        record = projects.get(key)
        if record is not None and (record.get('project_number') != display
                                  or record['working_groups'][0]['code'] != group):
            raise Invalid('Project record differs from its permanent number allocation')
    for key, record in projects.items():
        if record.get('project_number') and key not in keys:
            raise Invalid('Numbered project has no permanent registry allocation')
    return value


def number_updates(read, paths, ctx, *, now=None):
    """Only invoke for accepted main, never for an unmerged proposal tree."""
    value = deepcopy(ledger(read, paths, ctx.projects))
    counters = {}
    for item in value['allocations']:
        group = item['working_group']
        counters[group] = max(counters.get(group, 0), int(item['project_number'][len(group):]))
    pending = [p for p in ctx.projects.values() if not p.get('project_number')]
    # Use submission time for all projects visible in this accepted batch.
    pending.sort(key=lambda p: (p.get('submission', {}).get('submitted_at', ''), p['project_id']))
    updates = {}
    for record in pending:
        submitted = record.get('submission', {}).get('submitted_at')
        if submitted is None:
            raise Invalid('Accepted projects need submission provenance before numbering')
        group = record['working_groups'][0]['code']
        counters[group] = counters.get(group, 0) + 1
        candidate = deepcopy(record)
        candidate['project_number'] = group + str(counters[group])
        value['allocations'].append({'project_id': record['project_id'], 'project_number': candidate['project_number'],
                                    'working_group': group, 'submitted_at': submitted, 'assigned_at': now or timestamp()})
        updates[record_path(candidate)] = project_yaml(candidate)
    if pending:
        updates[NUMBER_REGISTRY] = project_yaml(value)
    return updates


def write_main(api, parent, updates, message, *, still_current=None):
    """Append one validated transaction; never force or create a review PR."""
    if not updates:
        return parent
    if any(path != NUMBER_REGISTRY and not re.fullmatch(r'projects/P[0-9]{3,}\.yaml', path) for path in updates):
        raise Invalid('Accepted-project transactions may write only project data')
    original = api.tree(parent)
    read = api.reader(original)
    # Validate the entire resulting registry, including allocation/record agreement.
    paths = set(original) | set(updates)
    candidate_read = lambda path: updates[path] if path in updates else read(path)
    ctx = load_context(candidate_read, paths=paths)
    ledger(candidate_read, paths, ctx.projects)
    tree = api.request('POST', 'git/trees', {'base_tree': api.get(f'git/commits/{parent}')['tree']['sha'],
        'tree': [{'path': p, 'mode': '100644', 'type': 'blob', 'content': text} for p, text in sorted(updates.items())]})
    commit = api.request('POST', 'git/commits', {'message': message, 'tree': tree['sha'], 'parents': [parent]})
    if still_current:
        still_current()
    if api.get('git/ref/heads/main')['object']['sha'] != parent:
        raise Invalid('Accepted registry changed during the transaction; retry the operation')
    try:
        api.request('PATCH', 'git/refs/heads/main', {'sha': commit['sha'], 'force': False})
    except urllib.error.HTTPError as exc:
        from project_workflow.sync_generated import protection_rejection
        if protection_rejection(exc):
            raise Invalid('Main protection blocked automatic project data writes. Configure the approved automation actor for these writes; no review PR was created.') from exc
        raise
    return commit['sha']


def assign(api, *, checkout_sha=None):
    parent = api.get('git/ref/heads/main')['object']['sha']
    if checkout_sha is not None and parent != checkout_sha:
        raise Invalid('Numbering must execute current trusted main code; rerun maintenance')
    tree = api.tree(parent)
    read = api.reader(tree)
    ctx = load_context(read, paths=tree)
    updates = number_updates(read, tree, ctx)
    write_main(api, parent, updates, 'Assign accepted project numbers')
    return len(updates) - 1 if updates else 0


def main():
    from project_workflow.validate_pr import GitHub
    try:
        api = GitHub(os.environ['GITHUB_REPOSITORY'], os.environ.get('PROJECT_DATA_TOKEN') or os.environ['GITHUB_TOKEN'])
        checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        print(f'Assigned {assign(api, checkout_sha=checkout)} accepted project number(s).')
        return 0
    except urllib.error.HTTPError as exc:
        print(f'Project numbering failed (GitHub HTTP {exc.code}); retry maintenance.', file=sys.stderr)
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
