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
    values = { "lead": "Alice Example", "lead_github_username": "alice",
              "working_groups": "SCM — Shape and Color Measurement",
              "deliverables_SCM": "SCM-1", "deliverables_CAS": "_No response_", "deliverables_CPIP": "_No response_",
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

    def request(self, method, endpoint, payload=None):
        self.writes.append((method, endpoint, deepcopy(payload)))
        if endpoint == "labels":
            return payload
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
        self.data=source_data();self.ctx=load_context(self.data.__getitem__,paths=self.data)

    def parse(self, issue=None, **kwargs):
        return proposal(issue or issue_fixture(),self.ctx,organization_member=kwargs.get('member',True),is_sc=kwargs.get('sc',False))

    def test_valid_ep_submission_and_submission_date(self):
        record=self.parse()
        self.assertEqual(record['kind'],'EP')
        self.assertEqual(record['working_groups'],[{'code':'SCM','deliverable_ids':['SCM-1']}])
        self.assertEqual(record['submission']['submitted_at'],'2026-10-11T06:00:00Z')
        self.assertEqual(parse_yaml(project_yaml(record)),record)

    def test_multiple_groups_empty_deliverables_and_ignored_unchecked_values(self):
        record=self.parse(issue_fixture(working_groups='SCM — Shape and Color Measurement, CAS — Catalogue And Statistics',deliverables_SCM='_No response_',deliverables_CAS='CAS-1',deliverables_CPIP='bogus'))
        self.assertEqual(record['working_groups'],[{'code':'SCM','deliverable_ids':[]},{'code':'CAS','deliverable_ids':['CAS-1']}])

    def test_required_fields_and_choices_fail_closed(self):
        for change in ({'lead':''},{'lead_github_username':''},{'working_groups':'_No response_'},
                       {'working_groups':'UNKNOWN'},{'deliverables_SCM':'CAS-1'},
                       {'phd_thesis':'bogus'},{'related_links':'javascript:alert(1)'},
                       {'lead_github_username':'@alice'},{'contributor_1_name':''}):
            with self.subTest(change=change),self.assertRaises(Invalid):self.parse(issue_fixture(**change))

    def test_nonmember_and_impersonation_rejected(self):
        with self.assertRaises(Invalid):self.parse(member=False)
        with self.assertRaises(Invalid):self.parse(issue_fixture(lead_github_username='bob'))
        self.parse(issue_fixture(lead_github_username='carol'),sc=True)

    def test_cpp_cannot_be_injected_through_body(self):
        i=issue_fixture();i['body']+='\n\n### Project kind\n\nCPP'
        with self.assertRaises(Invalid):self.parse(i)

    def test_headings_in_fences_remain_literal(self):
        description='```text\n### Working groups\n- [x] CAS\n```\nScientific goals'
        self.assertEqual(self.parse(issue_fixture(summary=description))['summary'],description)

    def test_duplicate_headings_are_rejected(self):
        i=issue_fixture();i['body']+='\n\n### Description\n\nInjected'
        with self.assertRaises(Invalid):self.parse(i)

    def test_submit_retry_keeps_one_pr_and_one_branch(self):
        api=FakeAPI();raw=api.issue['body']
        self.assertEqual(issue_intake.process(api,Teams(),12),'created')
        self.assertEqual(issue_intake.process(api,Teams(),12),'updated')
        self.assertEqual(len([x for x in api.writes if x[1]=='pulls']),1)
        self.assertEqual(set(api.refs),{'main','project-submissions/issue-12'})
        from project_workflow.submission import submission_body
        self.assertEqual(submission_body(api.issue),raw)
        self.assertTrue(api.issue['body'].startswith('<!-- pit-project-summary:v2 -->'))
        self.assertIn('**Project title**:',api.issue['body'])
        self.assertEqual(len(api.comments),1)
        self.assertIn('Closes #12',api.pr['body'])
        review=next(x[2] for x in api.writes if x[1].endswith('/requested_reviewers'))
        self.assertEqual(review,{'reviewers':['aguinot'],'team_reviewers':[]})
        self.assertEqual(api.dispatches[-1]['inputs'],{'operation':'validate','pr_number':'99'})

    def test_invalid_submission_makes_no_branch_or_pr(self):
        api=FakeAPI(issue_fixture(lead=''))
        self.assertEqual(issue_intake.process(api,Teams(),12),'invalid')
        self.assertIsNone(api.pr);self.assertEqual(set(api.refs),{'main'})
        self.assertIn('invalid',{x['name'] for x in api.issue['labels']})
        self.assertNotIn('correct-project',api.comments[0]['body'])

    def test_nonmember_cannot_create_branch(self):
        api=FakeAPI()
        self.assertEqual(issue_intake.process(api,Teams(member=False),12),'invalid')
        self.assertEqual(set(api.refs),{'main'})

    def test_two_submissions_use_distinct_branches(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.other_issues[13]={**issue_fixture(), 'number':13}
        issue_intake.process(api,Teams(),13)
        self.assertEqual(len(api.prs),2)
        self.assertIn('project-submissions/issue-13',api.refs)

    def test_manual_issue_change_blocks_pr_without_editing_proposal(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        head=api.pr['head']['sha'];body=api.pr['body']
        api.issue['body']=issue_fixture(summary='Updated scientific goals')['body']
        self.assertEqual(issue_intake.process(api,Teams(),12),'invalid')
        self.assertEqual(api.pr['head']['sha'],head)
        self.assertEqual(api.pr['body'],body)
        self.assertEqual(api.statuses[-1][1],'failure')

    def test_invalid_edit_blocks_existing_pr_without_overwriting_yaml(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        head=api.pr['head']['sha'];api.issue['body']=issue_fixture(lead='')['body']
        self.assertEqual(issue_intake.process(api,Teams(),12),'invalid')
        self.assertEqual(api.pr['head']['sha'],head)
        self.assertEqual(api.statuses[-1][1],'failure')

    def test_closed_issue_cancels_pr_and_branch_without_member_token(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12);api.issue['state']='closed'
        self.assertEqual(issue_intake.process(api,None,12),'cancelled')
        self.assertEqual(api.pr['state'],'closed');self.assertEqual(set(api.refs),{'main'})
        self.assertEqual(issue_intake.process(api,None,12),'cancelled')
        api.issue['state']='open'
        self.assertEqual(issue_intake.process(api,Teams(),12),'cancelled')
        self.assertEqual(set(api.refs),{'main'})

    def test_merged_pr_is_not_cancelled(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.pr.update(state='closed',merged_at='2026-10-12T00:00:00Z');api.issue['state']='closed'
        self.assertEqual(issue_intake.process(api,None,12),'already-merged')
        self.assertIn('project-submissions/issue-12',api.refs)

    def test_unrelated_branch_edits_are_never_deleted(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.data[api.pr['head']['sha']]['README.md']='manual change'
        api.issue['state']='closed'
        with self.assertRaises(Invalid):issue_intake.process(api,None,12)
        self.assertIn('project-submissions/issue-12',api.refs)
        self.assertEqual(api.pr['state'],'open')

    def test_edited_issue_invalidates_independent_pr_check(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        pr=api.pr;head=api.tree(pr['head']['sha']);base=api.tree(BASE_SHA)
        validate_bot_snapshot(base,head,api.reader(base),api.reader(head),pr,api.issue,self.ctx,organization_member=True,is_sc=False)
        api.issue['title']='Tampered title'
        with self.assertRaises(Invalid):validate_bot_snapshot(base,head,api.reader(base),api.reader(head),pr,api.issue,self.ctx,organization_member=True,is_sc=False)

    def test_unrelated_project_added_to_bot_pr_is_rejected(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.data[api.pr['head']['sha']]['projects/P000013.yaml']='{}'
        head=api.tree(api.pr['head']['sha']);base=api.tree(BASE_SHA)
        with self.assertRaises(Invalid):validate_bot_snapshot(base,head,api.reader(base),api.reader(head),api.pr,api.issue,self.ctx,organization_member=True,is_sc=False)

    def test_unlabelled_and_unrelated_closed_issues_are_ignored(self):
        api=FakeAPI();api.issue['labels']=[]
        self.assertEqual(issue_intake.process(api,Teams(),12),'ignored')
        api.issue['state']='closed'
        self.assertEqual(issue_intake.process(api,None,12),'ignored-closure')

    def test_pr_summary_neutralizes_mentions_and_closing_keywords(self):
        from project_workflow.review import proposal_body
        record=self.parse(issue_fixture(summary='@someone Closes #99\nFixes https://github.com/test-org/registry/issues/100'))
        body=proposal_body(record,self.ctx,'test-org/registry',12)
        self.assertNotIn('@someone',body);self.assertNotIn('Closes #99',body)
        self.assertNotIn('Fixes https://github.com',body)
        self.assertIn('Closes #12',body)

class FormattingTests(unittest.TestCase):
    def test_visible_summary_change_blocks_independent_validation(self):
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.issue['body']=api.issue['body'].replace('**Project title**:', '**Project title changed**:')
        ctx=load_context(api.base.__getitem__,paths=api.base)
        with self.assertRaises(Invalid):proposal(api.issue,ctx,organization_member=True,is_sc=False)

    def test_retry_recovers_after_formatting_before_branch_creation(self):
        api=FakeAPI();raw=api.issue['body'];request=api.request
        def interrupt(method,endpoint,payload=None):
            if endpoint=='git/trees':raise OSError('Simulated interruption')
            return request(method,endpoint,payload)
        api.request=interrupt
        with self.assertRaises(OSError):issue_intake.process(api,Teams(),12)
        self.assertTrue(api.issue['body'].startswith('<!-- pit-project-summary:v2 -->'))
        self.assertEqual(set(api.refs),{'main'})
        api.request=request
        self.assertEqual(issue_intake.process(api,Teams(),12),'created')
        self.assertEqual(len(api.prs),1)
        from project_workflow.submission import submission_body
        self.assertEqual(submission_body(api.issue),raw)

    def test_malformed_hidden_source_is_rejected(self):
        from project_workflow.submission import submission_body
        api=FakeAPI();issue_intake.process(api,Teams(),12)
        api.issue['body']=api.issue['body'].replace('pit-project-source:','pit-project-source:broken')
        with self.assertRaises(Invalid):submission_body(api.issue)

    def test_compressed_source_is_size_bounded(self):
        import base64,zlib
        from project_workflow.submission import submission_body,FORMATTED_MARKER
        encoded=base64.b64encode(zlib.compress(b'x'*100000)).decode('ascii')
        i=issue_fixture();i['body']=FORMATTED_MARKER+f'\n<!-- pit-project-source:{encoded} -->\n'
        with self.assertRaises(Invalid):submission_body(i)

    def test_large_submission_is_preserved_without_truncation(self):
        import random,string
        rng=random.Random(12)
        description=''.join(rng.choices(string.ascii_letters+string.digits,k=45000))
        api=FakeAPI(issue_fixture(summary=description));raw=api.issue['body']
        self.assertEqual(issue_intake.process(api,Teams(),12),'created')
        self.assertEqual(api.issue['body'],raw)
        self.assertEqual(parse_yaml(api.data[api.pr['head']['sha']][project_path(12)])['summary'],description)
