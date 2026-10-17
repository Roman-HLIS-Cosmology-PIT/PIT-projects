"""Exercise pre-publication policy and a separate notification handoff."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from project_workflow import dispatch, issue_intake, project_registry, validate_pr
from project_workflow.project_registry import Invalid
from test_cpp_claims import ClaimAPI
from test_cpp_declarations import cpp_issue
from test_issue_submission import BASE_SHA, FakeAPI, Teams


ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def pipeline(self, api, teams):
        with tempfile.TemporaryDirectory() as directory:
            event, output = Path(directory) / 'event.json', Path(directory) / 'output'
            event.write_text(json.dumps({'issue': api.issue, 'action': 'labeled'}))
            output.touch()
            env = {'GITHUB_EVENT_PATH': str(event), 'GITHUB_OUTPUT': str(output),
                   'GITHUB_REPOSITORY': api.repo, 'GITHUB_TOKEN': 'test',
                   'SC_TEAM_READ_TOKEN': 'test', 'POLICY_INLINE': 'true',
                   'SUBMISSION_ISSUE_NUMBER': '', 'POLICY_PR_NUMBER': ''}
            with patch.dict(os.environ, env), \
                 patch.object(issue_intake, 'GitHub', return_value=api), \
                 patch.object(validate_pr, 'GitHub', return_value=api), \
                 patch.object(issue_intake, 'TeamDirectory', return_value=teams), \
                 patch.object(validate_pr, 'TeamDirectory', return_value=teams), \
                 patch.object(issue_intake.subprocess, 'check_output', return_value=BASE_SHA), \
                 patch.object(sys, 'argv', ['validate_pr', '--publish-status']), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(issue_intake.main(), 0)
                result = 0
                self.assertTrue(output.read_text().startswith('pr_number=99\nslack_review='))
            return result, output.read_text()

    def test_ep_cpp_and_claim_have_prepublication_policy_and_correct_slack_routing(self):
        cases = [(FakeAPI(), Teams(), True),
                 (FakeAPI(cpp_issue()), Teams(sc=True), False),
                 (ClaimAPI(), Teams(), True)]
        for api, teams, review in cases:
            with self.subTest(review=review, labels=api.issue['labels']):
                result, output = self.pipeline(api, teams)
                self.assertEqual(result, 0)
                self.assertIn(f'slack_review={str(review).lower()}\n', output)
                self.assertEqual(api.statuses[-1][1], 'success')
                self.assertEqual(api.dispatches, [])

    def test_inline_request_rejects_injected_pr_and_does_not_silently_dispatch(self):
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            with patch.dict(os.environ, POLICY_INLINE='true', GITHUB_OUTPUT=str(output)):
                for number in ('99\nother=value', 0, True):
                    with self.subTest(number=number), self.assertRaises(Invalid):
                        dispatch.validate_pr(api, number)
            self.assertFalse(output.exists())
            self.assertEqual(api.dispatches, [])

    def test_standalone_callers_still_dispatch_validation(self):
        api = FakeAPI()
        with patch.dict(os.environ, POLICY_INLINE='false'):
            dispatch.validate_pr(api, 99)
        self.assertEqual(api.dispatches, [{'ref': 'main', 'inputs': {
            'operation': 'validate', 'pr_number': '99'}}])

    def test_retry_does_not_patch_an_unchanged_pr_summary(self):
        api = FakeAPI()
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        before = len(api.writes)
        issue_intake.process(api, Teams(), 12, repo_check=lambda _: None)
        self.assertFalse(any(method == 'PATCH' and endpoint == 'pulls/99'
                             for method, endpoint, _ in api.writes[before:]))

    def test_workflows_have_one_initial_intake_and_one_pr_generation_path(self):
        workflows = {p.stem: yaml.safe_load(p.read_text())
                     for p in (ROOT / '.github/workflows').glob('*.yml')}
        events = lambda value: value.get('on', value.get(True))
        self.assertIn('labeled', events(workflows['projects'])['issues']['types'])
        self.assertNotIn('opened', events(workflows['projects'])['issues']['types'])
        self.assertNotIn('pull_request_target', events(workflows['maintenance']))
        self.assertEqual(len(workflows['maintenance']['jobs']), 1)
        self.assertFalse(any(s.get('uses') == './project_workflow/policy'
                             for s in workflows['projects']['jobs']['submit']['steps']))
        self.assertEqual(workflows['projects']['jobs']['notify']['needs'], 'submit')
        for name in ('notify', 'validate'):
            steps = workflows['projects']['jobs'][name]['steps']
            self.assertEqual(sum(s.get('uses') == './project_workflow/policy' for s in steps), 1)

    def test_pr_generation_never_falls_back_to_main_for_missing_or_invalid_number(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/projects.yml').read_text())
        step = next(s for s in workflow['jobs']['validate']['steps']
                    if s.get('name') == 'Refresh generated files before checking the live PR head')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            called = folder / 'called'
            python = folder / 'python'
            python.write_text(f'#!{sys.executable}\nfrom pathlib import Path\n'
                              f'Path({str(called)!r}).touch()\n')
            python.chmod(0o700)
            for number in ('', '0', '-1', '99; bad-command', '99'):
                with self.subTest(number=number):
                    env = {**os.environ, 'PATH': str(folder) + os.pathsep + os.environ['PATH'],
                           'GENERATION_PR_NUMBER': number}
                    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode == 0, number == '99')
                    self.assertEqual(called.exists(), number == '99')


class ImmutableCacheTests(unittest.TestCase):
    def test_tree_cache_reuses_sha_but_cannot_be_mutated_by_a_reader(self):
        api = validate_pr.GitHub('test-org/registry', 'test')
        item = {'path': 'projects/P001.yaml', 'type': 'blob', 'mode': '100644', 'sha': 'b' * 40}
        api.get = Mock(return_value={'tree': [item]})
        first = api.tree('a' * 40)
        first[item['path']]['mode'] = '120000'
        self.assertEqual(api.tree('a' * 40)[item['path']]['mode'], '100644')
        self.assertEqual(api.get.call_count, 1)
        api.tree('c' * 40)
        self.assertEqual(api.get.call_count, 2)

    def test_shared_blob_cache_still_checks_each_tree_entry(self):
        api = validate_pr.GitHub('test-org/registry', 'test')
        api.get = Mock(return_value={'encoding': 'base64', 'size': 5,
                                    'content': base64.b64encode(b'value').decode()})
        tree = {'data.yaml': {'type': 'blob', 'mode': '100644', 'sha': 'b' * 40}}
        self.assertEqual(api.reader(tree)('data.yaml'), 'value')
        self.assertEqual(api.reader(deepcopy(tree))('data.yaml'), 'value')
        self.assertEqual(api.get.call_count, 1)
        for changes in ({'mode': '120000'}, {'mode': '100755'}, {'size': 1024 * 1024 + 1}):
            bad = {'data.yaml': {**tree['data.yaml'], **changes}}
            with self.subTest(changes=changes), self.assertRaises(Invalid):
                api.reader(bad)('data.yaml')

    def test_mutable_metadata_is_never_cached(self):
        api = validate_pr.GitHub('test-org/registry', 'test')
        api.request = Mock(side_effect=[{'state': 'open'}, {'state': 'closed'}])
        self.assertEqual(api.get('pulls/99')['state'], 'open')
        self.assertEqual(api.get('pulls/99')['state'], 'closed')


class ExportTests(unittest.TestCase):
    def test_combined_export_matches_existing_export_formats(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            folder = Path(directory)
            commands = [('export', '--output-dir', str(folder / 'combined')),
                        ('catalog', '--output', str(folder / 'projects.json')),
                        ('working-groups', '--output', str(folder / 'working_groups.yaml'))]
            for command in commands:
                with patch.object(sys, 'argv', ['project_registry', '--root', str(ROOT), *command]):
                    self.assertEqual(project_registry.main(), 0)
            for name in ('projects.json', 'working_groups.yaml'):
                self.assertEqual((folder / name).read_text(), (folder / 'combined' / name).read_text())


if __name__ == '__main__':
    unittest.main()
