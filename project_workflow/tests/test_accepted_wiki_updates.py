"""Offline acceptance numbering, wiki projections, and authenticated automatic updates."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from project_workflow import accepted, updates, wiki
from project_workflow.generated_files import render as generated
from project_workflow.project_registry import Invalid, load_context, parse_yaml, validate_record
from project_workflow.submission import project_yaml, proposal
from test_issue_submission import FakeAPI, Teams, BASE_SHA, issue_fixture
from test_registry import source_data

NOW = '2026-10-18T03:00:00Z'
PATH = 'projects/P000012.yaml'


def accepted_api(kind='EP'):
    api = FakeAPI()
    ctx = load_context(api.base.__getitem__, paths=api.base)
    record = proposal(api.issue, ctx, organization_member=True, is_sc=False)
    if kind == 'CPP':
        record.update(kind='CPP', status='open', leads=[], contributors=[])
        record.pop('phd_thesis')
    api.base[PATH] = project_yaml(record)
    accepted.assign(api)
    return api


def current_ctx(api):
    data = api.data[api.refs['main']]
    return load_context(data.__getitem__, paths=data)


def request(ctx, **changes):
    values = {key: '_No response_' for key in updates.FIELDS}
    values.update(project_number='SCM1', status='No change')
    values.update(changes)
    issue = issue_fixture()
    issue.update(title='Update SCM1', labels=[{'name': updates.UPDATE_LABEL}])
    issue['body'] = '\n\n'.join('### ' + field['attributes']['label'] + '\n\n' + values[field['id']]
        for field in updates.form(ctx)['body'] if field['type'] != 'markdown')
    return issue


class NumberingTests(unittest.TestCase):
    def test_proposals_do_not_consume_numbers_and_acceptance_is_idempotent(self):
        api = FakeAPI()
        self.assertEqual(accepted.assign(api), 0)
        api = accepted_api()
        ctx = current_ctx(api)
        self.assertEqual(ctx.projects['P000012']['project_number'], 'SCM1')
        writes = len(api.writes)
        self.assertEqual(accepted.assign(api), 0)
        self.assertEqual(len(api.writes), writes)
        self.assertFalse(any(endpoint == 'pulls' for _, endpoint, _ in api.writes))

    def test_shared_kind_sequence_per_group_and_submission_order_in_batch(self):
        api = FakeAPI()
        ctx = load_context(api.base.__getitem__, paths=api.base)
        record = proposal(api.issue, ctx, organization_member=True, is_sc=False)
        later = deepcopy(record)
        later.update(project_id='P000022', kind='CPP', leads=[], status='open')
        later['submission'].update(issue_number=22, submitted_at='2026-10-12T00:00:00Z')
        other = deepcopy(record)
        other.update(project_id='P000003', working_groups=[{'code':'CAS', 'deliverable_ids':[]}])
        other['submission'].update(issue_number=3)
        for p in (later, other, record):
            api.base['projects/' + p['project_id'] + '.yaml'] = project_yaml(p)
        self.assertEqual(accepted.assign(api), 3)
        ctx = current_ctx(api)
        self.assertEqual([ctx.projects[k]['project_number'] for k in ('P000012','P000022','P000003')],
                         ['SCM1','SCM2','CAS1'])

    def test_ledger_mismatch_duplicate_and_wrong_group_fail_closed(self):
        api = accepted_api()
        original = api.data[api.refs['main']]
        for mutation in ('record', 'duplicate', 'group'):
            data = deepcopy(original)
            registry = parse_yaml(data[accepted.NUMBER_REGISTRY])
            if mutation == 'record':
                p = parse_yaml(data[PATH]); p['project_number'] = 'SCM2'; data[PATH] = project_yaml(p)
            elif mutation == 'duplicate':
                registry['allocations'].append(deepcopy(registry['allocations'][0]))
            else:
                registry['allocations'][0]['working_group'] = 'CAS'
            data[accepted.NUMBER_REGISTRY] = project_yaml(registry)
            with self.subTest(mutation=mutation), self.assertRaises(Invalid):
                load_context(data.__getitem__, paths=data)

    def test_allocations_are_never_reused_even_if_record_removed(self):
        api = accepted_api()
        data = api.data[api.refs['main']]
        old = parse_yaml(data.pop(PATH))
        old['project_id'] = 'P000099'
        old.pop('project_number')
        old['submission']['issue_number'] = 99
        data['projects/P000099.yaml'] = project_yaml(old)
        accepted.assign(api)
        self.assertEqual(current_ctx(api).projects['P000099']['project_number'], 'SCM2')

    def test_main_change_prevents_stale_commit_publication(self):
        api = accepted_api()
        parent = api.refs['main']
        p = deepcopy(current_ctx(api).projects['P000012']); p['summary'] = 'Changed'
        with self.assertRaises(Invalid):
            accepted.write_main(api, parent, {PATH: project_yaml(p)}, 'Test',
                still_current=lambda: api.refs.update(main='f'*40))
        self.assertEqual(api.refs['main'], 'f'*40)


class AutomaticUpdateTests(unittest.TestCase):
    def setUp(self):
        self.api = accepted_api()
        self.ctx = current_ctx(self.api)
        self.api.writes.clear()

    def send(self, teams=None, user=None, **changes):
        self.api.issue = request(self.ctx, **changes)
        if user:
            self.api.issue['user'] = {'login':user, 'id':456, 'type':'User'}
        return updates.process(self.api, teams or Teams(), 12)

    def test_all_supported_operations_apply_atomically_without_pr_or_reviewers(self):
        result = self.send(add_lead_name='Jane Smith', add_lead_github_username='janesmith',
            remove_lead='alice', add_contributor_name='Carol Example', add_contributor_github_username='carol',
            remove_contributor='bob', status='Internal review', related_links='https://example.org/new',
            github_url='https://github.com/example/new', additional_information='### Progress\nSome **Markdown**.')
        self.assertEqual(result, 'applied')
        p = current_ctx(self.api).projects['P000012']
        self.assertEqual([x['github_username'] for x in p['leads']], ['janesmith'])
        self.assertEqual([x['github_username'] for x in p['contributors']], ['carol'])
        self.assertEqual(p['status'], 'IR')
        self.assertIn('https://example.org/new', p['related_links'])
        self.assertEqual(p['github_url'], 'https://github.com/example/new')
        self.assertEqual(p['additional_information'], '### Progress\nSome **Markdown**.')
        self.assertEqual(len(p['updates']), 1)
        self.assertEqual(self.api.issue['state'], 'closed')
        self.assertFalse(any('pulls' in endpoint or 'comments' in endpoint for _, endpoint, _ in self.api.writes))
        self.assertEqual(updates.process(self.api, Teams(), 12), 'already-applied')
        self.assertEqual(len(current_ctx(self.api).projects['P000012']['updates']), 1)

    def test_lead_and_sc_allowed_other_people_refused(self):
        for user, is_sc, allowed in [('alice',False,True), ('sc-user',True,True), ('mallory',False,False)]:
            self.setUp()
            if allowed:
                self.assertEqual(self.send(Teams(sc=is_sc), user, status='Paused'), 'applied')
            else:
                with self.assertRaises(updates.Refused):
                    self.send(Teams(sc=is_sc), user, status='Paused')
                self.assertFalse(self.api.writes)

    def test_blank_and_identical_updates_make_no_project_commit(self):
        for fields in ({}, {'status':'Active'}, {'related_links':'https://example.org/paper'}):
            self.setUp()
            before = self.api.refs['main']
            self.assertEqual(self.send(**fields), 'unchanged')
            self.assertEqual(self.api.refs['main'], before)
            self.assertFalse(any(endpoint.startswith('git/') for _, endpoint, _ in self.api.writes))

    def test_last_lead_incomplete_people_multiple_people_and_unsafe_links_rejected_atomically(self):
        for fields in ({'remove_lead':'alice'}, {'add_lead_name':'Jane Smith'},
                       {'add_contributor_name':'Jane Smith', 'add_contributor_github_username':'one, two'},
                       {'remove_contributor':'bob alice'}, {'related_links':'https://user:password@example.org'},
                       {'status':'open'}, {'github_url':'https://example.org/not-github'}):
            self.setUp()
            before = self.api.refs['main']
            with self.subTest(fields=fields), self.assertRaises(updates.Refused):
                self.send(**fields)
            self.assertEqual(self.api.refs['main'], before)
            self.assertFalse(self.api.writes)

    def test_replay_of_edited_applied_request_refused(self):
        self.send(status='Completed')
        self.api.issue['body'] += '\nChanged'
        with self.assertRaises(Invalid):
            updates.process(self.api, Teams(), 12)

    def test_changed_request_and_revoked_sc_authorization_block_writes(self):
        self.api.issue = request(self.ctx, status='Paused')
        self.api.issue['user'] = {'login':'sc-user','id':456,'type':'User'}
        teams = Teams(sc=True)
        calls = 0
        def role(*args):
            nonlocal calls
            calls += 1
            return calls < 3
        teams.is_active_member = role
        with self.assertRaises(Invalid):
            updates.process(self.api, teams, 12)
        self.assertEqual(current_ctx(self.api).projects['P000012']['status'], 'active')

    def test_request_edit_after_commit_preparation_blocks_publication(self):
        self.api.issue = request(self.ctx, status='Paused')
        real_request = self.api.request
        def changed(method, endpoint, payload=None):
            result = real_request(method, endpoint, payload)
            if endpoint == 'git/commits':
                self.api.issue['body'] += '\nChanged during processing'
            return result
        self.api.request = changed
        before = self.api.refs['main']
        with self.assertRaises(Invalid):
            updates.process(self.api, Teams(), 12)
        self.assertEqual(self.api.refs['main'], before)

    def test_lost_main_write_response_retries_without_repeating_operations(self):
        self.api.issue = request(self.ctx, remove_contributor='bob')
        real_request = self.api.request
        def lost(method, endpoint, payload=None):
            result = real_request(method, endpoint, payload)
            if endpoint == 'git/refs/heads/main':
                raise OSError('Response lost after commit')
            return result
        self.api.request = lost
        with self.assertRaises(OSError):
            updates.process(self.api, Teams(), 12)
        self.api.request = real_request
        self.assertEqual(updates.process(self.api, Teams(), 12), 'already-applied')
        self.assertEqual(len(current_ctx(self.api).projects['P000012']['updates']), 1)

    def test_numbered_open_cpp_claim_resolves_internal_id_and_becomes_active(self):
        from project_workflow import claims
        from project_workflow.issue_form import current_labels
        self.api = accepted_api('CPP'); self.ctx = current_ctx(self.api)
        issue = issue_fixture()
        issue.update(title='Claim CPP', labels=[{'name':'claim-cpp'}])
        values = {key:'_No response_' for key in current_labels(self.ctx,'CLAIM')}
        values.update(lead='Alice Example',lead_github_username='alice',claim_project='SCM1 — Project')
        issue['body'] = '\n\n'.join('### ' + label + '\n\n' + values[key]
            for key,label in current_labels(self.ctx,'CLAIM').items())
        claimed = claims.claim_proposal(issue, self.ctx, organization_member=True, is_sc=False)
        self.assertEqual(claimed['project_id'], 'P000012')
        self.assertEqual(claimed['project_number'], 'SCM1')
        self.assertEqual(claimed['status'], 'active')
        self.assertTrue(claims.project_options(self.ctx)[0].startswith('SCM1 — '))

    def test_prefill_is_not_authority_for_a_different_project(self):
        with self.assertRaises(updates.Refused):
            self.send(project_number='SCM999', status='Completed')
        self.assertFalse(self.api.writes)

    def test_open_cpp_cannot_bypass_claim_review(self):
        self.api = accepted_api('CPP'); self.ctx = current_ctx(self.api)
        with self.assertRaises(updates.Refused):
            self.send(Teams(sc=True), 'sc-user', add_lead_name='Jane Smith', add_lead_github_username='janesmith')
        self.assertEqual(current_ctx(self.api).projects['P000012']['status'], 'open')


class WikiTests(unittest.TestCase):
    def test_pages_have_requested_tables_links_badges_and_preserved_markdown(self):
        api = accepted_api()
        ctx = current_ctx(api)
        p = ctx.projects['P000012']
        p['additional_information'] = '**Progress**\n\nSome details.'
        p['github_url'] = 'https://github.com/example/project'
        pages = wiki.render(ctx, api.repo, now=NOW)
        self.assertTrue({'Home.md','CPP.md','EP.md','WG-SCM.md','WG-CAS.md','WG-CPIP.md','SCM1.md'} <= set(pages))
        self.assertLess(pages['Home.md'].index('## CPP projects'), pages['Home.md'].index('## EP projects'))
        self.assertIn('Project number | Project status | Working group | Project kind | Project lead | Project title', pages['Home.md'])
        self.assertIn('Alice Example', pages['Home.md'])
        self.assertIn(' | Active | ', pages['Home.md'])
        self.assertNotIn('Bob Example', pages['Home.md'])
        self.assertNotIn('Shape measurement validation', pages['WG-CAS.md'])
        self.assertIn('color=blue', pages['SCM1.md'])
        self.assertIn('project_number=SCM1', pages['SCM1.md'])
        self.assertIn('**Progress**', pages['SCM1.md'])
        self.assertLess(pages['SCM1.md'].index('## Project description'), pages['SCM1.md'].index('## Additional information'))
        self.assertNotIn('<iframe', pages['SCM1.md'])

    def test_open_cpp_claim_link_does_not_prefill_project(self):
        api = accepted_api('CPP')
        pages = wiki.render(current_ctx(api), api.repo)
        self.assertIn('color=white', pages['SCM1.md'])
        self.assertIn('/issues/new?template=claim-cpp.yml)', pages['SCM1.md'])
        self.assertNotIn('claim_project=', pages['SCM1.md'])

    def test_unaccepted_unumbered_records_are_not_displayed(self):
        data = source_data(); ctx = load_context(data.__getitem__, paths=data)
        ctx.projects['P000012'] = proposal(issue_fixture(), ctx, organization_member=True, is_sc=False)
        self.assertNotIn('SCM1.md', wiki.render(ctx, 'test-org/registry'))
        self.assertNotIn('Shape measurement validation', wiki.render(ctx, 'test-org/registry')['Home.md'])

    def test_private_repository_details_are_never_copied_and_prs_excluded(self):
        api = accepted_api(); ctx = current_ctx(api)
        ctx.projects['P000012']['github_url'] = 'https://github.com/example/project'
        class IssueAPI:
            private = True
            def __init__(self, *args): pass
            def get(self, endpoint): return {'private':self.private,'has_issues':True}
            def pages(self, endpoint):
                if self.private: raise AssertionError('Private issues must never be fetched')
                return [{'number':1,'title':'An issue'}, {'number':2,'title':'A PR','pull_request':{}}]
        result = wiki.snapshots(ctx, 'test', api_factory=IssueAPI)
        self.assertEqual(next(iter(result.values()))['state'], 'restricted')
        IssueAPI.private = False
        result = wiki.snapshots(ctx, 'test', api_factory=IssueAPI)
        page = wiki.render(ctx, api.repo, issues=result, now=NOW)['SCM1.md']
        self.assertIn('An issue', page)
        self.assertNotIn('A PR', page)
        self.assertIn(NOW, page)

    def test_manifest_removes_only_previously_generated_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'Manual.md').write_text('Keep this')
            wiki.write_pages(folder, {'Home.md':'First', 'SCM1.md':'Project'})
            wiki.write_pages(folder, {'Home.md':'Second'})
            self.assertFalse((folder/'SCM1.md').exists())
            self.assertEqual((folder/'Manual.md').read_text(), 'Keep this')
            (folder/wiki.MANIFEST).write_text(json.dumps(['../../outside.md']))
            with self.assertRaises(Invalid): wiki.write_pages(folder, {'Home.md':'Third'})

    def test_generated_update_form_has_only_project_number_required(self):
        ctx = current_ctx(accepted_api())
        self.assertIn(updates.UPDATE_FORM, generated(ctx))
        controls = [f for f in updates.form(ctx)['body'] if 'id' in f]
        self.assertEqual([f['id'] for f in controls if f['validations']['required']], ['project_number'])
        self.assertEqual(sum(f['id']=='add_lead_name' for f in controls), 1)

    def test_wiki_publisher_uses_nonforced_git_preserves_other_pages_and_is_idempotent(self):
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote, seed, output = root/'remote.git', root/'seed', root/'output'
            def local_git(*args, cwd=None):
                return real_run(['git',*args],cwd=cwd,capture_output=True,text=True,check=True).stdout
            local_git('init','--bare','--initial-branch=main',str(remote))
            local_git('init','--initial-branch=main',str(seed))
            local_git('config','user.name','Test',cwd=seed)
            local_git('config','user.email','test@example.org',cwd=seed)
            (seed/'Manual.md').write_text('Keep this page')
            (seed/'Home.md').write_text('Initial wiki')
            local_git('add','.',cwd=seed)
            local_git('commit','-m','Initialize',cwd=seed)
            local_git('remote','add','origin',str(remote),cwd=seed)
            local_git('push','origin','main',cwd=seed)
            wiki.write_pages(output,{'Home.md':'Generated home','SCM1.md':'Accepted project'})
            calls=[]
            def isolated(command, **kwargs):
                calls.append(list(command))
                if command[1]=='clone':
                    command = [*command[:-2],str(remote),command[-1]]
                return real_run(command,**kwargs)
            with patch.object(wiki.subprocess,'run',side_effect=isolated):
                self.assertEqual(wiki.publish('test-org/registry',output,'test-credential'),'published')
                self.assertEqual(wiki.publish('test-org/registry',output,'test-credential'),'unchanged')
            self.assertEqual(local_git('--git-dir='+str(remote),'show','main:Manual.md').strip(),'Keep this page')
            self.assertEqual(local_git('--git-dir='+str(remote),'show','main:SCM1.md').strip(),'Accepted project')
            self.assertTrue(any(c[1]=='push' for c in calls))
            self.assertFalse(any('--force' in c or 'test-credential' in ' '.join(c) for c in calls))


if __name__ == '__main__':
    unittest.main()
