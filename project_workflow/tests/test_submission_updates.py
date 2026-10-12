"""Behavior and security checks for the form and review routing."""
from copy import deepcopy
import unittest
import yaml

from project_workflow import issue_intake
from project_workflow.issue_form import current_labels, form, generated_text, validate_form
from project_workflow.project_registry import Invalid, load_context, parse_yaml
from project_workflow.review import proposal_body
from project_workflow.submission import issue_version, proposal
from test_issue_submission import FakeAPI, Teams, issue_fixture
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
    data = source_data()
    labels = current_labels(load_context(data.__getitem__, paths=data))
    issue["body"] = "\n\n".join(f"### {label}\n\n{values[k]}" for k, label in labels.items())
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
            with self.subTest(fields=fields), self.assertRaises(Invalid):
                self.parse(current_issue(**fields))

    def test_duplicate_and_lead_usernames_rejected_in_boxes(self):
        for fields in ({"contributor_1_name": "Alice", "contributor_1_github_username": "ALICE"},
                       {"contributor_1_name": "Bob", "contributor_1_github_username": "bob",
                        "contributor_2_name": "Bob again", "contributor_2_github_username": "BOB"}):
            with self.subTest(fields=fields), self.assertRaises(Invalid):
                self.parse(current_issue(**fields))

    def test_boolean_answers_are_stored_as_booleans(self):
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

    def test_display_edits_do_not_block_submission_or_pr_validation(self):
        spec = form(self.ctx)
        spec["name"], spec["description"] = "Propose science", "Editable introduction"
        spec["labels"] = "new-project"
        spec["body"].reverse()
        for item in spec["body"]:
            attributes = item["attributes"]
            if item["type"] == "markdown":
                attributes["value"] = "## A different section heading"
            else:
                item["id"] = "custom_" + item["id"]
                attributes["description"] = "New help text."
                attributes["placeholder"] = "A different example"
                attributes.pop("default", None)
        text = "# Editable formatting\n" + yaml.safe_dump(spec, sort_keys=True, width=50)
        api = FakeAPI(current_issue())
        api.base[".github/ISSUE_TEMPLATE/new-project.yml"] = text
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=lambda _: None), "created")
        validate_form(text, self.ctx)

    def test_catalogue_display_text_can_change_without_regenerating_form(self):
        original = generated_text(self.ctx)
        self.ctx.working_groups["SCM"]["description"] = "Updated working-group description"
        self.ctx.working_groups["SCM"]["deliverables"][0]["title"] = "Updated deliverable title"
        validate_form(original, self.ctx)
        record = self.parse(current_issue(working_group="SCM — My custom description", deliverable_ids="SCM / SCM-001 — My custom title"))
        self.assertEqual((record["working_group"], record["deliverable_ids"]), ("SCM", ["SCM-001"]))

    def test_form_changes_that_break_parsing_are_rejected(self):
        for change in ("missing_field", "wrong_label", "missing_trigger", "unknown_deliverable"):
            spec = form(self.ctx)
            fields = {item["id"]: item for item in spec["body"] if "id" in item}
            if change == "missing_field":
                spec["body"].remove(fields["lead"])
            elif change == "wrong_label":
                fields["lead"]["attributes"]["label"] = "Unrecognized answer heading"
            elif change == "missing_trigger":
                spec["labels"] = []
            else:
                fields["deliverable_ids"]["attributes"]["options"] = ["SCM / FAKE-ID — Anything"]
            with self.subTest(change=change), self.assertRaises(Invalid):
                validate_form(yaml.safe_dump(spec), self.ctx)

    def test_access_configuration_is_independent_of_review_routing(self):
        self.assertEqual(self.ctx.review_owners, ("@aguinot",))
        self.assertEqual(self.ctx.sc_team, ("test-org", "steering-committee"))
        for value in ("a/b/c", "@a/b", "a/../b", "a/b?query"):
            data = deepcopy(self.data)
            config = parse_yaml(data["project_workflow/config.yaml"])
            config["steering_committee"] = value
            data["project_workflow/config.yaml"] = yaml.safe_dump(config)
            with self.subTest(value=value), self.assertRaises(Invalid):
                load_context(data.__getitem__, paths=data)

    def test_pr_body_cannot_ping_other_people_or_close_unrelated_issues(self):
        record = self.parse(current_issue(contributor_1_name="Bob | @test-org/steering-committee", contributor_1_github_username="bob",
                                         summary="## Science\nNotify @someone. Closes #543.\nReal science.",
                                         related_links="https://example.org/paper"))
        body = proposal_body(record, self.ctx, "test-org/registry", 12)
        self.assertRegex(body, r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#12\b")
        self.assertNotIn("@test-org/steering-committee", body)
        self.assertNotIn("@someone", body)
        self.assertNotIn("Closes #543", body)

    def test_acknowledgment_precedes_validation_and_is_not_duplicated(self):
        api = FakeAPI(current_issue())
        api.base[".github/CODEOWNERS"] = "* @aguinot\n"
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertEqual(len(api.comments), 3)
        self.assertTrue(api.comments[0]["body"].startswith(issue_intake.ACK_MARKER))
        requests = [w for w in api.writes if w[1].endswith("/requested_reviewers")]
        self.assertEqual(requests[0][2], {"reviewers": ["aguinot"], "team_reviewers": []})
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertEqual(len(api.comments), 3)

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



if __name__ == "__main__":
    unittest.main()
