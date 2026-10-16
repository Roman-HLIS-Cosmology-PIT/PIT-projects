"""Exercise the pre-checkout workflow step without network access or credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


class ValidationCheckoutTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / '.github/workflows/projects.yml').read_text())
        self.steps = workflow['jobs']['validate']['steps']
        self.resolver = self.steps[0]

    def resolve(self, *, number='36', repo='test-org/registry', ref='main', sha='a' * 40,
                api_exit=0):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            gh = directory / 'gh'
            gh.write_text(f'#!{sys.executable}\n' + '''import json, os, pathlib, sys
pathlib.Path(os.environ['API_CALL']).write_text(json.dumps(sys.argv[1:]))
print(os.environ['API_RESPONSE'])
sys.exit(int(os.environ['API_EXIT']))
''')
            gh.chmod(0o700)
            output = directory / 'output'
            output.touch()
            call = directory / 'call'
            env = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                   'GITHUB_REPOSITORY': 'test-org/registry', 'POLICY_PR_NUMBER': number,
                   'GITHUB_SHA': 'c' * 40, 'GITHUB_OUTPUT': str(output), 'GH_TOKEN': 'dummy',
                   'API_CALL': str(call), 'API_RESPONSE': '\t'.join((repo, ref, sha)),
                   'API_EXIT': str(api_exit)}
            result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail',
                                     '-c', self.resolver['run']],
                                    env=env, capture_output=True, text=True)
            return result, output.read_text(), json.loads(call.read_text()) if call.exists() else None

    def test_dispatch_checks_out_older_pr_base_instead_of_newer_main(self):
        self.assertEqual(self.resolver['if'], "github.event_name == 'workflow_dispatch'")
        result, output, call = self.resolve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(output, 'sha=' + 'a' * 40 + '\n')
        self.assertEqual(call, ['api', 'repos/test-org/registry/pulls/36', '--jq',
                                '.base | [.repo.full_name, .ref, .sha] | @tsv'])
        checkout = self.steps[1]
        self.assertEqual(checkout['with']['ref'],
                         '${{ github.event.pull_request.base.sha || steps.pr-base.outputs.sha }}')
        self.assertFalse(checkout['with']['persist-credentials'])

    def test_invalid_pr_number_fails_before_api_or_checkout(self):
        for number in ('', '0', '-1', '36/commits', '36; echo injected'):
            with self.subTest(number=number):
                result, output, call = self.resolve(number=number)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')
                self.assertIsNone(call)

    def test_untrusted_base_or_invalid_sha_cannot_be_checked_out(self):
        for changes in ({'repo': 'other/repo'}, {'ref': 'untrusted-branch'},
                        {'sha': ''}, {'sha': 'main'}, {'sha': 'a' * 40 + '\nsha=bad'}):
            with self.subTest(changes=changes):
                result, output, _ = self.resolve(**changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')

    def test_api_failure_cannot_fall_back_to_main_or_pr_head(self):
        result, output, _ = self.resolve(api_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()
