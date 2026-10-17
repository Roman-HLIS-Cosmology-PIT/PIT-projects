"""Optional behavior checks for direct-main generation and protected-main fallback."""
from copy import deepcopy
import io
import unittest
import urllib.error


from project_workflow import issue_intake
from project_workflow.generated_files import OUTPUTS
from project_workflow.project_registry import CONFIG_PATH, Invalid, load_context
from project_workflow.sync_generated import GENERATION_BRANCH, synchronize_main, validate_generated_snapshot
from test_generation import GenerationAPI
from test_issue_submission import BASE_SHA, BOT, Teams
from test_issue_submission import issue_fixture as current_issue


class MaintenanceAPI(GenerationAPI):
    def __init__(self, protected=False):
        super().__init__()
        self.pr = None
        self.refs.pop("config/update")
        self.head = self.base
        self.protected = protected

    def pages(self, endpoint):
        if endpoint.startswith("pulls?"):
            if not self.pr or ("state=open" in endpoint and self.pr["state"] != "open"):
                return []
            return [deepcopy(self.pr)]
        return super().pages(endpoint)

    def get(self, endpoint):
        if endpoint == "pulls/99" and self.pr:
            self.pr["base"]["sha"] = self.refs["main"]
        return super().get(endpoint)

    def request(self, method, endpoint, payload):
        if endpoint == "git/refs/heads/main":
            self.writes.append((method, endpoint, deepcopy(payload)))
            if self.protected:
                raise urllib.error.HTTPError("https://api.github.com/example", 422, "Protected branch update failed", {}, io.BytesIO(b'{"message":"Changes must be made through a pull request"}'))
            self.refs["main"] = payload["sha"]
            if self.pr:
                self.pr["base"]["sha"] = payload["sha"]
            return {"object": {"sha": payload["sha"]}}
        return super().request(method, endpoint, payload)


