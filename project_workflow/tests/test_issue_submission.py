"""Offline submission/PR integration tests; all GitHub calls are simulated."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


from project_workflow import issue_intake, validate_pr
from project_workflow.issue_form import BOT_ID, BOT_LOGIN, current_labels
from project_workflow.project_registry import Invalid, load_context, parse_yaml, validate_record
from project_workflow.submission import project_path, project_yaml, proposal, validate_bot_snapshot
from test_registry import source_data

BOT = {"id": BOT_ID, "login": BOT_LOGIN, "type": "Bot"}
BASE_SHA = "a" * 40


def issue_fixture(**changes):
    data = source_data()
    config = load_context(data.__getitem__, paths=data)
    labels = current_labels(config)
    values = {"kind": "EP", "lead": "Alice Example", "lead_github_username": "alice",
              "working_group": "SCM", "deliverable_ids": "SCM / SCM-001",
              "phd_thesis": "True", "slack_channel": "#scm-shapes",
              "summary": "Measure shear calibration.\n\n### Scientific goals\nAssess selection effects.",
              "github_url": "_No response_", "related_links": "```text\nhttps://example.org/paper\n```"}
    for key in labels:
        if key.startswith("contributor_"):
            values[key] = "_No response_"
    values.update(contributor_1_name="Bob Example", contributor_1_github_username="bob")
    title = changes.pop("title", "Shape measurement validation")
    values.update(changes)
    body = "\n\n".join(f"### {label}\n\n{values[k]}" for k, label in labels.items())
    return {"number": 12, "state": "open", "title": title, "body": body, "created_at": "2026-10-11T06:00:00Z",
            "labels": [{"name": "new-project"}],
            "user": {"login": "alice", "id": 123, "type": "User"}}


def tree(data):
    result = {"projects": {"type": "tree", "mode": "040000", "sha": "0" * 40}}
    result.update({p: {"type": "blob", "mode": "100644", "sha": hashlib.sha1(s.encode()).hexdigest()}
                   for p, s in data.items()})
    return result


class Teams:
    def __init__(self, member=True, sc=False):
        self.member, self.sc = member, sc

    def is_org_member(self, org, actor):
        return self.member

    def is_active_member(self, org, slug, actor):
        return self.sc


class FakeAPI:
    """In-memory Git database, Issue, PR and status service."""
    def __init__(self, issue=None):
        self.repo = "test-org/registry"
        self.issue = issue or issue_fixture()
        self.base = source_data()
        self.data = {BASE_SHA: self.base}
        self.commits = {BASE_SHA: {"tree": {"sha": BASE_SHA}, "parents": []}}
        self.refs = {"main": BASE_SHA}
        self.pr = None
        self.prs = {}
        self.comments = []
        self.other_issues = {}
        self.other_comments = {}
        self.next_comment_id = 1
        self.writes = []
        self.statuses = []
        self.dispatches = []
        self.serial = 1

    def next_sha(self):
        self.serial += 1
        return f"{self.serial:040x}"

    def get(self, endpoint):
        if endpoint.startswith("issues/") and endpoint.split("/")[-1].isdigit() and len(endpoint.split("/")) == 2:
            n = int(endpoint.split("/")[-1])
            if n != 12 and n not in self.other_issues:
                raise urllib.error.HTTPError('https://api.github.com', 404, 'test', {}, None)
            return deepcopy(self.issue if n == 12 else self.other_issues[n])
        if endpoint.startswith("issues/comments/"):
            identifier = int(endpoint.rsplit("/", 1)[1])
            return deepcopy(next(c for c in self.comments + [c for rows in self.other_comments.values() for c in rows] if c['id'] == identifier))
        if endpoint.startswith("labels/"):
            return {'name': endpoint.split('/', 1)[1]}
        if endpoint.startswith("git/ref/heads/"):
            ref = endpoint.removeprefix("git/ref/heads/")
            if ref not in self.refs:
                raise urllib.error.HTTPError("https://api.github.com", 404, "test", {}, None)
            return {"object": {"sha": self.refs[ref]}}
        if endpoint.startswith("git/commits/"):
            return deepcopy(self.commits[endpoint.rsplit("/", 1)[1]])
        if endpoint.startswith('pulls/') and endpoint.split('/')[-1].isdigit():
            n = int(endpoint.split('/')[-1])
            pr = self.prs.get(n) or (self.pr if self.pr and self.pr['number'] == n else None)
            if pr:
                return deepcopy(pr)
        raise AssertionError(endpoint)

    def pages(self, endpoint):
        if endpoint.startswith("pulls?"):
            return deepcopy(list(self.prs.values())) if self.prs else ([deepcopy(self.pr)] if self.pr else [])
        if endpoint.startswith("issues/") and endpoint.endswith("/comments"):
            n = int(endpoint.split('/')[1])
            return deepcopy(self.comments if n == 12 else self.other_comments.get(n, []))
        raise AssertionError(endpoint)

    def tree(self, sha):
        return tree(self.data[sha])

    def reader(self, snapshot):
        # Locate data by its content hashes rather than assuming the latest head.
        for data in self.data.values():
            if tree(data) == snapshot:
                return data.__getitem__
        raise AssertionError("Unknown snapshot")

    def status(self, sha, state, description):
        self.statuses.append((sha, state, description))

    def request(self, method, endpoint, payload):
        self.writes.append((method, endpoint, deepcopy(payload)))
        if endpoint == "git/trees":
            sha = self.next_sha()
            data = deepcopy(self.data[payload["base_tree"]]) if "base_tree" in payload else {}
            for entry in payload["tree"]:
                data[entry["path"]] = entry["content"]
            self.data[sha] = data
            return {"sha": sha}
        if endpoint == "git/commits":
            sha = self.next_sha()
            self.data[sha] = self.data[payload["tree"]]
            self.commits[sha] = {"message": payload['message'], "tree": {"sha": payload["tree"]}, "parents": [{"sha": s} for s in payload["parents"]]}
            return {"sha": sha}
        if endpoint == "git/refs" or endpoint.startswith("git/refs/heads/"):
            if method == 'DELETE':
                ref = endpoint.removeprefix('git/refs/heads/')
                if ref not in self.refs:
                    raise urllib.error.HTTPError('https://api.github.com', 404, 'test', {}, None)
                del self.refs[ref]
                return None
            ref = payload["ref"].removeprefix("refs/heads/") if endpoint == "git/refs" else endpoint.removeprefix("git/refs/heads/")
            self.refs[ref] = payload["sha"]
            for pr in list(self.prs.values()) + ([self.pr] if self.pr else []):
                if pr['head']['ref'] == ref:
                    pr['head']['sha'] = payload['sha']
            return {"object": {"sha": payload["sha"]}}
        if endpoint == "pulls":
            self.pr = {"number": 99 + len(self.prs), "state": "open", "merged_at": None, "user": deepcopy(BOT),
                "base": {"sha": BASE_SHA, "ref": "main", "repo": {"full_name": self.repo}},
                "head": {"sha": self.refs[payload["head"]], "ref": payload["head"], "repo": {"full_name": self.repo}},
                "body": payload["body"]}
            self.prs[self.pr['number']] = self.pr
            return deepcopy(self.pr)
        if endpoint.startswith('pulls/'):
            n = int(endpoint.split('/')[1])
            pr = self.prs.get(n) or self.pr
            if endpoint.endswith('/requested_reviewers'):
                return deepcopy(pr)
            pr.update(payload)
            return deepcopy(pr)
        if endpoint.endswith("/dispatches"):
            self.dispatches.append(payload)
            return None
        if endpoint.startswith("issues/") and endpoint.endswith("/comments"):
            n = int(endpoint.split('/')[1])
            rows = self.comments if n == 12 else self.other_comments.setdefault(n, [])
            comment = {'id': self.next_comment_id, 'body': payload['body'], 'user': deepcopy(BOT)}
            self.next_comment_id += 1
            rows.append(comment)
            return deepcopy(comment)
        if endpoint.startswith("issues/comments/"):
            identifier = int(endpoint.rsplit("/", 1)[1])
            comment = next(c for c in self.comments + [c for rows in self.other_comments.values() for c in rows] if c['id'] == identifier)
            comment['body'] = payload['body']
            return deepcopy(comment)
        if endpoint.startswith("issues/"):
            n = int(endpoint.split('/')[1])
            issue = self.issue if n == 12 else self.other_issues[n]
            if '/labels/' in endpoint and method == 'DELETE':
                label = endpoint.rsplit('/', 1)[1]
                issue['labels'] = [e for e in issue['labels'] if e['name'] != label]
                return None
            if endpoint.endswith('/labels'):
                labels = {e['name'] for e in issue['labels']}
                issue['labels'].extend({'name': label} for label in payload['labels'] if label not in labels)
                return None
            issue.update(payload)
            return deepcopy(issue)
        raise AssertionError((method, endpoint))


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.data = source_data()
        self.ctx = load_context(self.data.__getitem__, paths=self.data)

    def parse(self, issue=None, **kwargs):
        return proposal(issue or issue_fixture(), self.ctx, organization_member=kwargs.get("member", True), is_sc=kwargs.get("sc", False))

    def test_valid_submission_roundtrip_and_provenance(self):
        record = self.parse()
        self.assertEqual(record["project_id"], "P000012")
        self.assertTrue(record["phd_thesis"])
        self.assertEqual(record["contributors"], [{"name": "Bob Example", "github_username": "bob"}])
        self.assertEqual(record["submission"]["submitted_by"], "alice")
        self.assertNotIn("email", record)
        self.assertEqual(parse_yaml(project_yaml(record)), record)

    def test_multiple_deliverables(self):
        self.ctx.working_groups["SCM"]["deliverables"].append({"id": "SCM-002", "title": "Second deliverable"})
        self.ctx.deliverables["SCM"].add("SCM-002")
        record = self.parse(issue_fixture(deliverable_ids="SCM / SCM-001, SCM / SCM-002"))
        self.assertEqual(record["deliverable_ids"], ["SCM-001", "SCM-002"])

    def test_bare_ids_accepted_after_markdown_edit(self):
        self.assertEqual(self.parse(issue_fixture(deliverable_ids="SCM-001"))["deliverable_ids"], ["SCM-001"])

    def test_wrong_group_and_contributor_errors_are_combined(self):
        with self.assertRaises(Invalid) as error:
            self.parse(issue_fixture(deliverable_ids="CAS / CAS-001", contributor_1_github_username=""))

    def test_nonmember_rejected_even_if_sc_flag_is_true(self):
        with self.assertRaises(Invalid):
            self.parse(member=False, sc=True)

    def test_member_cannot_declare_cpp_key(self):
        for kind in ("CPP", "KEY"):
            with self.subTest(kind=kind), self.assertRaises(Invalid):
                self.parse(issue_fixture(kind=kind))

    def test_sc_can_declare_unassigned_cpp_key(self):
        for kind in ("CPP", "KEY"):
            record = self.parse(issue_fixture(kind=kind, lead="_No response_", lead_github_username="_No response_", contributor_1_name="_No response_", contributor_1_github_username="_No response_"), sc=True)
            self.assertIsNone(record["lead"])
            self.assertEqual(record["contributors"], [])

    def test_member_cannot_impersonate_another_lead(self):
        with self.assertRaises(Invalid):
            self.parse(issue_fixture(lead_github_username="bob", contributor_1_name="_No response_", contributor_1_github_username="_No response_"))

    def test_missing_lead_or_half_lead_rejected(self):
        for fields in ({"lead": ""}, {"lead": "", "lead_github_username": ""}):
            with self.subTest(fields=fields), self.assertRaises(Invalid):
                self.parse(issue_fixture(**fields))

    def test_optional_blank_code_blocks_are_empty_lists(self):
        record = self.parse(issue_fixture(contributor_1_name="_No response_", contributor_1_github_username="_No response_", related_links="```text\n\n```"))
        self.assertEqual(record["contributors"], [])
        self.assertEqual(record["related_links"], [])

    def test_empty_required_fields_and_invalid_boolean(self):
        for fields in ({"title": ""}, {"summary": ""}, {"deliverable_ids": ""}, {"phd_thesis": "Maybe"}):
            with self.subTest(fields=fields), self.assertRaises(Invalid):
                self.parse(issue_fixture(**fields))

    def test_duplicate_case_insensitive_contributor_and_lead_rejected(self):
        for changes in ({"contributor_2_name": "Bob Again", "contributor_2_github_username": "BOB"},
                        {"contributor_1_name": "Alice", "contributor_1_github_username": "ALICE"}):
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                self.parse(issue_fixture(**changes))

    def test_invalid_related_links_and_unsafe_github_urls(self):
        for link in ("http://example.org", "https://user:secret@example.org", "https://example.org:bad", "https://"):
            with self.subTest(link=link), self.assertRaises(Invalid):
                self.parse(issue_fixture(related_links=link))
        for link in ("https://evil.example/repo", "https://github.com/org/repo/tree/main"):
            with self.subTest(link=link), self.assertRaises(Invalid):
                self.parse(issue_fixture(github_url=link))

    def test_heading_in_code_block_is_preserved_in_description(self):
        text = "Example:\n```markdown\n### Contributors\nThis is scientific text.\n```"
        self.assertEqual(self.parse(issue_fixture(summary=text))["summary"], text)

    def test_missing_or_duplicate_heading_and_oversize_rejected(self):
        issue = issue_fixture()
        for body in (issue["body"].replace("### Project kind", "### Removed heading"), issue["body"] + "\n### Project kind\nReplacement", "x" * 65537):
            with self.subTest(body=body[:30]), self.assertRaises(Invalid):
                self.parse({**issue, "body": body})

    def test_shell_and_yaml_content_is_only_data(self):
        text = "$(touch /tmp/nope)\n!unsafe tag\n::error::fake annotation"
        record = self.parse(issue_fixture(summary=text))
        self.assertEqual(parse_yaml(project_yaml(record))["summary"], text)

    def test_unlabelled_closed_or_pr_issue_rejected(self):
        for change in ({"labels": []}, {"state": "closed"}, {"pull_request": {"url": "x"}}):
            with self.subTest(change=change), self.assertRaises(Invalid):
                self.parse({**issue_fixture(), **change})


    def test_schema_validates_related_links_outside_issue_intake(self):
        record = self.parse()
        record["related_links"] = ["https://user:secret@example.org"]
        with self.assertRaises(Invalid):
            validate_record(record, project_path(12), self.ctx)


class IntakeIntegrationTests(unittest.TestCase):
    def run_process(self, api, teams=None, warning=None):
        return issue_intake.process(api, teams or Teams(), 12, repo_check=lambda url: warning)

    def test_create_revalidate_and_update_one_pr_and_one_comment(self):
        api = FakeAPI()
        self.assertEqual(self.run_process(api), "created")
        first_head = api.pr["head"]["sha"]
        self.assertRegex(api.pr["body"], r"(?i)\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#12\b")
        self.assertEqual(self.run_process(api), "updated")
        self.assertEqual(api.pr["head"]["sha"], first_head)
        api.issue = issue_fixture(summary="An improved scientific description.")
        self.assertEqual(self.run_process(api), "updated")
        self.assertNotEqual(api.pr["head"]["sha"], first_head)
        self.assertEqual(len([w for w in api.writes if w[:2] == ("POST", "pulls")]), 1)
        self.assertEqual(len(api.comments), 1)
        self.assertEqual(api.dispatches[-1], {"ref": "main", "inputs": {"operation": "validate", "pr_number": "99"}})

    def test_invalid_new_issue_has_feedback_and_no_pr(self):
        api = FakeAPI(issue_fixture(contributor_1_github_username=""))
        self.assertEqual(self.run_process(api), "invalid")
        self.assertIsNone(api.pr)
        self.assertIn("project-submissions/issue-12", api.refs)
        self.assertNotIn('project-workflow/state', api.refs)

    def test_invalid_edit_blocks_previously_valid_pr(self):
        api = FakeAPI()
        self.run_process(api)
        previous_yaml = api.data[api.pr['head']['sha']][project_path(12)]
        api.issue = issue_fixture(kind="KEY")
        self.assertEqual(self.run_process(api), "invalid")
        self.assertEqual(api.data[api.pr['head']['sha']][project_path(12)], previous_yaml)
        self.assertEqual(api.statuses[-1][1], "failure")
        self.assertEqual(len(api.dispatches), 1)

    def test_removing_label_blocks_existing_pr(self):
        api = FakeAPI()
        self.run_process(api)
        api.issue['labels'] = []
        self.assertEqual(self.run_process(api), 'invalid')
        self.assertEqual(api.statuses[-1][1], 'failure')

    def test_interrupted_pr_creation_reuses_orphan_branch(self):
        api = FakeAPI()
        original = api.request
        def interrupted(method, endpoint, payload):
            if endpoint == "pulls":
                raise urllib.error.HTTPError("https://api.github.com", 500, "test", {}, None)
            return original(method, endpoint, payload)
        api.request = interrupted
        with self.assertRaises(urllib.error.HTTPError):
            self.run_process(api)
        head = api.refs['project-submissions/issue-12']
        self.assertIsNone(api.pr)
        api.request = original
        self.run_process(api)
        self.assertEqual(api.pr['head']['sha'], head)
        self.assertEqual(len([w for w in api.writes if w[:2] == ('POST', 'pulls')]), 1)

    def test_nonmember_and_revoked_member_cannot_publish(self):
        api = FakeAPI()
        self.assertEqual(self.run_process(api, Teams(member=False)), "invalid")
        self.assertIsNone(api.pr)
        self.run_process(api)
        self.assertEqual(self.run_process(api, Teams(member=False)), "invalid")
        self.assertEqual(api.statuses[-1][1], "failure")

    def test_issue_changed_during_validation_does_not_publish(self):
        api = FakeAPI()
        def checker(url):
            api.issue = issue_fixture(summary="Changed during processing")
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=checker), "invalid")
        self.assertIsNone(api.pr)

    def test_project_id_collision_never_overwritten(self):
        api = FakeAPI()
        ctx = load_context(api.base.__getitem__, paths=api.base)
        api.base[project_path(12)] = project_yaml(proposal(api.issue, ctx, organization_member=True, is_sc=False))
        self.assertEqual(self.run_process(api), "invalid")
        self.assertIsNone(api.pr)

    def test_closed_or_merged_pr_is_not_duplicated(self):
        for merged in (False, True):
            api = FakeAPI()
            self.run_process(api)
            api.pr["state"] = "closed"
            api.pr["merged_at"] = "2026-10-11" if merged else None
            count = len(api.writes)
            result = self.run_process(api)
            self.assertEqual(result, "already-merged" if merged else "closed-pr")
            self.assertFalse(any(w[1] == "pulls" for w in api.writes[count:]))


    def test_unrelated_branch_edits_are_not_overwritten(self):
        api = FakeAPI()
        self.run_process(api)
        api.data[api.pr["head"]["sha"]]["project_workflow/evil.py"] = "print('unsafe')"
        self.assertEqual(self.run_process(api), "invalid")

    def test_bot_pr_policy_rechecks_live_issue_and_publishes_on_head(self):
        api = FakeAPI()
        self.run_process(api)
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text("{}")
            env = {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": api.repo,
                   "GITHUB_TOKEN": "dummy", "SC_TEAM_READ_TOKEN": "dummy",
                   "POLICY_PR_NUMBER": "99"}
            with patch.dict(os.environ, env), patch.object(sys, "argv", ["validate_pr", "--publish-status"]), \
                 patch.object(validate_pr, "GitHub", return_value=api), patch.object(validate_pr, "TeamDirectory", return_value=Teams()), \
                 patch.object(validate_pr.subprocess, "check_output", return_value=BASE_SHA + "\n"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(validate_pr.main(), 0)
                self.assertEqual(api.statuses[-1][:2], (api.pr["head"]["sha"], "success"))
                api.issue = issue_fixture(kind="KEY")
                self.assertEqual(validate_pr.main(), 1)
                self.assertEqual(api.statuses[-1][1], "failure")


class BotSecurityTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        issue_intake.process(self.api, Teams(), 12, repo_check=lambda url: None)
        self.ctx = load_context(self.api.base.__getitem__, paths=self.api.base)

    def check(self, pr=None, issue=None):
        pr = pr or self.api.pr
        base_tree = self.api.tree(BASE_SHA)
        head_tree = self.api.tree(self.api.pr["head"]["sha"])
        return validate_bot_snapshot(base_tree, head_tree, self.api.base.__getitem__, self.api.reader(head_tree), pr,
            issue or self.api.issue, self.ctx, organization_member=True, is_sc=False,
            input_values=issue_intake.revisions.input_values(self.api, issue or self.api.issue, self.ctx))

    def test_real_generated_proposal_passes(self):
        self.check()

    def test_spoofed_bot_author_or_branch_is_rejected(self):
        for user in ({**BOT, "id": 7}, {**BOT, "type": "User"}, {**BOT, "login": "fake[bot]"}):
            with self.subTest(user=user), self.assertRaises(Invalid):
                self.check({**self.api.pr, "user": user})
        pr = deepcopy(self.api.pr)
        pr["head"]["ref"] = "project-submissions/issue-13"
        with self.assertRaises(Invalid):
            self.check(pr)

    def test_forged_metadata_and_code_changes_are_rejected(self):
        data = self.api.data[self.api.pr["head"]["sha"]]
        original = data[project_path(12)]
        record = parse_yaml(original)
        record["submission"]["submitted_by"] = "sc-user"
        data[project_path(12)] = project_yaml(record)
        with self.assertRaises(Invalid):
            self.check()
        data[project_path(12)] = original
        data[".github/workflows/new-project.yml"] = "unsafe"
        with self.assertRaises(Invalid):
            self.check()

    def test_manual_edits_to_generated_record_must_match_issue(self):
        data = self.api.data[self.api.pr["head"]["sha"]]
        record = parse_yaml(data[project_path(12)])
        record["summary"] = "Unrequested rewrite"
        data[project_path(12)] = project_yaml(record)
        with self.assertRaises(Invalid):
            self.check()


class OrgMembershipTests(unittest.TestCase):
    def test_active_pending_absent_and_api_failures(self):
        for state, role, expected in (("active", "member", True), ("active", "admin", True), ("pending", "member", False)):
            api = validate_pr.TeamDirectory("dummy")
            api.get = lambda path: {"state": state, "role": role}
            self.assertEqual(api.is_org_member("test-org", "alice"), expected)
        for status in (404, 401, 403, 429, 500):
            api = validate_pr.TeamDirectory("dummy")
            def get(path):
                if "/memberships/" in path:
                    raise urllib.error.HTTPError("https://api.github.com", status, "test", {}, None)
                return {}
            api.get = get
            if status == 404:
                self.assertFalse(api.is_org_member("test-org", "alice"))
            else:
                with self.assertRaises(urllib.error.HTTPError):
                    api.is_org_member("test-org", "alice")

    def test_injected_identity_is_not_used_in_api_path(self):
        api = validate_pr.TeamDirectory("dummy")
        api.get = lambda path: self.fail("Must not request invalid identity")
        for actor in ("../alice", "bot[bot]", "alice?x=1"):
            self.assertFalse(api.is_org_member("test-org", actor))


if __name__ == "__main__":
    unittest.main()
