"""Trusted-main GitHub/Slack bridge; never execute a proposed branch's code."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from project_workflow.claims import fingerprint
from project_workflow.issue_intake import (
    ACCEPTED_MARKER, associated_pr, branch_snapshot, feedback, process, reject_submission,
)
from project_workflow.project_registry import Invalid, load_context
from project_workflow.submission import (
    branch_issue, is_submission_bot, issue_version, provenance, validate_bot_snapshot, workflow_kind,
)
from project_workflow.validate_pr import GitHub, NoRedirect, TeamDirectory, records

ROOT = Path(__file__).resolve().parent.parent


class Slack:
    """CLI service token stays in GitHub; explicit app/workspace on every call."""
    def __init__(self):
        self.app = os.environ['SLACK_APP_ID']
        self.team = os.environ['SLACK_TEAM_ID']
        self.token = os.environ['SLACK_SERVICE_TOKEN']
        self.webhook = os.environ['SLACK_SYNC_WEBHOOK']
        if not re.fullmatch(r'A[A-Z0-9]+', self.app) or not re.fullmatch(r'T[A-Z0-9]+', self.team):
            raise Invalid('Set explicit Slack app and workspace IDs')
        if not re.fullmatch(r'https://hooks\.slack\.com/triggers/[^\s]+', self.webhook):
            raise Invalid('Expected an official Slack webhook-trigger URL')

    def command(self, operation, store, data, *, json_output=True):
        args = ['slack', 'datastore', operation, json.dumps({'datastore': store, **data}),
                '--app', self.app, '--team', self.team, '--token', self.token,
                '--skip-update', '--no-color']
        if json_output:
            args += ['--output', 'json']
        # Capture both streams: CLI errors can contain credentials or payloads.
        try:
            result = subprocess.run(args, cwd=ROOT / 'slack', capture_output=True, text=True,
                                    timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise Invalid(f'Slack datastore {operation} could not finish') from None
        if result.returncode:
            raise Invalid(f'Slack datastore {operation} failed; inspect app access/token locally')
        if not json_output:
            return None
        if result.stdout.strip() == 'Not found':
            return None
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise Invalid('Unexpected Slack CLI JSON output; use the documented CLI version') from None

    def get(self, store, identifier):
        return self.command('get', store, {'id': identifier})

    def put(self, store, item):
        # CLI 4.10.0 put has no --output flag; success is its exit status.
        self.command('put', store, {'item': item}, json_output=False)

    def query(self, store, poll=None):
        output, cursor, seen = [], '', set()
        for _ in range(1000):
            expression = {'cursor': cursor} if cursor else {}
            if poll:
                expression.update(expression='#p = :p', expression_attributes={'#p': 'poll'},
                                  expression_values={':p': poll})
            result = self.command('query', store, expression)
            if not isinstance(result, dict) or not isinstance(result.get('items'), list):
                raise Invalid('Incomplete Slack datastore query')
            output.extend(result['items'])
            cursor = result.get('next_cursor') or result.get('response_metadata', {}).get('next_cursor', '')
            if not cursor:
                return output
            if cursor in seen:
                raise Invalid('Slack pagination repeated a cursor')
            seen.add(cursor)
        raise Invalid('Too many Slack records to inspect completely')

    def save(self, value):
        self.put('pit_projects', {'id': value['id'], 'payload': json.dumps(value, ensure_ascii=False)})

    def refresh(self, value):
        # Wait for an explicit delivery acknowledgement. Webhook HTTP 200 only
        # confirms invocation, not that Slack posted/updated the message.
        delivery = uuid.uuid4().hex
        value['delivery'] = delivery
        self.save(value)
        request = urllib.request.Request(self.webhook,
            data=json.dumps({'poll': value['id'], 'delivery': delivery}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                if response.status != 200:
                    raise Invalid('Slack did not accept the synchronization request')
        except (OSError, ValueError):
            # Do not print the exception: its URL is a credential.
            raise Invalid('Slack webhook invocation failed; check the app activity log') from None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            receipt = self.get('pit_messages', f'ack:{delivery}')
            if receipt and receipt.get('ts') == 'done':
                return
            time.sleep(3)
        raise Invalid('Slack synchronization not acknowledged; inspect slack activity before retrying')


def member_count(teams, team):
    teams.check_team(*team)
    names = set()
    for page in range(1, 101):
        members = teams.get(f'{team[0]}/teams/{team[1]}/members?role=all&per_page=100&page={page}')
        if not isinstance(members, list):
            raise Invalid('SC membership list is unavailable')
        for member in members:
            login = member.get('login')
            if not isinstance(login, str) or not login:
                raise Invalid('Invalid member in SC roster')
            names.add(login.lower())
        if len(members) < 100:
            if not names:
                raise Invalid('SC team has no active members; voting cannot be configured')
            return len(names)
    raise Invalid('SC membership pagination exceeded its limit')


def tally(value, events):
    """Keep in parity with logic.ts; sort events before taking latest per voter."""
    votes, simulation, force = {}, None, None
    relevant = [e for e in events if e['poll'] == value['id'] and e['revision'] == value['revision']
                and ('cutoff' not in value or float(e['stamp']) <= float(value['cutoff']))]
    for event in sorted(relevant, key=lambda e: (float(e['stamp']), e['id'])):
        kind, _, choice = event['action'].partition(':')
        if choice not in {'accept', 'refuse', 'abstain'}:
            continue
        if kind == 'force' and choice != 'abstain':
            force = choice
        elif kind == 'test' and value.get('test_buttons'):
            simulation = choice
        elif kind == 'vote':
            simulation = None
            votes[event['actor']] = choice
    counts = dict.fromkeys(('accept', 'refuse', 'abstain'), 0)
    if simulation:
        counts[simulation] = value['quorum']
    else:
        for choice in votes.values():
            counts[choice] += 1
    total = sum(counts.values())
    if total > value['members'] and not force:
        # No identity mapping was requested. Still fail closed if the channel
        # contains more voters than the authoritative SC roster permits.
        raise Invalid('More Slack voters than SC members; inspect private channel membership')
    decision = force or ('waiting' if total < value['quorum'] or counts['accept'] == counts['refuse']
                         else 'accept' if counts['accept'] > counts['refuse'] else 'refuse')
    return {**counts, 'participation': total, 'simulation': simulation, 'force': force, 'decision': decision}


def eligible_pr(api, pr):
    return (is_submission_bot(pr['user']) and
            pr['head'].get('repo', {}).get('full_name') == api.repo and
            pr['base'].get('repo', {}).get('full_name') == api.repo and
            pr['base']['ref'] == 'main' and
            re.fullmatch(r'project-submissions/issue-[1-9][0-9]*', pr['head']['ref']))


def validate(api, teams, pr):
    """Validate live main + exact proposed data, including authenticated edits."""
    if not eligible_pr(api, pr) or pr['state'] != 'open':
        raise Invalid('Not an open, verified issue-backed project PR')
    issue = api.get(f"issues/{branch_issue(pr['head']['ref'])}")
    if issue['state'] != 'open':
        raise Invalid('Submission Issue is closed')
    linked = associated_pr(api, issue['number'])
    if not linked or linked['number'] != pr['number']:
        raise Invalid('PR is not the unique associated proposal')
    sha, commit, stored = branch_snapshot(api, issue)
    if sha != pr['head']['sha']:
        raise Invalid('Submission ref differs from PR head')
    main = api.get('git/ref/heads/main')['object']['sha']
    if commit['parents'][0]['sha'] != main or pr['base']['sha'] != main:
        raise Invalid('Proposal needs rebasing onto current main; run the submission workflow then retry')
    base, head = api.tree(main), api.tree(sha)
    read, proposed = api.reader(base), api.reader(head)
    ctx = load_context(read, paths=base)
    if ctx.sc_team[0].lower() != api.repo.split('/')[0].lower():
        raise Invalid('SC team must belong to the repository organization')
    actor = issue['user']['login']
    record = validate_bot_snapshot(base, head, read, proposed, pr, issue, ctx,
        organization_member=teams.is_org_member(ctx.sc_team[0], actor),
        is_sc=teams.is_active_member(*ctx.sc_team, actor), api=api, teams=teams)
    records(head, proposed, ctx)
    latest = api.get(f"pulls/{pr['number']}")
    if latest['state'] != 'open' or latest['head']['sha'] != sha or latest['base']['sha'] != main:
        raise Invalid('PR changed during validation')
    if issue_version(api.get(f"issues/{issue['number']}")) != issue_version(issue):
        raise Invalid('Submission Issue changed during validation')
    return record, ctx, issue


def project_value(api, pr, record, ctx, members, channel):
    person = lambda p: f"{p['name']} (@{p['github_username']})"
    # Exclude provenance receipts: a repeated no-op edit must not reset votes.
    content = {k: v for k, v in record.items() if k not in {'submission', 'claim'}}
    return {'id': f"pr-{pr['number']}", 'repo': api.repo, 'pr': pr['number'],
            'issue': provenance(record)['issue_number'], 'head': pr['head']['sha'],
            'revision': fingerprint(content), 'content_hash': fingerprint(content),
            'channel': channel, 'kind': workflow_kind(record),
            'members': members, 'quorum': members // 2 + 1, 'status': 'waiting',
            'title': record['name'],
            'groups': ', '.join(g['code'] + ' — ' + ctx.working_groups[g['code']]['description']
                                for g in record['working_groups']),
            'leads': ', '.join(map(person, record['leads'])) or 'None',
            'contributors': ', '.join(map(person, record['contributors'])) or 'None',
            'description': record['summary'],
            'test_buttons': os.environ.get('SLACK_TEST_BUTTONS', 'false') == 'true'}


class Bridge:
    def __init__(self, api, teams, slack, channel):
        self.api, self.teams, self.slack, self.channel = api, teams, slack, channel

    def terminal(self, value, pr):
        # Reconcile GitHub first: a timed-out merge must never be mislabeled refused.
        if pr.get('merged_at'):
            value.update(status='accepted', note='GitHub confirms the review PR was merged.')
            self.slack.refresh(value)
            if value.get('reconciled'):
                return True
            feedback(self.api, value['issue'],
                f"**Your project has been reviewed and accepted.** [View the accepted pull request](https://github.com/{self.api.repo}/pull/{pr['number']}).",
                marker=ACCEPTED_MARKER)
            issue = self.api.get(f"issues/{value['issue']}")
            if issue['state'] != 'closed':
                self.api.request('PATCH', f"issues/{value['issue']}", {'state': 'closed', 'state_reason': 'completed'})
            # GITHUB_TOKEN merges do not trigger a new push workflow.
            self.api.request('POST', 'actions/workflows/maintenance.yml/dispatches', {'ref': 'main'})
            value['reconciled'] = True
            self.slack.save(value)
            return True
        if pr['state'] == 'closed':
            value.update(status='refused', note='GitHub confirms the review PR was closed without merging.')
            self.slack.refresh(value)
            if value.get('reconciled'):
                return True
            reject_submission(self.api, pr['number'])
            value['reconciled'] = True
            self.slack.save(value)
            return True
        return False

    def one(self, number, old, *, decide):
        pr = self.api.get(f'pulls/{number}')
        if not eligible_pr(self.api, pr):
            raise Invalid('Tracked PR no longer matches its submission binding')
        if old and (old['repo'] != self.api.repo or old['channel'] != self.channel or old['pr'] != number):
            raise Invalid('Existing poll belongs to a different repository/channel')
        if old and self.terminal(old, pr):
            return
        if pr['state'] != 'open':
            return
        issue = self.api.get(f"issues/{branch_issue(pr['head']['ref'])}")
        # Reuse the existing trusted submission workflow to refresh stale bot
        # branches; it verifies provenance and never executes proposed code.
        sha, commit, _ = branch_snapshot(self.api, issue)
        main = self.api.get('git/ref/heads/main')['object']['sha']
        if sha and commit['parents'][0]['sha'] != main:
            process(self.api, self.teams, issue['number'])
            pr = self.api.get(f'pulls/{number}')
        record, ctx, issue = validate(self.api, self.teams, pr)
        if workflow_kind(record) not in {'EP', 'CLAIM'}:
            return  # CPP declarations have no Slack notification or poll.
        members = member_count(self.teams, ctx.sc_team)
        value = project_value(self.api, pr, record, ctx, members, self.channel)
        if old:
            if old['content_hash'] == value['content_hash']:
                value['revision'] = old['revision']
            else:
                # Revisiting an earlier version must NOT resurrect its old votes.
                value['revision'] = fingerprint({'content': value['content_hash'], 'previous': old['revision']})
        if old and old['revision'] != value['revision']:
            value['note'] = 'Validated project information changed. Previous votes and overrides were reset.'
        same = bool(old and old['revision'] == value['revision'])
        override_pending = False
        if same and old.get('status') == 'processing':
            override_pending = any(e['revision'] == value['revision'] and e['action'] in {'force:accept', 'force:refuse'}
                                   and float(e['stamp']) > float(old['cutoff'])
                                   for e in self.slack.query('pit_ballots', value['id']))
        # A pending decision is retried only after a fresh validation. A changed
        # proposal cancels the frozen decision and starts a new vote.
        if same and old.get('status') == 'processing' and not override_pending:
            for key in ('cutoff', 'decision', 'members', 'quorum'):
                value[key] = old[key]
            value['status'] = 'processing'
        elif not decide and not override_pending:
            self.slack.refresh(value)
            return
        else:
            value['cutoff'] = f'{time.time():.6f}'
            self.slack.save(value)
            result = tally(value, self.slack.query('pit_ballots', value['id']))
            if result['decision'] == 'waiting':
                value.pop('cutoff')
                self.slack.refresh(value)
                return
            value.update(status='processing', decision=result['decision'],
                         note='Applying the recorded decision in GitHub. Voting is closed temporarily.')
        self.slack.refresh(value)
        # Validate again immediately before the write. The exact head SHA is a
        # merge precondition, so subsequent edits cannot be merged accidentally.
        latest = self.api.get(f'pulls/{number}')
        fresh, _, _ = validate(self.api, self.teams, latest)
        if latest['head']['sha'] != value['head'] or fingerprint(
                {k: v for k, v in fresh.items() if k not in {'submission', 'claim'}}) != value['content_hash']:
            raise Invalid('Proposal changed before decision; retry to start its new vote')
        if value['decision'] == 'accept':
            self.api.status(value['head'], 'success', 'Current proposal and requester permissions passed')
            result = self.api.request('PUT', f'pulls/{number}/merge',
                                      {'sha': value['head'], 'merge_method': 'squash'})
            if not result.get('merged'):
                raise Invalid('GitHub did not confirm merge; branch protections still apply')
        else:
            self.api.request('PATCH', f'pulls/{number}', {'state': 'closed'})
        if not self.terminal(value, self.api.get(f'pulls/{number}')):
            raise Invalid('Decision write not confirmed; retained for reconciliation on the next check')

    def run(self, *, decide, number=None):
        tracked = {}
        for item in self.slack.query('pit_projects'):
            value = json.loads(item['payload'])
            # Finalized records need no work unless explicitly requested. A
            # manual per-PR check can repair a later reconciliation failure.
            if not value.get('reconciled') or number == value['pr']:
                tracked[value['pr']] = value
        candidates = {p['number'] for p in self.api.pages('pulls?state=open&base=main') if eligible_pr(self.api, p)}
        candidates.update(tracked)
        if number is not None:
            candidates = {number}
        failed = False
        for candidate in sorted(candidates):
            try:
                self.one(candidate, tracked.get(candidate), decide=decide)
                print(f'PR #{candidate}: synchronized')
            except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
                failed = True
                # Never print API error bodies, webhook URLs, names or votes.
                message = str(exc) if isinstance(exc, Invalid) else type(exc).__name__
                print(f'PR #{candidate}: deferred: {message}', file=sys.stderr)
        return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['sync', 'check'])
    parser.add_argument('--pr', default='')
    args = parser.parse_args()
    try:
        number = int(args.pr) if args.pr else None
        if number is not None and number < 1:
            raise Invalid('PR number must be positive')
        channel = os.environ['SLACK_CHANNEL_ID']
        if not re.fullmatch(r'[CG][A-Z0-9]+', channel):
            raise Invalid('Set the explicit test-pit-projects channel ID')
        api = GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_TOKEN'])
        checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if checkout != api.get('git/ref/heads/main')['object']['sha']:
            raise Invalid('Bridge must run from current trusted main; rerun the workflow')
        return Bridge(api, TeamDirectory(os.environ.get('SC_TEAM_READ_TOKEN', '')), Slack(), channel).run(
            decide=args.operation == 'check', number=number)
    except (Invalid, OSError, ValueError, KeyError) as exc:
        print(str(exc) if isinstance(exc, Invalid) else type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
