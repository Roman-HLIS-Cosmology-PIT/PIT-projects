"""Synchronize native GitHub files on main or an SC PR, using trusted code."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
import urllib.error

from project_workflow.generated_files import OUTPUTS, relevant, updates_for, validate_outputs
from project_workflow.dispatch import validate_pr as dispatch_validation
from project_workflow.project_registry import Invalid, load_context
from project_workflow.submission import number, changed_files, is_submission_bot
from project_workflow.validate_pr import GitHub, TeamDirectory, records

GENERATION_BRANCH = "project-config/generated"


def commit_updates(api, parent, updates):
    if not updates or not set(updates) <= OUTPUTS:
        raise Invalid("Generation may change only the native form and CODEOWNERS")
    tree_sha = api.get(f"git/commits/{parent}")["tree"]["sha"]
    tree = api.request("POST", "git/trees", {"base_tree": tree_sha, "tree": [
        {"path": path, "mode": "100644", "type": "blob", "content": text}
        for path, text in sorted(updates.items())]})
    return api.request("POST", "git/commits", {
        "message": "Regenerate GitHub project files from configuration", "tree": tree["sha"], "parents": [parent]})


def generated_pr(api, *, state="open"):
    owner = api.repo.split("/", 1)[0]
    candidates = api.pages(f"pulls?state={state}&head={owner}:{GENERATION_BRANCH}&base=main")
    matches = [pr for pr in candidates if pr["head"].get("repo", {}).get("full_name") == api.repo
               and pr["head"]["ref"] == GENERATION_BRANCH]
    if len(matches) > 1 and state == "open":
        raise Invalid("Multiple synchronization PRs use the reserved branch")
    if any(not is_submission_bot(pr["user"]) for pr in matches):
        raise Invalid("The reserved generation branch is used by a human PR; do not overwrite it")
    return matches[0] if matches else None


def validate_generated_snapshot(api, base_tree, head_tree, base_read, head_read, pr, ctx):
    if pr["head"]["ref"] != GENERATION_BRANCH or not is_submission_bot(pr["user"]):
        raise Invalid("Unrecognized generated-file PR")
    commit = api.get(f"git/commits/{pr['head']['sha']}")
    if len(commit["parents"]) != 1 or commit["parents"][0]["sha"] != pr["base"]["sha"]:
        raise Invalid("Synchronization PR must be refreshed onto current main")
    changes = changed_files(base_tree, head_tree)
    expected = updates_for(base_read, base_tree, ctx)
    if changes != set(expected) or not changes <= OUTPUTS:
        raise Invalid("Synchronization PR may contain only the current required generated outputs")
    validate_outputs(head_read, head_tree, ctx)


def protection_rejection(exc):
    if exc.code not in {403, 422}:
        return False
    # Inspect a bounded API message without printing its body or credentials.
    try:
        body = exc.read(65536).decode("utf-8", errors="replace")
    except (OSError, AttributeError):
        body = ""
    message = (str(exc.reason) + " " + body).lower()
    return any(token in message for token in ("protected branch", "repository rule", "gh006", "gh013", "through a pull request"))


def refresh_generated_pr(api, ctx, main_sha, commit):
    from project_workflow.review import request_review
    pr = generated_pr(api)
    endpoint = "git/ref/heads/" + GENERATION_BRANCH
    try:
        existing = api.get(endpoint)["object"]["sha"]
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        existing = None
    if existing:
        previous = generated_pr(api, state="all")
        old_commit = api.get(f"git/commits/{existing}")
        if len(old_commit["parents"]) != 1:
            raise Invalid("Generation branch has unrelated history")
        prior_tree, prior_head = api.tree(old_commit["parents"][0]["sha"]), api.tree(existing)
        if not changed_files(prior_tree, prior_head) <= OUTPUTS:
            raise Invalid("Generation branch contains unrelated changes")
        if previous is None:
            # Recover if publication stopped after creating the reserved branch
            # but before opening its PR. Only the exact current output is allowed.
            if old_commit["parents"][0]["sha"] != main_sha:
                raise Invalid("Unverified reserved branch is not based on current main")
            current_tree = api.tree(main_sha)
            current_read = api.reader(current_tree)
            if changed_files(current_tree, prior_head) != set(updates_for(current_read, current_tree, ctx)):
                raise Invalid("Unverified reserved branch has unexpected changes")
            validate_outputs(api.reader(prior_head), prior_head, ctx)
    if api.get("git/ref/heads/main")["object"]["sha"] != main_sha:
        raise Invalid("Main changed before synchronization publication; rerun maintenance")
    api.status(commit["sha"], "pending", "Checking generated outputs against accepted configuration")
    if existing and existing != commit["sha"]:
        if api.get(endpoint)["object"]["sha"] != existing:
            raise Invalid("Generation branch advanced; rerun maintenance")
        # This reserved bot branch is rebased onto current main; other PRs append only.
        api.request("PATCH", "git/refs/heads/" + GENERATION_BRANCH, {"sha": commit["sha"], "force": True})
    elif existing is None:
        api.request("POST", "git/refs", {"ref": "refs/heads/" + GENERATION_BRANCH, "sha": commit["sha"]})
    payload = {"title": "Synchronize generated project files", "body":
        "Update the Issue Form and CODEOWNERS from `project_workflow/config.yaml` and `working_groups/` already accepted on main.\n\n"
        "Main protection prevented a direct generation commit. This PR contains only the required generated files; "
        "review and merge it to finish synchronization. No project record, source configuration or workflow is changed.\n"}
    pr = api.request("PATCH", f"pulls/{pr['number']}", payload) if pr else api.request("POST", "pulls", {**payload, "head": GENERATION_BRANCH, "base": "main"})
    request_review(api, pr, ctx)
    dispatch_validation(api, pr["number"])
    return "synchronization-pr"


def synchronize_main(api, *, checkout_sha=None):
    """Accepted main is trusted; generated output writes are still narrowly scoped."""
    main_sha = api.get("git/ref/heads/main")["object"]["sha"]
    if checkout_sha is not None and main_sha != checkout_sha:
        raise Invalid("Maintenance must execute current main code; rerun")
    tree = api.tree(main_sha)
    read = api.reader(tree)
    ctx = load_context(read, paths=tree)
    if ctx.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
        raise Invalid("SC team must belong to the repository organization")
    records(tree, read, ctx)
    updates = updates_for(read, tree, ctx)
    if not updates:
        pr = generated_pr(api)
        if pr:
            api.request("PATCH", f"pulls/{pr['number']}", {"state": "closed"})
        return "unchanged"
    existing_pr = generated_pr(api)
    commit = None
    if existing_pr:
        head_tree = api.tree(existing_pr["head"]["sha"])
        try:
            validate_generated_snapshot(api, tree, head_tree, read, api.reader(head_tree), existing_pr, ctx)
        except Invalid:
            pass  # Accepted config changed; refresh the one reserved bot PR.
        else:
            commit = {"sha": existing_pr["head"]["sha"]}
    if commit is None:
        commit = commit_updates(api, main_sha, updates)
    if api.get("git/ref/heads/main")["object"]["sha"] != main_sha:
        raise Invalid("Main advanced during generation; rerun maintenance")
    try:
        api.request("PATCH", "git/refs/heads/main", {"sha": commit["sha"], "force": False})
    except urllib.error.HTTPError as exc:
        if not protection_rejection(exc):
            raise
        return refresh_generated_pr(api, ctx, main_sha, commit)
    # GITHUB_TOKEN's push does not trigger another workflow. The current
    # maintenance job therefore builds exports and runs health checks itself.
    obsolete = generated_pr(api)
    if obsolete:
        api.request("PATCH", f"pulls/{obsolete['number']}", {"state": "closed"})
    return "updated-main"


def main():
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        supplied = os.environ.get("GENERATION_PR_NUMBER", "")
        pr_number = int(supplied) if supplied else (event.get("pull_request") or {}).get("number")
        if pr_number is not None:
            result = synchronize(api, TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", "")), pr_number, checkout_sha=checkout_sha)
        else:
            result = synchronize_main(api, checkout_sha=checkout_sha)
        print("Repository generation: " + result)
        return 0
    except urllib.error.HTTPError as exc:
        print(f"GitHub request failed (HTTP {exc.code}); inspect access or branch conflicts and rerun", file=sys.stderr)
    except (Invalid, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    return 1


def synchronize(api, teams, pr_number, *, checkout_sha=None):
    pr_number = number(pr_number)
    pr = api.get(f"pulls/{pr_number}")
    if pr.get("state") != "open":
        return "skipped"
    if pr["base"]["repo"]["full_name"] != api.repo or pr["base"]["ref"] != "main":
        raise Invalid("Generation requires an open PR targeting this repository's main")
    if pr["head"]["repo"]["full_name"] != api.repo or pr["head"]["ref"] == "main":
        raise Invalid("Generation only writes existing PR branches in this repository")
    if pr["user"].get("type") != "User":
        return "skipped"  # Submission PRs have their own narrow writer.
    changed = api.pages(f"pulls/{pr_number}/files")
    if not any(relevant(item["filename"]) or relevant(item.get("previous_filename", "")) for item in changed):
        return "skipped"
    main_sha = api.get("git/ref/heads/main")["object"]["sha"]
    if checkout_sha is not None and checkout_sha != main_sha:
        raise Invalid("Generation must execute current trusted main code; rerun the workflow")
    base_tree = api.tree(main_sha)
    base = load_context(api.reader(base_tree), paths=base_tree)
    actor = pr["user"]["login"]
    if base.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
        raise Invalid("SC team must belong to this repository's organization")
    if not teams.is_active_member(*base.sc_team, actor):
        raise Invalid("Only an active SC PR author can regenerate repository files")
    head_sha, branch = pr["head"]["sha"], pr["head"]["ref"]
    head_tree = api.tree(head_sha)
    read = api.reader(head_tree)
    ctx = load_context(read, paths=head_tree)
    if ctx.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
        raise Invalid("Proposed SC team must belong to this repository organization")
    records(head_tree, read, ctx)
    updates = updates_for(read, head_tree, ctx)

    def still_current():
        fresh = api.get(f"pulls/{pr_number}")
        if fresh.get("state") != "open" or fresh["user"] != pr["user"] or any(fresh[side] != pr[side] for side in ("base", "head")):
            raise Invalid("PR changed during generation; rerun the workflow")
        if api.get("git/ref/heads/main")["object"]["sha"] != main_sha:
            raise Invalid("Trusted main changed during generation; rerun the workflow")
        endpoint = "git/ref/heads/" + quote(branch, safe="/")
        if api.get(endpoint)["object"]["sha"] != head_sha:
            raise Invalid("PR branch advanced during generation; rerun the workflow")
        if not teams.is_active_member(*base.sc_team, actor):
            raise Invalid("PR author is no longer an active SC member")

    still_current()
    if updates:
        commit = commit_updates(api, head_sha, updates)
        still_current()
        api.status(commit["sha"], "pending", "Generated files updated; checking current proposal")
        # Append a child of the observed head. Non-fast-forward advances fail.
        api.request("PATCH", "git/refs/heads/" + quote(branch, safe="/"), {"sha": commit["sha"], "force": False})
    # GITHUB_TOKEN commits do not reliably trigger another target workflow.
    # Dispatch also retries validation if an earlier run stopped after publishing.
    dispatch_validation(api, pr_number)
    return "updated" if updates else "unchanged"



if __name__ == "__main__":
    raise SystemExit(main())
