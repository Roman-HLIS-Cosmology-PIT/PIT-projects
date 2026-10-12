"""Authenticated replacement Issues; formatting and state stay separate."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

from project_workflow.issue_form import KEEP_VALUE, current_labels
from project_workflow.project_registry import Invalid
from project_workflow.submission import answers, correction_issue, number, optional, resolved_values, issue_version, list_text, is_submission_bot
from project_workflow import workflow_state
from project_workflow.review import FORMATTED_MARKER, summary_body

MAX_REVISIONS = 100
MAX_URL_LENGTH = 7500
REFERENCE = re.compile(r"(?:\n\n)?<!-- pit-correction:([A-Za-z0-9_-]{20,600}) -->\s*$")
ROUTE = re.compile(r"\n<!-- pit-project-root:([1-9][0-9]*) -->\s*$")
REQUEST = re.compile(r"\n<!-- pit-edit-request:([A-Za-z0-9_-]{20,1200}) -->\s*$")
REQUEST_COMMENTS = ('<!-- pit-project-edit:v1 -->', '<!-- pit-project-submission-feedback:v1 -->')


def request_marker(request):
    encoded = base64.urlsafe_b64encode(json.dumps(request, sort_keys=True).encode()).decode().rstrip('=')
    return '\n<!-- pit-edit-request:' + encoded + ' -->'


def issued_request(api, state):
    """Only the bot's final, non-rendered comment trailer issues a form link.

    Requesting /edit must not update the PR's commit or its policy status.
    The request lives in that existing reply until a replacement is submitted.
    """
    candidates = []
    for comment in api.pages(f"issues/{latest_number(state)}/comments"):
        body = comment.get('body', '')
        if not is_submission_bot(comment['user']) or not body.startswith(REQUEST_COMMENTS):
            continue
        match = REQUEST.search(body)
        if not match:
            continue
        try:
            encoded = match[1]
            item = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
            if (set(item) != {'root', 'source', 'hash', 'mode', 'token', 'issued_at'}
                    or item['root'] != state['root'] or item['source'] != latest_number(state)
                    or not re.fullmatch(r'[0-9a-f]{64}', item['hash'])
                    or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', item['token'])
                    or item['mode'] not in {'edit', 'correction'} or type(item['issued_at']) is not int):
                continue
            candidates.append(item)
        except (ValueError, TypeError, KeyError):
            continue
    return max(candidates, key=lambda item: item['issued_at']) if candidates else state.get('request')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def content_hash(issue):
    data = [issue.get('title'), issue.get('body'), issue['user'].get('id'), issue['user'].get('login'), issue.get('created_at')]
    return digest(json.dumps(data, ensure_ascii=False))


def same_author(left, right):
    return (type(left['user'].get('id')) is int and left['user']['id'] == right['user'].get('id')
            and left['user']['login'].lower() == right['user']['login'].lower())


def read_state(api, root_number):
    state = workflow_state.read(api, workflow_state.root_path(root_number))
    try:
        history = state['history']
        if (state['root'] != root_number or type(state['author_id']) is not int or not isinstance(history, list)
                or not 1 <= len(history) <= MAX_REVISIONS or history[0]['number'] != root_number
                or len({e['number'] for e in history}) != len(history)):
            raise ValueError()
        for entry in history:
            number(entry['number'])
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid('Authenticated submission history is missing or malformed; retry the original submission') from exc
    state['request'] = issued_request(api, state)
    return state


def ensure_state(api, root):
    if workflow_state.read(api, workflow_state.root_path(root['number'])) is None:
        workflow_state.write(api, {
            workflow_state.root_path(root['number']): {'root': root['number'], 'author_id': root['user']['id'],
                'history': [{'number': root['number']}], 'request': None},
            workflow_state.issue_path(root['number']): {'root': root['number'], 'author_id': root['user']['id']}}, root=root['number'])
    return read_state(api, root['number'])


def reference_comment(root, previous, token):
    data = json.dumps({'root': root, 'previous': previous, 'token': token}, sort_keys=True, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(data).decode().rstrip('=')
    return '<!-- pit-correction:' + encoded + ' -->'


def strip_reference(text):
    return REFERENCE.sub('', text).strip()


def references(issue, ctx):
    values = answers(issue.get('body'), ctx, correction=True, require_fields=False)
    match = REFERENCE.search(values.get('correction_notes', ''))
    try:
        if match is None:
            raise ValueError()
        encoded = match[1]
        data = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
        if set(data) != {'root', 'previous', 'token'} or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', data['token']):
            raise ValueError()
        return number(data['root']), number(data['previous']), data['token']
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid('Use the supplied correction link and leave the Changes needed box unchanged') from exc


def routed_root(api, issue):
    match = ROUTE.search(issue.get('body') or '')
    root_number = number(int(match[1])) if match else issue['number']
    if match is None and correction_issue(issue):
        # An interrupted activation may have saved a receipt before replacing
        # the raw form body. This hint grants nothing; a receipt must confirm it.
        hints = re.findall(r'<!-- pit-correction:([A-Za-z0-9_-]{20,600}) -->', issue.get('body') or '')
        if len(hints) == 1:
            try:
                encoded = hints[0]
                root_number = number(json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                    altchars=b'-_', validate=True))['root'])
            except (ValueError, TypeError, KeyError):
                pass
    packet = workflow_state.read(api, workflow_state.issue_path(issue['number']), root=root_number)
    if packet is None:
        # Before the first new-style formatting, an existing active submission
        # may still have its receipt in the read-only shared branch.
        packet = workflow_state.legacy_read(api, workflow_state.issue_path(issue['number']))
    if packet is None:
        return None
    if packet.get('author_id') != issue['user'].get('id'):
        raise Invalid('Submission receipt does not match its GitHub author')
    return number(packet['root'])


def answer_packet(api, issue):
    root = routed_root(api, issue)
    return workflow_state.read(api, workflow_state.issue_path(issue['number']), root=root) if root else None


def input_values(api, issue, ctx, previous_values=None):
    packet = answer_packet(api, issue)
    if (issue.get('body') or '').startswith(FORMATTED_MARKER):
        if (packet is None or packet.get('author_id') != issue['user'].get('id')
                or packet.get('body_sha256') != digest(issue['body']) or not isinstance(packet.get('values'), dict)):
            raise Invalid('The formatted Issue was edited. Use the replacement form to change its answers.', fields={'summary'},
                          corrections={'summary': ['Use the replacement form to change the submitted answers.']})
        values = deepcopy(packet['values'])
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
            raise Invalid('Submission receipt contains unsupported answer types')
        values['name'] = issue.get('title') or ''
        return values
    return resolved_values(issue, ctx, previous_values, require_fields=False)


def saved_values(api, issue):
    packet = answer_packet(api, issue)
    if packet is None or packet.get('author_id') != issue['user'].get('id') or not isinstance(packet.get('values'), dict):
        raise Invalid('The previous answers could not be recovered')
    values = deepcopy(packet['values'])
    values['name'] = issue.get('title') or ''
    return values


def format_issue(api, issue, root, ctx, previous_values=None):
    values = input_values(api, issue, ctx, previous_values)
    values.pop('correction_notes', None)
    body = FORMATTED_MARKER + '\n' + summary_body(values, ctx)
    if len(body) > 64000:
        body = body[:62000] + '\n\n*Display shortened to fit GitHub. Complete answers are retained by the workflow.*\n'
    body += f"\n<!-- pit-project-root:{root['number']} -->"
    packet = {'root': root['number'], 'author_id': issue['user']['id'], 'values': values, 'body_sha256': digest(body)}
    workflow_state.write(api, {workflow_state.issue_path(issue['number']): packet}, root=root['number'])
    current = api.get(f"issues/{issue['number']}")
    if issue_version(current) != issue_version(issue):
        raise Invalid('The Issue changed before formatting; retry')
    if current['body'] != body:
        api.request('PATCH', f"issues/{issue['number']}", {'body': body})
    current = api.get(f"issues/{issue['number']}")
    if current['body'] != body or current['title'] != issue['title'] or not same_author(current, issue):
        raise Invalid('The Issue changed during formatting; retry')
    return current, values


def active_submission(api, root_number, ctx):
    root = api.get(f'issues/{number(root_number)}')
    if root.get('pull_request') or correction_issue(root):
        raise Invalid('Original submission must be a New Project Issue')
    state = read_state(api, root_number)
    if state['author_id'] != root['user'].get('id'):
        raise Invalid('Original submitter identity changed')
    previous_values = None
    active = root
    for i, entry in enumerate(state['history']):
        current = root if i == 0 else api.get(f"issues/{entry['number']}")
        if current.get('pull_request') or not same_author(root, current):
            raise Invalid('Every replacement must be authored by the original submitter')
        if entry.get('frozen_hash') and content_hash(current) != entry['frozen_hash']:
            raise Invalid('A superseded submission was edited; ask SC to restore it and retry')
        if i < len(state['history']) - 1:
            # A manually edited formatted Issue may itself be the invalid
            # source being replaced. Its authenticated saved answers remain
            # the source for retained long values; edited display text is not.
            previous_values = saved_values(api, current)
        active = current
    return root, state, active, previous_values


def latest_number(state):
    return state['history'][-1]['number']


def correction_notes(ctx, error):
    if error is None:
        return ''
    labels = {'name': 'Project title', **current_labels(ctx)}
    lines = []
    for key in labels:
        if key not in error.fields:
            continue
        messages = error.corrections.get(key, [])
        if not messages:
            messages = ['Complete or correct this answer.']
        text = ' '.join(dict.fromkeys(messages))
        label = labels[key]
        if text.startswith(label + ': '):
            text = text[len(label) + 2:]
        lines.append(label + ': ' + text)
    if not lines:
        lines = str(error).splitlines()
    return '\n'.join(lines)


def correction_link(api, root, state, issue, values, ctx, *, error=None):
    request = state.get('request')
    mode = 'correction' if error is not None else 'edit'
    version = content_hash(issue)
    if not request or request['source'] != issue['number'] or request['hash'] != version or request['mode'] != mode:
        request = {'root': root['number'], 'source': issue['number'], 'hash': version,
                   'mode': mode, 'token': secrets.token_urlsafe(24), 'issued_at': time.time_ns()}
    elif 'issued_at' not in request:
        request = {**request, 'root': root['number'], 'issued_at': time.time_ns()}
    params = {'template': 'correct-project.yml', 'title': values.get('name', issue.get('title', '')),
              'correction_notes': correction_notes(ctx, error)}
    for item in ctx.form_source['body']:
        key = item.get('id')
        if key and item.get('type') in {'input', 'textarea'}:
            params[key] = list_text(values.get(key, '')) if key == 'related_links' else optional(values.get(key, ''))
    suffix = '\n\n' + reference_comment(root['number'], issue['number'], request['token'])
    params['correction_notes'] = params['correction_notes'] + suffix
    prefix = f'https://github.com/{api.repo}/issues/new?'
    url = prefix + urlencode(params)
    omitted = []
    candidates = sorted((k for k in current_labels(ctx) if k in params), key=lambda k: len(params[k]), reverse=True)
    for key in candidates:
        if len(url) <= MAX_URL_LENGTH:
            break
        if len(params[key]) <= len(KEEP_VALUE):
            continue
        params[key] = KEEP_VALUE
        omitted.append(current_labels(ctx)[key])
        url = prefix + urlencode(params)
    if len(url) > MAX_URL_LENGTH:
        raise Invalid('The correction link is too large; ask SC to inspect this submission')
    return url, omitted, request


def receive_revision(api, issue, root, state, ctx):
    if latest_number(state) == issue["number"] and same_author(root, issue):
        # The transition has already been authenticated, even if an author
        # subsequently edits the reference fields in the Issue description.
        return state
    root_number, previous, token = references(issue, ctx)
    if root_number != root["number"] or not same_author(root, issue):
        raise Invalid("Only the original submitter may replace this proposal")
    request = state.get("request")
    if (not request or previous != latest_number(state) or request["source"] != previous
            or not secrets.compare_digest(token, request["token"])):
        raise Invalid("This correction link is stale. Use the latest submission Issue")
    source = api.get(f"issues/{previous}")
    if source.get("state") != "open" or content_hash(source) != request["hash"]:
        raise Invalid("The previous submission changed or was closed. Request a fresh link")
    if issue.get("state") != "open" or len(state["history"]) >= MAX_REVISIONS:
        raise Invalid("This replacement cannot be activated; ask SC to inspect it")
    if issue["number"] <= previous:
        raise Invalid("A replacement must be a newer Issue")
    state = deepcopy(state)
    state["history"][-1]["frozen_hash"] = content_hash(source)
    state["history"].append({"number": issue["number"], "token": token})
    state["request"] = None
    workflow_state.write(api, {workflow_state.root_path(root['number']): state,
                               workflow_state.issue_path(issue['number']): {'root': root['number'], 'author_id': issue['user']['id']}}, root=root['number'])
    return state


def set_label(api, issue, label, *, present=True):
    labels = {entry["name"] for entry in issue.get("labels", [])}
    if present and label not in labels:
        try:
            api.get(f"labels/{label}")
        except OSError as exc:
            if getattr(exc, "code", None) != 404:
                raise
            api.request("POST", "labels", {"name": label, "color": "d73a4a" if label == "invalid" else "cfd3d7"})
        api.request("POST", f"issues/{issue['number']}/labels", {"labels": [label]})
    elif not present and label in labels:
        api.request("DELETE", f"issues/{issue['number']}/labels/{label}", None)
