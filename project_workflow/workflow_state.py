"""One proposal branch holds its YAML and revision receipts.

Receipts are compressed commit metadata, not files in the proposed tree.
Every head has one main parent, so the PR diff contains only project YAML.
The old shared branch is read only, for migrating active test submissions.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import re
import urllib.error
import zlib

from project_workflow.project_registry import Invalid
from project_workflow.submission import changed_files, number, project_path, submission_branch

LEGACY_BRANCH = "project-workflow/state"
TRAILER = "\n\nPIT-Submission-State-v1: "
MAX_STATE_BYTES = 8 * 1024 * 1024


def root_path(identifier):
    return f"roots/{number(identifier)}.json"


def issue_path(identifier):
    return f"issues/{number(identifier)}.json"


def ref_sha(api, branch):
    try:
        return api.get(f"git/ref/heads/{branch}")["object"]["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return None


def legacy_read(api, path):
    sha = ref_sha(api, LEGACY_BRANCH)
    if not sha:
        return None
    tree = api.tree(sha)
    if path not in tree:
        return None
    value = json.loads(api.reader(tree)(path))
    if not isinstance(value, dict):
        raise Invalid("Automation state must be a JSON object")
    return value


def decode(message):
    if TRAILER not in message:
        return None
    encoded = message.split(TRAILER)[-1].strip()
    try:
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
            raise ValueError()
        return data
    except (ValueError, TypeError, zlib.error) as exc:
        raise Invalid("Submission branch metadata is malformed; ask SC to inspect it") from exc


def message(root, data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if len(raw) > MAX_STATE_BYTES:
        raise Invalid("Submission history exceeds the 8 MiB limit; ask SC to inspect it")
    encoded = base64.b64encode(zlib.compress(raw)).decode()
    return f"Project submission {root}" + TRAILER + encoded


def snapshot(api, root):
    root = number(root)
    sha = ref_sha(api, submission_branch(root))
    data = None
    commit = None
    if sha:
        commit = api.get(f"git/commits/{sha}")
        if len(commit['parents']) != 1:
            raise Invalid("Submission branch has unsupported history; ask SC to inspect it")
        parent_tree = api.tree(commit['parents'][0]['sha'])
        head_tree = api.tree(sha)
        path = project_path(root)
        if path in parent_tree or changed_files(parent_tree, head_tree) - {path}:
            raise Invalid("Submission branch contains unrelated changes; ask SC to inspect it")
        data = decode(commit.get('message', ''))
    if data is None:
        state = legacy_read(api, root_path(root))
        if sha and state is None:
            raise Invalid("Reserved submission branch has no authenticated metadata")
        data = {}
        if state is not None:
            data[root_path(root)] = state
            for entry in state['history']:
                packet = legacy_read(api, issue_path(entry['number']))
                if packet is not None:
                    data[issue_path(entry['number'])] = packet
    for path, value in data.items():
        if not re.fullmatch(r'(roots|issues)/[1-9][0-9]*\.json', path) or value.get('root') != root:
            raise Invalid("Submission metadata refers to an unrelated project")
    return sha, commit, data


def read(api, path, *, root=None):
    if root is None:
        if not path.startswith('roots/'):
            raise Invalid("Answer receipts need their original submission number")
        root = int(path.split('/')[1][:-5])
    return deepcopy(snapshot(api, root)[2].get(path))


def publish(api, root, data, tree_sha, base_sha, expected_sha):
    """Publish only after a freshness check; all writers share the policy queue."""
    commit = api.request('POST', 'git/commits', {'message': message(root, data),
        'tree': tree_sha, 'parents': [base_sha]})
    api.status(commit['sha'], 'pending', 'Awaiting current submission validation')
    branch = submission_branch(root)
    if ref_sha(api, branch) != expected_sha:
        raise Invalid('Submission branch advanced during processing; retry')
    if expected_sha:
        api.request('PATCH', f'git/refs/heads/{branch}', {'sha': commit['sha'], 'force': True})
    else:
        api.request('POST', 'git/refs', {'ref': 'refs/heads/' + branch, 'sha': commit['sha']})
    return commit['sha']


def write(api, changes, *, root):
    sha, commit, data = snapshot(api, root)
    updated = deepcopy(data)
    for path, value in changes.items():
        if (not re.fullmatch(r'(roots|issues)/[1-9][0-9]*\.json', path)
                or not isinstance(value, dict) or value.get('root') != root):
            raise Invalid('Unsupported submission state path or identity')
        updated[path] = value
    if updated == data and sha and decode(commit.get('message', '')) is not None:
        return sha
    if sha:
        base_sha = commit['parents'][0]['sha']
        tree_sha = commit['tree']['sha']
    else:
        base_sha = api.get('git/ref/heads/main')['object']['sha']
        tree_sha = api.get(f'git/commits/{base_sha}')['tree']['sha']
    return publish(api, root, updated, tree_sha, base_sha, sha)


def remove(api, root, expected_sha):
    branch = submission_branch(root)
    current = ref_sha(api, branch)
    if current is None:
        return
    if current != expected_sha:
        raise Invalid('Submission branch changed before cleanup; retry')
    api.request('DELETE', f'git/refs/heads/{branch}', None)
