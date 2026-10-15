"""Constrained CPP claims: accepted records stay authoritative until SC merges."""
from copy import deepcopy
import hashlib
import json
import re

from project_workflow.project_registry import Invalid, parse_yaml, validate_record, utc_timestamp


CLAIM_FIELDS = {'leads', 'contributors', 'slack_channel', 'github_url', 'related_links', 'claim'}


def eligible(record):
    # Retain claim provenance even if SC later removes a lead: no implicit re-release.
    return (record.get('kind') == 'CPP' and record.get('status') == 'active'
            and not record.get('leads') and 'claim' not in record)


def project_options(ctx):
    return [identifier + ' — ' + ' '.join(record['name'].split())
            for identifier, record in sorted(ctx.projects.items()) if eligible(record)]


def selected_project(value):
    from project_workflow.issue_form import option_value
    identifier = option_value(value)
    if not re.fullmatch(r'P[0-9]{3,}', identifier):
        raise Invalid('Select exactly one available CPP project from the dropdown')
    return identifier


def fingerprint(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_claim_change(original, candidate):
    """Never grant general member-write permission for existing project records."""
    if not eligible(original):
        raise Invalid('This CPP is assigned, previously claimed, inactive, or no longer available')
    if not candidate.get('leads') or 'claim' not in candidate:
        raise Invalid('A CPP claim requires a lead and verified claim provenance')
    if candidate['claim']['base_sha256'] != fingerprint(original):
        raise Invalid('The accepted CPP changed during review; cancel this claim and submit it again')
    if any(original.get(k) != candidate.get(k) or (k in original) != (k in candidate)
           for k in (set(original) | set(candidate)) - CLAIM_FIELDS):
        raise Invalid('A claim cannot change the project ID, kind, status, scientific definition or declaration history')


def claim_proposal(issue, ctx, *, organization_member, is_sc, verify_summary=True):
    from project_workflow.submission import (resolved_values, optional, optional_fields, number,
        submission_body, issue_title, record_path, FORMATTED_MARKER)
    if not organization_member:
        raise Invalid('The claimant must be an active organization member')
    values = resolved_values(issue,ctx)
    identifier = selected_project(values['claim_project'])
    original = ctx.projects.get(identifier)
    if original is None:
        raise Invalid('This CPP is not in the accepted registry or has been removed')
    if not eligible(original):
        raise Invalid('This CPP is assigned, previously claimed, inactive, or no longer available')
    name, username = optional(values['lead']), optional(values['lead_github_username'])
    if not name or not username:
        raise Invalid('Project lead name and GitHub username are required')
    if not is_sc and username.lower() != issue['user']['login'].lower():
        raise Invalid('A member must claim a CPP with themselves as its lead')
    cleaned, _ = optional_fields(values,ctx,codes=[])
    record = deepcopy(original)
    record['leads'] = [{'name':name, 'github_username':username}]
    people = [p for p in original['contributors'] + cleaned['contributors']
              if p['github_username'].lower() != username.lower()]
    seen = set()
    record['contributors'] = []
    for person in people:
        key = person['github_username'].lower()
        if key not in seen:
            record['contributors'].append(person)
            seen.add(key)
    for field in ('slack_channel','github_url'):
        if cleaned[field] is not None:
            record[field] = cleaned[field]
    links = list(dict.fromkeys(original.get('related_links',[]) + cleaned['related_links']))
    limit = ctx.schema['properties']['related_links'].get('maxItems',len(links))
    record['related_links'] = links[:limit]
    record['claim'] = {'issue_number':number(issue['number']), 'submitted_by':issue['user']['login'],
        'submitted_at':utc_timestamp(issue.get('created_at')),
        'issue_title_sha256':hashlib.sha256(('Claim CPP: ' + record['name'])[:256].encode('utf-8')).hexdigest(),
        'issue_body_sha256':hashlib.sha256(submission_body(issue).encode('utf-8')).hexdigest(),
        'base_sha256':fingerprint(original)}
    validate_record(record,record_path(record),ctx)
    validate_claim_change(original,record)
    if verify_summary and issue['body'].startswith(FORMATTED_MARKER):
        from project_workflow.review import formatted_issue_body
        if issue['body'] != formatted_issue_body(record,ctx,submission_body(issue)) or issue['title'] != issue_title(record):
            raise Invalid('The formatted CPP claim was changed outside the command workflow')
    return record


def check_reservation(api, issue, record):
    """Live main and one open bot proposal per target; called again before writes."""
    from project_workflow.submission import (is_submission_bot, branch_issue, record_path,
                                             workflow_kind)
    from project_workflow.issue_intake import branch_snapshot
    path = record_path(record)
    main = api.get('git/ref/heads/main')['object']['sha']
    snapshot = api.tree(main)
    if path not in snapshot:
        raise Invalid('The selected CPP has been removed from the accepted registry')
    original = parse_yaml(api.reader(snapshot)(path))
    validate_claim_change(original,record)
    for pr in api.pages('pulls?state=open&base=main'):
        if pr.get('state') != 'open' or pr.get('merged_at') or not is_submission_bot(pr['user']):
            continue
        if pr['head'].get('repo',{}).get('full_name') != api.repo:
            continue
        branch = pr['head']['ref']
        if not branch.startswith('project-submissions/issue-'):
            continue
        other_number = branch_issue(branch)
        if other_number == issue['number']:
            continue
        other_issue = api.get(f'issues/{other_number}')
        if other_issue.get('state') != 'open':
            continue
        sha, _, candidate = branch_snapshot(api,other_issue)
        if sha != pr['head']['sha']:
            raise Invalid('Another proposal is changing; retry this claim')
        if candidate and workflow_kind(candidate) == 'CLAIM' and candidate['project_id'] == record['project_id']:
            # Even a relabelled proposal reserves its target until closed.
            raise Invalid(f'This CPP already has an open claim in Issue #{other_number}; wait for its review or cancellation')


def refresh_checks(api):
    """A main push can invalidate a pending claim without changing its head SHA."""
    from project_workflow.submission import is_submission_bot, branch_issue
    from project_workflow.issue_intake import branch_snapshot
    from project_workflow.dispatch import validate_pr
    for pr in api.pages('pulls?state=open&base=main'):
        if (pr.get('state') != 'open' or pr.get('merged_at') or not is_submission_bot(pr['user'])
                or pr['head'].get('repo',{}).get('full_name') != api.repo
                or not pr['head']['ref'].startswith('project-submissions/issue-')):
            continue
        issue = api.get(f"issues/{branch_issue(pr['head']['ref'])}")
        _, _, record = branch_snapshot(api,issue)
        if record and 'claim' in record:
            api.status(pr['head']['sha'],'pending','Accepted registry changed; rechecking CPP claim')
            validate_pr(api,pr['number'])
