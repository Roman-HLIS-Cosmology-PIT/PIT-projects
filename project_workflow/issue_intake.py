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
    changed_files, is_submission_bot, issue_version, labelled,
    number, project_path, project_yaml, proposal, submission_branch,
)
from project_workflow.validate_pr import GitHub, NoRedirect, TeamDirectory, records
from project_workflow.dispatch import validate_pr as dispatch_validation
from project_workflow.review import proposal_body, request_review, safe_text, summary_body
from project_workflow.project_registry import parse_yaml, validate_record

COMMENT_MARKER = "<!-- pit-project-submission-feedback:v1 -->"
CANCEL_MARKER = "<!-- pit-project-cancelled:v1 -->"


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


def branch_snapshot(api, issue):
    """Verify a reserved branch before updating or deleting it."""
    branch = submission_branch(issue['number'])
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
    path = project_path(issue['number'])
    if path in base_tree or changed_files(base_tree,head_tree) != {path}:
        raise Invalid('Submission branch contains unrelated changes; it will not be overwritten or deleted')
    read = api.reader(head_tree)
    ctx = load_context(api.reader(base_tree),paths=base_tree)
    record = parse_yaml(read(path))
    validate_record(record,path,ctx)
    provenance = record.get('submission',{})
    if provenance.get('issue_number') != issue['number'] or provenance.get('submitted_by','').lower() != issue['user']['login'].lower():
        raise Invalid('Submission branch belongs to another requester')
    return sha, commit, record


def cancelled(api, issue_number):
    return any(is_submission_bot(c['user']) and c.get('body','').startswith(CANCEL_MARKER)
               for c in api.pages(f'issues/{issue_number}/comments'))


def cancel_submission(api, issue):
    pr = associated_pr(api,issue['number'])
    if pr and pr.get('merged_at'):
        return 'already-merged'
    sha, _, _ = branch_snapshot(api,issue)
    if sha is None and pr is None:
        return 'ignored-closure'
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
    feedback(api,issue['number'],'**Project submission cancelled.** Its unmerged PR is closed. Use the New Project form to start again.',marker=CANCEL_MARKER)
    if sha:
        branch = submission_branch(issue['number'])
        if api.get('git/ref/heads/' + branch)['object']['sha'] != sha:
            raise Invalid('Submission branch advanced before deletion; retry')
        api.request('DELETE','git/refs/heads/' + branch)
    return 'cancelled'


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


def authenticate(issue, ctx, teams, repo):
    org = repo.split("/", 1)[0]
    if ctx.sc_team[0].lower() != org.lower():
        raise Invalid("SC team must belong to this repository's organization")
    actor = issue["user"]["login"]
    member = teams.is_org_member(org, actor)
    sc = teams.is_active_member(*ctx.sc_team, actor) if member else False
    return member, sc


def fresh(api, issue, base_sha, ctx, teams):
    current = api.get(f"issues/{issue['number']}")
    if issue_version(current) != issue_version(issue):
        raise Invalid('Issue changed during processing; rerun the submission')
    if api.get('git/ref/heads/main')['object']['sha'] != base_sha:
        raise Invalid('Main changed during processing; rerun the submission')
    member, sc = authenticate(current,ctx,teams,api.repo)
    return proposal(current,ctx,organization_member=member,is_sc=sc)


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


