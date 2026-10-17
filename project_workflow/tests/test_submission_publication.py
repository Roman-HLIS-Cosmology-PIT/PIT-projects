"""Submission failures must stop before PR creation, without rollback PRs."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_workflow import issue_intake
from project_workflow.project_registry import Invalid
from test_cpp_claims import ClaimAPI
from test_cpp_declarations import cpp_issue
from test_issue_submission import BASE_SHA, FakeAPI, Teams


class PublicationTests(unittest.TestCase):
    def assert_unpublished(self, api):
        self.assertEqual(api.prs, {})
        self.assertFalse(any(endpoint == 'pulls' for _, endpoint, _ in api.writes))
        self.assertFalse(any(endpoint.startswith('pulls/') for _, endpoint, _ in api.writes))

    def run_main(self, api):
        with tempfile.TemporaryDirectory() as directory:
            event, output = Path(directory) / 'event.json', Path(directory) / 'output'
            event.write_text(json.dumps({'action': 'labeled', 'issue': api.issue}))
            env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_OUTPUT': str(output),
                   'GITHUB_REPOSITORY': api.repo, 'GITHUB_TOKEN': 'test',
                   'SC_TEAM_READ_TOKEN': 'test', 'SUBMISSION_ISSUE_NUMBER': '12',
                   'POLICY_INLINE': 'true'}
            with patch.dict(os.environ, env), patch.object(issue_intake, 'GitHub', return_value=api), \
                 patch.object(issue_intake, 'TeamDirectory', return_value=Teams()), \
                 patch.object(issue_intake.subprocess, 'check_output', return_value=BASE_SHA), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return issue_intake.main()

    def test_preparation_api_failures_create_no_pr(self):
        for target in ('issues/12', 'git/trees', 'git/commits', 'git/refs', 'issues/12/comments'):
            with self.subTest(target=target):
                api = FakeAPI()
                request = api.request
                def fail(method, endpoint, payload=None):
                    if endpoint == target:
                        raise OSError('simulated failure before publication')
                    return request(method, endpoint, payload)
                api.request = fail
                self.assertEqual(self.run_main(api), 1)
                self.assert_unpublished(api)

    def test_exact_candidate_policy_rejects_extra_files_before_publication(self):
        api = FakeAPI()
        request = api.request
        def corrupt(method, endpoint, payload=None):
            result = request(method, endpoint, payload)
            if endpoint == 'git/trees':
                api.data[result['sha']]['unrelated.txt'] = 'unexpected change'
            return result
        api.request = corrupt
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=lambda _: None), 'invalid')
        self.assert_unpublished(api)
        self.assertIn('single project YAML', api.comments[0]['body'])

    def test_policy_error_and_status_publication_failure_create_no_pr(self):
        for stage in ('policy', 'status'):
            with self.subTest(stage=stage):
                api = FakeAPI()
                target = 'validate_submission_snapshot' if stage == 'policy' else None
                if target:
                    with patch.object(issue_intake, target, side_effect=Invalid('policy rejected')):
                        self.assertEqual(self.run_main(api), 0)
                else:
                    with patch.object(api, 'status', side_effect=OSError('status unavailable')):
                        self.assertEqual(self.run_main(api), 1)
                self.assert_unpublished(api)

    def test_permissions_revoked_at_final_check_create_no_pr(self):
        api = FakeAPI()
        teams = Teams()
        status = api.status
        def revoke(*args):
            status(*args)
            teams.member = False
        api.status = revoke
        self.assertEqual(issue_intake.process(api, teams, 12, repo_check=lambda _: None), 'invalid')
        self.assert_unpublished(api)

    def test_main_advance_since_checkout_cannot_publish_a_pr(self):
        api = FakeAPI()
        with self.assertRaises(OSError):
            issue_intake.process(api, Teams(), 12, trusted_base='b' * 40)
        self.assertEqual(api.issue['state'], 'open')
        self.assert_unpublished(api)

    def test_unwritable_handoff_is_detected_before_pr_creation(self):
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, POLICY_INLINE='true', GITHUB_OUTPUT=directory):
            with self.assertRaises(OSError):
                issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assert_unpublished(api)

    def test_ep_cpp_and_claim_policy_and_status_finish_before_pr_post(self):
        for api, teams in ((FakeAPI(), Teams()), (FakeAPI(cpp_issue()), Teams(sc=True)),
                           (ClaimAPI(), Teams())):
            with self.subTest(labels=api.issue['labels']):
                validate = issue_intake.validate_submission_snapshot
                request = api.request
                passed = []
                def preflight(*args, **kwargs):
                    self.assertIsNone(api.pr)
                    result = validate(*args, **kwargs)
                    passed.append(True)
                    return result
                def publish(method, endpoint, payload=None):
                    if endpoint == 'pulls':
                        self.assertEqual(passed, [True])
                        self.assertIn('Submission checks passed', api.comments[0]['body'])
                        self.assertEqual(api.statuses[-1][1], 'success')
                        self.assertEqual(api.refs[payload['head']], api.statuses[-1][0])
                    return request(method, endpoint, payload)
                api.request = publish
                with patch.object(issue_intake, 'validate_submission_snapshot', side_effect=preflight):
                    self.assertEqual(issue_intake.process(api, teams, 12, repo_check=lambda _: None), 'created')

    def test_lost_creation_response_reconciles_success_without_duplicate_or_closure(self):
        api = FakeAPI()
        issue_intake.feedback(api, 12, '**Submission automation could not complete.**')
        request = api.request
        def lose_response(method, endpoint, payload=None):
            result = request(method, endpoint, payload)
            if endpoint == 'pulls':
                self.assertIn('Submission checks passed', api.comments[0]['body'])
                self.assertNotIn('could not complete', api.comments[0]['body'])
                raise OSError('accepted by GitHub, response lost')
            return result
        api.request = lose_response
        self.assertEqual(self.run_main(api), 0)
        self.assertEqual(len(api.prs), 1)
        self.assertEqual(api.pr['state'], 'open')
        self.assertIn('Your project has been submitted', api.comments[0]['body'])
        self.assertFalse(any(method == 'PATCH' and endpoint == 'pulls/99'
                             for method, endpoint, _ in api.writes))

    def test_failed_creation_request_creates_no_pr_and_remains_retryable(self):
        api = FakeAPI()
        request = api.request
        def fail(method, endpoint, payload=None):
            if endpoint == 'pulls':
                raise OSError('not accepted by GitHub')
            return request(method, endpoint, payload)
        api.request = fail
        self.assertEqual(self.run_main(api), 1)
        self.assert_unpublished(api)
        self.assertEqual(api.issue['state'], 'open')
        api.request = request
        self.assertEqual(self.run_main(api), 0)
        self.assertEqual(len(api.prs), 1)

    def test_postpublication_delivery_failures_never_report_failed_submission(self):
        for stage in ('reviewer', 'routing', 'confirmation'):
            with self.subTest(stage=stage):
                api = FakeAPI()
                target = {'reviewer': 'request_review', 'routing': 'dispatch_validation',
                          'confirmation': 'feedback'}[stage]
                original = getattr(issue_intake, target)
                def fail_after_publication(*args, **kwargs):
                    if api.pr is not None:
                        raise OSError('delivery unavailable')
                    return original(*args, **kwargs)
                with patch.object(issue_intake, target, side_effect=fail_after_publication):
                    self.assertEqual(self.run_main(api), 0)
                self.assertEqual(len(api.prs), 1)
                self.assertEqual(api.pr['state'], 'open')
                self.assertEqual(api.issue['state'], 'open')
                self.assertEqual(api.statuses[-1][1], 'success')
                self.assertFalse(any('could not be submitted' in c['body'] for c in api.comments))
                if stage != 'confirmation':
                    self.assertIn('Submitted successfully; follow-up delivery needs retry', api.comments[0]['body'])


if __name__ == '__main__':
    unittest.main()
