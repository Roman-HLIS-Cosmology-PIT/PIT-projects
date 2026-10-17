"""Create one project proposal PR from a verified GitHub Issue.

All repository writes use the scoped Actions token. Organization membership
reads use SC_TEAM_READ_TOKEN separately. Never execute submitted content.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from project_workflow.project_registry import Invalid, load_context
from project_workflow.submission import (
    changed_files, is_submission_bot, issue_version, labelled, provenance, workflow_kind, record_path, issue_title,
    number, project_path, project_yaml, submission_branch, submission_body, FORMATTED_MARKER, optional_fields, resolved_values,
)
from project_workflow.validate_pr import GitHub, NoRedirect, TeamDirectory, records
from project_workflow.dispatch import validate_pr as dispatch_validation
from project_workflow.review import proposal_body, request_review, safe_text, formatted_issue_body
from project_workflow.project_registry import parse_yaml, validate_record

COMMENT_MARKER = "<!-- pit-project-submission-feedback:v1 -->"
CANCEL_MARKER = "<!-- pit-project-cancelled:v1 -->"
ACCEPTED_MARKER = "<!-- pit-project-accepted:v1 -->"


def feedback(api, issue_number, message, *, marker=COMMENT_MARKER):
    body = marker + "\n" + message
    comments = [c for c in api.pages(f"issues/{issue_number}/comments")
                if is_submission_bot(c["user"]) and c.get("body", "").startswith(marker)]
    if comments:
        if comments[0]["body"] != body:
            api.request("PATCH", f"issues/comments/{comments[0]['id']}", {"body":body})
    else:
        api.request("POST", f"issues/{issue_number}/comments", {"body":body})


def associated_pr(api, issue_number):
    branch = submission_branch(issue_number)
    owner = api.repo.split("/", 1)[0]
    candidates = api.pages(f"pulls?state=all&head={owner}:{branch}&base=main")
    matches = [p for p in candidates if p["head"].get("repo", {}).get("full_name") == api.repo
               and p["head"]["ref"] == branch]
    if len(matches) > 1 or (matches and not is_submission_bot(matches[0]["user"])):
        raise Invalid("The reserved submission branch has an unexpected PR; ask SC to inspect it")
    return matches[0] if matches else None


def branch_snapshot(api, issue, *, sha=None):
    """Verify a reserved branch before updating or deleting it."""
    branch = submission_branch(issue['number'])
    if sha is None:
        try:
            sha = api.get('git/ref/heads/' + branch)['object']['sha']
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, None, None
            raise
    commit = api.get('git/commits/' + sha)
    if len(commit['parents']) != 1 or commit['message'] != f"Project submission {issue['number']}":
        raise Invalid('Submission branch has unrelated history; ask SC to inspect it')
    base_tree, head_tree = api.tree(commit['parents'][0]['sha']), api.tree(sha)
    changed = changed_files(base_tree,head_tree)
    if len(changed) != 1:
        raise Invalid('Submission branch contains unrelated changes; it will not be overwritten or deleted')
    path = next(iter(changed))
    read = api.reader(head_tree)
    ctx = load_context(api.reader(base_tree),paths=base_tree)
    record = parse_yaml(read(path))
    validate_record(record,path,ctx)
    if path != record_path(record):
        raise Invalid('Proposal path does not match its project ID')
    if workflow_kind(record) == 'CLAIM':
        if path not in base_tree:
            raise Invalid('Claim branch must update an existing accepted CPP')
        from project_workflow.claims import validate_claim_change
        validate_claim_change(ctx.projects[record['project_id']],record)
    elif path in base_tree or path != project_path(issue['number']):
        raise Invalid('A declaration branch may only add its own new project')
    receipt = provenance(record)
    if receipt.get('issue_number') != issue['number'] or receipt.get('submitted_by','').lower() != issue['user']['login'].lower():
        raise Invalid('Submission branch belongs to another requester')
    return sha, commit, record


def cancelled(api, issue_number):
    return any(is_submission_bot(c['user']) and (c.get('body','').startswith(CANCEL_MARKER)
               or (c.get('body','').startswith(COMMENT_MARKER) and c.get('body','').endswith(CANCEL_MARKER)))
               for c in api.pages(f'issues/{issue_number}/comments'))


def cancel_submission(api, issue, *, message=None):
    pr = associated_pr(api,issue['number'])
    if pr and pr.get('merged_at'):
        feedback(api,issue['number'],f"**Your project has been reviewed and accepted.** [View the accepted pull request](https://github.com/{api.repo}/pull/{pr['number']}).",marker=ACCEPTED_MARKER)
        return 'already-merged'
    sha, _, _ = branch_snapshot(api,issue)
    if sha is None and pr is None:
        return 'cancelled' if cancelled(api,issue['number']) else 'ignored-closure'
    if api.get(f"issues/{issue['number']}").get('state') != 'closed':
        return 'closure-reversed'
    if pr:
        pr = api.get(f"pulls/{pr['number']}")
        if pr.get('merged_at'):
            return 'already-merged'
        if sha and pr['head']['sha'] != sha:
            raise Invalid('Submission branch changed before cancellation; retry')
        if pr['state'] == 'open':
            api.status(pr['head']['sha'],'failure','Project submission cancelled')
            pr = api.request('PATCH',f"pulls/{pr['number']}",{'state':'closed'})
            if pr.get('merged_at'):
                return 'already-merged'
    # Record cancellation first so reopening cannot resurrect a proposal,
    # including after a partial cleanup failure.
    if not cancelled(api,issue['number']):
        feedback(api,issue['number'],message or '**Project submission cancelled.** Its unmerged PR is closed. Use the appropriate project form to start again.',marker=CANCEL_MARKER)
    if sha:
        if pr:
            latest = api.get(f"pulls/{pr['number']}")
            if latest.get('merged_at'):
                return 'already-merged'
            if latest.get('state') != 'closed':
                return 'closure-reversed'
            if latest['head'] != pr['head']:
                raise Invalid('Proposal head changed before cleanup; retry')
        branch = submission_branch(issue['number'])
        if api.get('git/ref/heads/' + branch)['object']['sha'] != sha:
            raise Invalid('Submission branch advanced before deletion; retry')
        api.request('DELETE','git/refs/heads/' + branch)
    return 'cancelled'


def reject_submission(api, pr_number):
    """Closing an unmerged review PR terminates its EP/CPP declaration or claim."""
    from project_workflow.submission import branch_issue
    pr = api.get(f'pulls/{number(pr_number)}')
    if (not is_submission_bot(pr['user'])
            or pr['head'].get('repo',{}).get('full_name') != api.repo
            or pr['base'].get('repo',{}).get('full_name') != api.repo
            or pr['base']['ref'] != 'main'
            or not pr['head']['ref'].startswith('project-submissions/issue-')):
        return 'ignored'
    if pr.get('merged_at'):
        return 'already-merged'
    if pr.get('state') != 'closed':
        return 'closure-reversed'
    issue_number = branch_issue(pr['head']['ref'])
    issue = api.get(f'issues/{issue_number}')
    associated = associated_pr(api,issue_number)
    if associated is None or associated['number'] != pr['number']:
        raise Invalid('Closed PR is not the verified proposal for this Issue')
    sha, _, stored = branch_snapshot(api,issue)
    if sha is None:
        # A manually deleted ref must not prevent closing the linked Issue.
        # The retained PR head is still checked against the same scope rules.
        _, _, stored = branch_snapshot(api,issue,sha=pr['head']['sha'])
    elif sha != pr['head']['sha']:
        raise Invalid('Proposal branch changed before rejection cleanup; retry')
    latest = api.get(f"pulls/{pr['number']}")
    if latest.get('merged_at'):
        return 'already-merged'
    if latest.get('state') != 'closed':
        return 'closure-reversed'
    if any(latest[key] != pr[key] for key in ('user','head','base')):
        raise Invalid('Closed PR changed before cleanup; retry')
    current = api.get(f'issues/{issue_number}')
    if current['user'] != issue['user']:
        raise Invalid('Submission requester changed before cleanup; retry')
    api.status(pr['head']['sha'],'failure','Review PR closed without acceptance')
    if current.get('state') != 'closed':
        current = api.request('PATCH',f'issues/{issue_number}',{'state':'closed','state_reason':'not_planned'})
        if current.get('state') != 'closed' or current.get('user') != issue['user']:
            raise Invalid('Could not confirm Issue closure; retry cleanup')
    noun = 'CPP claim' if workflow_kind(stored) == 'CLAIM' else 'Project proposal'
    message = '**' + noun + ' closed without acceptance.** Its review PR was closed without merging, so this submission is cancelled. Use the appropriate form to submit a new proposal or claim.'
    if workflow_kind(stored) == 'CLAIM':
        message += '\n\nThe accepted CPP record was not changed. An eligible, unassigned CPP remains available for another claim.'
    result = cancel_submission(api,current,message=message)
    return 'rejected' if result == 'cancelled' else result


def repository_warning(url):
    if not url:
        return None
    # Syntax has already passed the schema. Use an anonymous request: a token
    # with organization membership permissions never travels to another repo.
    suffix = url.removeprefix("https://github.com/").rstrip("/")
    request = urllib.request.Request("https://api.github.com/repos/" + suffix,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "pit-project-submissions"})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            if response.status == 200:
                return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "The main GitHub repository could not be verified: it may be private or may not exist. SC should check the link."
        return "The main GitHub repository could not be verified (GitHub HTTP response). SC should check the link."
    except OSError:
        return "The main GitHub repository could not be verified because the lookup failed. SC should check the link."
    return "The main GitHub repository could not be verified. SC should check the link."


def fresh(api, issue, base_sha, ctx, teams):
    current = api.get(f"issues/{issue['number']}")
    if issue_version(current) != issue_version(issue):
        raise Invalid('Issue changed during processing; rerun the submission')
    if api.get('git/ref/heads/main')['object']['sha'] != base_sha:
        raise Invalid('Main changed during processing; rerun the submission')
    from project_workflow.commands import current_record
    _, _, stored = branch_snapshot(api,current)
    return current_record(api,current,ctx,teams,stored,allow_previous=True)


def set_invalid(api, issue, invalid):
    present = 'invalid' in {x['name'] for x in issue.get('labels',[])}
    if invalid and not present:
        try:
            api.request('POST','labels',{'name':'invalid','color':'d73a4a'})
        except urllib.error.HTTPError as exc:
            if exc.code != 422:
                raise
        api.request('POST',f"issues/{issue['number']}/labels",{'labels':['invalid']})
    elif present and not invalid:
        api.request('DELETE',f"issues/{issue['number']}/labels/invalid")


def process(api, teams, issue_number, *, repo_check=repository_warning, command_sync=False):
    issue = api.get(f'issues/{number(issue_number)}')
    if issue.get('pull_request'):
        return 'ignored'
    if issue.get('state') == 'closed':
        return cancel_submission(api,issue)
    pr = associated_pr(api,issue_number)
    if pr and pr.get('merged_at'):
        return 'already-merged'
    if cancelled(api,issue_number):
        feedback(api,issue_number,'**This submission was cancelled.** Use the appropriate project form to start again.')
        return 'cancelled'
    if pr and pr.get('state') == 'closed':
        return reject_submission(api,pr['number'])
    if not labelled(issue) and pr is None:
        return 'ignored'
    if pr:
        api.status(pr['head']['sha'],'pending','Checking the current submission')
    try:
        if not labelled(issue):
            raise Invalid('Restore the original project submission label before this proposal can be reviewed')
        if teams is None:
            raise Invalid('SC must configure the SC_TEAM_READ_TOKEN Actions secret')
        base_sha = api.get('git/ref/heads/main')['object']['sha']
        base_tree = api.tree(base_sha)
        read = api.reader(base_tree)
        ctx = load_context(read,paths=base_tree)
        records(base_tree,read,ctx)
        if ctx.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
            raise Invalid("SC team must belong to this repository's organization")
        from project_workflow.commands import current_record
        old_sha, old_commit, old_record = branch_snapshot(api,issue)
        record = current_record(api,issue,ctx,teams,old_record,allow_previous=True)
        path = record_path(record)
        if path in base_tree and workflow_kind(record) != 'CLAIM':
            raise Invalid('The project ID already exists; it will not be overwritten')
        values = resolved_values(issue,ctx)
        cleaned, removed = optional_fields(values,ctx)
        if workflow_kind(record) == 'CLAIM':
            original = ctx.projects[record['project_id']]
            if len(set(original.get('related_links',[]) + cleaned['related_links'])) > len(record['related_links']):
                removed.append('Additional related links were removed to keep the combined list within its allowed length.')
        if provenance(record).get('edits'):
            removed = []
        warning = repo_check(record.get('github_url'))
        if old_record is not None and old_record != record:
            raise Invalid('The submitted proposal was changed. Cancel it and submit a new project; proposal editing is not supported')
        if pr and old_sha != pr['head']['sha']:
            raise Invalid('Proposal branch changed; retry')
        if fresh(api,issue,base_sha,ctx,teams) != record:
            raise Invalid('Requester permissions changed during processing; retry')
        rendered = formatted_issue_body(record,ctx,submission_body(issue))
        target_title = issue_title(record) if workflow_kind(record) == 'CLAIM' or 'original_title' in provenance(record) else issue['title']
        if issue['body'] != rendered or issue['title'] != target_title:
            # Preserve very long native submissions rather than exceeding the
            # Issue body's API limit or silently truncating scientific content.
            if len(rendered.encode('utf-8')) <= 65536:
                fresh(api,issue,base_sha,ctx,teams)
                payload = {'body':rendered}
                if issue['title'] != target_title:
                    payload['title'] = target_title
                updated = api.request('PATCH',f'issues/{issue_number}',payload)
                if (updated.get('body') != rendered or updated.get('title') != target_title
                        or updated.get('user') != issue['user']):
                    raise Invalid('Issue changed while formatting; inspect it before retrying')
                issue = updated
                if fresh(api,issue,base_sha,ctx,teams) != record:
                    raise Invalid('Submission changed while formatting; retry')
        if workflow_kind(record) == 'CLAIM' and issue['title'] != target_title:
            updated = api.request('PATCH',f'issues/{issue_number}',{'title':target_title})
            if updated.get('title') != target_title or updated.get('user') != issue['user'] or updated.get('body') != issue['body']:
                raise Invalid('Claim Issue changed while updating its title; retry')
            issue = updated
            if fresh(api,issue,base_sha,ctx,teams) != record:
                raise Invalid('Claim changed while updating its title; retry')
        if old_record == record and old_commit['parents'][0]['sha'] == base_sha:
            head_sha = old_sha
        else:
            base_commit = api.get('git/commits/' + base_sha)
            tree = api.request('POST','git/trees',{'base_tree':base_commit['tree']['sha'],
                'tree':[{'path':path,'mode':'100644','type':'blob','content':project_yaml(record)}]})
            commit = api.request('POST','git/commits',{'message':f'Project submission {issue_number}',
                'tree':tree['sha'],'parents':[base_sha]})
            head_sha = commit['sha']
            fresh(api,issue,base_sha,ctx,teams)
            api.status(head_sha,'pending','Checking proposal policy')
            branch = submission_branch(issue_number)
            if old_sha:
                if api.get('git/ref/heads/' + branch)['object']['sha'] != old_sha:
                    raise Invalid('Submission branch advanced; retry')
                api.request('PATCH','git/refs/heads/' + branch,{'sha':head_sha,'force':True})
            else:
                api.request('POST','git/refs',{'ref':'refs/heads/' + branch,'sha':head_sha})
        prefix = 'Claim CPP: ' if workflow_kind(record) == 'CLAIM' else 'New CPP project: ' if record['kind'] == 'CPP' else 'New project: '
        payload = {'title':(prefix + record['name'])[:256],
                   'body':proposal_body(record,ctx,api.repo,issue_number,warning)}
        had_pr = pr is not None
        if pr and any(pr.get(key) != value for key, value in payload.items()):
            pr = api.request('PATCH',f"pulls/{pr['number']}",payload)
        elif pr is None:
            pr = api.request('POST','pulls',{**payload,'head':submission_branch(issue_number),'base':'main'})
        request_review(api,pr,ctx)
        dispatch_validation(api,pr['number'])
        set_invalid(api,issue,False)
        heading = '**Your CPP claim has been submitted.**' if workflow_kind(record) == 'CLAIM' else '**Your project has been submitted.**'
        message = heading + ' [View the pull request](https://github.com/' + api.repo + f"/pull/{pr['number']}).\n\nOnce the project has been reviewed and accepted, you will be notified.\n"
        if not issue['body'].startswith(FORMATTED_MARKER):
            message += 'The submission is too long to reformat within GitHub’s Issue size limit; the complete original answers are retained.\n'
        message += '\nEntries can be edited using commands. Comment `/help` or `/edit help` to see the available options.\n'
        if removed:
            message += '\n**The following optional entries were removed:**\n\n' + '\n'.join('- ' + safe_text(x) for x in removed)
            message += '\n\nThese details can be added with commands; use `/edit help` for the options.\n'
        if warning:
            message += '\n**Note:** ' + safe_text(warning)
        if len(message) > 64000:
            message = message[:63000] + '\n\n*Summary shortened; the full submission remains in this Issue and the project YAML.*'
        feedback(api,issue_number,message)
        return 'updated' if had_pr else 'created'
    except Invalid as exc:
        if command_sync:
            raise  # A command synchronization failure must never close the submission.
        current_pr = associated_pr(api,issue_number)
        if current_pr and current_pr['state'] == 'open':
            api.status(current_pr['head']['sha'],'failure','Submission validation failed; see the Issue')
        set_invalid(api,issue,True)
        labels = {x['name'] for x in issue.get('labels',[])}
        template = 'claim-cpp.yml' if 'claim-cpp' in labels else 'new-cpp-project.yml' if 'new-cpp-project' in labels else 'new-project.yml'
        url = f'https://github.com/{api.repo}/issues/new?template={template}'
        message = '**Your project could not be submitted. This Issue has been closed.**\n\n' + '\n'.join('- ' + safe_text(line) for line in str(exc).splitlines())
        message += f'\n\nAddress the problems above, then [submit a new project](<{url}>).'
        closed = api.request('PATCH',f'issues/{issue_number}',{'state':'closed','state_reason':'not_planned'})
        feedback(api,issue_number,message[:63000] + '\n' + CANCEL_MARKER)
        cancel_submission(api,closed)
        return 'invalid'


def main():
    api = None
    issue_number = None
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        raw = os.environ.get('SUBMISSION_ISSUE_NUMBER','')
        api = GitHub(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'])
        checkout = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        if checkout != api.get('git/ref/heads/main')['object']['sha']:
            raise Invalid('Submission code must run from current main; rerun this workflow')
        if event.get('pull_request'):
            # Reopening an old claim must not create a second active reservation.
            from project_workflow.submission import branch_issue
            pr = api.get(f"pulls/{number(event['pull_request']['number'])}")
            if (event.get('action') not in {'reopened','closed'} or not is_submission_bot(pr['user'])
                    or pr['head']['repo']['full_name'] != api.repo
                    or pr['base']['repo']['full_name'] != api.repo or pr['base']['ref'] != 'main'):
                return 0
            issue_number = branch_issue(pr['head']['ref'])
            if event.get('action') == 'closed':
                result = reject_submission(api,pr['number'])
                print(json.dumps({'issue':issue_number,'result':result}))
                return 0
        else:
            issue_number = number(int(raw)) if raw else number(event['issue']['number'])
        teams = TeamDirectory(os.environ['SC_TEAM_READ_TOKEN']) if os.environ.get('SC_TEAM_READ_TOKEN') else None
        if event.get('comment'):
            from project_workflow.commands import process as command_process
            result = command_process(api,teams,issue_number,event['comment'],action=event.get('action','created'))
        else:
            result = process(api,teams,issue_number)
        print(json.dumps({'issue':issue_number,'result':result}))
        return 1 if result == 'incomplete' else 0
    except (urllib.error.HTTPError,Invalid,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'error':f'GitHub API error (HTTP {exc.code})' if isinstance(exc,urllib.error.HTTPError) else 'Submission automation could not complete'}),file=sys.stderr)
        if api is not None and issue_number is not None:
            try:
                pr = associated_pr(api,issue_number)
                if pr and pr['state'] == 'open':
                    api.status(pr['head']['sha'],'failure','Automation failed; SC must inspect the workflow')
                feedback(api,issue_number,'**Submission automation could not complete.** Ask SC to inspect the failed Actions run and retry. No proposal is approved.')
            except (OSError,ValueError,Invalid,KeyError):
                pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