def process(api, teams, issue_number, *, repo_check=repository_warning):
    issue = api.get(f'issues/{number(issue_number)}')
    if issue.get('pull_request'):
        return 'ignored'
    if issue.get('state') == 'closed':
        return cancel_submission(api,issue)
    pr = associated_pr(api,issue_number)
    if pr and pr.get('merged_at'):
        return 'already-merged'
    if cancelled(api,issue_number):
        feedback(api,issue_number,'**This submission was cancelled.** Use the New Project form to start again.')
        return 'cancelled'
    if not labelled(issue) and pr is None:
        return 'ignored'
    if pr:
        api.status(pr['head']['sha'],'pending','Checking the current submission')
    try:
        if not labelled(issue):
            raise Invalid('Restore the new-project label before this proposal can be reviewed')
        if teams is None:
            raise Invalid('SC must configure the SC_TEAM_READ_TOKEN Actions secret')
        base_sha = api.get('git/ref/heads/main')['object']['sha']
        base_tree = api.tree(base_sha)
        read = api.reader(base_tree)
        ctx = load_context(read,paths=base_tree)
        records(base_tree,read,ctx)
        member, sc = authenticate(issue,ctx,teams,api.repo)
        record = proposal(issue,ctx,organization_member=member,is_sc=sc)
        path = project_path(issue_number)
        if path in base_tree:
            raise Invalid('The project ID already exists; it will not be overwritten')
        if pr and pr['state'] == 'closed':
            raise Invalid('The proposal PR was closed without merging. Ask SC to reopen it; no duplicate PR will be created')
        warning = repo_check(record['github_url'])
        old_sha, old_commit, old_record = branch_snapshot(api,issue)
        if old_record is not None and old_record != record:
            raise Invalid('The submitted proposal was changed. Cancel it and submit a new project; proposal editing is not supported')
        if pr and old_sha != pr['head']['sha']:
            raise Invalid('Proposal branch changed; retry')
        if fresh(api,issue,base_sha,ctx,teams) != record:
            raise Invalid('Requester permissions changed during processing; retry')
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
        payload = {'title':('New project: ' + record['name'])[:256],
                   'body':proposal_body(record,ctx,api.repo,issue_number,warning)}
        had_pr = pr is not None
        if pr:
            pr = api.request('PATCH',f"pulls/{pr['number']}",payload)
        else:
            pr = api.request('POST','pulls',{**payload,'head':submission_branch(issue_number),'base':'main'})
        request_review(api,pr,ctx)
        dispatch_validation(api,pr['number'])
        set_invalid(api,issue,False)
        message = '**Proposal validated.** [Review PR](https://github.com/' + api.repo + f"/pull/{pr['number']}). Review and acceptance are still required.\n\n"
        message += summary_body(record,ctx)
        if warning:
            message += '\n**Note:** ' + safe_text(warning)
        if len(message) > 64000:
            message = message[:63000] + '\n\n*Summary shortened; the full submission remains in this Issue and the project YAML.*'
        feedback(api,issue_number,message)
        return 'updated' if had_pr else 'created'
    except Invalid as exc:
        current_pr = associated_pr(api,issue_number)
        if current_pr and current_pr['state'] == 'open':
            api.status(current_pr['head']['sha'],'failure','Submission validation failed; see the Issue')
        set_invalid(api,issue,True)
        url = f'https://github.com/{api.repo}/issues/new?template=new-project.yml'
        message = '**Submission could not be validated.**\n\n' + '\n'.join('- ' + safe_text(line) for line in str(exc).splitlines())
        message += f'\n\nClose this Issue to cancel the submission, then [submit a new project](<{url}>). Closing it also closes any unmerged proposal PR and removes its branch.'
        feedback(api,issue_number,message[:64000])
        return 'invalid'


def main():
    api = None
    issue_number = None
    try:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        raw = os.environ.get('SUBMISSION_ISSUE_NUMBER','')
        issue_number = number(int(raw)) if raw else number(event['issue']['number'])
        api = GitHub(os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_TOKEN'])
        checkout = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
        if checkout != api.get('git/ref/heads/main')['object']['sha']:
            raise Invalid('Submission code must run from current main; rerun this workflow')
        teams = TeamDirectory(os.environ['SC_TEAM_READ_TOKEN']) if os.environ.get('SC_TEAM_READ_TOKEN') else None
        result = process(api,teams,issue_number)
        print(json.dumps({'issue':issue_number,'result':result}))
        return 0
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
