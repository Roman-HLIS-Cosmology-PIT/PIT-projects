"""Unmerged review PR closure terminates only its verified submission."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_workflow import claims, commands, issue_intake
from project_workflow.project_registry import Invalid, load_context, parse_yaml
from test_commands import CommandTeams
from test_cpp_claims import ClaimAPI, PATH, claim_issue
from test_cpp_declarations import cpp_issue
from test_issue_submission import FakeAPI, BASE_SHA


class RejectionTests(unittest.TestCase):
    def setUp(self):
        lookup = patch('project_workflow.issue_intake.urllib.request.build_opener')
        lookup.start()
        self.addCleanup(lookup.stop)

    def submitted(self,kind='EP',*,corrected=False):
        api = ClaimAPI() if kind == 'CLAIM' else FakeAPI(cpp_issue()) if kind == 'CPP' else FakeAPI()
        teams = CommandTeams(sc={'alice'} if kind == 'CPP' else ())
        self.assertEqual(issue_intake.process(api,teams,12,repo_check=lambda u:None),'created')
        if corrected:
            comment = {'id':1000,'body':'/edit slack #corrected',
                       'user':deepcopy(api.issue['user']),
                       'issue_url':'https://api.github.com/repos/test-org/registry/issues/12'}
            api.comments.append(comment)
            self.assertEqual(commands.process(api,teams,12,comment),'edited')
        return api,teams

    def close_pr(self,api):
        api.prs[99]['state'] = 'closed'

    def test_ep_cpp_and_claim_rejection_after_correction_closes_issue_and_deletes_branch(self):
        for kind in ('EP','CPP','CLAIM'):
            with self.subTest(kind=kind):
                api,_ = self.submitted(kind,corrected=True)
                main = deepcopy(api.data[BASE_SHA])
                self.close_pr(api)
                self.assertEqual(issue_intake.reject_submission(api,99),'rejected')
                self.assertEqual(api.issue['state'],'closed')
                self.assertEqual(api.issue['state_reason'],'not_planned')
                self.assertEqual(api.prs[99]['state'],'closed')
                self.assertNotIn('project-submissions/issue-12',api.refs)
                self.assertEqual(api.data[BASE_SHA],main)
                self.assertEqual(api.refs['main'],BASE_SHA)
                self.assertEqual(api.statuses[-1][1],'failure')
                feedback = [c for c in api.comments if c['body'].startswith(issue_intake.CANCEL_MARKER)]
                self.assertEqual(len(feedback),1)
                self.assertIn('closed without acceptance',feedback[0]['body'])

    def test_rejected_claim_releases_cpp_to_another_member(self):
        api,teams = self.submitted('CLAIM',corrected=True)
        original = deepcopy(parse_yaml(api.data[BASE_SHA][PATH]))
        self.close_pr(api)
        issue_intake.reject_submission(api,99)
        ctx = load_context(api.base.__getitem__,paths=api.base)
        self.assertTrue(claims.eligible(ctx.projects[original['project_id']]))
        self.assertTrue(any(x.startswith(original['project_id']+' — ') for x in claims.project_options(ctx)))
        other = claim_issue(ctx,lead='Jane Smith',lead_github_username='janesmith')
        other.update(number=13,user={'login':'janesmith','id':456,'type':'User'})
        api.other_issues[13] = other
        self.assertEqual(issue_intake.process(api,teams,13,repo_check=lambda u:None),'created')
        self.assertEqual(api.prs[100]['state'],'open')
        self.assertEqual(parse_yaml(api.data[BASE_SHA][PATH]),original)
        claimed = parse_yaml(api.data[api.prs[100]['head']['sha']][PATH])
        self.assertEqual(claimed['leads'][0]['github_username'],'janesmith')
        # A delayed duplicate closure must never cancel the new member's proposal.
        self.assertEqual(issue_intake.reject_submission(api,99),'rejected')
        self.assertEqual(api.prs[100]['state'],'open')
        self.assertIn('project-submissions/issue-13',api.refs)

    def test_repeated_close_event_is_idempotent(self):
        api,_ = self.submitted()
        self.close_pr(api)
        issue_intake.reject_submission(api,99)
        writes = deepcopy(api.writes)
        self.assertEqual(issue_intake.reject_submission(api,99),'rejected')
        self.assertEqual(sum(m=='DELETE' and p.startswith('git/refs/') for m,p,d in api.writes),1)
        self.assertEqual(sum(m=='PATCH' and p=='issues/12' and d.get('state')=='closed' for m,p,d in api.writes),1)
        self.assertEqual(len([c for c in api.comments if c['body'].startswith(issue_intake.CANCEL_MARKER)]),1)
        self.assertEqual(api.writes,writes)

    def test_already_deleted_branch_still_closes_verified_issue(self):
        api,_ = self.submitted('CLAIM')
        self.close_pr(api)
        api.refs.pop('project-submissions/issue-12')
        self.assertEqual(issue_intake.reject_submission(api,99),'rejected')
        self.assertEqual(api.issue['state'],'closed')
        self.assertTrue(claims.eligible(parse_yaml(api.base[PATH])))

    def test_merged_pr_never_closes_issue_as_rejected_or_deletes_branch(self):
        for kind in ('EP','CPP','CLAIM'):
            with self.subTest(kind=kind):
                api,_ = self.submitted(kind)
                api.prs[99].update(state='closed',merged_at='2026-10-15T00:00:00Z')
                writes = deepcopy(api.writes)
                self.assertEqual(issue_intake.reject_submission(api,99),'already-merged')
                self.assertEqual(api.writes,writes)
                self.assertEqual(api.issue['state'],'open')
                self.assertIn('project-submissions/issue-12',api.refs)

    def test_stale_closed_event_for_reopened_pr_does_nothing(self):
        api,_ = self.submitted()
        writes = deepcopy(api.writes)
        self.assertEqual(issue_intake.reject_submission(api,99),'closure-reversed')
        self.assertEqual(api.writes,writes)
        self.assertEqual(api.issue['state'],'open')

    def test_unrelated_human_fork_generated_or_other_base_prs_are_ignored(self):
        for change in ('human','fork','generated','other-base'):
            with self.subTest(change=change):
                api,_ = self.submitted()
                self.close_pr(api)
                pr = api.prs[99]
                if change=='human':pr['user']={'login':'sc-user','id':123,'type':'User'}
                elif change=='fork':pr['head']['repo']['full_name']='someone/fork'
                elif change=='generated':pr['head']['ref']='project-config/generated'
                else:pr['base']['ref']='develop'
                writes = deepcopy(api.writes)
                self.assertEqual(issue_intake.reject_submission(api,99),'ignored')
                self.assertEqual(api.writes,writes)

    def test_unrelated_branch_content_is_not_deleted_and_issue_stays_open(self):
        api,_ = self.submitted('CLAIM')
        self.close_pr(api)
        api.data[api.pr['head']['sha']]['README.md']='unrelated content'
        writes = deepcopy(api.writes)
        with self.assertRaises(Invalid):issue_intake.reject_submission(api,99)
        self.assertEqual(api.writes,writes)
        self.assertEqual(api.issue['state'],'open')
        self.assertIn('project-submissions/issue-12',api.refs)

    def test_branch_head_change_before_cleanup_fails_without_closing_issue(self):
        api,_ = self.submitted()
        self.close_pr(api)
        api.prs[99]['head']['sha']='f'*40
        writes = deepcopy(api.writes)
        with self.assertRaises(Invalid):issue_intake.reject_submission(api,99)
        self.assertEqual(api.writes,writes)
        self.assertEqual(api.issue['state'],'open')

    def test_reopen_or_merge_during_verification_does_not_delete_or_close(self):
        for merged in (False,True):
            with self.subTest(merged=merged):
                api,_ = self.submitted()
                self.close_pr(api)
                get = api.get
                count = 0
                def racing(endpoint):
                    nonlocal count
                    if endpoint=='pulls/99':
                        count += 1
                        if count == 2:
                            api.prs[99]['state']='closed' if merged else 'open'
                            api.prs[99]['merged_at']='2026-10-15T00:00:00Z' if merged else None
                    return get(endpoint)
                api.get=racing
                writes = deepcopy(api.writes)
                result = issue_intake.reject_submission(api,99)
                self.assertEqual(result,'already-merged' if merged else 'closure-reversed')
                self.assertEqual(api.writes,writes)
                self.assertEqual(api.issue['state'],'open')
                self.assertIn('project-submissions/issue-12',api.refs)

    def test_partial_deletion_failure_can_retry_without_duplicate_comments(self):
        api,_ = self.submitted('CLAIM')
        self.close_pr(api)
        request = api.request
        def interrupted(method,endpoint,payload=None):
            if method=='DELETE' and endpoint.startswith('git/refs/'):
                raise OSError('Interrupted ref deletion')
            return request(method,endpoint,payload)
        api.request=interrupted
        with self.assertRaises(OSError):issue_intake.reject_submission(api,99)
        self.assertEqual(api.issue['state'],'closed')
        api.request=request
        self.assertEqual(issue_intake.reject_submission(api,99),'rejected')
        self.assertNotIn('project-submissions/issue-12',api.refs)
        self.assertEqual(len([c for c in api.comments if c['body'].startswith(issue_intake.CANCEL_MARKER)]),1)

    def test_manual_submit_retries_cleanup_for_an_already_closed_pr_without_membership(self):
        api,_ = self.submitted('CLAIM',corrected=True)
        self.close_pr(api)
        self.assertEqual(issue_intake.process(api,None,12),'rejected')
        self.assertEqual(api.issue['state'],'closed')
        self.assertNotIn('invalid',{label['name'] for label in api.issue['labels']})
        self.assertNotIn('project-submissions/issue-12',api.refs)

    def test_closed_pr_webhook_needs_no_sc_membership_token(self):
        api,_ = self.submitted('CPP')
        self.close_pr(api)
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder)/'event.json'
            event.write_text(json.dumps({'action':'closed','pull_request':api.prs[99]}))
            env={'GITHUB_EVENT_PATH':str(event),'GITHUB_REPOSITORY':api.repo,'GITHUB_TOKEN':'dummy',
                 'SC_TEAM_READ_TOKEN':'','SUBMISSION_ISSUE_NUMBER':''}
            with patch.dict(os.environ,env),patch.object(issue_intake,'GitHub',return_value=api), \
                 patch.object(issue_intake.subprocess,'check_output',return_value=BASE_SHA), \
                 patch.object(issue_intake,'TeamDirectory',side_effect=AssertionError('Membership must not be needed')), \
                 redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(issue_intake.main(),0)
        self.assertEqual(api.issue['state'],'closed')
        self.assertNotIn('project-submissions/issue-12',api.refs)
