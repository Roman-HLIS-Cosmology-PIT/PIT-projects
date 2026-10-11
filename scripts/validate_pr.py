"""Validate PR data with code/policy from its trusted base commit. Read-only."""
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

from scripts.project_registry import (
    Invalid, PROJECT_PATH, USERNAME, authorize_change, load_context, local_reader, parse_yaml, validate_record,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, repo, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise Invalid("Invalid repository identifier")
        self.repo = repo
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, endpoint):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}/{endpoint}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "project-management-validator"},
        )
        with self.opener.open(request, timeout=30) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            raise Invalid("GitHub response exceeded size limit")
        return json.loads(raw)

    def tree(self, sha):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Invalid("Invalid commit SHA")
        tree = self.get(f"git/trees/{sha}?recursive=1")
        if tree.get("truncated") or len(tree["tree"]) > 10000:
            raise Invalid("Repository tree is too large to validate completely")
        return {item["path"]: item for item in tree["tree"]}

    def reader(self, tree):
        cache = {}

        def read(path):
            if path not in tree:
                raise Invalid(f"Required data file is missing: {path}")
            item = tree[path]
            if item["type"] != "blob" or item["mode"] != "100644":
                raise Invalid("Data files must be ordinary nonexecutable files")
            if item.get("size", 0) > 1024 * 1024:
                raise Invalid("Data file exceeds the 1 MiB limit")
            if path not in cache:
                blob = self.get(f"git/blobs/{item['sha']}")
                if blob["encoding"] != "base64" or blob["size"] > 1024 * 1024:
                    raise Invalid("Unsupported or oversized Git blob")
                # GitHub wraps base64 at line boundaries.
                encoded = "".join(blob["content"].split())
                cache[path] = base64.b64decode(encoded, validate=True).decode("utf-8")
            return cache[path]
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


def records(tree, read, ctx):
    result = {}
    for path in sorted(tree):
        if path.startswith("projects/") and path != "projects/.gitkeep":
            if not PROJECT_PATH.fullmatch(path):
                raise Invalid("Unsupported file/directory in projects/")
            result[path] = parse_yaml(read(path))
            validate_record(result[path], path, ctx)
    # An empty registry retains its directory using .gitkeep.
    if "projects" not in tree or tree["projects"]["type"] != "tree":
        raise Invalid("projects/ must be present as a directory")
    expected = {f"config/deliverables/{wg}.yaml" for wg in ctx.deliverables}
    actual = {p for p in tree if p.startswith("config/deliverables/")}
    if actual != expected:
        raise Invalid("Deliverable catalogue files must match the configured working groups")
    return result


def validate_snapshot(base_tree, head_tree, base_read, head_read, author, *, sc_lookup):
    base_ctx = load_context(base_read)
    # Phase 1 has no submission bridge. Do not trust bots' PR bodies, labels,
    # YAML usernames or workflow dispatch payloads as authenticated requesters.
    if not sc_lookup(*base_ctx.sc_team, author):
        raise Invalid("Stage 1 accepts direct SC-authored PRs only; intake authentication is not installed")
    proposed_ctx = load_context(head_read)
    old = records(base_tree, base_read, base_ctx)
    new = records(head_tree, head_read, proposed_ctx)
    for path in set(old) | set(new):
        if old.get(path) != new.get(path):
            authorize_change(old.get(path), new.get(path), author, base_ctx, organization_member=True, is_sc=True)
    return len(new)


def main():
    try:
        team_api = TeamDirectory(os.environ.get("SC_TEAM_READ_TOKEN", ""))
        if sys.argv[1:] == ["--check-team"]:
            ctx = load_context(local_reader(Path.cwd()))
            team_api.check_team(*ctx.sc_team)
            print("Configured SC team is accessible; no membership roster was stored.")
            return 0
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        number = event["pull_request"]["number"]
        if type(number) is not int or number < 1:
            raise Invalid("Invalid PR number")
        pr = api.get(f"pulls/{number}")
        if pr["base"]["repo"]["full_name"] != api.repo or pr["base"]["ref"] != "main":
            raise Invalid("Project PRs must target this repository's main branch")
        if pr["head"]["repo"]["full_name"] != api.repo:
            raise Invalid("Stage 1 uses SC branches in this repository, not fork PRs")
        for side in ("base", "head"):
            if event["pull_request"][side]["sha"] != pr[side]["sha"]:
                raise Invalid("PR changed while the check was starting; rerun the check")
        checkout_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checkout_sha != pr["base"]["sha"]:
            raise Invalid("Validator must run from the exact trusted PR base commit")
        base_tree = api.tree(pr["base"]["sha"])
        head_tree = api.tree(pr["head"]["sha"])
        base_read = api.reader(base_tree)
        ctx = load_context(base_read)
        if ctx.sc_team[0].lower() != api.repo.split("/", 1)[0].lower():
            raise Invalid("SC team must belong to this repository's organization")
        count = validate_snapshot(base_tree, head_tree, base_read, api.reader(head_tree), pr["user"]["login"], sc_lookup=team_api.is_active_member)
        latest = api.get(f"pulls/{number}")
        if any(latest[side]["sha"] != pr[side]["sha"] for side in ("base", "head")):
            raise Invalid("PR changed during validation; rerun the check")
        if not team_api.is_active_member(*ctx.sc_team, pr["user"]["login"]):
            raise Invalid("PR author is no longer an active member of the SC team")
        print(f"Project policy passed: {count} project(s) in the proposed registry.")
        print("Only data was read from the proposed commit; its code/workflows were not executed.")
        return 0
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": f"GitHub read failed (HTTP {exc.code})"}), file=sys.stderr)
    except (Invalid, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
