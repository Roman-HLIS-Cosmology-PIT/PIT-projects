"""Record actual project-creation merge metadata through a separate review PR.

Never approve/merge PRs or write main directly. Read historical approved data;
closed submission Issues are not reparsed or used to rewrite accepted projects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error

from project_workflow.project_registry import Invalid, PROJECT_PATH, USERNAME, load_context, parse_yaml, validate_record
from project_workflow.submission import branch_issue, changed_files, is_submission_bot, number, project_path, project_yaml
from project_workflow.dispatch import validate_pr as dispatch_validation
from project_workflow.review import request_review
from project_workflow.validate_pr import GitHub, TeamDirectory, records

BRANCH_PREFIX = "project-acceptances/pr-"


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise Invalid("Acceptance time must be an ISO 8601 UTC timestamp ending in Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise Invalid("Acceptance time is not a valid calendar date/time") from exc
    return value


def branch_source(branch):
    match = re.fullmatch(re.escape(BRANCH_PREFIX) + r"([1-9][0-9]*)", branch or "")
    if not match:
        raise Invalid("Unsupported acceptance metadata branch")
    return number(int(match[1]))


def merge_version(pr):
    return (pr["number"], pr.get("merged"), pr.get("merged_at"), pr.get("merge_commit_sha"),
            (pr.get("merged_by") or {}).get("login"), pr["head"]["sha"])


def source_projects(api, source, ctx, teams):
    if (source.get("state") != "closed" or source.get("merged") is not True
            or not source.get("merged_at")):
        raise Invalid("Acceptance metadata requires a merged project-creation PR")
    if source["base"]["repo"]["full_name"] != api.repo or source["base"]["ref"] != "main":
        raise Invalid("The creation PR must have been merged into this repository's main")
    if source["head"]["repo"]["full_name"] != api.repo:
        raise Invalid("The creation PR must use a branch in this repository")
    files = api.pages(f"pulls/{number(source['number'])}/files")
    paths = [f["filename"] for f in files if f["status"] == "added" and PROJECT_PATH.fullmatch(f["filename"])]
    if not paths:
        return {}, None
    if len(paths) != len(set(paths)):
        raise Invalid("Repeated project path in creation PR")
    org = api.repo.split("/", 1)[0]
    if ctx.sc_team[0].lower() != org.lower():
        raise Invalid("SC team must belong to the repository organization")
    if is_submission_bot(source["user"]):
        expected = project_path(branch_issue(source["head"]["ref"]))
        if len(files) != 1 or paths != [expected]:
            raise Invalid("An Issue submission PR may create only its associated project")
    elif not teams.is_active_member(*ctx.sc_team, source["user"]["login"]):
        raise Invalid("A manual creation PR must have an active SC author")
    merger = (source.get("merged_by") or {}).get("login")
    if not isinstance(merger, str) or len(merger) > 39 or not USERNAME.fullmatch(merger):
        raise Invalid("The original creation PR must have a recorded human GitHub merger")
    metadata = {"accepted_at": timestamp(source["merged_at"]), "pull_request": source["number"], "merged_by": merger}
    approved_tree = api.tree(source["head"]["sha"])
    merged_tree = api.tree(source["merge_commit_sha"])
    approved_read, merged_read = api.reader(approved_tree), api.reader(merged_tree)
    result = {}
    for path in paths:
        record = parse_yaml(approved_read(path))
        if "acceptance" in record:
            raise Invalid("Creation PRs must not predeclare their own acceptance timestamp")
        validate_record(record, path, ctx)
        if parse_yaml(merged_read(path)) != record:
            raise Invalid("Creation PR data does not match the actual merge commit")
        if is_submission_bot(source["user"]):
            if record.get("submission", {}).get("issue_number") != branch_issue(source["head"]["ref"]):
                raise Invalid("Creation PR provenance does not match its submission branch")
        result[path] = record
    return result, metadata


def acceptance_changes(api, source, base_tree, base_read, ctx, teams):
    created, metadata = source_projects(api, source, ctx, teams)
    updates = {}
    for path, original in created.items():
        current = parse_yaml(base_read(path))
        validate_record(current, path, ctx)
        if current["project_id"] != original["project_id"] or current.get("submission") != original.get("submission"):
            raise Invalid("The current project is no longer bound to this original creation PR")
        if "acceptance" in current:
            if current["acceptance"] != metadata:
                raise Invalid("Existing acceptance metadata conflicts with the original merge; it will not be overwritten")
            continue
        updates[path] = {**current, "acceptance": deepcopy(metadata)}
    return updates


def validate_acceptance_snapshot(api, base_tree, head_tree, base_read, head_read, pr, ctx, teams):
    if not is_submission_bot(pr["user"]):
        raise Invalid("Automated acceptance PR must be authored by GitHub Actions")
    source = api.get(f"pulls/{branch_source(pr['head']['ref'])}")
    expected = acceptance_changes(api, source, base_tree, base_read, ctx, teams)
    if not expected or changed_files(base_tree, head_tree) != set(expected):
        raise Invalid("Acceptance PR may change only the metadata of projects created by its original merged PR")
    for path, record in expected.items():
        if parse_yaml(head_read(path)) != record:
            raise Invalid("Acceptance PR changed project content or supplied incorrect merge metadata")
        validate_record(record, path, ctx)
    return expected


def process(api, teams, source_number):
    source_number = number(source_number)
    source = api.get(f"pulls/{source_number}")
    if source.get("merged") is not True:
        return "not-merged"
    if source["head"]["ref"].startswith(BRANCH_PREFIX):
        return "metadata-pr"
    base_sha = api.get("git/ref/heads/main")["object"]["sha"]
    base_tree = api.tree(base_sha)
    base_read = api.reader(base_tree)
    ctx = load_context(base_read, paths=base_tree)
    records(base_tree, base_read, ctx)
    updates = acceptance_changes(api, source, base_tree, base_read, ctx, teams)
    if not updates:
        return "already-recorded-or-no-new-projects"
    branch = BRANCH_PREFIX + str(source_number)
    owner = api.repo.split("/", 1)[0]
    candidates = api.pages(f"pulls?state=all&head={owner}:{branch}&base=main")
    candidates = [p for p in candidates if p["head"]["ref"] == branch
                  and p["head"].get("repo", {}).get("full_name") == api.repo]
    if len(candidates) > 1 or (candidates and not is_submission_bot(candidates[0]["user"])):
        raise Invalid("The reserved acceptance branch has unexpected PRs")
    pr = candidates[0] if candidates else None
    if pr and pr["state"] != "open":
        raise Invalid("Acceptance PR is closed; inspect/reopen that same PR instead of creating a duplicate")
    if pr:
        api.status(pr["head"]["sha"], "pending", "Revalidating acceptance metadata")
    try:
        existing_sha = api.get(f"git/ref/heads/{branch}")["object"]["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        existing_sha = None
    if pr and existing_sha != pr["head"]["sha"]:
        raise Invalid("Acceptance branch changed during processing; rerun")
    unchanged = False
    if existing_sha:
        commit = api.get(f"git/commits/{existing_sha}")
        if len(commit["parents"]) != 1:
            raise Invalid("Acceptance branch has unsupported history")
        parent_sha = commit["parents"][0]["sha"]
        parent_tree, existing_tree = api.tree(parent_sha), api.tree(existing_sha)
        parent_read, existing_read = api.reader(parent_tree), api.reader(existing_tree)
        original_updates = acceptance_changes(api, source, parent_tree, parent_read, ctx, teams)
        if changed_files(parent_tree, existing_tree) != set(original_updates):
            raise Invalid("Acceptance branch contains unrelated edits; it will not be overwritten")
        if any(parse_yaml(existing_read(p)) != r for p, r in original_updates.items()):
            raise Invalid("Acceptance branch contains manual edits; it will not be overwritten")
        unchanged = parent_sha == base_sha and all(existing_read(p) == project_yaml(r) for p, r in updates.items())
    if merge_version(api.get(f"pulls/{source_number}")) != merge_version(source):
        raise Invalid("Original PR changed during acceptance processing")
    if api.get("git/ref/heads/main")["object"]["sha"] != base_sha:
        raise Invalid("Main changed during processing; rerun to rebuild the metadata PR")
    if unchanged:
        head_sha = existing_sha
    else:
        base_commit = api.get(f"git/commits/{base_sha}")
        new_tree = api.request("POST", "git/trees", {"base_tree": base_commit["tree"]["sha"],
            "tree": [{"path": p, "mode": "100644", "type": "blob", "content": project_yaml(r)} for p, r in updates.items()]})
        new_commit = api.request("POST", "git/commits", {"message": f"Record project acceptance from PR #{source_number}",
            "tree": new_tree["sha"], "parents": [base_sha]})
        head_sha = new_commit["sha"]
        api.status(head_sha, "pending", "Awaiting independent acceptance metadata validation")
        if api.get("git/ref/heads/main")["object"]["sha"] != base_sha:
            raise Invalid("Main changed before publication; rerun")
        if existing_sha:
            if api.get(f"git/ref/heads/{branch}")["object"]["sha"] != existing_sha:
                raise Invalid("Acceptance branch changed before publication; rerun")
            api.request("PATCH", f"git/refs/heads/{branch}", {"sha": head_sha, "force": True})
        else:
            api.request("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": head_sha})
    metadata = next(iter(updates.values()))["acceptance"]
    body = (f"## Record project acceptance\n\nOriginal creation PR: https://github.com/{api.repo}/pull/{source_number}\n\n"
            f"- Accepted at: **{metadata['accepted_at']}** (UTC)\n"
            f"- Merged by: [{metadata['merged_by']}](https://github.com/{metadata['merged_by']})\n\n"
            "This PR adds only verified acceptance metadata. It preserves the current project content. "
            "The timestamp refers to the original project acceptance, not to this metadata PR.\n\n"
            + "\n".join(f"- `{p}`" for p in sorted(updates)) + "\n")
    payload = {"title": f"Record project acceptance from PR #{source_number}", "body": body}
    pr = api.request("PATCH", f"pulls/{pr['number']}", payload) if pr else api.request("POST", "pulls", {**payload, "head": branch, "base": "main"})
    request_review(api, pr, ctx)
    dispatch_validation(api, pr["number"])
    return "updated" if existing_sha else "created"


def main():
    api, pr = None, None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        supplied = os.environ.get("ACCEPTANCE_PR_NUMBER", "")
        source_number = number(int(supplied)) if supplied else number(event["pull_request"]["number"])
        api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checkout != api.get("git/ref/heads/main")["object"]["sha"]:
            raise Invalid("Acceptance code must run from current main; rerun")
        # Invalidate an existing pending PR before checking membership secrets.
        candidates = api.pages(f"pulls?state=open&head={api.repo.split('/')[0]}:{BRANCH_PREFIX}{source_number}&base=main")
        for candidate in candidates:
            if candidate["head"].get("repo", {}).get("full_name") == api.repo and is_submission_bot(candidate["user"]):
                pr = candidate
                api.status(pr["head"]["sha"], "pending", "Revalidating acceptance metadata")
        result = process(api, TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", "")), source_number)
        print(json.dumps({"creation_pr": source_number, "result": result}))
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        # Never print HTTP response bodies or any credential.
        message = f"GitHub API error (HTTP {exc.code})" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        print(json.dumps({"error": message}), file=sys.stderr)
        if api is not None and pr is not None:
            try:
                current = api.get(f"pulls/{pr['number']}")
                api.status(current["head"]["sha"], "failure", "Acceptance recording failed; inspect and rerun")
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
