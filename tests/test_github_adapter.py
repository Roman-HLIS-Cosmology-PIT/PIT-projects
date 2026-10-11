"""Offline end-to-end adapter tests. No network or GitHub mutations."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import validate_pr
from test_registry import is_test_sc, project, source_data, snapshot


class AdapterTests(unittest.TestCase):
    def run_adapter(self, author="sc-user", event_head=None, latest_changed=False, revoked=False):
        base_sha = "a" * 40
        head_sha = "b" * 40
        data = source_data()
        proposed = {**data, "projects/P001.yaml": yaml.safe_dump(project())}
        repo = {"full_name": "test-org/project-management"}
        pr = {"number": 1, "user": {"login": author},
              "base": {"sha": base_sha, "ref": "main", "repo": repo},
              "head": {"sha": head_sha, "ref": "proposal/P001", "repo": repo}}
        event_pr = deepcopy(pr)
        if event_head:
            event_pr["head"]["sha"] = event_head
        event = {"pull_request": event_pr, "sender": {"login": "sc-user"}}
        calls = []
        membership_checks = []

        class FakeTeams:
            def __init__(self, token):
                assert token == "dummy-team-token"

            def is_active_member(self, org, slug, username):
                membership_checks.append((org, slug, username))
                return is_test_sc(org, slug, username) and not (revoked and len(membership_checks) > 1)

        class FakeAPI:
            def __init__(self, *args):
                self.repo = repo["full_name"]

            def get(self, endpoint):
                calls.append(endpoint)
                if latest_changed and len(calls) == 2:
                    changed = deepcopy(pr)
                    changed["head"]["sha"] = "c" * 40
                    return changed
                return pr

            def tree(self, sha):
                return snapshot(data if sha == base_sha else proposed)

            def reader(self, tree):
                return (proposed if "projects/P001.yaml" in tree else data).__getitem__

        with tempfile.TemporaryDirectory() as directory:
            event_path = Path(directory) / "event.json"
            event_path.write_text(json.dumps(event))
            environment = {"GITHUB_EVENT_PATH": str(event_path),
                           "GITHUB_REPOSITORY": repo["full_name"], "GITHUB_TOKEN": "dummy",
                           "SC_TEAM_READ_TOKEN": "dummy-team-token"}
            output = io.StringIO()
            with patch.dict(os.environ, environment), patch.object(validate_pr, "GitHub", FakeAPI), \
                 patch.object(validate_pr, "TeamDirectory", FakeTeams), \
                 patch.object(validate_pr.subprocess, "check_output", return_value=base_sha + "\n"), \
                 redirect_stdout(output), redirect_stderr(output):
                result = validate_pr.main()
        return result, output.getvalue()

    def test_sc_pr_end_to_end(self):
        result, output = self.run_adapter()
        self.assertEqual(result, 0, output)
        self.assertIn("1 project(s)", output)

    def test_api_pr_author_overrides_sc_event_sender(self):
        result, output = self.run_adapter(author="mallory")
        self.assertEqual(result, 1)
        self.assertIn("direct SC-authored", output)

    def test_event_head_mismatch_fails_closed(self):
        result, output = self.run_adapter(event_head="c" * 40)
        self.assertEqual(result, 1)
        self.assertIn("changed while", output)

    def test_mid_validation_push_fails_closed(self):
        result, output = self.run_adapter(latest_changed=True)
        self.assertEqual(result, 1)
        self.assertIn("changed during", output)

    def test_sc_removal_during_check_fails_closed(self):
        result, output = self.run_adapter(revoked=True)
        self.assertEqual(result, 1)
        self.assertIn("no longer an active", output)


if __name__ == "__main__":
    unittest.main()
