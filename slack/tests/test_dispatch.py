"""Execute the real validation job's dispatch step with an isolated fake gh CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
FAKE_GH = r'''
import json, os, pathlib, sys
args = sys.argv[1:]
state_file = pathlib.Path(os.environ['FAKE_GH_STATE'])
state = json.loads(state_file.read_text())
if args[0] == 'api' and '--method' not in args:
    if os.environ.get('FAKE_READ_FAIL') == '1':
        sys.exit(42)
    if '/pulls/' in args[1]:
        print('\t'.join([os.environ.get('FAKE_HEAD', 'a' * 40),
                          os.environ.get('FAKE_AUTHOR', '41898282'),
                          os.environ.get('FAKE_BRANCH', 'project-submissions/issue-41'),
                          os.environ.get('FAKE_PR_STATE', 'open')]))
    elif '/commits/' in args[1] and args[1].endswith('/status'):
        head = args[1].split('/')[-2]
        print(os.environ.get('FAKE_POLICY', 'success') + '\t' + state.get('queued', {}).get(head, 'missing'))
    else:
        raise AssertionError(args)
elif args[:2] == ['workflow', 'run']:
    state.setdefault('dispatches', []).append(args)
    state_file.write_text(json.dumps(state))
    sys.exit(1 if os.environ.get('FAKE_DISPATCH_FAIL') == '1' else 0)
elif args[:3] == ['api', '--method', 'POST'] and '/statuses/' in args[3]:
    state.setdefault('receipts', []).append(args)
    state.setdefault('queued', {})[args[3].split('/')[-1]] = 'success'
    state_file.write_text(json.dumps(state))
else:
    raise AssertionError(args)
'''


class DispatchTests(unittest.TestCase):
    def setUp(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/projects.yml').read_text())
        self.job = workflow['jobs']['validate']
        action = yaml.safe_load((ROOT / 'project_workflow/policy/action.yml').read_text())
        self.steps = action['runs']['steps']
        self.step = next(step for step in self.steps
                         if step.get('name') == 'Queue Slack sync for the validated submission PR')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        folder = Path(self.directory.name)
        self.state = folder / 'state.json'
        self.state.write_text('{}')
        gh = folder / 'gh'
        gh.write_text('#!' + sys.executable + '\n' + FAKE_GH)
        gh.chmod(0o700)
        self.env = {**os.environ, 'PATH': str(folder) + os.pathsep + os.environ['PATH'],
                    'FAKE_GH_STATE': str(self.state), 'GH_TOKEN': 'test-token',
                    'GITHUB_REPOSITORY': 'test-org/registry', 'GITHUB_RUN_ID': '123', 'REVIEW_PR': '42',
                    'SLACK_APP_ID': 'A123', 'SLACK_REVIEW': 'true'}

    def run_step(self, **changes):
        return subprocess.run(['bash', '-c', self.step['run']], env={**self.env, **changes},
                              capture_output=True, text=True, timeout=10)

    def saved(self):
        return json.loads(self.state.read_text())

    def test_duplicate_validation_of_one_head_queues_only_one_sync(self):
        first = self.run_step()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_step()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.saved()['dispatches']), 1)
        self.assertEqual(len(self.saved()['receipts']), 1)
        self.assertIn('operation=sync', self.saved()['dispatches'][0])
        self.assertIn('pr_number=42', self.saved()['dispatches'][0])

    def test_new_validated_head_can_queue_its_own_sync(self):
        self.assertEqual(self.run_step().returncode, 0)
        self.assertEqual(self.run_step(FAKE_HEAD='b' * 40).returncode, 0)
        self.assertEqual(len(self.saved()['dispatches']), 2)

    def test_failed_or_pending_policy_cannot_dispatch(self):
        for state in ('pending', 'failure', 'missing'):
            with self.subTest(state=state):
                self.assertNotEqual(self.run_step(FAKE_POLICY=state).returncode, 0)
        self.assertFalse(self.saved().get('dispatches'))

    def test_failed_dispatch_keeps_the_head_retryable(self):
        self.assertNotEqual(self.run_step(FAKE_DISPATCH_FAIL='1').returncode, 0)
        self.assertFalse(self.saved().get('receipts'))
        self.assertEqual(self.run_step().returncode, 0)
        self.assertEqual(len(self.saved()['receipts']), 1)

    def test_non_submission_or_closed_pr_does_not_dispatch(self):
        for changes in ({'FAKE_AUTHOR': '123'}, {'FAKE_BRANCH': 'sc-edit'}, {'FAKE_PR_STATE': 'closed'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.run_step(**changes).returncode, 0)
        self.assertFalse(self.saved().get('dispatches'))

    def test_invalid_inputs_and_read_failure_do_not_dispatch(self):
        for changes in ({'REVIEW_PR': '42; bad-command'}, {'FAKE_HEAD': 'invalid'}, {'FAKE_READ_FAIL': '1'}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.run_step(**changes).returncode, 0)
        self.assertFalse(self.saved().get('dispatches'))

    def test_dispatch_is_after_validation_and_manual_schedule_are_preserved(self):
        self.assertEqual(self.step['if'],
            "success() && (inputs.prevalidated_review != '' || steps.pr-base.outputs.open == 'true')")
        self.assertEqual(self.job['permissions']['actions'], 'write')
        steps = self.steps
        policy = next(i for i, step in enumerate(steps)
                      if step.get('run') == 'python -m project_workflow.validate_pr --publish-status')
        self.assertGreater(steps.index(self.step), policy)
        slack = yaml.safe_load((ROOT / '.github/workflows/slack.yml').read_text())
        events = slack.get('on', slack.get(True))  # PyYAML's YAML 1.1 parses "on" as True.
        self.assertNotIn('workflow_run', events)
        self.assertNotIn('issues', events)
        self.assertEqual(events['pull_request_target']['types'], ['closed'])
        self.assertEqual(events['schedule'][0]['cron'], '0 0 * * *')
        self.assertEqual(events['workflow_dispatch']['inputs']['operation']['options'], ['check', 'sync'])

    def test_both_callers_pass_slack_setting_into_composite_scope(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/projects.yml').read_text())
        for name in ('notify', 'validate'):
            action = next(step for step in workflow['jobs'][name]['steps']
                          if step.get('uses') == './project_workflow/policy')
            self.assertEqual(action['with']['slack_app_id'], '${{ vars.SLACK_APP_ID }}')
        action = (ROOT / 'project_workflow/policy/action.yml').read_text()
        self.assertNotIn('vars.', action)
        self.assertEqual(self.step['env']['SLACK_APP_ID'], '${{ inputs.slack_app_id }}')
        self.assertEqual(self.step['env']['SLACK_REVIEW'], '${{ inputs.prevalidated_review || steps.policy.outputs.slack_review }}')

    def test_unconfigured_slack_and_cpp_have_explicit_skip_reasons(self):
        for changes, reason in (({'SLACK_APP_ID': ''}, 'Slack sync disabled'),
                                ({'SLACK_REVIEW': 'false'}, 'Slack sync not needed')):
            with self.subTest(changes=changes):
                result = self.run_step(**changes)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(reason, result.stdout)
        self.assertFalse(self.saved().get('dispatches'))

    def test_missing_policy_output_fails_instead_of_silently_skipping_slack(self):
        result = self.run_step(SLACK_REVIEW='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not supply its Slack routing output', result.stdout)
        self.assertFalse(self.saved().get('dispatches'))


if __name__ == '__main__':
    unittest.main()
