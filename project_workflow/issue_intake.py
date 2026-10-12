"""Create/update one project proposal PR from a verified GitHub Issue.

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
    branch_issue, changed_files, is_submission_bot, issue_version, labelled,
    number, project_path, project_yaml, proposal, submission_branch,
)
from project_workflow.validate_pr import GitHub, NoRedirect, TeamDirectory, records
from project_workflow.dispatch import validate_pr as dispatch_validation
from project_workflow.review import proposal_body, request_review, safe_text, summary_body
from project_workflow import revisions, workflow_state
from project_workflow.submission import correction_issue, resolved_values

COMMENT_MARKER = "<!-- pit-project-submission-feedback:v1 -->"
CANCEL_MARKER = "<!-- pit-project-cancelled:v1 -->"


def feedback(api, issue_number, message, *, marker=COMMENT_MARKER, request=None):
    body = marker + "\n" + message
    if request is not None:
        body += revisions.request_marker(request)
    previous = [c for c in api.pages(f"issues/{issue_number}/comments")
                if is_submission_bot(c["user"]) and c.get("body", "").startswith(marker)]
    if previous:
        if previous[0]["body"] != body:
            api.request("PATCH", f"issues/comments/{previous[0]['id']}", {"body": body})
    else:
        api.request("POST", f"issues/{issue_number}/comments", {"body": body})


def associated_pr(api, issue_number):
    branch = submission_branch(issue_number)
    owner = api.repo.split("/", 1)[0]
    candidates = api.pages(f"pulls?state=all&head={owner}:{branch}&base=main")
    matches = [p for p in candidates if p["head"].get("repo", {}).get("full_name") == api.repo
               and p["head"]["ref"] == branch]
    if len(matches) > 1:
        raise Invalid("Multiple PRs already use this submission branch; ask SC to resolve them")
    if matches and not is_submission_bot(matches[0]["user"]):
        raise Invalid("The reserved submission branch is used by a manually authored PR; ask SC to resolve it")
    return matches[0] if matches else None


def cancelled_root(api, issue_number):
    for comment in api.pages(f'issues/{issue_number}/comments'):
        body = comment.get('body', '')
        if is_submission_bot(comment['user']) and body.startswith(CANCEL_MARKER):
            match = revisions.ROUTE.search(body)
            if match:
                return number(int(match[1]))
    return None


def cancel_submission(api, issue):
    """Close the review PR and delete only the latest submission's own branch.

    A durable bot cancellation reply allows a retry after partial cleanup and
    prevents reopening from silently resurrecting a cancelled proposal.
    No current organization membership is needed to finish cleanup.
    """
    cancellation = cancelled_root(api, issue['number'])
    root_number = cancellation or revisions.routed_root(api, issue)
    if root_number is None:
        route = revisions.ROUTE.search(issue.get('body') or '')
        if route:
            root_number = number(int(route[1]))
    if root_number is None:
        return 'ignored-closure'
    root = api.get(f'issues/{root_number}')
    if not revisions.same_author(root, issue):
        raise Invalid('Closed submission does not match its original author')
    pr = associated_pr(api, root_number)
    if pr and pr.get('merged_at'):
        # Closing via Closes #N after merge accepts, rather than cancels.
        return 'already-merged'
    sha, _, data = workflow_state.snapshot(api, root_number)
    if sha is None and cancellation is not None:
        return 'cancelled'
    state = data.get(workflow_state.root_path(root_number))
    if state is not None and revisions.latest_number(state) != issue['number']:
        return 'superseded'
    if state is None and cancelled_root(api, issue['number']) is None:
        return 'ignored-closure'
    current = api.get(f"issues/{issue['number']}")
    if current.get('state') != 'closed':
        return 'closure-reversed'
    if pr:
        live_pr = api.get(f"pulls/{pr['number']}")
        if live_pr.get('merged_at'):
            return 'already-merged'
        if live_pr['head']['sha'] != sha and sha is not None:
            raise Invalid('Proposal branch changed before cancellation; retry')
        if live_pr['state'] == 'open':
            api.status(live_pr['head']['sha'], 'failure', 'Project submission cancelled')
            closed_pr = api.request('PATCH', f"pulls/{pr['number']}", {'state': 'closed'})
            if closed_pr.get('merged_at'):
                return 'already-merged'
    message = ('**Project submission cancelled.** Its unmerged review PR is closed and branch cleanup is requested. '
               'To start again, use the New Project form.' + f"\n<!-- pit-project-root:{root_number} -->")
    # Record cancellation before deleting the branch, including on the root
    # Issue when the latest submission is a replacement.
    feedback(api, root_number, message, marker=CANCEL_MARKER)
    if issue['number'] != root_number:
        feedback(api, issue['number'], message, marker=CANCEL_MARKER)
    workflow_state.remove(api, root_number, sha)
    message = message.replace('branch cleanup is requested', 'its submission branch is removed')
    feedback(api, root_number, message, marker=CANCEL_MARKER)
    if issue['number'] != root_number:
        feedback(api, issue['number'], message, marker=CANCEL_MARKER)
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


def fresh(api, issue, base_sha, ctx, teams, root, state):
    current = api.get(f"issues/{issue['number']}")
    if issue_version(current) != issue_version(issue):
        raise Invalid("The Issue changed during processing. Save it again or ask SC to rerun the submission")
    if api.get("git/ref/heads/main")["object"]["sha"] != base_sha:
        raise Invalid("The registry changed during processing. Save the Issue again to retry")
    live_root, live_state, active, previous_values = revisions.active_submission(api, root['number'], ctx)
    if live_state != state or active['number'] != issue['number'] or issue_version(live_root) != issue_version(root):
        raise Invalid("The active revision changed during processing; retry")
    member, sc = authenticate(current, ctx, teams, api.repo)
    return proposal(current, ctx, organization_member=member, is_sc=sc,
                    original_issue=root, previous_values=previous_values,
                    input_values=revisions.input_values(api, current, ctx, previous_values))


def process_active(api, teams, issue, root, state, previous_values, *, repo_check=repository_warning):
    issue_number = issue['number']
    root_number = root['number']
    pr = associated_pr(api, root_number)
    if pr and pr.get("merged_at"):
        return "already-merged"
    if pr and pr["state"] == "closed":
        feedback(api, issue_number, "**The previous proposal PR was closed without merging.** Ask SC to reopen that PR before continuing. No duplicate PR was created.")
        return "closed-pr"
    if pr:
        api.status(pr["head"]["sha"], "pending", "Issue changed; proposal needs revalidation")
    try:
        base_sha = api.get("git/ref/heads/main")["object"]["sha"]
        base_tree = api.tree(base_sha)
        base_read = api.reader(base_tree)
        ctx = load_context(base_read, paths=base_tree)
        form_path = ".github/ISSUE_TEMPLATE/new-project.yml"
        if form_path in base_tree:
            from project_workflow.issue_form import validate_form
            validate_form(base_read(form_path), ctx)
        records(base_tree, base_read, ctx)
        member, sc = authenticate(issue, ctx, teams, api.repo)
        issue, values = revisions.format_issue(api, issue, root, ctx, previous_values)
        if issue['number'] == root['number']:
            root = issue
        record = proposal(issue, ctx, organization_member=member, is_sc=sc,
                          original_issue=root, previous_values=previous_values, input_values=values)
        path = project_path(root_number)
        if path in base_tree:
            raise Invalid("The project ID reserved for this Issue already exists. Ask SC to resolve the collision; it will not be overwritten")
        warning = repo_check(record["github_url"])
        branch = submission_branch(root_number)
        # Formatting saved answers on the same branch. Refresh the PR because
        # its head may have advanced while its YAML was preserved.
        pr = associated_pr(api, root_number)
        existing_sha, commit, branch_data = workflow_state.snapshot(api, root_number)
        if pr and existing_sha != pr["head"]["sha"]:
            raise Invalid("The submission branch changed during processing; save the Issue again to retry")
        if existing_sha:
            existing_tree = api.tree(existing_sha)
            # Allow a branch based on older main, but reject unrelated edits.
            # Compare against its own parent, which our commits always set to main.
            existing_record = api.reader(existing_tree)(path) if path in existing_tree else None
            unchanged = existing_record == project_yaml(record) and commit["parents"][0]["sha"] == base_sha
        else:
            unchanged = False
        if fresh(api, issue, base_sha, ctx, teams, root, state) != record:
            raise Invalid("Requester permissions changed; save the Issue again to retry")
        if unchanged:
            head_sha = existing_sha
        else:
            base_commit = api.get(f"git/commits/{base_sha}")
            tree = api.request("POST", "git/trees", {"base_tree": base_commit["tree"]["sha"],
                "tree": [{"path": path, "mode": "100644", "type": "blob", "content": project_yaml(record)}]})
            fresh(api, issue, base_sha, ctx, teams, root, state)
            head_sha = workflow_state.publish(api, root_number, branch_data, tree['sha'], base_sha, existing_sha)
        body = proposal_body(record, ctx, api.repo, issue_number, warning)
        title = ("New project: " + record["name"])[:256]
        had_pr = pr is not None
        if pr:
            pr = api.request("PATCH", f"pulls/{pr['number']}", {"title": title, "body": body})
        else:
            pr = api.request("POST", "pulls", {"title": title,
                "head": branch, "base": "main", "body": body})
        request_review(api, pr, ctx)
        # GITHUB_TOKEN-created PRs do not reliably start pull_request_target.
        # Explicit dispatch runs trusted main code and publishes a status on
        # the actual PR head (not on the workflow's main-branch commit).
        dispatch_validation(api, pr["number"])
        message = f"**Proposal validated.** Review PR: https://github.com/{api.repo}/pull/{pr['number']}\n\n"
        message += "Review by the configured Code Owner is still required. To edit this proposal before merging, comment `/edit` on this Issue. A replacement-form link will be provided, and the same PR will be updated."
        revisions.set_label(api, issue, 'invalid', present=False)
        if warning:
            message += "\n\n**Note:** " + warning
        feedback(api, issue_number, message)
        return "updated" if had_pr else "created"
    except Invalid as exc:
        if pr:
            current = api.get(f"pulls/{pr['number']}")
            api.status(current["head"]["sha"], "failure", "Correct the submission Issue before this PR can be merged")
        revisions.set_label(api, issue, 'invalid')
        try:
            try:
                values = revisions.input_values(api, issue, ctx, previous_values)
            except Invalid:
                values = revisions.saved_values(api, issue)
            url, omitted, request = revisions.correction_link(api, root, state, issue, values, ctx, error=exc)
            intro = "**Your proposal needs correction.** Problematic answers are marked with 🔴 in the box below.\n\n"
            opening = '<details open>\n<summary><strong>Submitted project</strong></summary>\n\n'
            closing = '\n</details>\n\n'
            actions = f"[Correct this submission](<{url}>)\n\nCheck the dropdown selections, then submit the replacement form."
            if omitted:
                actions += "\n\nLong answers are retained through **Keep previous value**. Replace that value to change an answer."
            if not exc.fields:
                actions += "\n\n" + safe_text(str(exc), markdown=True)
            body = summary_body(values, ctx, error=exc)
            # Keep the container closed and its action link visible even when
            # GitHub's comment limit requires shortening a very long summary.
            budget = 64000 - len(intro + opening + closing + actions)
            if len(body) > budget:
                note = '\n\n*Display shortened. Complete answers are retained by the workflow.*\n'
                body = body[:max(0, budget - len(note))] + note
            message = intro + opening + body + closing + actions
        except Invalid:
            request = None
            message = "**Your proposal needs correction.** " + safe_text(str(exc), markdown=True)
            message += "\n\nThe answers could not be recovered. Ask SC to inspect the submission."
        feedback(api, issue_number, message, request=request)
        return "invalid"



def supersede_previous(api, root, state, issue):
    if len(state['history']) < 2:
        return
    previous = api.get(f"issues/{state['history'][-2]['number']}")
    feedback(api, previous['number'],
             f"**This submission has been replaced by [Issue #{issue['number']}](https://github.com/{api.repo}/issues/{issue['number']}).** Follow the latest Issue for validation and review.",
             marker="<!-- pit-project-superseded:v1 -->")
    revisions.set_label(api, previous, 'superseded')
    if previous.get('state') != 'closed':
        api.request('PATCH', f"issues/{previous['number']}", {'state': 'closed', 'state_reason': 'not_planned'})


def process(api, teams, issue_number, *, repo_check=repository_warning, edit_comment=None):
    issue_number = number(issue_number)
    issue = api.get(f"issues/{issue_number}")
    if issue.get('pull_request') or (edit_comment is not None and not labelled(issue)):
        return 'ignored-command'
    if issue.get('state') == 'closed':
        return cancel_submission(api, issue)
    if cancelled_root(api, issue_number) is not None:
        feedback(api, issue_number, '**This submission was cancelled.** Use the New Project form to start again.')
        return 'cancelled'
    if not labelled(issue):
        pr = bound_pr(api, issue_number)
        if pr and pr['state'] == 'open':
            api.status(pr['head']['sha'], 'failure', 'Restore the active submission label before merging')
        return 'invalid'
    base_sha = api.get('git/ref/heads/main')['object']['sha']
    base_tree = api.tree(base_sha)
    ctx = load_context(api.reader(base_tree), paths=base_tree)
    member, _ = authenticate(issue, ctx, teams, api.repo)
    if not member:
        pr = bound_pr(api, issue_number)
        if pr and pr['state'] == 'open':
            api.status(pr['head']['sha'], 'failure', 'The submitter is no longer an organization member')
        feedback(api, issue_number, '**Submission could not be validated.** The submitter must be an active organization member.')
        if correction_issue(issue) and revisions.routed_root(api, issue) is None and issue.get('state') != 'closed':
            api.request('PATCH', f'issues/{issue_number}', {'state': 'closed', 'state_reason': 'not_planned'})
        return 'rejected-correction' if correction_issue(issue) else 'invalid'
    pr = None
    try:
        if correction_issue(issue):
            root_number = revisions.routed_root(api, issue)
            if root_number is None:
                root_number, _, _ = revisions.references(issue, ctx)
            try:
                root = api.get(f"issues/{root_number}")
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                raise Invalid('The original submission Issue does not exist') from exc
            if cancelled_root(api, root_number) is not None:
                raise Invalid('This project submission was cancelled. Use the New Project form to start again')
            state = revisions.read_state(api, root_number)
            if not revisions.same_author(root, issue):
                raise Invalid('Only the original submitter may replace this proposal')
            if root.get('pull_request') or correction_issue(root):
                raise Invalid('A correction must refer to an original New Project submission')
            pr = associated_pr(api, root_number)
        else:
            root = issue
            existing_pr = associated_pr(api, root['number'])
            pr = existing_pr
            if existing_pr and existing_pr.get('merged_at'):
                return 'already-merged'
            state = revisions.ensure_state(api, root)
            if revisions.latest_number(state) != issue_number:
                return 'superseded'
        member, _ = authenticate(issue, ctx, teams, api.repo)
        if not member:
            raise Invalid('The submitter must still be an active organization member')
        pr = associated_pr(api, root['number'])
        if pr and (pr.get('merged_at') or pr['state'] == 'closed'):
            if correction_issue(issue):
                if pr.get('merged_at') and revisions.latest_number(state) == issue_number:
                    return 'already-merged'
                raise Invalid('This proposal PR has already merged or closed; it cannot be replaced')
            if pr.get('merged_at'):
                return 'already-merged'
            feedback(api, issue_number, '**The proposal PR was closed without merging.** Ask SC to reopen it before continuing.')
            return 'closed-pr'
        if edit_comment is not None:
            comment = api.get(f"issues/comments/{number(edit_comment)}")
            if (comment.get('body', '').strip() != '/edit'
                    or comment.get('issue_url') != f"https://api.github.com/repos/{api.repo}/issues/{issue_number}"
                    or not revisions.same_author(root, comment)):
                return 'ignored-command'
            live_root, state, active, previous_values = revisions.active_submission(api, root['number'], ctx)
            if active['number'] != issue_number or not pr:
                feedback(api, issue_number, 'Use the correction link on the latest submission. `/edit` is available while its review PR is open.', marker='<!-- pit-project-edit:v1 -->')
                return 'edit-unavailable'
            values = revisions.input_values(api, issue, ctx, previous_values)
            url, omitted, request = revisions.correction_link(api, live_root, state, issue, values, ctx)
            message = f"[Edit this submission](<{url}>)\n\nSubmitting this form creates a replacement Issue and updates the existing PR. Opening this link leaves the current proposal unchanged."
            if omitted:
                message += "\n\nLong answers are retained through **Keep previous value**. Replace that value to change an answer."
            feedback(api, issue_number, message, marker='<!-- pit-project-edit:v1 -->', request=request)
            return 'edit-requested'
        if correction_issue(issue):
            # Check the transition completely before changing this proposal's status.
            request = state.get('request')
            if revisions.latest_number(state) != issue_number:
                refs = revisions.references(issue, ctx)
                if (not request or request['source'] != refs[1] or request['token'] != refs[2]
                        or refs[1] != revisions.latest_number(state)):
                    raise Invalid('This correction link is stale. Use the latest submission Issue')
                source = api.get(f"issues/{refs[1]}")
                if source.get('state') != 'open' or revisions.content_hash(source) != request['hash']:
                    raise Invalid('The previous submission changed; request a fresh link')
            if pr:
                api.status(pr['head']['sha'], 'pending', 'Replacement submitted; revalidation required')
            state = revisions.receive_revision(api, issue, root, state, ctx)
            supersede_previous(api, root, state, issue)
        root, state, active, previous_values = revisions.active_submission(api, root['number'], ctx)
        if active['number'] != issue_number:
            return 'superseded'
        # The original Issue may just have been closed as superseded.
        return process_active(api, teams, active, root, state, previous_values, repo_check=repo_check)
    except Invalid as exc:
        if correction_issue(issue):
            feedback(api, issue_number, '**This correction could not be used.** ' + safe_text(str(exc), markdown=True)
                     + f"\n\nFor a new project, [use the New Project form](https://github.com/{api.repo}/issues/new?template=new-project.yml). For a correction, use the latest link provided on your submission.")
            # Never close an already-authenticated active replacement on a retry.
            if pr is None or revisions.latest_number(state) != issue_number:
                if issue.get('state') != 'closed':
                    api.request('PATCH', f"issues/{issue_number}", {'state': 'closed', 'state_reason': 'not_planned'})
            elif pr:
                api.status(pr['head']['sha'], 'failure', 'The active replacement could not be validated')
            return 'rejected-correction'
        if pr:
            api.status(pr['head']['sha'], 'failure', 'Submission cannot be validated')
        feedback(api, issue_number, '**Submission could not be validated.** ' + safe_text(str(exc), markdown=True))
        return 'invalid'

def bound_pr(api, issue_number):
    """Find only this authenticated active proposal when failing closed."""
    issue = api.get(f'issues/{issue_number}')
    root_number = revisions.routed_root(api, issue)
    if root_number is None:
        root_number = issue_number
    if root_number != issue_number:
        root = api.get(f'issues/{root_number}')
        state = revisions.read_state(api, root_number)
        if revisions.latest_number(state) != issue_number or not revisions.same_author(root, issue):
            return None
    else:
        from project_workflow import workflow_state
        if workflow_state.read(api, workflow_state.root_path(root_number)):
            if revisions.latest_number(revisions.read_state(api, root_number)) != issue_number:
                return None
    return associated_pr(api, root_number)


def main():
    api = None
    issue_number = None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        raw = os.environ.get("SUBMISSION_ISSUE_NUMBER", "")
        issue_number = number(int(raw)) if raw else number(event["issue"]["number"])
        api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        # A manual dispatch must also execute accepted code from main.
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checkout != api.get("git/ref/heads/main")["object"]["sha"]:
            raise Invalid("Submission code must run from current main; rerun this workflow")
        current_issue = api.get(f'issues/{issue_number}')
        if current_issue.get('state') == 'closed' and not current_issue.get('pull_request'):
            result = cancel_submission(api, current_issue)
            print(json.dumps({'issue': issue_number, 'result': result}))
            return 0
        teams = TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", "")) if os.environ.get("SC_TEAM_READ_TOKEN") else None
        if teams is None:
            pr = bound_pr(api, issue_number)
            if pr and pr["state"] == "open":
                api.status(pr["head"]["sha"], "failure", "Membership credential is missing; SC must fix the automation")
            raise Invalid("SC must configure the SC_TEAM_READ_TOKEN Actions secret")
        edit_comment = event['comment']['id'] if event.get('comment') else None
        result = process(api, teams, issue_number, edit_comment=edit_comment)
        print(json.dumps({"issue": issue_number, "result": result}))
        return 0
    except (urllib.error.HTTPError, Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        message = f"GitHub API error (HTTP {exc.code})" if isinstance(exc, urllib.error.HTTPError) else "Submission automation could not complete"
        print(json.dumps({"error": message}), file=sys.stderr)
        if api is not None and issue_number is not None:
            try:
                pr = bound_pr(api, issue_number)
                if pr and pr["state"] == "open":
                    api.status(pr["head"]["sha"], "failure", "Automation failed; SC must retry or fix the workflow")
                feedback(api, issue_number, "**Submission automation could not complete.** No proposal is approved. Ask SC to inspect the failed Actions run and rerun it. Your answers are retained in this Issue.")
            except (OSError, ValueError, Invalid, KeyError):
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
