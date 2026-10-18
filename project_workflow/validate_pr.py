"""Validate PR data using trusted-base code; optionally publish its head status."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

from project_workflow.project_registry import (
    Invalid, PROJECT_PATH, USERNAME, authorize_change, load_context, load_local_context, parse_yaml, validate_record,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_error_message(exc):
    message = f"GitHub API error (HTTP {exc.code})"
    request = getattr(exc, 'api_request', '')
    return message + (f" during {request}" if request else '')


class GitHub:
    def __init__(self, repo, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise Invalid("Invalid repository identifier")
        self.repo = repo
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)
        # Only immutable Git objects are cached. Issues, refs, PRs, comments and
        # membership must be fetched again for every freshness/authorization check.
        self._trees = {}
        self._blobs = {}

    def get(self, endpoint):
        return self.request("GET", endpoint)

    def request(self, method, endpoint, data=None):
        if not isinstance(endpoint, str) or endpoint.startswith("/") or ".." in endpoint.split("/") or "://" in endpoint:
            raise Invalid("Invalid GitHub API endpoint")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}" + (f'/{endpoint}' if endpoint else ''),
            data=json.dumps(data).encode("utf-8") if data is not None else None,
            method=method,
            headers={**({"Authorization": f"Bearer {self.token}"} if self.token else {}),
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "project-management-validator"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read(20 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            # Never print request headers, query arguments, payloads or API bodies.
            # A relative method/path is enough to locate a failing operation.
            path = endpoint.split('?', 1)[0]
            if re.fullmatch(r'[A-Za-z0-9_./%-]+', path):
                exc.api_request = f'{method} {path}'
            raise
        if len(raw) > 20 * 1024 * 1024:
            raise Invalid("GitHub response exceeded size limit")
        return json.loads(raw) if raw else None

    def pages(self, endpoint):
        result = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, 101):
            batch = self.get(f"{endpoint}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise Invalid("Unexpected GitHub list response")
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise Invalid("Too many GitHub records to inspect completely")

    def status(self, sha, state, description):
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or state not in {"pending", "success", "failure", "error"}:
            raise Invalid("Invalid status target")
        payload = {"state": state, "context": "Project policy", "description": description[:140]}
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if run_id.isdigit():
            payload["target_url"] = f"https://github.com/{self.repo}/actions/runs/{run_id}"
        return self.request("POST", f"statuses/{sha}", payload)

    def tree(self, sha):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Invalid("Invalid commit SHA")
        if sha in self._trees:
            return {path: dict(item) for path, item in self._trees[sha].items()}
        tree = self.get(f"git/trees/{sha}?recursive=1")
        if tree.get("truncated") or len(tree["tree"]) > 10000:
            raise Invalid("Repository tree is too large to validate completely")
        result = {item["path"]: item for item in tree["tree"]}
        self._trees[sha] = result
        return {path: dict(item) for path, item in result.items()}

    def reader(self, tree):
        def read(path):
            if path not in tree:
                raise Invalid(f"Required data file is missing: {path}")
            item = tree[path]
            if item["type"] != "blob" or item["mode"] != "100644":
                raise Invalid("Data files must be ordinary nonexecutable files")
            if item.get("size", 0) > 1024 * 1024:
                raise Invalid("Data file exceeds the 1 MiB limit")
            if item['sha'] not in self._blobs:
                blob = self.get(f"git/blobs/{item['sha']}")
                if blob["encoding"] != "base64" or blob["size"] > 1024 * 1024:
                    raise Invalid("Unsupported or oversized Git blob")
                # GitHub wraps base64 at line boundaries.
                encoded = "".join(blob["content"].split())
                self._blobs[item['sha']] = base64.b64decode(encoded, validate=True).decode("utf-8")
            return self._blobs[item['sha']]
        return read


class TeamDirectory:
    """Read-only organization API; token is kept out of repository blob access."""
    def __init__(self, token):
        if not token:
            raise Invalid("Set the SC_TEAM_READ_TOKEN Actions secret with organization Members: Read")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, endpoint):
        request = urllib.request.Request(
            f"https://api.github.com/orgs/{endpoint}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "project-management-team-check"},
        )
        with self.opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise Invalid("Team API response exceeded size limit")
        return json.loads(raw)

    def check_team(self, org, slug):
        if not USERNAME.fullmatch(org) or not USERNAME.fullmatch(slug):
            raise Invalid("Invalid SC team identifier")
        # Check visibility first: a missing/inaccessible team must not be
        # confused with an individual who has no membership.
        self.get(f"{org}/teams/{slug}")

    def is_active_member(self, org, slug, username):
        if not isinstance(username, str) or len(username) > 39 or not USERNAME.fullmatch(username):
            return False
        self.check_team(org, slug)
        try:
            membership = self.get(f"{org}/teams/{slug}/memberships/{username}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        return membership.get("state") == "active" and membership.get("role") in {"member", "maintainer"}

    def is_org_member(self, org, username):
        if not USERNAME.fullmatch(org) or not isinstance(username, str) or len(username) > 39 or not USERNAME.fullmatch(username):
            return False
        # A separate organization lookup detects inaccessible organizations.
        self.get(org)
        try:
            membership = self.get(f"{org}/memberships/{username}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        return membership.get("state") == "active" and membership.get("role") in {"member", "admin"}


def records(tree, read, ctx):
    result = {}
    for path in sorted(tree):
        if path.startswith("projects/") and path != "projects/.gitkeep":
            if path == 'projects/number_registry.yaml':
                continue  # Validated by load_context with ordinary-file reader checks.
            if not PROJECT_PATH.fullmatch(path):
                raise Invalid("Unsupported file/directory in projects/")
            result[path] = parse_yaml(read(path))
            validate_record(result[path], path, ctx)
    # An empty registry retains its directory using .gitkeep.
    if "projects" not in tree or tree["projects"]["type"] != "tree":
        raise Invalid("projects/ must be present as a directory")
    return result


def validate_snapshot(base_tree, head_tree, base_read, head_read, author, *, sc_lookup):
    base_ctx = load_context(base_read, paths=base_tree)
    # This path handles direct SC PRs only. main() separately authenticates
    # Issue-backed bot PRs. Never trust editable bodies/labels as requesters.
    if not sc_lookup(*base_ctx.sc_team, author):
        raise Invalid("Only direct SC-authored PRs or verified Issue submissions are accepted")
    proposed_ctx = load_context(head_read, paths=head_tree)
    from project_workflow.issue_form import FORM_PATHS, validate_form
    for kind, form_path in FORM_PATHS.items():
        if form_path in base_tree or form_path in head_tree:
            validate_form(head_read(form_path), proposed_ctx, kind)
    from project_workflow.generated_files import validate_outputs
    validate_outputs(head_read, head_tree, proposed_ctx)
    old = records(base_tree, base_read, base_ctx)
    new = records(head_tree, head_read, proposed_ctx)
    for path in set(old) | set(new):
        if old.get(path) != new.get(path):
            authorize_change(old.get(path), new.get(path), author, base_ctx, organization_member=True, is_sc=True)
    return len(new)


def skip_closed_pr(pr):
    """A queued check is obsolete after acceptance/rejection, not a failed policy."""
    if pr.get('state', 'open') == 'open' and not pr.get('merged_at'):
        return False
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            output.write('slack_review=false\n')
    print('PR is already closed or merged; delayed policy validation skipped.')
    return True


def main():
    api = None
    status_sha = None
    publish = sys.argv[1:] == ["--publish-status"]
    try:
        if sys.argv[1:] == ["--check-team"]:
            team_api = TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", ""))
            ctx = load_local_context(Path.cwd())
            team_api.check_team(*ctx.sc_team)
            print("Configured SC team is accessible; no membership roster was stored.")
            return 0
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        from project_workflow.submission import branch_issue, is_submission_bot, issue_version, validate_bot_snapshot, workflow_kind
        supplied_number = os.environ.get("POLICY_PR_NUMBER", "")
        number = int(supplied_number) if supplied_number else event["pull_request"]["number"]
        if type(number) is not int or number < 1:
            raise Invalid("Invalid PR number")
        pr = api.get(f"pulls/{number}")
        if skip_closed_pr(pr):
            return 0
        status_sha = pr["head"]["sha"]
        if publish:
            api.status(status_sha, "pending", "Checking current proposal and requester permissions")
        team_api = TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", ""))
        if pr["base"]["repo"]["full_name"] != api.repo or pr["base"]["ref"] != "main":
            raise Invalid("Project PRs must target this repository's main branch")
        if pr["head"]["repo"]["full_name"] != api.repo:
            raise Invalid("Project proposals must use branches in this repository, not forks")
        if not supplied_number:
            for side in ("base", "head"):
                if event["pull_request"][side]["sha"] != pr[side]["sha"]:
                    raise Invalid("PR changed while the check was starting; rerun the check")
        checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checkout_sha != pr["base"]["sha"]:
            raise Invalid("Validator must run from the exact trusted PR base commit")
        base_tree = api.tree(pr["base"]["sha"])
        head_tree = api.tree(pr["head"]["sha"])
        base_read = api.reader(base_tree)
        ctx = load_context(base_read, paths=base_tree)
        if ctx.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
            raise Invalid("SC team must belong to this repository's organization")
        head_read = api.reader(head_tree)
        issue = None
        record = None
        generation_pr = False
        if is_submission_bot(pr["user"]):
            from project_workflow.sync_generated import GENERATION_BRANCH, validate_generated_snapshot
            if pr["head"]["ref"] == GENERATION_BRANCH:
                generation_pr = True
                validate_generated_snapshot(api, base_tree, head_tree, base_read, head_read, pr, ctx)
            else:
                issue = api.get(f"issues/{branch_issue(pr['head']['ref'])}")
                actor = issue["user"]["login"]
                record = validate_bot_snapshot(base_tree, head_tree, base_read, head_read, pr, issue, ctx,
                    organization_member=team_api.is_org_member(ctx.sc_team[0], actor),
                    is_sc=team_api.is_active_member(*ctx.sc_team, actor), api=api, teams=team_api)
            count = len(records(head_tree, head_read, ctx))
        else:
            count = validate_snapshot(base_tree, head_tree, base_read, head_read, pr["user"]["login"], sc_lookup=team_api.is_active_member)
        latest = api.get(f"pulls/{number}")
        if skip_closed_pr(latest):
            return 0
        if any(latest[side]["sha"] != pr[side]["sha"] for side in ("base", "head")):
            raise Invalid("PR changed during validation; rerun the check")
        if issue is not None:
            current = api.get(f"issues/{issue['number']}")
            if issue_version(current) != issue_version(issue):
                raise Invalid("Issue changed during validation; rerun the submission workflow")
            actor = issue["user"]["login"]
            validate_bot_snapshot(base_tree, head_tree, base_read, head_read, pr, current, ctx,
                organization_member=team_api.is_org_member(ctx.sc_team[0], actor),
                is_sc=team_api.is_active_member(*ctx.sc_team, actor), api=api, teams=team_api)
        elif generation_pr:
            validate_generated_snapshot(api, base_tree, head_tree, base_read, head_read, pr, ctx)
        elif not team_api.is_active_member(*ctx.sc_team, pr["user"]["login"]):
            raise Invalid("PR author is no longer an active member of the SC team")
        if publish:
            api.status(status_sha, "success", "Current proposal and requester permissions passed")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                review = record is not None and workflow_kind(record) in {"EP", "CLAIM"}
                output.write(f"slack_review={str(review).lower()}\n")
        print(f"Project policy passed: {count} project(s) in the proposed registry.")
        print("Only data was read from the proposed commit; its code/workflows were not executed.")
        return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": http_error_message(exc)}), file=sys.stderr)
    except (Invalid, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    if publish and api is not None and status_sha is not None:
        try:
            if skip_closed_pr(api.get(f'pulls/{number}')):
                return 0
        except (OSError, ValueError, Invalid):
            pass  # An unavailable live read cannot turn a real policy failure into success.
        try:
            api.status(status_sha, "failure", "Policy validation failed; see the workflow log or submission Issue")
        except (OSError, ValueError, Invalid):
            print("Could not publish failure status; a pending/missing status must block merging.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
