"""Bot-managed state outside main; repository readers cannot modify it."""
from __future__ import annotations

import json
import urllib.error

from project_workflow.project_registry import Invalid
from project_workflow.submission import number

BRANCH = "project-workflow/state"


def root_path(identifier):
    return f"roots/{number(identifier)}.json"


def issue_path(identifier):
    return f"issues/{number(identifier)}.json"


def snapshot(api):
    try:
        sha = api.get(f"git/ref/heads/{BRANCH}")["object"]["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return None, {}
    return sha, api.tree(sha)


def read(api, path):
    _, tree = snapshot(api)
    if path not in tree:
        return None
    value = json.loads(api.reader(tree)(path))
    if not isinstance(value, dict):
        raise Invalid("Automation state must be a JSON object")
    return value


def write(api, changes):
    """Append one non-forced commit; submission/policy jobs share a queue."""
    for path in changes:
        parts = path.split('/')
        if len(parts) != 2 or parts[0] not in {'roots', 'issues'} or not parts[1].endswith('.json'):
            raise Invalid('Unsupported automation state path')
        number(int(parts[1][:-5]))
    sha, tree = snapshot(api)
    read_text = api.reader(tree) if sha else None
    entries = []
    for path, value in changes.items():
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        if len(content.encode()) > 1024 * 1024:
            raise Invalid('Automation state file exceeds 1 MiB')
        if path not in tree or read_text(path) != content:
            entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'content': content})
    if not entries:
        return
    payload = {'tree': entries}
    if sha:
        payload['base_tree'] = api.get(f'git/commits/{sha}')['tree']['sha']
    created_tree = api.request('POST', 'git/trees', payload)
    commit = api.request('POST', 'git/commits', {'message': 'Update project submission state',
        'tree': created_tree['sha'], 'parents': [sha] if sha else []})
    current_sha, _ = snapshot(api)
    if current_sha != sha:
        raise Invalid('Automation state advanced during processing; retry')
    if sha:
        api.request('PATCH', f'git/refs/heads/{BRANCH}', {'sha': commit['sha'], 'force': False})
    else:
        api.request('POST', 'git/refs', {'ref': 'refs/heads/' + BRANCH, 'sha': commit['sha']})
