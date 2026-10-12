"""Optional checks for authoritative timestamps and the single-PR submission path."""
from copy import deepcopy
from pathlib import Path
import unittest

import yaml

from project_workflow import issue_intake
from project_workflow.project_registry import Invalid, load_context, parse_yaml, validate_record
from project_workflow.submission import issue_version, project_path, project_yaml, proposal, validate_bot_snapshot
from test_generation import GenerationAPI
from test_issue_submission import BASE_SHA, Teams
from test_submission_updates import current_issue


class SubmissionDateTests(unittest.TestCase):
    def setUp(self):
        self.api = GenerationAPI()
        self.api.pr = None
        self.api.issue = current_issue()
        self.ctx = load_context(self.api.base.__getitem__, paths=self.api.base)

    def submit(self):
        return issue_intake.process(self.api, Teams(), 12, repo_check=lambda _: None)

    def test_initial_pr_contains_original_issue_time_and_only_one_project(self):
        self.assertEqual(self.submit(), "created")
        record = parse_yaml(self.api.data[self.api.pr["head"]["sha"]][project_path(12)])
        self.assertEqual(record["submission"]["submitted_at"], self.api.issue["created_at"])
        self.assertNotIn("acceptance", record)
        body = self.api.pr["body"]
        self.assertIn("Submitted at (UTC)", body)
        self.assertIn(self.api.issue["created_at"], body)
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ("POST", "pulls")]), 1)

    def test_issue_edits_and_retries_preserve_original_date(self):
        self.submit()
        self.api.issue = current_issue(title="Corrected title", summary="Corrected summary")
        self.api.issue["updated_at"] = "2026-10-12T01:00:00Z"
        self.assertEqual(self.submit(), "updated")
        sha = self.api.pr["head"]["sha"]
        record = parse_yaml(self.api.data[sha][project_path(12)])
        self.assertEqual(record["submission"]["submitted_at"], "2026-10-11T06:00:00Z")
        self.assertEqual(self.submit(), "updated")
        self.assertEqual(self.api.pr["head"]["sha"], sha)
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ("POST", "pulls")]), 1)

    def test_missing_or_invalid_issue_time_is_rejected(self):
        for value in (None, True, "2026-02-30T00:00:00Z", "2026-10-11", "2026-10-11T06:00:00+00:00"):
            issue = {**self.api.issue, "created_at": value}
            with self.subTest(value=value), self.assertRaises(Invalid):
                proposal(issue, self.ctx, organization_member=True, is_sc=False)

    def test_forged_timestamp_blocks_bot_pr(self):
        self.submit()
        head = self.api.data[self.api.pr["head"]["sha"]]
        record = parse_yaml(head[project_path(12)])
        record["submission"]["submitted_at"] = "2000-01-01T00:00:00Z"
        head[project_path(12)] = project_yaml(record)
        base_tree, head_tree = self.api.tree(BASE_SHA), self.api.tree(self.api.pr["head"]["sha"])
        with self.assertRaises(Invalid):
            validate_bot_snapshot(base_tree, head_tree, self.api.reader(base_tree), self.api.reader(head_tree),
                                  self.api.pr, self.api.issue, self.ctx, organization_member=True, is_sc=False,
                                  input_values=issue_intake.revisions.input_values(self.api, self.api.issue, self.ctx))

    def test_creation_time_is_part_of_freshness_check(self):
        changed = {**self.api.issue, "created_at": "2026-10-10T06:00:00Z"}
        self.assertNotEqual(issue_version(self.api.issue), issue_version(changed))

    def test_incomplete_provenance_and_acceptance_metadata_are_rejected(self):
        record = proposal(self.api.issue, self.ctx, organization_member=True, is_sc=False)
        for key in ("submitted_at", "issue_title_sha256"):
            invalid = deepcopy(record)
            del invalid["submission"][key]
            with self.subTest(key=key), self.assertRaises(Invalid):
                validate_record(invalid, project_path(12), self.ctx)
        invalid = {**record, "acceptance": {"accepted_at": "2026-10-11T08:00:00Z"}}
        with self.assertRaises(Invalid):
            validate_record(invalid, project_path(12), self.ctx)

    def test_merged_submission_is_not_rewritten_or_followed_by_another_pr(self):
        self.submit()
        self.api.pr.update(state="closed", merged=True, merged_at="2026-10-11T08:00:00Z")
        before = deepcopy(self.api.writes)
        self.assertEqual(self.submit(), "already-merged")
        self.assertEqual(self.api.writes, before)
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.load((root / ".github/workflows/projects.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["jobs"]), {"submit", "validate"})
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["operation"]["options"], ["validate", "submit"])
        self.assertNotIn("closed", workflow["on"]["pull_request_target"]["types"])
        self.assertFalse((root / "project_workflow/acceptance.py").exists())


if __name__ == "__main__":
    unittest.main()
