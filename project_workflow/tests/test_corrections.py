"""Optional offline checks for revision identity, PR reuse, markers and recovery."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from project_workflow import issue_intake, revisions, validate_pr
from project_workflow.issue_form import KEEP_SELECTION, KEEP_VALUE, form
from project_workflow.project_registry import Invalid, load_context, parse_yaml
from project_workflow.submission import project_path
from test_issue_submission import BASE_SHA, BOT, FakeAPI, Teams, issue_fixture


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.ctx = load_context(self.api.base.__getitem__, paths=self.api.base)

    def process(self, n=12, **kwargs):
        return issue_intake.process(self.api, Teams(), n, repo_check=lambda _: None, **kwargs)

    def params(self, n=12, marker=issue_intake.COMMENT_MARKER):
        rows = self.api.comments if n == 12 else self.api.other_comments[n]
        body = next(c['body'] for c in rows if c['body'].startswith(marker))
        url = re.search(r'\]\(<(https://github\.com/[^>]+)>\)', body)[1]
        return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}

    def replacement(self, params, n=13, **changes):
        controls = form(self.ctx, correction=True)['body']
        values = {}
        for item in controls:
            if item.get('type') == 'markdown':
                continue
            key = item['id']
            values[key] = KEEP_SELECTION if item['type'] == 'dropdown' else params.get(key, '') or '_No response_'
        title = changes.pop('title', params['title'])
        values.update(changes)
        issue = {**issue_fixture(), 'number': n, 'title': title, 'created_at': '2026-10-12T12:00:00Z',
                 'labels': [{'name': 'project-correction'}],
                 'body': '\n\n'.join('### ' + item['attributes']['label'] + '\n\n' + values[item['id']]
                                      for item in controls if item.get('type') != 'markdown')}
        self.api.other_issues[n] = issue
        return issue

    def command(self, n=12, user=None):
        rows = self.api.comments if n == 12 else self.api.other_comments.setdefault(n, [])
        comment = {'id': self.api.next_comment_id, 'body': '/edit', 'user': user or deepcopy(self.api.issue['user']),
                   'issue_url': f'https://api.github.com/repos/{self.api.repo}/issues/{n}'}
        self.api.next_comment_id += 1
        rows.append(comment)
        return self.process(n, edit_comment=comment['id'])

    def record(self):
        return parse_yaml(self.api.data[self.api.pr['head']['sha']][project_path(12)])

    def policy(self):
        with tempfile.TemporaryDirectory() as d:
            event = Path(d) / 'event.json'
            event.write_text('{}')
            env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_REPOSITORY': self.api.repo, 'GITHUB_TOKEN': 'fake',
                   'SC_TEAM_READ_TOKEN': 'fake', 'POLICY_PR_NUMBER': '99'}
            with patch.dict(os.environ, env), patch.object(sys, 'argv', ['validate_pr', '--publish-status']), \
                 patch.object(validate_pr, 'GitHub', return_value=self.api), \
                 patch.object(validate_pr, 'TeamDirectory', return_value=Teams()), \
                 patch.object(validate_pr.subprocess, 'check_output', return_value=BASE_SHA + '\n'), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return validate_pr.main()

    def test_invalid_markers_only_on_wrong_dropdown_values_and_blank_paired_field(self):
        self.api.issue = issue_fixture(deliverable_ids='SCM / SCM-001, CAS / CAS-001', contributor_1_github_username='')
        self.assertEqual(self.process(), 'invalid')
        params = self.params()
        self.assertEqual(params['previous_deliverable_ids'], 'Previous selection:\nSCM-001\nCAS-001 🔴')
        self.assertEqual(params['previous_working_group'], 'Previous selection:\nSCM')
        self.assertEqual(params['contributor_1_github_username'], '🔴')
        self.assertNotIn('🔴', params['summary'])
        self.assertIn('invalid', {x['name'] for x in self.api.issue['labels']})
        self.replacement(params, deliverable_ids='SCM / SCM-001', contributor_1_github_username='bob')
        self.assertEqual(self.process(13), 'created')
        self.assertEqual(self.api.issue['state'], 'closed')
        self.assertEqual(self.record()['project_id'], 'P000012')
        self.assertEqual(self.record()['submission']['submitted_at'], '2026-10-11T06:00:00Z')
        self.assertEqual(self.record()['submission']['original_issue_number'], 12)
        self.assertEqual(self.record()['submission']['issue_number'], 13)
        self.assertEqual(self.policy(), 0)
        self.assertIn('Closes #13', self.api.pr['body'])

    def test_edit_command_leaves_valid_proposal_unchanged_and_same_pr_is_updated(self):
        self.process()
        head = self.api.pr['head']['sha']
        statuses = deepcopy(self.api.statuses)
        self.assertEqual(self.command(), 'edit-requested')
        self.assertEqual(self.api.statuses, statuses)
        self.assertEqual(self.api.pr['head']['sha'], head)
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        self.assertFalse(any('🔴' in value for value in params.values()))
        self.replacement(params, summary='Updated science', title='Updated title')
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(self.record()['summary'], 'Updated science')
        self.assertEqual(self.record()['name'], 'Updated title')
        self.assertEqual(self.api.pr['head']['ref'], 'project-submissions/issue-12')
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ('POST', 'pulls')]), 1)
        self.assertNotIn('invalid', {x['name'] for x in self.api.issue['labels']})
        self.assertEqual(self.policy(), 0)
        self.assertEqual(self.process(), 'superseded')
        self.assertEqual(self.api.statuses[-1][1], 'success')

    def test_invalid_replacement_blocks_old_yaml_then_corrects_same_pr(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'), kind='KEY')
        head = self.api.pr['head']['sha']
        self.assertEqual(self.process(13), 'invalid')
        self.assertEqual(self.api.pr['head']['sha'], head)
        self.assertEqual(self.api.statuses[-1][1], 'failure')
        self.assertEqual(self.policy(), 1)
        params = self.params(13)
        self.assertEqual(params['previous_kind'], 'Previous selection:\nKEY 🔴')
        self.replacement(params, n=14, kind='EP')
        self.assertEqual(self.process(14), 'updated')
        self.assertEqual(self.record()['submission']['issue_number'], 14)
        self.assertEqual(self.record()['submission']['submitted_at'], '2026-10-11T06:00:00Z')
        self.assertEqual(self.policy(), 0)
        self.assertEqual(self.api.other_issues[13]['state'], 'closed')

    def test_previous_guidance_cannot_change_preserved_answers(self):
        self.process()
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        self.replacement(params, previous_deliverable_ids='CAS-001', previous_kind='KEY', previous_working_group='CAS')
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(self.record()['kind'], 'EP')
        self.assertEqual(self.record()['working_group'], 'SCM')
        self.assertEqual(self.record()['deliverable_ids'], ['SCM-001'])

    def test_unauthorized_or_stale_corrections_do_not_change_the_proposal(self):
        self.process()
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        for change in ('author', 'token', 'original', 'previous'):
            issue = self.replacement(params)
            if change == 'author':
                issue['user'] = {'id': 999, 'login': 'mallory', 'type': 'User'}
            else:
                target = {'token': 'correction_token', 'original': 'original_issue', 'previous': 'previous_issue'}[change]
                issue['body'] = issue['body'].replace('### ' + revisions.CORRECTION_FIELDS[target] + '\n\n' + params[target], '### ' + revisions.CORRECTION_FIELDS[target] + '\n\n' + ('999999999999' if change != 'token' else 'x'*32))
            statuses = deepcopy(self.api.statuses)
            head = self.api.pr['head']['sha']
            self.assertEqual(self.process(13), 'rejected-correction')
            self.assertEqual(self.api.statuses, statuses)
            self.assertEqual(self.api.pr['head']['sha'], head)
            self.assertEqual(issue['state'], 'closed')
        self.replacement(params)
        self.process(13)
        statuses = deepcopy(self.api.statuses)
        self.replacement(params, n=14)
        self.assertEqual(self.process(14), 'rejected-correction')
        self.assertEqual(self.api.statuses, statuses)

    def test_correction_form_without_references_is_closed_and_points_to_new_form(self):
        params = {'title': 'Wrong entry point'}
        issue = self.replacement(params)
        self.assertEqual(self.process(13), 'rejected-correction')
        self.assertEqual(issue['state'], 'closed')
        self.assertIsNone(self.api.pr)
        self.assertIn('template=new-project.yml', self.api.other_comments[13][-1]['body'])

    def test_another_user_cannot_request_edit(self):
        self.process()
        self.assertEqual(self.command(user={'id': 999, 'login': 'mallory', 'type': 'User'}), 'ignored-command')
        self.assertIsNone(revisions.read_state(self.api, 12)['request'])

    def test_multiple_keep_and_replacement_dropdown_choices_are_invalid(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'), deliverable_ids=KEEP_SELECTION + ', SCM / SCM-001')
        self.assertEqual(self.process(13), 'invalid')
        self.assertEqual(self.api.statuses[-1][1], 'failure')

    def test_long_answers_are_preserved_and_optional_blank_clears(self):
        self.api.issue = issue_fixture(summary='Scientific description. '*1500)
        self.process()
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        self.assertEqual(params['summary'], KEEP_VALUE)
        original = self.record()['summary']
        self.replacement(params, slack_channel='_No response_', related_links='_No response_')
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(self.record()['summary'], original)
        self.assertIsNone(self.record()['slack_channel'])
        self.assertEqual(self.record()['related_links'], [])

    def test_retries_after_interrupted_transition_reuse_one_issue_chain_and_pr(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'))
        original = self.api.request
        def fail_once(method, endpoint, payload):
            if endpoint == 'issues/12/labels':
                raise OSError('Interrupted after recording revision')
            return original(method, endpoint, payload)
        self.api.request = fail_once
        with self.assertRaises(OSError):
            self.process(13)
        self.api.request = original
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(len(revisions.read_state(self.api, 12)['history']), 2)
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ('POST', 'pulls')]), 1)
        self.assertEqual(self.policy(), 0)

    def test_edited_previous_issue_or_forged_state_blocks_policy(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'))
        self.process(13)
        self.api.issue['title'] = 'Changed superseded Issue'
        self.assertEqual(self.policy(), 1)
        self.api.issue['title'] = 'Shape measurement validation'
        state = next(c for c in self.api.comments if c['body'].startswith(revisions.STATE_MARKER))
        state['user'] = {'login': 'mallory', 'id': 999, 'type': 'User'}
        self.assertEqual(self.policy(), 1)

    def test_missing_answer_heading_can_be_recovered_using_a_replacement_form(self):
        self.api.issue['body'] = self.api.issue['body'].replace('### Description', '### Wrong heading')
        self.assertEqual(self.process(), 'invalid')
        params = self.params()
        self.assertEqual(params['summary'], '🔴')
        self.replacement(params, summary='Recovered description', kind='EP')
        self.assertEqual(self.process(13), 'created')
        self.assertEqual(self.record()['summary'], 'Recovered description')

    def test_activated_issue_cannot_be_redirected_by_editing_reference_boxes(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'))
        self.process(13)
        issue = self.api.other_issues[13]
        issue['body'] = issue['body'].replace('### Original submission Issue\n\n12', '### Original submission Issue\n\n999')
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(self.api.pr['head']['ref'], 'project-submissions/issue-12')
        self.assertEqual(self.policy(), 0)

    def test_label_removal_and_missing_credentials_block_active_replacement(self):
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'))
        self.process(13)
        issue = self.api.other_issues[13]
        issue['labels'] = []
        self.assertEqual(self.process(13), 'invalid')
        self.assertEqual(self.api.statuses[-1][1], 'failure')
        issue['labels'] = [{'name': 'project-correction'}]
        with tempfile.TemporaryDirectory() as d:
            event = Path(d) / 'event.json'
            event.write_text(json.dumps({'issue': issue}))
            env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_REPOSITORY': self.api.repo,
                   'GITHUB_TOKEN': 'fake', 'SC_TEAM_READ_TOKEN': '', 'SUBMISSION_ISSUE_NUMBER': ''}
            with patch.dict(os.environ, env), patch.object(issue_intake, 'GitHub', return_value=self.api), \
                 patch.object(issue_intake.subprocess, 'check_output', return_value=BASE_SHA + '\n'), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(issue_intake.main(), 1)
        self.assertEqual(self.api.statuses[-1][1], 'failure')


if __name__ == '__main__':
    unittest.main()
