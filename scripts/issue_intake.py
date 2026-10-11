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

from scripts.project_registry import Invalid, load_context
from scripts.submission import (
    branch_issue, changed_files, is_submission_bot, issue_version, labelled,
    number, project_path, project_yaml, proposal, submission_branch,
)
from scripts.validate_pr import GitHub, NoRedirect, TeamDirectory, records

COMMENT_MARKER = "<!-- pit-project-submission-feedback:v1 -->"


def feedback(api, issue_number, message):
    body = COMMENT_MARKER + "\n" + message
    previous = [c for c in api.pages(f"issues/{issue_number}/comments")
                if is_submission_bot(c["user"]) and c.get("body", "").startswith(COMMENT_MARKER)]
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
        raise Invalid("The Issue changed during processing. Save it again or ask SC to rerun the submission")
    if api.get("git/ref/heads/main")["object"]["sha"] != base_sha:
        raise Invalid("The registry changed during processing. Save the Issue again to retry")
    member, sc = authenticate(current, ctx, teams, api.repo)
    return proposal(current, ctx, organization_member=member, is_sc=sc)


def process(api, teams, issue_number, *, repo_check=repository_warning):
    issue_number = number(issue_number)
    issue = api.get(f"issues/{issue_number}")
    pr = associated_pr(api, issue_number)
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
            from scripts.issue_form import generated_text
            if base_read(form_path) != generated_text(ctx):
                raise Invalid("SC must regenerate the Issue Form to match the working-group catalogue")
        records(base_tree, base_read, ctx)
        member, sc = authenticate(issue, ctx, teams, api.repo)
        record = proposal(issue, ctx, organization_member=member, is_sc=sc)
        path = project_path(issue_number)
        if path in base_tree:
            raise Invalid("The project ID reserved for this Issue already exists. Ask SC to resolve the collision; it will not be overwritten")
        warning = repo_check(record["github_url"])
        branch = submission_branch(issue_number)
        try:
            existing_sha = api.get(f"git/ref/heads/{branch}")["object"]["sha"]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            existing_sha = None
        if pr and existing_sha != pr["head"]["sha"]:
            raise Invalid("The submission branch changed during processing; save the Issue again to retry")
        if existing_sha:
            existing_tree = api.tree(existing_sha)
            # Allow a branch based on older main, but reject unrelated edits.
            # Compare against its own parent, which our commits always set to main.
            commit = api.get(f"git/commits/{existing_sha}")
            if len(commit["parents"]) != 1:
                raise Invalid("Submission branch has unsupported history; ask SC to resolve it")
            parent_tree = api.tree(commit["parents"][0]["sha"])
            if changed_files(parent_tree, existing_tree) != {path} or path in parent_tree:
                raise Invalid("Submission branch contains unrelated changes; ask SC to resolve it")
            existing_record = api.reader(existing_tree)(path)
            unchanged = existing_record == project_yaml(record) and commit["parents"][0]["sha"] == base_sha
        else:
            unchanged = False
        if fresh(api, issue, base_sha, ctx, teams) != record:
            raise Invalid("Requester permissions changed; save the Issue again to retry")
        if unchanged:
            head_sha = existing_sha
        else:
            base_commit = api.get(f"git/commits/{base_sha}")
            tree = api.request("POST", "git/trees", {"base_tree": base_commit["tree"]["sha"],
                "tree": [{"path": path, "mode": "100644", "type": "blob", "content": project_yaml(record)}]})
            commit = api.request("POST", "git/commits", {"message": f"Project proposal from Issue #{issue_number}",
                "tree": tree["sha"], "parents": [base_sha]})
            head_sha = commit["sha"]
            # Publish pending before attaching this commit to the PR branch.
            api.status(head_sha, "pending", "Awaiting independent project policy validation")
            fresh(api, issue, base_sha, ctx, teams)
            if existing_sha:
                current_sha = api.get(f"git/ref/heads/{branch}")["object"]["sha"]
                if current_sha != existing_sha:
                    raise Invalid("Submission branch changed before publication; retry")
                api.request("PATCH", f"git/refs/heads/{branch}", {"sha": head_sha, "force": True})
            else:
                api.request("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": head_sha})
        body = (f"Project proposal from Issue #{issue_number}.\n\n"
                "The policy check verifies the original Issue author and the current Issue answers. "
                "Review the generated YAML and scientific content before accepting.\n\n"
                f"Closes #{issue_number}\n")
        if warning:
            body += "\n**Repository lookup:** " + warning + "\n"
        title = ("New project: " + record["name"])[:256]
        if pr:
            pr = api.request("PATCH", f"pulls/{pr['number']}", {"title": title, "body": body})
        else:
            pr = api.request("POST", "pulls", {"title": title,
                "head": branch, "base": "main", "body": body})
        # GITHUB_TOKEN-created PRs do not reliably start pull_request_target.
        # Explicit dispatch runs trusted main code and publishes a status on
        # the actual PR head (not on the workflow's main-branch commit).
        api.request("POST", "actions/workflows/project-policy.yml/dispatches",
                    {"ref": "main", "inputs": {"pr_number": str(pr["number"])}})
        message = f"**Proposal validated.** Review PR: https://github.com/{api.repo}/pull/{pr['number']}\n\n"
        message += "SC approval is still required. To correct the proposal, edit this Issue's description and save; the same PR will be updated."
        if warning:
            message += "\n\n**Note:** " + warning
        feedback(api, issue_number, message)
        return "updated" if existing_sha else "created"
    except Invalid as exc:
        if pr:
            current = api.get(f"pulls/{pr['number']}")
            api.status(current["head"]["sha"], "failure", "Correct the submission Issue before this PR can be merged")
        message = "**Your proposal needs correction.**\n\n" + "\n".join("- " + line for line in str(exc).splitlines())
        message += "\n\nOn the original Issue description, select **⋯ → Edit**, correct the answers beneath the headings, and **Save**. Validation will run automatically. A separate comment does not change the proposal."
        feedback(api, issue_number, message)
        return "invalid"


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
        # Construct the membership client after marking an existing PR pending
        # so missing credentials cannot leave a stale successful status behind.
        teams = TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", "")) if os.environ.get("SC_TEAM_READ_TOKEN") else None
        if teams is None:
            pr = associated_pr(api, issue_number)
            if pr and pr["state"] == "open":
                api.status(pr["head"]["sha"], "failure", "Membership credential is missing; SC must fix the automation")
            raise Invalid("SC must configure the SC_TEAM_READ_TOKEN Actions secret")
        result = process(api, teams, issue_number)
        print(json.dumps({"issue": issue_number, "result": result}))
        return 0
    except (urllib.error.HTTPError, Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        message = f"GitHub API error (HTTP {exc.code})" if isinstance(exc, urllib.error.HTTPError) else "Submission automation could not complete"
        print(json.dumps({"error": message}), file=sys.stderr)
        if api is not None and issue_number is not None:
            try:
                pr = associated_pr(api, issue_number)
                if pr and pr["state"] == "open":
                    api.status(pr["head"]["sha"], "failure", "Automation failed; SC must retry or fix the workflow")
                feedback(api, issue_number, "**Submission automation could not complete.** No proposal is approved. Ask SC to inspect the failed Actions run and rerun it. Your answers are retained in this Issue.")
            except (OSError, ValueError, Invalid, KeyError):
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
