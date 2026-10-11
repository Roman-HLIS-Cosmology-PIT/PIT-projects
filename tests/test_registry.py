import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from scripts.project_registry import (
    Invalid, authorize_change, load_context, parse_yaml, validate_record,
)
from scripts.validate_pr import GitHub, validate_snapshot

ROOT = Path(__file__).resolve().parents[1]


def source_data():
    data = {}
    for folder in ("config", "schema"):
        for file in (ROOT / folder).rglob("*"):
            if file.is_file():
                data[file.relative_to(ROOT).as_posix()] = file.read_text()
    # Tests use a synthetic team, never a stored production membership roster.
    data[".github/CODEOWNERS"] = "* @test-org/steering-committee\n"
    data["config/deliverables/SCM.yaml"] = "working_group: SCM\ndeliverables: [{id: SCM-001, title: Test deliverable}]\n"
    data["config/deliverables/CAS.yaml"] = "working_group: CAS\ndeliverables: [{id: CAS-001, title: Other deliverable}]\n"
    return data


def person(username, name=None):
    return {"name": name or f"{username.title()} Example", "github_username": username}


def project(**changes):
    result = {
        "schema_version": 1, "project_id": "P001", "name": "Test science",
        "lead": "Alice Example", "lead_github_username": "alice",
        "contributors": [person("bob")], "summary": "A long\nMarkdown summary.\n",
        "kind": "EP", "status": "active", "working_group": "SCM",
        "deliverable_ids": ["SCM-001"], "github_url": None,
    }
    result.update(changes)
    return result


def snapshot(data):
    tree = {"projects": {"type": "tree", "mode": "040000", "sha": "0" * 40}}
    tree.update({path: {"type": "blob", "mode": "100644", "sha": "0" * 40}
                 for path in data})
    return tree


