"""CPP declarations reuse proposal transactions without assignment or claiming."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import yaml

from project_workflow import commands, issue_intake
from project_workflow.generated_files import render, validate_outputs
from project_workflow.issue_form import current_labels, form, FORM_PATHS
from project_workflow.project_registry import CONFIG_PATH, Invalid, load_context, parse_yaml
from project_workflow.submission import (issue_version, project_path, project_yaml, proposal,
                                        submission_body, validate_bot_snapshot)
from test_commands import CommandTeams
from test_issue_submission import BASE_SHA, FakeAPI, issue_fixture
from test_registry import source_data


def cpp_issue():
    data = source_data()
    ctx = load_context(data.__getitem__, paths=data)
    issue = issue_fixture()
    # Use the actual shared answer labels and keep the same retained values.
    raw = issue['body']
    from project_workflow.submission import answers
    values = answers(raw, ctx)
    issue['body'] = '\n\n'.join(f'### {label}\n\n{values[key]}'
                                for key, label in current_labels(ctx, 'CPP').items())
    issue['labels'] = [{'name': 'new-cpp-project'}]
    return issue


class CPPDeclarationTests(unittest.TestCase):
    def setUp(self):
        data = source_data()
        self.ctx = load_context(data.__getitem__, paths=data)
        self.api = FakeAPI(cpp_issue())
        self.teams = CommandTeams(sc={'alice', 'sc-user'})
        self.next_id = 1000
        lookup = patch('project_workflow.issue_intake.urllib.request.build_opener')
        lookup.start()
        self.addCleanup(lookup.stop)

    def submit(self):
        return issue_intake.process(self.api, self.teams, 12, repo_check=lambda url: None)

    def record(self):
        return parse_yaml(self.api.data[self.api.pr['head']['sha']][project_path(12)])

    def send(self, text, login='alice', user_id=123):
        self.next_id += 1
        c = {'id': self.next_id, 'body': text,
             'user': {'login': login, 'id': user_id, 'type': 'User'},
             'issue_url': 'https://api.github.com/repos/test-org/registry/issues/12'}
        self.api.comments.append(c)
        return commands.process(self.api, self.teams, 12, deepcopy(c)), c

    def validate(self):
        base, head = self.api.tree(BASE_SHA), self.api.tree(self.api.pr['head']['sha'])
        return validate_bot_snapshot(base, head, self.api.reader(base), self.api.reader(head),
            self.api.pr, self.api.issue, self.ctx, organization_member=True, is_sc=True,
            api=self.api, teams=self.teams)

    def test_cpp_fields_match_ep_except_people_and_phd(self):
        ep = {f['id']: f for f in form(self.ctx)['body'] if 'id' in f}
        cpp = {f['id']: f for f in form(self.ctx, 'CPP')['body'] if 'id' in f}
        expected = {k: v for k, v in ep.items() if k not in {'lead', 'lead_github_username', 'phd_thesis'}
                    and not k.startswith('contributor_')}
        self.assertEqual(cpp, expected)
        self.assertEqual(form(self.ctx, 'CPP')['labels'], ['new-cpp-project'])
        for node in form(self.ctx, 'CPP')['body']:
            self.assertNotIn('ep_only', node)
            self.assertNotIn('## Contributors', node.get('attributes', {}).get('value', ''))

    def test_sc_declaration_unassigned_without_phd_and_retry_keeps_one_pr(self):
        raw = self.api.issue['body']
        self.assertEqual(self.submit(), 'created')
        record = self.record()
        self.assertEqual(record['kind'], 'CPP')
        self.assertEqual(record['leads'], [])
        self.assertEqual(record['contributors'], [])
        self.assertNotIn('phd_thesis', record)
        self.assertEqual(record['submission']['submitted_by'], 'alice')
        self.assertEqual(submission_body(self.api.issue), raw)
        self.assertEqual(self.validate(), record)
        self.assertEqual(self.submit(), 'updated')
        self.assertEqual(len(self.api.prs), 1)
        self.assertEqual(self.record(), record)
        self.assertIn('Closes #12', self.api.pr['body'])
        review = next(p for m, e, p in self.api.writes if e.endswith('/requested_reviewers'))
        self.assertEqual(review, {'reviewers': ['aguinot'], 'team_reviewers': []})

    def test_member_nonmember_and_non_sc_cannot_declare(self):
        for member, sc in ((True, False), (False, False), (False, True)):
            with self.subTest(member=member, sc=sc):
                with self.assertRaises(Invalid):
                    proposal(cpp_issue(), self.ctx, organization_member=member, is_sc=sc)
        self.teams.sc_users.clear()
        self.assertEqual(self.submit(), 'invalid')
        self.assertIsNone(self.api.pr)
        self.assertEqual(set(self.api.refs), {'main'})
        self.assertEqual(self.api.issue['state'], 'closed')
        self.assertIn('new-cpp-project.yml', self.api.comments[0]['body'])

    def test_summary_reports_cpp_without_removed_sections(self):
        self.submit()
        for body in (self.api.issue['body'], self.api.pr['body']):
            self.assertIn('CPP — Critical Path Project', body)
            for heading in ('**Project lead', '**Contributors**', '**Part of a PhD thesis**'):
                self.assertNotIn(heading, body)
        self.assertNotIn('**Project kind**: EP', self.api.pr['body'])

    def test_removed_native_fields_cannot_be_injected(self):
        labels = current_labels(self.ctx)
        for key in ('lead', 'lead_github_username', 'phd_thesis', 'contributor_1_name',
                    'contributor_1_github_username'):
            with self.subTest(key=key):
                issue = cpp_issue()
                issue['body'] += f'\n\n### {labels[key]}\n\nInjected'
                with self.assertRaisesRegex(Invalid, 'not available in CPP'):
                    proposal(issue, self.ctx, organization_member=True, is_sc=True)

    def test_removed_headings_inside_fences_are_literal_description(self):
        issue = cpp_issue()
        issue['body'] = issue['body'].replace('Measure shear calibration.',
            '```text\n### Part of your PhD thesis?\nFalse\n```\nMeasure shear calibration.')
        record = proposal(issue, self.ctx, organization_member=True, is_sc=True)
        self.assertIn('### Part of your PhD thesis?', record['summary'])
        self.assertNotIn('phd_thesis', record)

    def test_ambiguous_label_refused_and_label_switch_changes_version(self):
        issue = cpp_issue()
        before = issue_version(issue)
        issue['labels'] = [{'name': 'new-project'}]
        self.assertNotEqual(issue_version(issue), before)
        issue['labels'].append({'name': 'new-cpp-project'})
        with self.assertRaisesRegex(Invalid, 'exactly one submission label'):
            proposal(issue, self.ctx, organization_member=True, is_sc=True)

    def test_cpp_help_excludes_removed_commands_and_nested_help(self):
        self.submit()
        for text in ('/help', '/edit help', '/edit add help', '/edit remove help'):
            with self.subTest(text=text):
                self.assertEqual(self.send(text)[0], 'help')
                body = self.api.comments[-1]['body']
                self.assertIn('Only SC members may edit CPP', body)
                for key in commands.CPP_UNAVAILABLE_COMMANDS:
                    self.assertNotIn('/' + key, body)
        self.assertEqual(self.send('/edit add lead help')[0], 'rejected')
        self.assertEqual(self.send('/edit phd help')[0], 'rejected')

    def test_removed_commands_reject_atomically_even_for_sc(self):
        self.submit()
        before = self.record()
        body, sha = self.api.issue['body'], self.api.pr['head']['sha']
        requests = ('/edit add lead name=Jane Smith username=janesmith',
                    '/edit remove lead name=Jane Smith',
                    '/edit add contributor name=Jane Smith username=janesmith',
                    '/edit remove contributor username=janesmith', '/edit phd true')
        for text in requests:
            with self.subTest(text=text):
                statuses = len(self.api.statuses)
                self.assertEqual(self.send(text)[0], 'rejected')
                self.assertIn('not available in CPP declarations', self.api.comments[-1]['body'])
                self.assertEqual(self.record(), before)
                self.assertEqual(self.api.issue['body'], body)
                self.assertEqual(self.api.pr['head']['sha'], sha)
                self.assertEqual(len(self.api.statuses), statuses)
                with self.assertRaises(Invalid):
                    commands.apply(before, commands.parse(text), self.ctx)
        self.assertEqual(self.validate(), before)

    def test_all_retained_edit_commands_replay_and_preserve_identity(self):
        self.submit()
        provenance = deepcopy(self.record()['submission'])
        requests = ('/edit title Updated CPP', '/edit description\nNew **scientific** goals.\nhttps://example.org/a/b',
                    '/edit working-group CPIP', '/edit deliverables CPI-1',
                    '/edit slack #cpp-test', '/edit github https://github.com/example/science',
                    '/edit add links https://example.org/new', '/edit remove links https://example.org/new',
                    '/edit remove slack', '/edit remove github', '/edit remove deliverables')
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(self.send(text)[0], 'edited')
                self.assertEqual(self.validate(), self.record())
        record = self.record()
        for key, value in provenance.items():
            self.assertEqual(record['submission'][key], value)
        self.assertEqual(record['leads'], [])
        self.assertEqual(record['contributors'], [])
        self.assertNotIn('phd_thesis', record)
        self.assertEqual(self.api.issue['title'], 'Updated CPP')
        self.assertEqual(self.api.pr['title'], 'New CPP project: Updated CPP')
        self.assertEqual(len(self.api.prs), 1)

    def test_only_live_sc_can_edit_including_original_submitter(self):
        self.submit()
        before = self.record()
        self.assertEqual(self.send('/edit slack #member', 'bob', 456)[0], 'rejected')
        self.assertEqual(self.record(), before)
        self.assertEqual(self.send('/edit slack #sc', 'sc-user', 789)[0], 'edited')
        self.assertEqual(self.validate(), self.record())
        self.teams.sc_users.remove('alice')
        self.assertEqual(self.send('/edit title Former SC')[0], 'rejected')
        with self.assertRaisesRegex(Invalid, 'Only SC members can declare'):
            self.validate()

    def test_replay_rechecks_command_author_sc_membership(self):
        self.submit()
        self.send('/edit slack #sc', 'sc-user', 789)
        self.teams.sc_users.remove('sc-user')
        with self.assertRaisesRegex(Invalid, 'Only SC members may edit CPP'):
            self.validate()

    def test_forged_assignment_or_phd_in_yaml_fails_independent_validation(self):
        self.submit()
        sha = self.api.pr['head']['sha']
        original = self.record()
        mutations = ({'leads': [{'name': 'Jane Smith', 'github_username': 'janesmith'}]},
                     {'contributors': [{'name': 'Jane Smith', 'github_username': 'janesmith'}]},
                     {'phd_thesis': False}, {'kind': 'KEY'})
        for change in mutations:
            with self.subTest(change=change):
                self.api.data[sha][project_path(12)] = project_yaml({**original, **change})
                with self.assertRaises(Invalid):
                    self.validate()
        self.api.data[sha][project_path(12)] = project_yaml(original)
        self.assertEqual(self.validate(), original)

    def test_forbidden_command_cannot_be_smuggled_into_authenticated_receipts(self):
        self.submit()
        _, comment = self.send('/edit phd true')
        record = self.record()
        record['phd_thesis'] = True
        record['submission']['edits'] = [commands.receipt(comment)]
        self.api.data[self.api.pr['head']['sha']][project_path(12)] = project_yaml(record)
        with self.assertRaisesRegex(Invalid, 'not available in CPP'):
            self.validate()

    def test_label_switch_cannot_convert_existing_declaration(self):
        self.submit()
        self.api.issue['labels'] = [{'name': 'new-project'}]
        self.assertEqual(self.send('/edit slack #convert')[0], 'rejected')
        with self.assertRaises(Invalid):
            self.validate()

    def test_cpp_cancellation_cleanup_and_acceptance(self):
        self.submit()
        self.api.issue['state'] = 'closed'
        self.assertEqual(issue_intake.process(self.api, None, 12), 'cancelled')
        self.assertEqual(self.api.pr['state'], 'closed')
        self.assertEqual(set(self.api.refs), {'main'})
        self.api.issue['state'] = 'open'
        self.assertEqual(self.submit(), 'cancelled')
        accepted = FakeAPI(cpp_issue())
        issue_intake.process(accepted, self.teams, 12)
        accepted.pr.update(state='closed', merged_at='2026-10-15T00:00:00Z')
        accepted.issue['state'] = 'closed'
        self.assertEqual(issue_intake.process(accepted, None, 12), 'already-merged')
        self.assertIn('accepted', accepted.comments[-1]['body'])

    def test_retry_command_and_interrupted_title_sync_reuse_same_receipt(self):
        self.submit()
        request = self.api.request
        def interrupted(method, endpoint, payload=None):
            if method == 'PATCH' and endpoint == f"pulls/{self.api.pr['number']}":
                raise OSError('Interrupted PR update')
            return request(method, endpoint, payload)
        self.api.request = interrupted
        with patch.object(commands.time, 'sleep'):
            result, comment = self.send('/edit title Interrupted CPP title')
        self.assertEqual(result, 'incomplete')
        self.api.request = request
        self.assertEqual(commands.process(self.api, self.teams, 12, comment), 'replayed')
        self.assertEqual(len(self.record()['submission']['edits']), 1)
        self.assertEqual(self.validate(), self.record())

    def test_cpp_and_ep_keep_distinct_proposal_branches(self):
        self.submit()
        self.api.other_issues[13] = {**issue_fixture(), 'number': 13}
        self.assertEqual(issue_intake.process(self.api, self.teams, 13), 'created')
        self.assertEqual(len(self.api.prs), 2)
        for number in (12, 13):
            self.assertIn(f'project-submissions/issue-{number}', self.api.refs)

    def test_informational_commands_remain_available_to_members(self):
        self.submit()
        for text, result in (('/help', 'help'), ('/description', 'description'), ('/deliverables SCM', 'deliverables')):
            self.assertEqual(self.send(text, 'bob', 456)[0], result)

    def test_accepted_cpp_cannot_be_edited_through_declaration_issue(self):
        self.submit()
        self.api.pr.update(state='closed', merged_at='2026-10-15T00:00:00Z')
        self.api.issue['state'] = 'closed'
        self.assertEqual(self.send('/edit slack #later')[0], 'rejected')


class CPPGenerationTests(unittest.TestCase):
    def test_catalogue_and_shared_wording_changes_update_both_forms(self):
        data = source_data()
        config = yaml.safe_load(data[CONFIG_PATH])
        next(x for x in config['form']['body'] if x.get('id') == 'summary')['attributes']['description'] = 'Updated science wording'
        data[CONFIG_PATH] = yaml.safe_dump(config)
        data['working_groups/NEW.yaml'] = yaml.safe_dump({'working_group': 'NEW', 'description': 'New group',
            'deliverables': [{'id': 'NEW-1', 'title': 'New deliverable'}]})
        ctx = load_context(data.__getitem__, paths=data)
        outputs = render(ctx)
        for kind, path in FORM_PATHS.items():
            if kind == 'CLAIM':
                continue  # Claim forms intentionally have no scientific-definition controls.
            fields = {x['id']: x for x in yaml.safe_load(outputs[path])['body'] if 'id' in x}
            self.assertEqual(fields['deliverables_NEW']['attributes']['options'], ['NEW-1 — New deliverable'])
            self.assertEqual(fields['summary']['attributes']['description'], 'Updated science wording')
        data.update(outputs)
        validate_outputs(data.__getitem__, data, ctx)
        data.pop(FORM_PATHS['CPP'])
        with self.assertRaises(Invalid):
            validate_outputs(data.__getitem__, data, ctx)
