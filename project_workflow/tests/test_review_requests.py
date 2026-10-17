"""Review-request races and safe diagnostics for late intake API failures."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import urllib.error

from project_workflow import issue_intake
from project_workflow.review import request_review
from project_workflow.validate_pr import GitHub, http_error_message
from test_issue_submission import FakeAPI, Teams


def failure(code=422):
    return urllib.error.HTTPError('https://api.github.com', code, 'private error body', {}, None)


class ReviewRequestTests(unittest.TestCase):
    def api(self, owners=('@aguinot',)):
        api = FakeAPI()
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        api.writes.clear()
        return api, SimpleNamespace(review_owners=owners)

    def test_refresh_avoids_duplicate_automatic_codeowner_request(self):
        api, ctx = self.api()
        stale = deepcopy(api.pr)
        api.pr['requested_reviewers'] = [{'login': 'aguinot'}]
        request_review(api, stale, ctx)
        self.assertEqual(api.writes, [])

    def test_422_with_confirmed_users_and_teams_is_idempotent_success(self):
        api, ctx = self.api(('@aguinot', '@test-org/sc'))
        def assign_then_fail(*args):
            api.pr['requested_reviewers'] = [{'login': 'AGUINOT'}]
            api.pr['requested_teams'] = [{'slug': 'SC'}]
            raise failure()
        api.request = Mock(side_effect=assign_then_fail)
        request_review(api, api.pr, ctx)
        self.assertEqual(api.request.call_count, 1)

    def test_422_without_all_requested_assignments_still_fails(self):
        api, ctx = self.api(('@aguinot', '@test-org/sc'))
        def assign_partially(*args):
            api.pr['requested_reviewers'] = [{'login': 'aguinot'}]
            raise failure()
        api.request = Mock(side_effect=assign_partially)
        with self.assertRaises(urllib.error.HTTPError):
            request_review(api, api.pr, ctx)

    def test_access_or_server_errors_are_never_suppressed(self):
        for code in (401, 403, 429, 500):
            with self.subTest(code=code):
                api, ctx = self.api()
                api.request = Mock(side_effect=failure(code))
                with self.assertRaises(urllib.error.HTTPError):
                    request_review(api, api.pr, ctx)

    def test_full_intake_continues_after_confirmed_assignment_race(self):
        api = FakeAPI()
        request = api.request
        def assign_then_fail(method, endpoint, payload=None):
            if method == 'POST' and endpoint.endswith('/requested_reviewers'):
                api.pr['requested_reviewers'] = [{'login': 'aguinot'}]
                raise failure()
            return request(method, endpoint, payload)
        api.request = assign_then_fail
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=lambda _: None), 'created')
        self.assertEqual(len(api.prs), 1)
        self.assertEqual(len(api.dispatches), 1)
        self.assertIn('Your project has been submitted', api.comments[0]['body'])

    def test_failed_assignment_is_retryable_on_same_issue_and_pr(self):
        api = FakeAPI()
        request = api.request
        def fail_review(method, endpoint, payload=None):
            if method == 'POST' and endpoint.endswith('/requested_reviewers'):
                raise failure()
            return request(method, endpoint, payload)
        api.request = fail_review
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=lambda _: None), 'created')
        self.assertIn('Submitted successfully; follow-up delivery needs retry', api.comments[0]['body'])
        self.assertEqual(len(api.dispatches), 1)
        self.assertEqual(api.issue['state'], 'open')
        api.request = request
        self.assertEqual(issue_intake.process(api, Teams(), 12, repo_check=lambda _: None), 'updated')
        self.assertEqual(len(api.prs), 1)


class HttpDiagnosticsTests(unittest.TestCase):
    def test_error_identifies_operation_without_request_secrets_or_query(self):
        api = GitHub('test-org/registry', 'SECRET_TOKEN')
        api.opener = Mock()
        api.opener.open.side_effect = failure()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            api.request('POST', 'pulls/52/requested_reviewers?private=SECRET_QUERY',
                        {'reviewers': ['PRIVATE_PAYLOAD']})
        message = http_error_message(caught.exception)
        self.assertEqual(message, 'GitHub API error (HTTP 422) during POST pulls/52/requested_reviewers')
        for sensitive in ('SECRET_TOKEN', 'SECRET_QUERY', 'PRIVATE_PAYLOAD', 'private error body'):
            self.assertNotIn(sensitive, message)

    def test_unannotated_http_errors_remain_supported(self):
        self.assertEqual(http_error_message(failure()), 'GitHub API error (HTTP 422)')


if __name__ == '__main__':
    unittest.main()