def is_test_sc(org, slug, actor):
    return (org.lower(), slug.lower(), actor.lower()) == ("test-org", "steering-committee", "sc-user")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.data = source_data()
        self.ctx = load_context(self.data.__getitem__)

    def valid(self, record):
        validate_record(record, f"projects/{record['project_id']}.yaml", self.ctx)

    def authorize(self, old, new, actor="alice", member=True):
        self.valid(new)
        authorize_change(old, new, actor, self.ctx, organization_member=member,
                         is_sc=is_test_sc(*self.ctx.sc_team, actor))

    def denied(self, old, new, actor="alice", member=True):
        with self.assertRaises(Invalid):
            self.authorize(old, new, actor, member)

    def test_new_ep_member_as_lead(self):
        self.authorize(None, project())

    def test_new_ep_cannot_name_someone_else_lead(self):
        self.denied(None, project(lead_github_username="bob"))

    def test_member_cannot_declare_cpp_or_key(self):
        for kind in ("CPP", "KEY"):
            with self.subTest(kind=kind):
                self.denied(None, project(kind=kind))

    def test_sc_can_declare_unassigned_cpp_key(self):
        for kind in ("CPP", "KEY"):
            with self.subTest(kind=kind):
                self.authorize(None, project(kind=kind, lead=None, lead_github_username=None, contributors=[]), "sc-user")

    def test_ep_cannot_be_unassigned_even_for_sc(self):
        self.denied(None, project(lead=None, lead_github_username=None), "sc-user")

    def test_nonmember_cannot_submit(self):
        self.denied(None, project(), member=False)

    def test_cpp_claim_member(self):
        old = project(kind="CPP", lead=None, lead_github_username=None, contributors=[])
        self.authorize(old, {**old, "lead": "Alice Example", "lead_github_username": "alice"})

    def test_cpp_claim_promotes_existing_contributor(self):
        old = project(kind="CPP", lead=None, lead_github_username=None, contributors=[person("alice"), person("bob")])
        self.authorize(old, {**old, "lead": "Alice Example", "lead_github_username": "alice", "contributors": [person("bob")]})

    def test_claim_cannot_rewrite_content_or_roster(self):
        old = project(kind="CPP", lead=None, lead_github_username=None, contributors=[])
        self.denied(old, {**old, "lead": "Alice Example", "lead_github_username": "alice", "summary": "Rewritten"})
        self.denied(old, {**old, "lead": "Alice Example", "lead_github_username": "alice", "contributors": [person("bob")]})

    def test_key_not_claimable_by_member(self):
        old = project(kind="KEY", lead=None, lead_github_username=None)
        self.denied(old, {**old, "lead": "Alice Example", "lead_github_username": "alice"})

    def test_already_assigned_cpp_not_claimable(self):
        old = project(kind="CPP", lead_github_username="bob", contributors=[])
        self.denied(old, {**old, "lead_github_username": "alice"})

    def test_sc_can_assign_key_lead(self):
        old = project(kind="KEY", lead=None, lead_github_username=None)
        self.authorize(old, {**old, "lead": "Alice Example", "lead_github_username": "alice"}, "sc-user")

    def test_lead_can_extend_contributors(self):
        old = project()
        self.authorize(old, {**old, "contributors": [person("bob"), person("carol")]})

    def test_lead_can_remove_contributor(self):
        old = project()
        self.authorize(old, {**old, "contributors": []})

    def test_contributor_can_edit_content(self):
        old = project()
        self.authorize(old, {**old, "summary": "Updated"}, "bob")

    def test_contributor_cannot_extend_contributors(self):
        old = project()
        self.denied(old, {**old, "contributors": [person("bob"), person("carol")]}, "bob")

    def test_unregistered_person_cannot_edit(self):
        old = project()
        self.denied(old, {**old, "summary": "Updated"}, "mallory")

    def test_removing_member_revokes_future_edit(self):
        old = project(contributors=[])
        self.denied(old, {**old, "summary": "Updated"}, "bob")

    def test_member_cannot_transfer_lead_or_reclassify(self):
        old = project()
        self.denied(old, {**old, "lead_github_username": "carol"})
        self.denied(old, {**old, "kind": "CPP"})

    def test_sc_can_transfer_lead_and_reclassify(self):
        old = project()
        self.authorize(old, {**old, "lead": "Carol Example", "lead_github_username": "carol", "kind": "CPP"}, "sc-user")

    def test_case_insensitive_identity(self):
        old = project()
        self.authorize(old, {**old, "summary": "Updated"}, "ALICE")

    def test_username_roster_duplicates_and_lead_repetition(self):
        for people in ([person("bob"), person("BOB", "Different Name")], [person("alice")], [person("@bob")]):
            with self.subTest(people=people), self.assertRaises(Invalid):
                self.valid(project(contributors=people))

    def test_lead_must_be_single_person_name(self):
        with self.assertRaises(Invalid):
            self.valid(project(lead=["alice", "bob"]))

    def test_names_and_usernames_are_required_separately(self):
        item = project()
        del item["lead_github_username"]
        with self.assertRaises(Invalid):
            self.valid(item)
        for contributor in ({"name": "Bob Example"}, {"github_username": "bob"}, "bob"):
            with self.subTest(contributor=contributor), self.assertRaises(Invalid):
                self.valid(project(contributors=[contributor]))

    def test_unassigned_lead_requires_both_fields_null(self):
        for item in (project(kind="CPP", lead=None), project(kind="CPP", lead_github_username=None)):
            with self.subTest(item=item), self.assertRaises(Invalid):
                self.valid(item)

    def test_display_names_are_unicode_and_not_identity(self):
        item = project(lead="Élodie Martin", contributors=[person("bob", "Élodie Martin")])
        self.valid(item)
        self.authorize(None, item, "alice")
        self.denied(None, project(lead="alice", lead_github_username="mallory"), "alice")
        self.denied(item, {**item, "summary": "Updated"}, "Élodie Martin")

    def test_contributor_authority_uses_username_not_display_name(self):
        old = project(contributors=[person("bob", "mallory")])
        self.authorize(old, {**old, "summary": "Updated"}, "BOB")
        self.denied(old, {**old, "summary": "Updated"}, "mallory")

    def test_person_names_cannot_be_whitespace(self):
        for item in (project(lead="  "), project(contributors=[person("bob", " \n ")])):
            with self.subTest(item=item), self.assertRaises(Invalid):
                self.valid(item)

    def test_username_syntax_checked_for_lead_and_contributors(self):
        for username in ("@alice", "alice\n", "../alice", "alice example"):
            for item in (project(lead_github_username=username), project(contributors=[person(username)])):
                with self.subTest(username=username, item=item), self.assertRaises(Invalid):
                    self.valid(item)

    def test_lead_can_correct_display_name_without_transferring_identity(self):
        old = project()
        self.authorize(old, {**old, "lead": "Alice Updated"})
        self.denied(old, {**old, "lead": "Alice Updated"}, "bob")

    def test_claim_does_not_allow_renaming_other_contributors(self):
        old = project(kind="CPP", lead=None, lead_github_username=None, contributors=[person("bob")])
        new = {**old, "lead": "Alice Example", "lead_github_username": "alice", "contributors": [person("bob", "Changed Name")]}
        self.denied(old, new)

    def test_unknown_fields_fail(self):
        with self.assertRaises(Invalid):
            self.valid(project(submitter="sc-user"))

    def test_wrong_deliverable_working_group(self):
        with self.assertRaises(Invalid):
            self.valid(project(deliverable_ids=["CAS-001"]))

    def test_empty_deliverables_and_optional_url(self):
        item = project(deliverable_ids=[])
        del item["github_url"]
        self.valid(item)

    def test_github_url_strict(self):
        self.valid(project(github_url="https://github.com/alice/my-analysis"))
        for url in ("https://example.com/alice/repo", "http://github.com/alice/repo", "https://github.com/alice/..", "https://github.com/alice/repo/tree/main"):
            with self.subTest(url=url), self.assertRaises(Invalid):
                self.valid(project(github_url=url))

    def test_project_id_must_match_path(self):
        with self.assertRaises(Invalid):
            validate_record(project(), "projects/P002.yaml", self.ctx)

    def test_invalid_status_and_whitespace_summary(self):
        for item in (project(status="pending"), project(summary=" \n"), project(name="  ")):
            with self.assertRaises(Invalid):
                self.valid(item)

    def test_project_deletion_denied_to_sc(self):
        with self.assertRaises(Invalid):
            authorize_change(project(), None, "sc-user", self.ctx, organization_member=True, is_sc=True)

    def test_duplicate_yaml_keys_rejected(self):
        with self.assertRaises(Invalid):
            parse_yaml("kind: EP\nkind: KEY\n")

    def test_yaml_code_tags_aliases_and_nonstring_keys_rejected(self):
        for text in ("!!python/object/apply:os.system ['echo bad']", "a: &x [1]\nb: *x", "1: value"):
            with self.subTest(text=text), self.assertRaises(Invalid):
                parse_yaml(text)

    def test_oversized_and_deep_yaml_rejected(self):
        for text in ("a" * (1024 * 1024 + 1), "[" * 41 + "]" * 41):
            with self.assertRaises(Invalid):
                parse_yaml(text)

    def test_remote_schema_ref_rejected(self):
        schema = json.loads(self.data["schema/project.schema.json"])
        schema["properties"]["name"] = {"$ref": "https://example.com/schema"}
        self.data["schema/project.schema.json"] = json.dumps(schema)
        with self.assertRaises(Invalid):
            load_context(self.data.__getitem__)

    def test_add_optional_field_without_changing_validator(self):
        schema = json.loads(self.data["schema/project.schema.json"])
        schema["properties"]["tags"] = {"type": "array", "items": {"type": "string"}}
        self.data["schema/project.schema.json"] = json.dumps(schema)
        ctx = load_context(self.data.__getitem__)
        validate_record(project(tags=["weak-lensing"]), "projects/P001.yaml", ctx)

    def test_add_enum_status_without_changing_validator(self):
        schema = json.loads(self.data["schema/project.schema.json"])
        schema["properties"]["status"]["enum"].append("archived")
        self.data["schema/project.schema.json"] = json.dumps(schema)
        ctx = load_context(self.data.__getitem__)
        validate_record(project(status="archived"), "projects/P001.yaml", ctx)

    def test_unknown_kind_policy_fails_closed(self):
        policy = parse_yaml(self.data["config/policy.yaml"])
        del policy["kinds"]["KEY"]
        self.data["config/policy.yaml"] = yaml.safe_dump(policy)
        with self.assertRaises(Invalid):
            load_context(self.data.__getitem__)

    def test_codeowners_requires_one_team_not_individual_usernames(self):
        self.data[".github/CODEOWNERS"] = "* @alice\n"
        with self.assertRaises(Invalid):
            load_context(self.data.__getitem__)

    def test_proposed_sc_team_cannot_grant_requester_permission(self):
        proposed = deepcopy(self.data)
        proposed[".github/CODEOWNERS"] = "* @test-org/mallory-team\n"
        with self.assertRaises(Invalid):
            validate_snapshot(snapshot(self.data), snapshot(proposed), self.data.__getitem__, proposed.__getitem__, "mallory", sc_lookup=is_test_sc)

    def test_sc_data_pr_accepted_but_proposed_code_never_run(self):
        proposed = deepcopy(self.data)
        proposed["projects/P001.yaml"] = yaml.safe_dump(project())
        proposed["scripts/evil.py"] = "raise AssertionError('must never execute')"
        count = validate_snapshot(snapshot(self.data), snapshot(proposed), self.data.__getitem__, proposed.__getitem__, "SC-USER", sc_lookup=is_test_sc)
        self.assertEqual(count, 1)

    def test_existing_projects_rechecked_when_deliverables_change(self):
        self.data["projects/P001.yaml"] = yaml.safe_dump(project())
        proposed = deepcopy(self.data)
        proposed["config/deliverables/SCM.yaml"] = "working_group: SCM\ndeliverables: []\n"
        with self.assertRaises(Invalid):
            validate_snapshot(snapshot(self.data), snapshot(proposed), self.data.__getitem__, proposed.__getitem__, "sc-user", sc_lookup=is_test_sc)

    def test_extra_deliverable_catalogue_rejected(self):
        proposed = deepcopy(self.data)
        proposed["config/deliverables/UNKNOWN.yaml"] = "working_group: UNKNOWN\ndeliverables: []\n"
        with self.assertRaises(Invalid):
            validate_snapshot(snapshot(self.data), snapshot(proposed), self.data.__getitem__, proposed.__getitem__, "sc-user", sc_lookup=is_test_sc)

    def test_api_reader_rejects_symlink_and_executable(self):
        api = GitHub("example/registry", "test-token")
        for mode in ("120000", "100755"):
            with self.subTest(mode=mode), self.assertRaises(Invalid):
                api.reader({"data": {"type": "blob", "mode": mode}})("data")

    def test_api_reader_decodes_wrapped_base64(self):
        api = GitHub("example/registry", "test-token")
        raw = base64.b64encode(b"name: science\n").decode()
        api.get = lambda endpoint: {"encoding": "base64", "size": 14, "content": raw[:4] + "\n" + raw[4:]}
        read = api.reader({"data": {"type": "blob", "mode": "100644", "sha": "0" * 40}})
        self.assertEqual(read("data"), "name: science\n")

    def test_workflow_uses_trusted_base_and_readonly_token(self):
        workflow = yaml.load((ROOT / ".github/workflows/project-policy.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertIn("pull_request_target", workflow["on"])
        self.assertEqual(workflow["permissions"], {"contents": "read", "pull-requests": "read"})
        steps = workflow["jobs"]["project-policy"]["steps"]
        checkout = steps[0]
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.pull_request.base.sha }}")
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")
            self.assertNotIn("github.event.pull_request.head", json.dumps(step))


if __name__ == "__main__":
    unittest.main()
