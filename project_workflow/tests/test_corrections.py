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
from project_workflow.issue_form import KEEP_VALUE, form
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
        url = re.search(r'\]\(<(https://github\.com/[^>]+/issues/new\?[^>]+)>\)', body)[1]
        return {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}

    def replacement(self, params, n=13, **changes):
        controls = form(self.ctx, correction=True)['body']
        values = {}
        for item in controls:
            if item.get('type') == 'markdown':
                continue
            key = item['id']
            values[key] = {'kind': 'EP', 'working_group': 'SCM', 'deliverable_ids': 'SCM / SCM-001', 'phd_thesis': 'True'}[key] if item['type'] == 'dropdown' else params.get(key, '') or '_No response_'
        title = changes.pop('title', params['title'])
        if 'correction_notes' in changes:
            reference = revisions.REFERENCE.search(params.get('correction_notes', ''))
            if reference:
                changes['correction_notes'] += '\n\n' + reference[0].strip()
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

    def test_error_feedback_has_full_summary_and_form_has_only_concise_corrections(self):
        self.api.issue = issue_fixture(deliverable_ids='SCM / SCM-001, CAS / CAS-001', contributor_1_github_username='')
        self.assertEqual(self.process(), 'invalid')
        params = self.params()
        self.assertEqual(set(params) & {'original_issue', 'previous_issue', 'correction_token', 'previous_kind', 'previous_deliverable_ids'}, set())
        self.assertNotIn('🔴', params['summary'])
        self.assertIn('Deliverables:', params['correction_notes'])
        self.assertIn('CAS-001 does not belong to SCM', params['correction_notes'])
        self.assertIn('Contributor 1 GitHub username:', params['correction_notes'])
        self.assertNotIn('Working group:', params['correction_notes'])
        body = self.api.comments[0]['body']
        self.assertIn('**Project title**:', body)
        self.assertIn('CAS-001 — Other deliverable 🔴', body)
        self.assertNotIn('SCM-001 — Test deliverable 🔴', body)
        self.assertEqual(len(self.api.comments), 1)
        self.replacement(params, contributor_1_github_username='bob')
        self.assertEqual(self.process(13), 'created')
        self.assertEqual(self.api.issue['state'], 'closed')
        self.assertEqual(self.record()['project_id'], 'P000012')
        self.assertEqual(self.record()['submission']['submitted_at'], '2026-10-11T06:00:00Z')
        self.assertEqual(self.record()['submission']['original_issue_number'], 12)
        self.assertEqual(self.record()['submission']['issue_number'], 13)
        self.assertNotIn('pit-correction:', self.record()['summary'])
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
        previous_yaml = self.api.data[self.api.pr['head']['sha']][project_path(12)]
        self.assertEqual(self.process(13), 'invalid')
        self.assertEqual(self.api.data[self.api.pr['head']['sha']][project_path(12)], previous_yaml)
        self.assertEqual(self.api.statuses[-1][1], 'failure')
        self.assertEqual(self.policy(), 1)
        params = self.params(13)
        self.assertIn('Project kind:', params['correction_notes'])
        self.replacement(params, n=14, kind='EP')
        self.assertEqual(self.process(14), 'updated')
        self.assertEqual(self.record()['submission']['issue_number'], 14)
        self.assertEqual(self.record()['submission']['submitted_at'], '2026-10-11T06:00:00Z')
        self.assertEqual(self.policy(), 0)
        self.assertEqual(self.api.other_issues[13]['state'], 'closed')

    def test_guidance_cannot_change_answers_and_dropdowns_must_be_selected_again(self):
        self.process()
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        self.assertEqual(revisions.strip_reference(params['correction_notes']), '')
        self.assertNotIn('kind', params)
        self.assertNotIn('working_group', params)
        self.assertNotIn('deliverable_ids', params)
        self.replacement(params, correction_notes='Change to KEY / CAS', working_group='_No response_')
        self.assertEqual(self.process(13), 'invalid')
        self.assertEqual(self.api.statuses[-1][1], 'failure')
        self.replacement(self.params(13), n=14)
        self.assertEqual(self.process(14), 'updated')
        self.assertEqual(self.record()['kind'], 'EP')
        self.assertEqual(self.record()['working_group'], 'SCM')

    def test_unauthorized_or_stale_corrections_do_not_change_the_proposal(self):
        self.process()
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        for change in ('author', 'token', 'original', 'previous'):
            issue = self.replacement(params)
            if change == 'author':
                issue['user'] = {'id': 999, 'login': 'mallory', 'type': 'User'}
            else:
                root, previous, token = revisions.references(issue, self.ctx)
                modified = revisions.reference_comment(999999 if change == 'original' else root,
                    999999 if change == 'previous' else previous, 'x'*32 if change == 'token' else token)
                issue['body'] = revisions.REFERENCE.sub('\n\n' + modified, issue['body']) if revisions.REFERENCE.search(issue['body']) else issue['body'].replace(revisions.reference_comment(root, previous, token), modified)
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

    def test_edited_previous_issue_or_missing_state_blocks_policy(self):
        from project_workflow.submission import submission_branch
        self.process()
        self.command()
        self.replacement(self.params(marker='<!-- pit-project-edit:v1 -->'))
        self.process(13)
        self.api.issue['title'] = 'Changed superseded Issue'
        self.assertEqual(self.policy(), 1)
        self.api.issue['title'] = 'Shape measurement validation'
        del self.api.refs[submission_branch(12)]
        self.assertEqual(self.policy(), 1)

    def test_missing_answer_heading_can_be_recovered_using_a_replacement_form(self):
        self.api.issue['body'] = self.api.issue['body'].replace('### Description', '### Wrong heading')
        self.assertEqual(self.process(), 'invalid')
        params = self.params()
        self.assertEqual(params['summary'], '')
        self.replacement(params, summary='Recovered description', kind='EP')
        self.assertEqual(self.process(13), 'created')
        self.assertEqual(self.record()['summary'], 'Recovered description')

    def test_edited_formatted_body_cannot_silently_change_the_project(self):
        self.process()
        head = self.api.pr['head']['sha']
        self.api.issue['body'] = self.api.issue['body'].replace('**Working group**: SCM', '**Working group**: CAS')
        self.assertEqual(self.policy(), 1)
        self.assertEqual(self.process(), 'invalid')
        self.assertEqual(self.api.pr['head']['sha'], head)
        params = self.params()
        self.assertIn('replacement form', params['correction_notes'])
        self.replacement(params)
        self.assertEqual(self.process(13), 'updated')
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

    def test_interrupted_issue_formatting_recovers_without_duplicate_pr_or_feedback(self):
        original = self.api.request
        def interrupted(method, endpoint, payload):
            if method == 'PATCH' and endpoint == 'issues/12' and 'body' in payload:
                raise OSError('Interrupted between storing answers and updating the display')
            return original(method, endpoint, payload)
        self.api.request = interrupted
        with self.assertRaises(OSError):
            self.process()
        self.assertIsNone(self.api.pr)
        self.api.request = original
        self.assertEqual(self.process(), 'created')
        self.assertEqual(self.process(), 'updated')
        self.assertEqual(len(self.api.comments), 1)
        self.assertEqual(len([w for w in self.api.writes if w[:2] == ('POST', 'pulls')]), 1)
        self.assertEqual(self.policy(), 0)

    def test_copying_a_formatted_issue_does_not_forge_an_answer_receipt(self):
        from project_workflow.review import FORMATTED_MARKER
        self.api.issue['body'] = FORMATTED_MARKER + '\n**Project kind**: KEY\n'
        self.assertEqual(self.process(), 'invalid')
        self.assertIsNone(self.api.pr)

    def test_nonmember_does_not_create_automation_state(self):
        from project_workflow.submission import submission_branch
        self.assertEqual(issue_intake.process(self.api, Teams(member=False), 12, repo_check=lambda _: None), 'invalid')
        self.assertNotIn(submission_branch(12), self.api.refs)
        self.assertNotIn('project-workflow/state', self.api.refs)
        self.assertIsNone(self.api.pr)

    def test_clean_prefills_allow_description_and_links_to_be_replaced(self):
        self.api.issue = issue_fixture(summary='Test', related_links='```text\n\n```')
        self.assertEqual(self.process(), 'created')
        self.command()
        params = self.params(marker='<!-- pit-project-edit:v1 -->')
        self.assertEqual(params['summary'], 'Test')
        self.assertEqual(params['related_links'], '')
        self.assertIsNotNone(revisions.REFERENCE.search(params['correction_notes']))
        self.replacement(params, summary='Changed description', related_links='https://example.org/new')
        self.assertEqual(self.process(13), 'updated')
        self.assertEqual(self.record()['summary'], 'Changed description')
        self.assertEqual(self.record()['related_links'], ['https://example.org/new'])
        self.assertNotIn('pit-correction:', self.record()['summary'])
        self.assertEqual(self.policy(), 0)
        self.command(13)
        self.replacement(self.params(13, marker='<!-- pit-project-edit:v1 -->'), n=14,
                         summary='Another description', related_links='_No response_')
        self.assertEqual(self.process(14), 'updated')
        self.assertEqual(self.record()['summary'], 'Another description')
        self.assertEqual(self.record()['related_links'], [])
        self.assertEqual(self.policy(), 0)


if __name__ == '__main__':
    unittest.main()