class MaintenanceTests(unittest.TestCase):
    def changed_main(self, protected=False):
        api = MaintenanceAPI(protected=protected)
        api.source("form", lambda d: d.update(description="Updated directly on main"))
        return api

    def test_main_commit_generates_only_native_outputs_and_retries_are_noop(self):
        api = self.changed_main()
        before = deepcopy(api.base)
        self.assertEqual(synchronize_main(api, checkout_sha=BASE_SHA), "updated-main")
        sha = api.refs["main"]
        after = api.data[sha]
        self.assertEqual(api.commits[sha]["parents"], [{"sha": BASE_SHA}])
        self.assertTrue({p for p in after if after[p] != before.get(p)} <= OUTPUTS)
        self.assertEqual(after[CONFIG_PATH], before[CONFIG_PATH])
        writes = len(api.writes)
        self.assertEqual(synchronize_main(api, checkout_sha=sha), "unchanged")
        self.assertEqual(len(api.writes), writes)
        self.assertIsNone(api.pr)

    def test_protected_main_opens_one_scoped_pr_and_reuses_commit(self):
        api = self.changed_main(protected=True)
        self.assertEqual(synchronize_main(api, checkout_sha=BASE_SHA), "synchronization-pr")
        self.assertEqual(api.refs["main"], BASE_SHA)
        self.assertEqual(api.pr["head"]["ref"], GENERATION_BRANCH)
        sha = api.pr["head"]["sha"]
        count = len([x for x in api.writes if x[1] == "git/commits"])
        self.assertEqual(synchronize_main(api, checkout_sha=BASE_SHA), "synchronization-pr")
        self.assertEqual(api.pr["head"]["sha"], sha)
        self.assertEqual(len([x for x in api.writes if x[1] == "git/commits"]), count)
        self.assertEqual(len([x for x in api.writes if x[1] == "pulls"]), 1)
        review = next(payload for method, path, payload in api.writes if path == "pulls/99/requested_reviewers" and method == "POST")
        self.assertEqual(review, {"reviewers": ["aguinot"], "team_reviewers": []})

    def test_generated_pr_verification_rejects_unrelated_changes(self):
        api = self.changed_main(protected=True)
        synchronize_main(api, checkout_sha=BASE_SHA)
        base_tree = api.tree(BASE_SHA)
        read = api.reader(base_tree)
        ctx = load_context(read, paths=base_tree)
        head = api.pr["head"]["sha"]
        validate_generated_snapshot(api, base_tree, api.tree(head), read, api.reader(api.tree(head)), api.pr, ctx)
        api.data[head][CONFIG_PATH] += "\n# An unrelated source change\n"
        with self.assertRaises(Invalid):
            validate_generated_snapshot(api, base_tree, api.tree(head), read, api.reader(api.tree(head)), api.pr, ctx)

    def test_generated_pr_verification_rejects_spoofed_bot(self):
        api = self.changed_main(protected=True)
        synchronize_main(api, checkout_sha=BASE_SHA)
        api.pr["user"] = {"login": BOT["login"], "id": 123, "type": "Bot"}
        base_tree = api.tree(BASE_SHA)
        read = api.reader(base_tree)
        head = api.tree(api.pr["head"]["sha"])
        with self.assertRaises(Invalid):
            validate_generated_snapshot(api, base_tree, head, read, api.reader(head), api.pr, load_context(read, paths=base_tree))

    def test_main_generation_rejects_stale_checkout_and_invalid_config(self):
        api = self.changed_main()
        with self.assertRaises(Invalid):
            synchronize_main(api, checkout_sha="c" * 40)
        api.head[CONFIG_PATH] = "!!python/object/apply:os.system ['false']"
        with self.assertRaises(Invalid):
            synchronize_main(api, checkout_sha=BASE_SHA)
        self.assertEqual(api.writes, [])

    def test_main_advance_does_not_force_overwrite(self):
        api = self.changed_main()
        request = api.request
        def advance(method, endpoint, payload):
            result = request(method, endpoint, payload)
            if endpoint == "git/commits":
                api.refs["main"] = "c" * 40
            return result
        api.request = advance
        with self.assertRaises(Invalid):
            synchronize_main(api, checkout_sha=BASE_SHA)
        self.assertFalse(any(path == "git/refs/heads/main" for _, path, _ in api.writes))

    def test_access_error_does_not_open_a_fallback_pr(self):
        api = self.changed_main()
        request = api.request
        def deny(method, endpoint, payload):
            if endpoint == "git/refs/heads/main":
                raise urllib.error.HTTPError("https://api.github.com/example", 403, "Resource not accessible by integration", {}, io.BytesIO(b'{}'))
            return request(method, endpoint, payload)
        api.request = deny
        with self.assertRaises(urllib.error.HTTPError):
            synchronize_main(api, checkout_sha=BASE_SHA)
        self.assertIsNone(api.pr)

    def test_partial_pr_publication_is_recoverable(self):
        api = self.changed_main(protected=True)
        request = api.request
        def fail_pr(method, endpoint, payload):
            if endpoint == "pulls":
                raise OSError("Simulated interruption before PR creation")
            return request(method, endpoint, payload)
        api.request = fail_pr
        with self.assertRaises(OSError):
            synchronize_main(api, checkout_sha=BASE_SHA)
        self.assertIn(GENERATION_BRANCH, api.refs)
        self.assertIsNone(api.pr)
        api.request = request
        self.assertEqual(synchronize_main(api, checkout_sha=BASE_SHA), "synchronization-pr")
        self.assertEqual(api.pr["head"]["ref"], GENERATION_BRANCH)

    def test_configuration_changes_that_need_no_native_output_make_no_commit(self):
        api = MaintenanceAPI()
        api.source("schema", lambda d: d["properties"]["status"].update(default="paused"))
        self.assertEqual(synchronize_main(api, checkout_sha=BASE_SHA), "unchanged")
        self.assertEqual(api.writes, [])

    def test_human_reserved_branch_pr_is_not_overwritten(self):
        api = self.changed_main(protected=True)
        synchronize_main(api, checkout_sha=BASE_SHA)
        api.pr["user"] = {"login": "sc-user", "id": 12, "type": "User"}
        api.writes.clear()
        with self.assertRaises(Invalid):
            synchronize_main(api, checkout_sha=BASE_SHA)
        self.assertEqual(api.writes, [])

    def test_issue_workflow_uses_unified_config_and_new_validation_dispatch(self):
        api = MaintenanceAPI()
        api.issue = current_issue()
        self.assertEqual(issue_intake.process(api, Teams(member=True, sc=False), 12, repo_check=lambda url: None), "created")
        self.assertEqual(api.dispatches[-1]["inputs"], {"operation": "validate", "pr_number": "99"})
        self.assertEqual(issue_intake.process(api, Teams(member=True, sc=False), 12, repo_check=lambda url: None), "updated")
        self.assertEqual(len([x for x in api.writes if x[1] == "pulls"]), 1)


if __name__ == "__main__":
    unittest.main()
