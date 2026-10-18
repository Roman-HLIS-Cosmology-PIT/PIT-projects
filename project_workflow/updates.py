"""Authorize and atomically apply optional accepted-project updates, without PRs."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error

import yaml

from project_workflow.accepted import timestamp, write_main
from project_workflow.project_registry import Invalid, USERNAME, load_context, validate_record
from project_workflow.submission import https_url, number, optional, project_yaml, record_path

UPDATE_LABEL = 'project-update'
UPDATE_FORM = '.github/ISSUE_TEMPLATE/update-project.yml'
STATUS_OPTIONS = {'No change': None, 'Active': 'active', 'Internal review': 'IR',
                  'Completed': 'completed', 'Paused': 'paused'}
FIELDS = {'project_number', 'add_lead_name', 'add_lead_github_username', 'remove_lead',
          'add_contributor_name', 'add_contributor_github_username', 'remove_contributor',
          'status', 'related_links', 'github_url', 'additional_information'}


class Refused(Invalid):
    """Invalid or unauthorized input, distinct from a retryable transaction failure."""


def form(ctx):
    spec = deepcopy(ctx.form_source['update'])
    controls = [f for f in spec['body'] if f['type'] != 'markdown']
    if (spec['labels'] != [UPDATE_LABEL] or len(controls) != len(FIELDS)
            or {f['id'] for f in controls} != FIELDS):
        raise Invalid('Update form controls must match the automatic handler')
    for f in controls:
        if f['id'] == 'status' and (f['type'] != 'dropdown' or f['attributes']['options'] != list(STATUS_OPTIONS)
                                  or f['attributes'].get('multiple', False)):
            raise Invalid('Project status must be a single-selection dropdown')
        if bool(f.get('validations', {}).get('required')) != (f['id'] == 'project_number'):
            raise Invalid('Only the project number is required in an update')
    labels = [f['attributes']['label'] for f in controls]
    if len(set(labels)) != len(labels):
        raise Invalid('Update form labels must be unique')
    return spec


def generated_text(ctx):
    return '# Generated from project_workflow/config.yaml; do not edit this output.\n' + yaml.safe_dump(
        form(ctx), sort_keys=False, allow_unicode=True, width=110)


def answers(body, ctx):
    if not isinstance(body, str) or len(body.encode('utf-8')) > 65536:
        raise Invalid('Update form is missing or exceeds 64 KiB')
    controls = {f['attributes']['label']: f['id'] for f in form(ctx)['body'] if f['type'] != 'markdown'}
    found, current, fence = {}, None, None
    for line in body.replace('\r\n', '\n').splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not marker[2].strip():
                fence = None
        heading = re.fullmatch(r'### (.+?)\s*', line) if fence is None and not marker else None
        if heading and heading[1] in controls:
            current = controls[heading[1]]
            if current in found:
                raise Invalid('Repeated update field heading')
            found[current] = []
        elif current is not None:
            found[current].append(line)
        elif line.strip():
            raise Invalid('Keep the native update form headings')
    values = {key: optional('\n'.join(found.get(key, [])).strip()) for key in FIELDS}
    if not values['project_number']:
        raise Invalid('A project number is required')
    return values


def require_claimed(record):
    if record['kind'] == 'CPP' and not record['leads']:
        raise Invalid('Updates to an unclaimed CPP are refused for everyone, including SC. Claim the project first.')


def authorized(record, user, ctx, teams):
    require_claimed(record)
    if (user.get('type') != 'User' or type(user.get('id')) is not int or user['id'] < 1
            or not isinstance(user.get('login'), str) or not USERNAME.fullmatch(user['login'])):
        raise Invalid('Updates require an authenticated GitHub user')
    if any(p['github_username'].lower() == user['login'].lower() for p in record['leads']):
        return
    if not teams.is_active_member(*ctx.sc_team, user['login']):
        raise Invalid('Only a current lead of this project or an active SC member can update it')


def apply(record, values, ctx):
    """Validate every operation before any write; a failed field rejects all changes."""
    require_claimed(record)
    candidate = deepcopy(record)
    for role, field in (('lead', 'leads'), ('contributor', 'contributors')):
        name = values.get(f'add_{role}_name', '')
        username = values.get(f'add_{role}_github_username', '')
        removal = values.get(f'remove_{role}', '')
        if bool(name) != bool(username):
            raise Invalid(f'Adding a {role} requires both a name and GitHub username')
        if username:
            if len(username) > 39 or not USERNAME.fullmatch(username):
                raise Invalid(f'Add only one {role}, using a GitHub username without @')
            existing = next((p for p in candidate[field] if p['github_username'].lower() == username.lower()), None)
            person = {'name': name, 'github_username': username}
            if existing and existing != person:
                raise Invalid(f'This {role} already exists; adding does not rename people')
            if not existing:
                candidate[field].append(person)
        if removal:
            if len(removal) > 39 or not USERNAME.fullmatch(removal):
                raise Invalid(f'Remove only one {role}, using a GitHub username without @')
            matches = [p for p in candidate[field] if p['github_username'].lower() == removal.lower()]
            if not matches:
                raise Invalid(f'The {role} to remove is not listed on this project')
            candidate[field] = [p for p in candidate[field] if p['github_username'].lower() != removal.lower()]
            if field == 'leads' and not candidate[field]:
                raise Invalid('Cannot remove the last project lead')
    status = values.get('status', '') or 'No change'
    if status not in STATUS_OPTIONS:
        raise Invalid('Choose a status from the dropdown')
    if STATUS_OPTIONS[status] is not None:
        candidate['status'] = STATUS_OPTIONS[status]
    for field in ('github_url', 'additional_information'):
        if values.get(field):
            candidate[field] = values[field]
    links = values.get('related_links', '')
    if links:
        new_links = [s.strip() for s in links.splitlines() if s.strip()]
        for url in new_links:
            https_url(url)
        candidate['related_links'] = list(dict.fromkeys(candidate.get('related_links', []) + new_links))
    validate_record(candidate, record_path(candidate), ctx)
    return candidate


def version(issue):
    return (issue['user'], issue.get('title'), issue.get('body'), issue.get('state'), issue.get('labels'))


def process(api, teams, issue_number, *, checkout_sha=None, writer=None):
    issue_number = number(issue_number)
    issue = api.get(f'issues/{issue_number}')
    if issue.get('pull_request') or UPDATE_LABEL not in {x['name'] for x in issue.get('labels', [])}:
        return 'skipped'
    parent = api.get('git/ref/heads/main')['object']['sha']
    if checkout_sha is not None and parent != checkout_sha:
        raise Invalid('Updates must execute current trusted main code; retry the update')
    tree = api.tree(parent)
    ctx = load_context(api.reader(tree), paths=tree)
    if ctx.sc_team[0].lower() != api.repo.split('/')[0].lower():
        raise Invalid('SC team must belong to this repository organization')
    digest = hashlib.sha256((issue.get('body') or '').encode('utf-8')).hexdigest()
    for project in ctx.projects.values():
        prior = next((u for u in project.get('updates', []) if u['issue_number'] == issue_number), None)
        if prior:
            if prior['body_sha256'] != digest or prior['author_id'] != issue['user'].get('id'):
                raise Invalid('This update was already applied; edited requests cannot be replayed')
            finish(api, issue_number, 'Applied')
            return 'already-applied'
    if issue.get('state') != 'open':
        return 'closed'
    try:
        values = answers(issue.get('body'), ctx)
        matches = [p for p in ctx.projects.values() if p.get('project_number') == values['project_number']]
        if len(matches) != 1:
            raise Invalid('Select an accepted project using its permanent project number')
        original = matches[0]
        authorized(original, issue['user'], ctx, teams)
        candidate = apply(original, values, ctx)
    except Invalid as exc:
        raise Refused(str(exc)) from exc
    if candidate == original:
        finish(api, issue_number, 'No changes')
        return 'unchanged'
    candidate.setdefault('updates', []).append({'issue_number': issue_number, 'author_id': issue['user']['id'],
        'submitted_by': issue['user']['login'], 'applied_at': timestamp(), 'body_sha256': digest})

    def fresh():
        if version(api.get(f'issues/{issue_number}')) != version(issue):
            raise Invalid('Update request changed during processing; retry')
        authorized(original, issue['user'], ctx, teams)

    fresh()
    write_main(writer or api, parent, {record_path(candidate): project_yaml(candidate)},
               f'Update {original["project_number"]} from issue {issue_number}', still_current=fresh)
    finish(api, issue_number, 'Applied')
    return 'applied'


def finish(api, identifier, outcome):
    # No PR, reviewer, assignee, @mention or extra comment notification.
    issue = api.get(f'issues/{identifier}')
    title = re.sub(r'^\[(?:Applied|No changes|Refused)\] ', '', issue.get('title') or 'Update project')
    api.request('PATCH', f'issues/{identifier}', {'state': 'closed', 'state_reason': 'completed',
                                               'title': f'[{outcome}] {title}'[:256]})


def ensure_label(api):
    try:
        api.get('labels/' + UPDATE_LABEL)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        try:
            api.request('POST', 'labels', {'name': UPDATE_LABEL, 'color': '1d76db',
                                         'description': 'Automatically validated accepted-project update'})
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                raise
            api.get('labels/' + UPDATE_LABEL)  # A concurrent creation must be confirmed.


def main():
    from project_workflow.validate_pr import GitHub, TeamDirectory
    api, identifier = None, None
    try:
        api = GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_TOKEN'])
        if sys.argv[1:] == ['--ensure-label']:
            ensure_label(api)
            print('Project update label is available.')
            return 0
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        identifier = int(os.environ.get('UPDATE_ISSUE_NUMBER') or event['issue']['number'])
        writer = GitHub(api.repo, os.environ['PROJECT_DATA_TOKEN']) if os.environ.get('PROJECT_DATA_TOKEN') else None
        checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        result = process(api, TeamDirectory(os.environ.get('SC_TEAM_READ_TOKEN', '')), identifier, checkout_sha=checkout, writer=writer)
        if result in {'applied', 'already-applied'}:
            api.request('POST', 'actions/workflows/maintenance.yml/dispatches', {'ref': 'main'})
        print('Project update: ' + result)
        return 0
    except Refused as exc:
        from project_workflow.review import safe_text
        # Refusal is visible on the request, without pinging reviewers or leads.
        if api is not None and identifier is not None:
            try:
                issue = api.get(f'issues/{identifier}')
                if issue.get('state') == 'open':
                    api.request('PATCH', f'issues/{identifier}', {'state': 'closed', 'state_reason': 'not_planned',
                        'title': ('[Refused] ' + issue.get('title', 'Update project'))[:256],
                        'body': (issue.get('body') or '') + '\n\n---\n**Update refused:** ' + safe_text(str(exc))})
            except (OSError, Invalid):
                print('Could not record the refusal on the request.', file=sys.stderr)
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f'Project update failed (GitHub HTTP {exc.code}); retry the update.', file=sys.stderr)
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        # Do not mutate request content: its exact hash is the replay guard.
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
