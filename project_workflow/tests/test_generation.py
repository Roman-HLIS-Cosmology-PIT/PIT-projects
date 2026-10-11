"""Optional offline checks for generation scope, authorization and retries."""
from copy import deepcopy
import unittest

import yaml

from project_workflow.generated_files import OUTPUTS, render, validate_outputs
from project_workflow.issue_form import form
from project_workflow.project_registry import CONFIG_PATH, Invalid, load_context
from project_workflow.submission import proposal
from project_workflow.sync_generated import synchronize
from test_issue_submission import BASE_SHA, FakeAPI, Teams
from test_registry import source_data
from test_submission_updates import current_issue

HEAD_SHA = "b" * 40


class GenerationAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.base = source_data()
        self.base.update(render(load_context(self.base.__getitem__, paths=self.base)))
        self.data[BASE_SHA] = self.base
        self.head = deepcopy(self.base)
        self.data[HEAD_SHA] = self.head
        self.commits[HEAD_SHA] = {"tree": {"sha": HEAD_SHA}, "parents": [{"sha": BASE_SHA}]}
        self.refs["config/update"] = HEAD_SHA
        self.pr = {"number": 99, "state": "open", "user": {"login": "sc-user", "type": "User", "id": 123},
                   "base": {"sha": BASE_SHA, "ref": "main", "repo": {"full_name": self.repo}},
                   "head": {"sha": HEAD_SHA, "ref": "config/update", "repo": {"full_name": self.repo}}}

    def pages(self, endpoint):
        if endpoint == "pulls/99/files":
            head = self.data[self.pr["head"]["sha"]]
            return [{"filename": p} for p in set(head) | set(self.base) if head.get(p) != self.base.get(p)]
        return super().pages(endpoint)

    def source(self, path, edit):
        if path in {"form", "policy", "schema"}:
            config = yaml.safe_load(self.head[CONFIG_PATH])
            edit(config[path])
            self.head[CONFIG_PATH] = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        else:
            value = yaml.safe_load(self.head[path])
            edit(value)
            self.head[path] = yaml.safe_dump(value, sort_keys=False, allow_unicode=True)

    def setting(self, key, value):
        config = yaml.safe_load(self.head[CONFIG_PATH])
        config[key] = value
        self.head[CONFIG_PATH] = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.api = GenerationAPI()
        self.teams = Teams(sc=True)

    def run_sync(self):
        return synchronize(self.api, self.teams, 99, checkout_sha=BASE_SHA)

    def test_source_edit_appends_only_outputs_and_is_idempotent(self):
        self.api.source("form", lambda d: d.update(description="Custom proposal wording"))
        self.api.head["project_workflow/untrusted.py"] = "raise RuntimeError('must never execute PR code')\n"
        before = deepcopy(self.api.head)
        self.assertEqual(self.run_sync(), "updated")
        sha = self.api.pr["head"]["sha"]
        result = self.api.data[sha]
        changed = {p for p in result if result[p] != before.get(p)}
        self.assertTrue(changed)
        self.assertTrue(changed <= OUTPUTS)
        self.assertEqual(self.api.commits[sha]["parents"], [{"sha": HEAD_SHA}])
        self.assertEqual(self.api.refs["main"], BASE_SHA)
        self.assertFalse(next(payload for method, endpoint, payload in self.api.writes if method == "PATCH" and endpoint.startswith("git/refs"))["force"])
        self.assertEqual(self.api.dispatches[-1], {"ref": "main", "inputs": {"operation": "validate", "pr_number": "99"}})
        count = len([x for x in self.api.writes if x[1] == "git/commits"])
        self.assertEqual(self.run_sync(), "unchanged")
        self.assertEqual(len([x for x in self.api.writes if x[1] == "git/commits"]), count)

    def test_catalogue_edit_updates_form_without_second_manual_edit(self):
        self.api.source("working_groups/SCM.yaml", lambda d: d["deliverables"].append({"id": "SCM-NEW", "title": "New deliverable"}))
        self.run_sync()
        spec = yaml.safe_load(self.api.data[self.api.pr["head"]["sha"]][".github/ISSUE_TEMPLATE/new-project.yml"])
        field = next(f for f in spec["body"] if f.get("id") == "deliverable_ids")
        self.assertTrue(any("SCM-NEW" in x for x in field["attributes"]["options"]))

    def test_review_source_updates_codeowners_and_runtime_routing(self):
        self.api.setting("reviewers", "steering_committee")
        self.run_sync()
        head = self.api.data[self.api.pr["head"]["sha"]]
        ctx = load_context(head.__getitem__, paths=head)
        self.assertEqual(ctx.review_owners, ("@test-org/steering-committee",))
        self.assertIn("* @test-org/steering-committee", head[".github/CODEOWNERS"])

    def test_kind_rules_populate_form_and_schema(self):
        self.api.source("policy", lambda d: d["kinds"].update(TEST={"member_can_create": False, "member_can_claim": False, "allow_unassigned": True}))
        self.run_sync()
        head = self.api.data[self.api.pr["head"]["sha"]]
        self.assertIn("TEST", load_context(head.__getitem__, paths=head).schema["properties"]["kind"]["enum"])
        spec = yaml.safe_load(head[".github/ISSUE_TEMPLATE/new-project.yml"])
        self.assertIn("TEST", next(f for f in spec["body"] if f.get("id") == "kind")["attributes"]["options"])

    def test_custom_label_is_used_by_submission_parser(self):
        def rename(data):
            next(f for f in data["body"] if f.get("id") == "summary")["attributes"]["label"] = "Scientific goals"
        self.api.source("form", rename)
        ctx = load_context(self.api.head.__getitem__, paths=self.api.head)
        issue = current_issue()
        issue["body"] = issue["body"].replace("### Description\n", "### Scientific goals\n")
        self.assertEqual(proposal(issue, ctx, organization_member=True, is_sc=False)["summary"], "Science description.")
    def test_descriptive_kind_options_keep_stable_codes_and_permissions(self):
        self.api.source("policy", lambda d: d["kinds"]["KEY"].pop("description", None))
        self.api.source("policy", lambda d: d["kinds"]["EP"].update(description="Enhancing Project"))
        self.api.source("policy", lambda d: d["kinds"]["CPP"].update(description="Critical Path Project"))
        self.run_sync()
        head = self.api.data[self.api.pr["head"]["sha"]]
        ctx = load_context(head.__getitem__, paths=head)
        options = next(f for f in form(ctx)["body"] if f.get("id") == "kind")["attributes"]["options"]
        self.assertIn("EP - Enhancing Project", options)
        self.assertIn("CPP - Critical Path Project", options)
        self.assertIn("KEY", options)
        self.assertEqual(ctx.schema["properties"]["kind"]["enum"], ["EP", "CPP", "KEY"])
        for label in ("EP", "EP - Enhancing Project", "EP — Previous wording"):
            issue = current_issue()
            issue["body"] = issue["body"].replace("### Project kind\n\nEP", "### Project kind\n\n" + label)
            self.assertEqual(proposal(issue, ctx, organization_member=True, is_sc=False)["kind"], "EP")
        issue = current_issue()
        issue["body"] = issue["body"].replace("### Project kind\n\nEP", "### Project kind\n\nCPP - Critical Path Project")
        with self.assertRaises(Invalid):
            proposal(issue, ctx, organization_member=True, is_sc=False)

    def test_kind_description_does_not_relax_policy_validation(self):
        for edit in (lambda rules: rules.update(description=False),
                     lambda rules: rules.update(description=" "),
                     lambda rules: rules.update(member_can_create="true", description="Enhancing Project"),
                     lambda rules: rules.update(extra_permission=True, description="Enhancing Project")):
            data = deepcopy(self.api.head)
            config = yaml.safe_load(data[CONFIG_PATH])
            edit(config["policy"]["kinds"]["EP"])
            data[CONFIG_PATH] = yaml.safe_dump(config, sort_keys=False)
            with self.assertRaises(Invalid):
                load_context(data.__getitem__, paths=data)

    def test_extra_contributor_pair_needs_only_form_source_edit(self):
        def add_slot(data):
            for suffix, label in (("name", "Contributor 6 name"), ("github_username", "Contributor 6 GitHub username")):
                data["body"].append({"type": "input", "id": f"contributor_6_{suffix}",
                                     "attributes": {"label": label}, "validations": {"required": False}})
        self.api.source("form", add_slot)
        ctx = load_context(self.api.head.__getitem__, paths=self.api.head)
        self.assertTrue(any(f.get("id") == "contributor_6_name" for f in form(ctx)["body"]))
        # Previously created Issues have no slot 6 and remain editable.
        old = current_issue()
        self.assertEqual(proposal(old, ctx, organization_member=True, is_sc=False)["contributors"], [])
        old["body"] += "\n\n### Contributor 6 name\n\nCarol\n\n### Contributor 6 GitHub username\n\ncarol"
        self.assertEqual(proposal(old, ctx, organization_member=True, is_sc=False)["contributors"], [{"name": "Carol", "github_username": "carol"}])

    def test_initial_status_and_schema_version_follow_schema_source(self):
        source = yaml.safe_load(self.api.head[CONFIG_PATH])["schema"]
        source["properties"]["status"]["default"] = "paused"
        source["properties"]["schema_version"]["const"] = 2
        self.api.setting("schema", source)
        ctx = load_context(self.api.head.__getitem__, paths=self.api.head)
        record = proposal(current_issue(), ctx, organization_member=True, is_sc=False)
        self.assertEqual((record["status"], record["schema_version"]), ("paused", 2))

    def test_non_sc_cannot_publish_even_if_proposed_team_changes(self):
        self.api.setting("steering_committee", "test-org/self-appointed")
        self.teams.sc = False
        with self.assertRaises(Invalid):
            self.run_sync()
        self.assertEqual(self.api.writes, [])

    def test_main_fork_and_stale_checkout_are_rejected(self):
        self.api.source("form", lambda d: d.update(description="New text"))
        original = deepcopy(self.api.pr)
        for mutate in (lambda p: p["head"].update(ref="main"), lambda p: p["head"]["repo"].update(full_name="someone/fork")):
            self.api.pr = deepcopy(original)
            mutate(self.api.pr)
            with self.assertRaises(Invalid):
                self.run_sync()
        self.api.pr = original
        with self.assertRaises(Invalid):
            synchronize(self.api, self.teams, 99, checkout_sha="c" * 40)
        self.assertEqual(self.api.writes, [])

    def test_branch_advance_is_not_overwritten(self):
        self.api.source("form", lambda d: d.update(description="New text"))
        self.api.refs["config/update"] = "c" * 40
        with self.assertRaises(Invalid):
            self.run_sync()
        self.assertEqual(self.api.writes, [])

    def test_dispatch_failure_can_retry_without_duplicate_commit(self):
        self.api.source("form", lambda d: d.update(description="New text"))
        request = self.api.request
        def fail_dispatch(method, endpoint, payload):
            if endpoint.endswith("/dispatches"):
                raise OSError("Simulated dispatch failure")
            return request(method, endpoint, payload)
        self.api.request = fail_dispatch
        with self.assertRaises(OSError):
            self.run_sync()
        sha = self.api.pr["head"]["sha"]
        self.api.request = request
        self.assertEqual(self.run_sync(), "unchanged")
        self.assertEqual(self.api.pr["head"]["sha"], sha)

    def test_unsafe_yaml_is_rejected_before_writes(self):
        self.api.head[CONFIG_PATH] = "!!python/object/apply:os.system ['false']"
        with self.assertRaises(Invalid):
            self.run_sync()
        self.assertEqual(self.api.writes, [])

    def test_output_validation_ignores_formatting_but_rejects_stale_data(self):
        ctx = load_context(self.api.base.__getitem__, paths=self.api.base)
        reformatted = deepcopy(self.api.base)
        path = ".github/ISSUE_TEMPLATE/new-project.yml"
        reformatted[path] = yaml.safe_dump(yaml.safe_load(reformatted[path]), sort_keys=True, width=70)
        validate_outputs(reformatted.__getitem__, reformatted, ctx)
        self.api.source("form", lambda d: d.update(description="Changed source"))
        head_ctx = load_context(self.api.head.__getitem__, paths=self.api.head)
        with self.assertRaises(Invalid):
            validate_outputs(self.api.head.__getitem__, self.api.head, head_ctx)


if __name__ == "__main__":
    unittest.main()
