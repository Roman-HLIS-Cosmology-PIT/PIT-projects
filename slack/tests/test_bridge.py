"""Bridge regression tests; all GitHub and Slack writes use in-memory adapters."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from slack.bridge import Bridge, Invalid, Slack, member_count, project_value, tally
from project_workflow import issue_intake
from project_workflow import commands

# Reuse the actual submission fixtures, not a second implementation of policy.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'project_workflow/tests'))
from test_issue_submission import FakeAPI, Teams
from test_cpp_claims import ClaimAPI
from test_cpp_declarations import cpp_issue


class Directory(Teams):
    def check_team(self, *args):
        pass

    def get(self, endpoint):
        return [{'login': f'sc{i}'} for i in range(5)]


class Store:
    def __init__(self):
        self.projects, self.events, self.messages = {}, [], []

    def save(self, value):
        self.projects[value['id']] = deepcopy(value)

    def refresh(self, value):
        self.save(value)
        self.messages.append(deepcopy(value))

    def query(self, store, poll=None):
        if store == 'pit_projects':
            return [{'id': key, 'payload': json.dumps(value)} for key, value in self.projects.items()]
        return deepcopy([e for e in self.events if not poll or e['poll'] == poll])


class MergeAPI(FakeAPI):
    blocked = False
    crash_after_merge = False

    def request(self, method, endpoint, payload=None):
        if method == 'PUT' and endpoint.endswith('/merge'):
            self.writes.append((method, endpoint, deepcopy(payload)))
            if self.blocked:
                raise Invalid('Protected branch requires review')
            assert payload['sha'] == self.pr['head']['sha']
            self.pr.update(state='closed', merged_at='2026-10-16T00:00:00Z')
            if self.crash_after_merge:
                raise OSError('Connection lost after GitHub merged')
            return {'merged': True}
        return super().request(method, endpoint, payload)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        lookup = patch('project_workflow.issue_intake.urllib.request.build_opener')
        lookup.start()
        self.addCleanup(lookup.stop)
        self.api, self.teams, self.store = MergeAPI(), Directory(), Store()
        issue_intake.process(self.api, self.teams, 12, repo_check=lambda _: None)
        self.bridge = Bridge(self.api, self.teams, self.store, 'C123')

    def vote(self, action='test:accept', revision=None):
        value = self.store.projects['pr-99']
        self.store.events.append({'id': str(len(self.store.events)), 'poll': 'pr-99',
            'revision': revision or value['revision'], 'actor': 'U1', 'stamp': '1', 'action': action})

    def sync(self):
        with patch.dict(os.environ, SLACK_TEST_BUTTONS='true'):
            return self.bridge.run(decide=False)

    def check(self):
        with patch.dict(os.environ, SLACK_TEST_BUTTONS='true'):
            return self.bridge.run(decide=True)

    def test_sync_publishes_valid_ep_with_live_roster_and_no_decision(self):
        self.assertEqual(self.sync(), 0)
        value = self.store.projects['pr-99']
        self.assertEqual((value['kind'], value['members'], value['quorum']), ('EP', 5, 3))
        self.assertIn('Shape and Color', value['groups'])
        self.assertEqual(self.api.pr['state'], 'open')

    def test_accept_merges_exact_head_and_confirms_status(self):
        self.sync(); self.vote()
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'accepted')
        self.assertTrue(self.store.projects['pr-99']['reconciled'])
        self.assertEqual(self.api.issue['state'], 'closed')
        merge = next(w for w in self.api.writes if w[1].endswith('/merge'))
        self.assertEqual(merge[2]['sha'], self.api.pr['head']['sha'])
        self.assertTrue(any(w[1] == 'actions/workflows/maintenance.yml/dispatches' for w in self.api.writes))

    def test_refuse_closes_without_merge_and_cleans_submission(self):
        self.sync(); self.vote('test:refuse')
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'refused')
        self.assertIsNone(self.api.pr['merged_at'])
        self.assertEqual(self.api.issue['state'], 'closed')
        self.assertNotIn('project-submissions/issue-12', self.api.refs)

    def test_abstain_quorum_remains_waiting(self):
        self.sync(); self.vote('test:abstain')
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'waiting')
        self.assertEqual(self.api.pr['state'], 'open')

    def test_protection_failure_never_shows_accepted_and_retries(self):
        self.sync(); self.vote(); self.api.blocked = True
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.store.projects['pr-99']['status'], 'processing')
        self.assertFalse(any(v['status'] == 'accepted' for v in self.store.messages))
        self.api.blocked = False
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'accepted')

    def test_lost_merge_response_reconciles_without_second_merge(self):
        self.sync(); self.vote(); self.api.crash_after_merge = True
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'accepted')
        self.assertEqual(sum(w[1].endswith('/merge') for w in self.api.writes), 1)

    def test_new_override_can_refuse_a_blocked_acceptance(self):
        self.sync(); self.vote(); self.api.blocked = True
        self.assertEqual(self.check(), 1)
        self.vote('force:refuse')
        self.store.events[-1]['stamp'] = str(time.time())
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'refused')

    def test_force_refuse_without_participation(self):
        self.sync(); self.vote('force:refuse')
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.store.projects['pr-99']['status'], 'refused')

    def test_stale_revision_votes_ignored(self):
        self.sync(); self.vote(revision='old-revision')
        self.assertEqual(self.check(), 0)
        self.assertEqual(self.api.pr['state'], 'open')

    def test_valid_edit_resets_votes_and_invalid_edit_preserves_revision(self):
        self.sync(); self.vote()
        previous = self.store.projects['pr-99']['revision']
        comment = {'id': 1000, 'body': '/edit description\nUpdated scientific goals.',
                   'user': {'login': 'alice', 'id': 123, 'type': 'User'},
                   'issue_url': 'https://api.github.com/repos/test-org/registry/issues/12'}
        self.api.comments.append(deepcopy(comment))
        self.assertEqual(commands.process(self.api, self.teams, 12, comment), 'edited')
        self.assertEqual(self.check(), 0)
        value = self.store.projects['pr-99']
        self.assertNotEqual(value['revision'], previous)
        self.assertEqual(value['status'], 'waiting')
        self.assertEqual(value['description'], 'Updated scientific goals.')
        revision = value['revision']
        comment.update(id=1001, body='/edit deliverables NOT-A-DELIVERABLE')
        self.api.comments.append(deepcopy(comment))
        self.assertEqual(commands.process(self.api, self.teams, 12, comment), 'rejected')
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.store.projects['pr-99']['revision'], revision)
        comment.update(id=1002, body='/edit description\nMeasure shear calibration.\n\n### Scientific goals\nAssess selection effects.')
        self.api.comments.append(deepcopy(comment))
        self.assertEqual(commands.process(self.api, self.teams, 12, comment), 'edited')
        self.assertEqual(self.check(), 0)
        self.assertNotEqual(self.store.projects['pr-99']['revision'], previous)
        self.assertEqual(self.store.projects['pr-99']['status'], 'waiting')

    def test_invalid_live_issue_cannot_notify_or_merge(self):
        self.api.issue['body'] += '\nTampered'
        self.assertEqual(self.check(), 1)
        self.assertFalse(self.store.projects)
        self.assertEqual(self.api.pr['state'], 'open')

    def test_other_channel_cannot_reuse_existing_poll(self):
        self.sync(); self.bridge.channel = 'C_OTHER'
        self.assertEqual(self.check(), 1)
        self.assertEqual(self.api.pr['state'], 'open')

    def test_cpp_declaration_never_posts(self):
        self.api = MergeAPI(cpp_issue())
        self.teams = Directory(sc=True)
        issue_intake.process(self.api, self.teams, 12, repo_check=lambda _: None)
        bridge = Bridge(self.api, self.teams, self.store, 'C123')
        self.assertEqual(bridge.run(decide=True), 0)
        self.assertFalse(self.store.projects)

    def test_cpp_claim_posts_correct_type(self):
        api = ClaimAPI()
        teams = Directory()
        issue_intake.process(api, teams, 12, repo_check=lambda _: None)
        bridge = Bridge(api, teams, self.store, 'C123')
        self.assertEqual(bridge.run(decide=False), 0)
        self.assertEqual(self.store.projects['pr-99']['kind'], 'CLAIM')


class RulesTests(unittest.TestCase):
    def test_cli_empty_datastore_is_not_an_incomplete_response(self):
        # Slack CLI 4.10.0 marshals Items with json:"items,omitempty".
        # A fresh installed app therefore returns only the datastore name.
        slack = object.__new__(Slack)
        slack.app, slack.team, slack.token = 'ATEST', 'TTEST', 'test-secret'
        result = subprocess.CompletedProcess([], 0, stdout='{"datastore":"pit_projects"}\n', stderr='')
        with patch('slack.bridge.subprocess.run', return_value=result):
            self.assertEqual(slack.query('pit_projects'), [])

    def test_cli_omitted_empty_page_does_not_hide_later_results(self):
        slack = object.__new__(Slack)
        slack.app, slack.team, slack.token = 'ATEST', 'TTEST', 'test-secret'
        pages = [
            subprocess.CompletedProcess([], 0, stdout='{"datastore":"pit_ballots","next_cursor":"page2"}', stderr=''),
            subprocess.CompletedProcess([], 0, stdout='{"datastore":"pit_ballots","items":[{"id":"last"}]}', stderr=''),
        ]
        with patch('slack.bridge.subprocess.run', side_effect=pages) as command:
            self.assertEqual(slack.query('pit_ballots', 'pr-99'), [{'id': 'last'}])
            self.assertEqual(json.loads(command.call_args_list[1].args[0][3])['cursor'], 'page2')

    def test_cli_invalid_responses_cannot_be_mistaken_for_empty_results(self):
        slack = object.__new__(Slack)
        for response in [None, {}, {'datastore': 'another_app_store'},
                         {'datastore': 'pit_projects', 'items': None},
                         {'datastore': 'pit_projects', 'items': {}},
                         {'datastore': 'pit_projects', 'items': ['malformed']},
                         {'datastore': 'pit_projects', 'ok': False},
                         {'datastore': 'pit_projects', 'error': 'access_denied'}]:
            with self.subTest(response=response), patch.object(slack, 'command', return_value=response):
                with self.assertRaises(Invalid):
                    slack.query('pit_projects')

    def test_shared_rule_cases(self):
        cases = json.loads(Path(__file__).with_name('cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']):
                value = {'id': 'pr-99', 'revision': 'r1', 'members': case['members'],
                         'quorum': case['members'] // 2 + 1, 'test_buttons': case.get('test_buttons', True)}
                events = [{'id': str(i), 'poll': 'pr-99', 'revision': 'r1', 'actor': actor,
                           'stamp': str(i), 'action': action} for i, (actor, action) in enumerate(case['events'])]
                self.assertEqual(tally(value, events)['decision'], case['expect'])

    def test_roster_pagination_and_unique_member_count(self):
        teams = Directory()
        first = [{'login': f'sc{i}'} for i in range(100)]
        with patch.object(teams, 'get', side_effect=[first, [{'login': 'SC0'}, {'login': 'extra'}]]):
            self.assertEqual(member_count(teams, ('test-org', 'sc')), 101)
        with patch.object(teams, 'get', return_value=[]), self.assertRaises(Invalid):
            member_count(teams, ('test-org', 'sc'))

    def test_cli_pagination_uses_top_level_next_cursor_even_on_empty_page(self):
        slack = object.__new__(Slack)
        with patch.object(slack, 'command', side_effect=[
            {'datastore': 'pit_ballots', 'items': [], 'next_cursor': 'page2'},
            {'datastore': 'pit_ballots', 'items': [{'id': 'x'}]}]) as command:
            self.assertEqual(slack.query('pit_ballots', 'pr-99'), [{'id': 'x'}])
            self.assertEqual(command.call_args_list[1].args[2]['cursor'], 'page2')


if __name__ == '__main__':
    unittest.main()
