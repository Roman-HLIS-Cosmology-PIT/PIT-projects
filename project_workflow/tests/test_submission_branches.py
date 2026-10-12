"""Offline branch isolation, cancellation and safe state migration checks."""
from copy import deepcopy
import json
import unittest

from project_workflow import issue_intake, revisions, workflow_state
from project_workflow.project_registry import Invalid
from project_workflow.submission import changed_files, project_path, submission_branch
import test_corrections
from test_issue_submission import BASE_SHA, Teams, issue_fixture


class SubmissionBranchTests(unittest.TestCase):
    def setUp(self):
        self.helper = test_corrections.CorrectionTests()
        self.helper.setUp()
        self.api = self.helper.api
        self.branch = submission_branch(12)

    def close(self, n=12):
        (self.api.issue if n == 12 else self.api.other_issues[n])['state'] = 'closed'
        return self.helper.process(n)

    def replace(self, **changes):
        self.helper.command()
        self.helper.replacement(self.helper.params(marker='<!-- pit-project-edit:v1 -->'), **changes)
        return self.helper.process(13)

    def test_invalid_submission_has_one_branch_without_pr_and_cancels(self):
        self.api.issue = issue_fixture(kind='KEY')
        self.assertEqual(self.helper.process(), 'invalid')
        self.assertEqual(self.api.tree(self.api.refs[self.branch]), self.api.tree(BASE_SHA))
        self.assertNotIn(workflow_state.LEGACY_BRANCH, self.api.refs)
        self.assertIsNone(self.api.pr)
        self.assertEqual(self.close(), 'cancelled')
        self.assertNotIn(self.branch, self.api.refs)
        self.assertEqual(self.close(), 'cancelled')
        self.assertNotIn(self.branch, self.api.refs)

    def test_valid_issue_and_pr_share_branch_and_cancellation_closes_both(self):
        self.helper.process()
        self.assertEqual(set(self.api.refs), {'main', self.branch})
        self.assertEqual(self.api.pr['head']['ref'], self.branch)
        self.assertEqual(changed_files(self.api.tree(BASE_SHA), self.api.tree(self.api.refs[self.branch])), {project_path(12)})
        self.assertEqual(self.close(), 'cancelled')
        self.assertEqual(self.api.pr['state'], 'closed')
        self.assertIsNone(self.api.pr['merged_at'])
        self.assertNotIn(self.branch, self.api.refs)
        self.assertEqual(self.close(), 'cancelled')

    def test_superseded_closure_keeps_branch_and_latest_closure_cancels(self):
        self.helper.process()
        self.assertEqual(self.replace(), 'updated')
        head = self.api.refs[self.branch]
        self.assertEqual(self.close(12), 'superseded')
        self.assertEqual(self.api.refs[self.branch], head)
        self.assertEqual(self.api.pr['state'], 'open')
        self.assertEqual(self.close(13), 'cancelled')
        self.assertEqual(self.api.pr['state'], 'closed')
        self.assertNotIn(self.branch, self.api.refs)

    def test_invalid_replacement_can_cancel_existing_pr(self):
        self.helper.process()
        self.assertEqual(self.replace(kind='KEY'), 'invalid')
        self.assertEqual(self.close(13), 'cancelled')
        self.assertEqual(self.api.pr['state'], 'closed')
        self.assertNotIn(self.branch, self.api.refs)

    def test_merge_closure_accepts_even_when_github_already_deleted_branch(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                self.setUp()
                self.helper.process()
                self.api.pr.update(state='closed', merged_at='2026-10-12T12:00:00Z')
                if deleted:
                    del self.api.refs[self.branch]
                writes = len(self.api.writes)
                self.assertEqual(self.close(), 'already-merged')
                self.assertFalse(any(w[0] == 'DELETE' or w[1].endswith('/comments') for w in self.api.writes[writes:]))
                self.assertIsNone(issue_intake.cancelled_root(self.api, 12))

    def test_reopening_cancelled_issue_does_not_resurrect_branch_or_pr(self):
        self.helper.process()
        self.close()
        self.api.issue['state'] = 'open'
        self.assertEqual(self.helper.process(), 'cancelled')
        self.assertNotIn(self.branch, self.api.refs)
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ('POST', 'pulls')]), 1)

    def test_cleanup_retry_after_delete_failure_finishes_without_new_pr(self):
        self.helper.process()
        original = self.api.request
        def interrupted(method, endpoint, payload):
            if method == 'DELETE' and endpoint.startswith('git/refs/'):
                raise OSError('Temporary delete failure')
            return original(method, endpoint, payload)
        self.api.request = interrupted
        with self.assertRaises(OSError):
            self.close()
        self.assertEqual(self.api.pr['state'], 'closed')
        self.assertIn(self.branch, self.api.refs)
        self.api.request = original
        self.assertEqual(self.close(), 'cancelled')
        self.assertNotIn(self.branch, self.api.refs)

    def test_two_active_proposals_have_independent_branches_and_prs(self):
        self.helper.process()
        first_head = self.api.refs[self.branch]
        self.api.other_issues[21] = {**issue_fixture(), 'number': 21, 'title': 'Second proposal'}
        self.assertEqual(self.helper.process(21), 'created')
        self.assertEqual(set(self.api.refs), {'main', self.branch, submission_branch(21)})
        self.assertEqual(len(self.api.prs), 2)
        self.assertEqual(self.api.refs[self.branch], first_head)
        self.assertEqual(self.close(21), 'cancelled')
        self.assertEqual(self.api.prs[99]['state'], 'open')
        self.assertEqual(self.api.refs[self.branch], first_head)
        self.assertEqual(self.api.prs[100]['state'], 'closed')
        self.assertNotIn(submission_branch(21), self.api.refs)

    def test_cleanup_never_deletes_unrelated_manual_branch_changes(self):
        self.helper.process()
        self.api.data[self.api.refs[self.branch]]['unexpected.txt'] = 'manual change\n'
        with self.assertRaisesRegex(Invalid, 'unrelated changes'):
            self.close()
        self.assertIn(self.branch, self.api.refs)
        self.assertEqual(self.api.pr['state'], 'open')

    def test_closed_issue_cleanup_does_not_require_current_membership_or_labels(self):
        self.helper.process()
        self.api.issue.update(state='closed', labels=[])
        self.assertEqual(issue_intake.process(self.api, Teams(member=False), 12), 'cancelled')
        self.assertNotIn(self.branch, self.api.refs)
        self.assertEqual(self.api.pr['state'], 'closed')

    def test_existing_shared_state_migrates_without_new_shared_commits(self):
        self.helper.process()
        sha = self.api.refs[self.branch]
        data = deepcopy(workflow_state.snapshot(self.api, 12)[2])
        legacy_sha = 'b' * 40
        self.api.data[legacy_sha] = {path: json.dumps(value) for path, value in data.items()}
        self.api.refs[workflow_state.LEGACY_BRANCH] = legacy_sha
        self.api.commits[sha]['message'] = 'Project proposal from Issue #12'
        self.api.issue['body'] = revisions.ROUTE.sub('', self.api.issue['body'])
        packet = data[workflow_state.issue_path(12)]
        packet['body_sha256'] = revisions.digest(self.api.issue['body'])
        self.api.data[legacy_sha][workflow_state.issue_path(12)] = json.dumps(packet)
        writes = len(self.api.writes)
        self.assertEqual(self.helper.process(), 'updated')
        self.assertEqual(self.helper.policy(), 0)
        self.assertEqual(self.api.refs[workflow_state.LEGACY_BRANCH], legacy_sha)
        self.assertFalse(any(workflow_state.LEGACY_BRANCH in w[1] for w in self.api.writes[writes:]))
        del self.api.refs[workflow_state.LEGACY_BRANCH]
        self.assertEqual(self.helper.policy(), 0)


if __name__ == '__main__':
    unittest.main()
