"""Behavior and security checks for the form, review routing and merge metadata."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import acceptance, issue_intake, validate_pr
from scripts.issue_form import CURRENT_FIELD_LABELS, form, generated_text, matches_form
from scripts.project_registry import Invalid, authorize_change, load_context, parse_yaml, validate_record
from scripts.review import proposal_body
from scripts.submission import issue_version, project_path, project_yaml, proposal
from test_issue_submission import BOT, BASE_SHA, FakeAPI, Teams, issue_fixture
from test_registry import source_data


def current_issue(**changes):
    values = {"kind": "EP", "lead": "Alice Example", "lead_github_username": "alice",
              "working_group": "SCM — Shape and Color Measurement", "deliverable_ids": "SCM / SCM-001 — Test deliverable",
              "phd_thesis": "False", "slack_channel": "_No response_", "summary": "Science description.",
              "github_url": "_No response_", "related_links": "_No response_"}
    for i in range(1, 6):
        values[f"contributor_{i}_name"] = "_No response_"
        values[f"contributor_{i}_github_username"] = "_No response_"
    values.update({k: v for k, v in changes.items() if k != "title"})
    issue = issue_fixture()
    issue["title"] = changes.get("title", "Project from the issue title")
    issue["body"] = "\n\n".join(f"### {CURRENT_FIELD_LABELS[k]}\n\n{values[k]}" for k in CURRENT_FIELD_LABELS)
    return issue


class FormUpdateTests(unittest.TestCase):
    def setUp(self):
        self.data = source_data()
        self.data[".github/CODEOWNERS"] = "* @aguinot\n"
        self.ctx = load_context(self.data.__getitem__, paths=self.data)

    def parse(self, issue=None):
        return proposal(issue or current_issue(), self.ctx, organization_member=True, is_sc=False)

    def test_issue_title_is_the_only_name_and_edit_changes_version(self):
        first = current_issue()
        second = current_issue(title="Renamed project")
        self.assertEqual(self.parse(first)["name"], first["title"])
        self.assertNotEqual(issue_version(first), issue_version(second))
        self.assertNotEqual(self.parse(first)["submission"], self.parse(second)["submission"])
        self.assertEqual(self.parse(second)["name"], "Renamed project")

    def test_prefix_is_not_required(self):
        self.assertEqual(self.parse(current_issue(title="No prefix needed"))["name"], "No prefix needed")

    def test_five_pairs_can_be_used_and_blank_pairs_are_omitted(self):
        fields = {}
        for i in range(1, 6):
            fields[f"contributor_{i}_name"] = f"Person {i}"
            fields[f"contributor_{i}_github_username"] = f"person-{i}"
        self.assertEqual(len(self.parse(current_issue(**fields))["contributors"]), 5)
        self.assertEqual(self.parse()["contributors"], [])
        self.assertEqual(self.parse(current_issue(contributor_4_name="Bob", contributor_4_github_username="bob"))["contributors"], [{"name": "Bob", "github_username": "bob"}])

    def test_partial_pair_has_numbered_error(self):
        for fields in ({"contributor_3_name": "Bob"}, {"contributor_3_github_username": "bob"}):
            with self.subTest(fields=fields), self.assertRaisesRegex(Invalid, "Contributor 3: fill both"):
                self.parse(current_issue(**fields))

    def test_duplicate_and_lead_usernames_rejected_in_boxes(self):
        for fields in ({"contributor_1_name": "Alice", "contributor_1_github_username": "ALICE"},
                       {"contributor_1_name": "Bob", "contributor_1_github_username": "bob",
                        "contributor_2_name": "Bob again", "contributor_2_github_username": "BOB"}):
            with self.subTest(fields=fields), self.assertRaises(Invalid):
                self.parse(current_issue(**fields))

    def test_contributors_end_form_and_false_is_default(self):
        spec = form(self.ctx)
        self.assertNotIn("title", spec)
        fields = {x["id"]: x for x in spec["body"] if "id" in x}
        self.assertNotIn("name", fields)
        self.assertNotIn("contributors", fields)
        self.assertEqual(fields["lead"]["attributes"]["description"], "Required for EP.")
        self.assertEqual(fields["phd_thesis"]["attributes"]["options"], ["False", "True"])
        self.assertEqual(fields["phd_thesis"]["attributes"]["default"], 0)
        self.assertEqual(spec["body"][-1]["id"], "contributor_5_github_username")
        headings = [x["attributes"]["value"] for x in spec["body"] if x["type"] == "markdown"]
        self.assertTrue(any(x.startswith("## Contributors") for x in headings))
        self.assertEqual(len([x for x in headings if x.startswith("### Contributor ")]), 5)
        self.assertFalse(self.parse()["phd_thesis"])
        self.assertTrue(self.parse(current_issue(phd_thesis="True"))["phd_thesis"])

    def test_missing_pair_heading_is_rejected(self):
        issue = current_issue()
        issue["body"] = issue["body"].replace("### Contributor 2 name", "### Wrong heading")
        with self.assertRaises(Invalid):
            self.parse(issue)

    def test_full_labels_map_to_codes_and_wrong_group_fails(self):
        record = self.parse()
        self.assertEqual(record["working_group"], "SCM")
        self.assertEqual(record["deliverable_ids"], ["SCM-001"])
        with self.assertRaises(Invalid):
            self.parse(current_issue(deliverable_ids="CAS / CAS-001 — Other deliverable"))

    def test_display_titles_with_commas_do_not_break_multi_select(self):
        self.ctx.working_groups["SCM"]["deliverables"].append({"id": "SCM-002", "title": "Shapes, colours"})
        self.ctx.deliverables["SCM"].add("SCM-002")
        record = self.parse(current_issue(deliverable_ids="SCM / SCM-001 — Test deliverable, SCM / SCM-002 — Shapes; colours"))
        self.assertEqual(record["deliverable_ids"], ["SCM-001", "SCM-002"])

    def test_legacy_issues_and_generated_form_remain_valid(self):
        self.assertEqual(proposal(issue_fixture(), self.ctx, organization_member=True, is_sc=False)["name"], "Shape measurement validation")
        self.assertTrue(matches_form(generated_text(self.ctx, legacy=True), self.ctx))
        self.assertTrue(matches_form(generated_text(self.ctx), self.ctx))
        self.assertFalse(matches_form(generated_text(self.ctx).replace("SCM-001", "FAKE-ID"), self.ctx))

    def test_access_configuration_and_legacy_fallback(self):
        self.assertEqual(self.ctx.review_owners, ("@aguinot",))
        self.assertEqual(self.ctx.sc_team, ("test-org", "steering-committee"))
        for value in ("a/b/c", "@a/b", "a/../b", "a/b?query"):
            data = deepcopy(self.data)
            data["config/access.yaml"] = f"steering_committee: {value}\n"
            with self.subTest(value=value), self.assertRaises(Invalid):
                load_context(data.__getitem__, paths=data)
        legacy = deepcopy(self.data)
        del legacy["config/access.yaml"]
        legacy[".github/CODEOWNERS"] = "* @test-org/steering-committee\n"
        self.assertEqual(load_context(legacy.__getitem__, paths=legacy).sc_team, self.ctx.sc_team)

    def test_formatted_pr_has_all_fields_and_no_untrusted_pings_or_closing_references(self):
        record = self.parse(current_issue(contributor_1_name="Bob | @test-org/steering-committee", contributor_1_github_username="bob",
                                         summary="## Science\nNotify @someone. Closes #543.\nReal science.",
                                         related_links="https://example.org/paper"))
        body = proposal_body(record, self.ctx, "test-org/registry", 12)
        for text in ("Shape and Color Measurement", "Test deliverable", "### Contributors", "### Description", "Real science.", "https://github.com/bob", "https://example.org/paper", "Closes #12"):
            self.assertIn(text, body)
        self.assertNotIn("@test-org/steering-committee", body)
        self.assertNotIn("@someone", body)
        self.assertNotIn("Closes #543", body)

    def test_acknowledgment_precedes_validation_and_is_not_duplicated(self):
        api = FakeAPI(current_issue())
        api.base[".github/CODEOWNERS"] = "* @aguinot\n"
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertEqual(len(api.comments), 2)
        self.assertTrue(api.comments[0]["body"].startswith(issue_intake.ACK_MARKER))
        self.assertIn("awaiting validation", api.comments[0]["body"])
        self.assertIn("Proposal validated", api.comments[1]["body"])
        requests = [w for w in api.writes if w[1].endswith("/requested_reviewers")]
        self.assertEqual(requests[0][2], {"reviewers": ["aguinot"], "team_reviewers": []})
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertEqual(len(api.comments), 2)

    def test_current_issue_edit_refreshes_same_pr_summary(self):
        api = FakeAPI(current_issue())
        api.base[".github/CODEOWNERS"] = "* @aguinot\n"
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        api.issue = current_issue(title="New title", contributor_5_name="New person", contributor_5_github_username="new-person")
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertIn("New title", api.pr["body"])
        self.assertIn("New person", api.pr["body"])
        self.assertEqual(len([w for w in api.writes if w[:2] == ("POST", "pulls")]), 1)

    def test_refresh_removes_old_team_review_requests(self):
        api = FakeAPI(current_issue())
        api.base[".github/CODEOWNERS"] = "* @aguinot\n"
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        api.pr["requested_teams"] = [{"slug": "steering-committee"}]
        api.pr["requested_reviewers"] = [{"login": "other-reviewer"}]
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        removals = [w for w in api.writes if w[0] == "DELETE"]
        self.assertEqual(removals[-1][2], {"reviewers": ["other-reviewer"], "team_reviewers": ["steering-committee"]})


class AcceptanceAPI(FakeAPI):
    def __init__(self):
        super().__init__(current_issue())
        self.base[".github/CODEOWNERS"] = "* @aguinot\n"
        issue_intake.process(self, Teams(), 12, repo_check=lambda _: None)
        self.creation = deepcopy(self.pr)
        self.creation.update(state="closed", merged=True, merged_at="2026-10-11T05:30:00Z",
                             merged_by={"login": "aguinot", "type": "User"}, merge_commit_sha=self.pr["head"]["sha"])
        self.refs["main"] = self.creation["merge_commit_sha"]
        self.pr = None
        self.writes = []
        self.dispatches = []

    def get(self, endpoint):
        if endpoint == "pulls/99" and hasattr(self, "creation"):
            return deepcopy(self.creation)
        if endpoint == "pulls/100" and self.pr:
            return deepcopy(self.pr)
        return super().get(endpoint)

    def pages(self, endpoint):
        if endpoint == "pulls/99/files":
            return [{"filename": project_path(12), "status": "added"}]
        return super().pages(endpoint)

    def request(self, method, endpoint, payload):
        if endpoint == "pulls/100/requested_reviewers":
            self.writes.append((method, endpoint, deepcopy(payload)))
            return deepcopy(self.pr)
        if endpoint == "pulls/100":
            self.writes.append((method, endpoint, deepcopy(payload)))
            self.pr.update(payload)
            return deepcopy(self.pr)
        result = super().request(method, endpoint, payload)
        if endpoint == "pulls" and hasattr(self, "creation"):
            self.pr["number"] = 100
            self.pr["base"]["sha"] = self.refs["main"]
            return deepcopy(self.pr)
        return result


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.api = AcceptanceAPI()
        self.base_sha = self.api.refs["main"]
        self.base_tree = self.api.tree(self.base_sha)
        self.base_read = self.api.reader(self.base_tree)
        self.ctx = load_context(self.base_read, paths=self.base_tree)

    def run_process(self):
        return acceptance.process(self.api, Teams(), 99)

    def check(self):
        head_tree = self.api.tree(self.api.pr["head"]["sha"])
        return acceptance.validate_acceptance_snapshot(self.api, self.base_tree, head_tree,
            self.base_read, self.api.reader(head_tree), self.api.pr, self.ctx, Teams())

    def test_exact_original_timestamp_merger_and_pr_recorded(self):
        self.assertEqual(self.run_process(), "created")
        record = next(iter(self.check().values()))
        self.assertEqual(record["acceptance"], {"accepted_at": "2026-10-11T05:30:00Z", "pull_request": 99, "merged_by": "aguinot"})
        old = parse_yaml(self.base_read(project_path(12)))
        self.assertEqual({k: v for k, v in record.items() if k != "acceptance"}, old)
        self.assertEqual(self.api.refs["main"], self.base_sha)
        self.assertEqual(self.api.dispatches[-1]["inputs"], {"pr_number": "100"})
        review = [w for w in self.api.writes if w[1].endswith("/requested_reviewers")][0]
        self.assertEqual(review[2], {"reviewers": ["aguinot"], "team_reviewers": []})

    def test_repeated_runs_use_same_pr_and_commit(self):
        self.run_process()
        sha = self.api.pr["head"]["sha"]
        self.assertEqual(self.run_process(), "updated")
        self.assertEqual(self.api.pr["head"]["sha"], sha)
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ("POST", "pulls")]), 1)

    def test_closed_unmerged_original_does_not_record_acceptance(self):
        self.api.creation.update(merged=False, merged_at=None)
        self.assertEqual(self.run_process(), "not-merged")
        self.assertFalse(self.api.writes)

    def test_closed_issue_changes_do_not_rewrite_accepted_project(self):
        self.api.issue = {**current_issue(title="Malicious late edit", summary="Different science"), "state": "closed"}
        self.run_process()
        record = next(iter(self.check().values()))
        self.assertNotEqual(record["name"], "Malicious late edit")

    def test_forged_timestamp_science_extra_file_or_bot_identity_fails(self):
        self.run_process()
        head = self.api.data[self.api.pr["head"]["sha"]]
        original = head[project_path(12)]
        for key, value in (("summary", "Unrequested rewrite"), ("acceptance", {"accepted_at": "2000-01-01T00:00:00Z", "pull_request": 99, "merged_by": "aguinot"})):
            record = parse_yaml(original)
            record[key] = value
            head[project_path(12)] = project_yaml(record)
            with self.subTest(key=key), self.assertRaises(Invalid):
                self.check()
        head[project_path(12)] = original
        head["scripts/evil.py"] = "unsafe"
        with self.assertRaises(Invalid):
            self.check()
        del head["scripts/evil.py"]
        self.api.pr["user"]["id"] = 7
        with self.assertRaises(Invalid):
            self.check()

    def test_reserved_branch_manual_edits_are_not_overwritten(self):
        self.run_process()
        head = self.api.data[self.api.pr["head"]["sha"]]
        head["config/evil.yaml"] = "unrelated"
        with self.assertRaisesRegex(Invalid, "unrelated"):
            self.run_process()

    def test_closed_metadata_pr_not_duplicated(self):
        self.run_process()
        self.api.pr["state"] = "closed"
        with self.assertRaises(Invalid):
            self.run_process()
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ("POST", "pulls")]), 1)

    def test_already_recorded_metadata_is_idempotent_and_never_overwritten(self):
        self.run_process()
        self.api.refs["main"] = self.api.pr["head"]["sha"]
        self.assertEqual(self.run_process(), "already-recorded-or-no-new-projects")
        data = self.api.data[self.api.refs["main"]]
        record = parse_yaml(data[project_path(12)])
        record["acceptance"]["pull_request"] = 50
        data[project_path(12)] = project_yaml(record)
        with self.assertRaisesRegex(Invalid, "conflicts"):
            self.run_process()

    def test_current_accepted_science_edits_are_preserved(self):
        old_main = self.base_sha
        current_main = "e" * 40
        updated = deepcopy(self.api.data[old_main])
        record = parse_yaml(updated[project_path(12)])
        record["summary"] = "Later accepted SC update"
        updated[project_path(12)] = project_yaml(record)
        self.api.data[current_main] = updated
        self.api.commits[current_main] = {"tree": {"sha": current_main}, "parents": [{"sha": old_main}]}
        self.api.refs["main"] = current_main
        self.run_process()
        result = parse_yaml(self.api.data[self.api.pr["head"]["sha"]][project_path(12)])
        self.assertEqual(result["summary"], "Later accepted SC update")

    def test_manual_sc_project_creation_supported(self):
        self.api.creation["user"] = {"id": 55, "type": "User", "login": "sc-user"}
        self.api.creation["head"]["ref"] = "manual-project"
        self.assertEqual(acceptance.process(self.api, Teams(sc=True), 99), "created")

    def test_manual_non_sc_creation_rejected(self):
        self.api.creation["user"] = {"id": 55, "type": "User", "login": "ordinary-user"}
        with self.assertRaises(Invalid):
            self.run_process()

    def test_original_merge_content_must_match_approved_content(self):
        merged_sha = "d" * 40
        changed = deepcopy(self.api.data[self.base_sha])
        record = parse_yaml(changed[project_path(12)])
        record["summary"] = "Different content in the actual merge"
        changed[project_path(12)] = project_yaml(record)
        self.api.data[merged_sha] = changed
        self.api.creation["merge_commit_sha"] = merged_sha
        with self.assertRaisesRegex(Invalid, "actual merge commit"):
            self.run_process()

    def test_main_advance_rebuilds_same_pr_preserving_latest_science(self):
        self.run_process()
        original_pr = self.api.pr["number"]
        current_main = "e" * 40
        updated = deepcopy(self.api.data[self.base_sha])
        record = parse_yaml(updated[project_path(12)])
        record["summary"] = "Accepted edit after metadata PR opened"
        updated[project_path(12)] = project_yaml(record)
        self.api.data[current_main] = updated
        self.api.commits[current_main] = {"tree": {"sha": current_main}, "parents": [{"sha": self.base_sha}]}
        self.api.refs["main"] = current_main
        self.assertEqual(self.run_process(), "updated")
        self.assertEqual(self.api.pr["number"], original_pr)
        actual = parse_yaml(self.api.data[self.api.pr["head"]["sha"]][project_path(12)])
        self.assertEqual(actual["summary"], record["summary"])
        self.assertEqual(actual["acceptance"]["accepted_at"], self.api.creation["merged_at"])

    def test_multiple_manual_sc_creations_share_original_merge_timestamp(self):
        second = "projects/P013.yaml"
        data = self.api.data[self.base_sha]
        record = parse_yaml(data[project_path(12)])
        record["project_id"] = "P013"
        del record["submission"]
        data[second] = project_yaml(record)
        self.api.creation["user"] = {"id": 55, "type": "User", "login": "sc-user"}
        self.api.creation["head"]["ref"] = "two-manual-projects"
        pages = self.api.pages
        self.api.pages = lambda endpoint: ([{"filename": p, "status": "added"} for p in (project_path(12), second)]
                                          if endpoint == "pulls/99/files" else pages(endpoint))
        self.assertEqual(acceptance.process(self.api, Teams(sc=True), 99), "created")
        head = self.api.data[self.api.pr["head"]["sha"]]
        for path in (project_path(12), second):
            self.assertEqual(parse_yaml(head[path])["acceptance"]["accepted_at"], self.api.creation["merged_at"])

    def test_metadata_pr_does_not_trigger_another_metadata_pr(self):
        self.api.creation["head"]["ref"] = "project-acceptances/pr-50"
        self.assertEqual(self.run_process(), "metadata-pr")

    def test_invalid_dates_rejected_and_member_cannot_supply_metadata(self):
        for date in ("2026-02-30T00:00:00Z", "2026-10-11", "2026-10-11T01:00:00+01:00"):
            with self.subTest(date=date), self.assertRaises(Invalid):
                acceptance.timestamp(date)
        old = parse_yaml(self.base_read(project_path(12)))
        new = {**old, "acceptance": {"accepted_at": "2026-10-11T05:30:00Z", "pull_request": 99, "merged_by": "aguinot"}}
        validate_record(new, project_path(12), self.ctx)
        with self.assertRaises(Invalid):
            authorize_change(old, new, "alice", self.ctx, organization_member=True)
        with self.assertRaises(Invalid):
            authorize_change(None, new, "alice", self.ctx, organization_member=True)

    def test_policy_workflow_validates_metadata_and_sets_head_status(self):
        self.run_process()
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text("{}")
            env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": self.api.repo,
                   "GITHUB_TOKEN": "dummy", "SC_TEAM_READ_TOKEN": "dummy", "POLICY_PR_NUMBER": "100"}
            with patch.dict(os.environ, env), patch.object(sys, "argv", ["validate_pr", "--publish-status"]), \
                 patch.object(validate_pr, "GitHub", return_value=self.api), patch.object(validate_pr, "TeamDirectory", return_value=Teams()), \
                 patch.object(validate_pr.subprocess, "check_output", return_value=self.base_sha + "\n"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(validate_pr.main(), 0)
                self.assertEqual(self.api.statuses[-1][:2], (self.api.pr["head"]["sha"], "success"))


if __name__ == "__main__":
    unittest.main()
