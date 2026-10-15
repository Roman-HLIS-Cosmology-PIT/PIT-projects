"""Claims update one accepted CPP; every permission is checked from live inputs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from project_workflow import claims, commands, issue_intake, validate_pr
from project_workflow.generated_files import render, updates_for, relevant
from project_workflow.issue_form import current_labels, form, FORM_PATHS
from project_workflow.project_registry import Invalid, load_context, parse_yaml, validate_record
from project_workflow.submission import (project_yaml, provenance, record_path, issue_project_path,
    proposal, submission_body, validate_bot_snapshot)
from test_commands import CommandTeams
from test_issue_submission import FakeAPI, BASE_SHA, issue_fixture, BOT
from test_registry import source_data, project

TARGET = 'P000042'
PATH = 'projects/' + TARGET + '.yaml'


def accepted_cpp(**changes):
    result = project()
    result.update(project_id=TARGET, kind='CPP', leads=[],
        slack_channel='#sc-declared', github_url='https://github.com/example/original',
        related_links=['https://example.org/sc-paper'],
        submission={'issue_number':42,'submitted_by':'sc-user','submitted_at':'2026-10-15T00:00:00Z',
                    'issue_title_sha256':'a'*64,'issue_body_sha256':'b'*64})
    result.pop('phd_thesis',None)
    result.update(changes)
    return result


def claim_issue(ctx, **changes):
    issue = issue_fixture()
    values = {key:'_No response_' for key in current_labels(ctx,'CLAIM')}
    values.update(lead='Jane Smith',lead_github_username='alice',
                  claim_project=TARGET+' — Project', related_links='https://example.org/member-paper')
    values.update(changes)
    issue.update(title='Claim CPP', labels=[{'name':'claim-cpp'}])
    issue['body'] = '\n\n'.join('### '+label+'\n\n'+values[key]
        for key,label in current_labels(ctx,'CLAIM').items())
    return issue


class ClaimAPI(FakeAPI):
    def __init__(self, record=None, **answers):
        super().__init__()
        self.base[PATH] = project_yaml(record if record is not None else accepted_cpp())
        self.ctx = load_context(self.base.__getitem__,paths=self.base)
        self.base.update(render(self.ctx))
        self.issue = claim_issue(self.ctx,**answers)

    def request(self, method, endpoint, payload=None):
        result = super().request(method,endpoint,payload)
        if method == 'POST' and endpoint == 'pulls':
            self.pr['title'] = payload['title']
            result['title'] = payload['title']
        return result

    def change_main(self, record=None, delete=False):
        sha = self.next_sha()
        self.data[sha] = deepcopy(self.data[self.refs['main']])
        if delete:
            self.data[sha].pop(PATH,None)
        elif record is not None:
            self.data[sha][PATH] = project_yaml(record)
        self.refs['main'] = sha
        self.commits[sha] = {'tree':{'sha':sha},'parents':[{'sha':BASE_SHA}]}
        return sha


class CPPClaimTests(unittest.TestCase):
    def setUp(self):
        self.api = ClaimAPI()
        self.ctx = self.api.ctx
        self.teams = CommandTeams(sc={'sc-user'})
        lookup = patch('project_workflow.issue_intake.urllib.request.build_opener')
        lookup.start()
        self.addCleanup(lookup.stop)
        self.next_comment = 1000

    def submit(self):
        return issue_intake.process(self.api,self.teams,12,repo_check=lambda url:None)

    def record(self):
        return parse_yaml(self.api.data[self.api.prs[99]['head']['sha']][PATH])

    def send(self,text,login='alice',user_id=123):
        self.next_comment += 1
        comment = {'id':self.next_comment,'body':text,'user':{'login':login,'id':user_id,'type':'User'},
                   'issue_url':'https://api.github.com/repos/test-org/registry/issues/12'}
        self.api.comments.append(comment)
        return commands.process(self.api,self.teams,12,deepcopy(comment)),comment

    def validate(self):
        pr = self.api.prs[99]
        base,head = self.api.tree(pr['base']['sha']),self.api.tree(pr['head']['sha'])
        return validate_bot_snapshot(base,head,self.api.reader(base),self.api.reader(head),pr,
            self.api.issue,self.ctx,organization_member=True,is_sc=False,api=self.api,teams=self.teams)

    def test_form_order_single_required_project_and_no_sc_or_phd_inputs(self):
        spec = form(self.ctx,'CLAIM')
        fields = [f for f in spec['body'] if 'id' in f]
        self.assertEqual([f['id'] for f in fields[:3]],['lead','lead_github_username','claim_project'])
        dropdown = fields[2]
        self.assertFalse(dropdown['attributes']['multiple'])
        self.assertTrue(dropdown['validations']['required'])
        self.assertEqual(spec['title'],'Claim CPP')
        self.assertEqual(spec['labels'],['claim-cpp'])
        for f in fields:
            self.assertNotIn(f['id'],{'working_groups','summary','phd_thesis'})
            self.assertFalse(f['id'].startswith('deliverables_'))

    def test_claim_updates_existing_id_preserves_declaration_and_main_until_merge(self):
        original = deepcopy(self.ctx.projects[TARGET])
        raw = self.api.issue['body']
        self.assertEqual(self.submit(),'created')
        record = self.record()
        self.assertEqual(record['project_id'],TARGET)
        self.assertEqual(record['kind'],'CPP')
        self.assertEqual(record['leads'],[{'name':'Jane Smith','github_username':'alice'}])
        self.assertEqual(record['submission'],original['submission'])
        self.assertEqual(record['claim']['issue_number'],12)
        self.assertEqual(record['related_links'],['https://example.org/sc-paper','https://example.org/member-paper'])
        self.assertEqual(submission_body(self.api.issue),raw)
        self.assertEqual(parse_yaml(self.api.data[BASE_SHA][PATH]),original)
        self.assertEqual(set(self.api.data[self.api.pr['head']['sha']])-set(self.api.base),set())
        self.assertEqual(self.api.issue['title'],'Claim CPP: Project')
        self.assertEqual(self.api.pr['title'],'Claim CPP: Project')
        self.assertIn('**Project lead**: Jane Smith',self.api.issue['body'])
        self.assertIn('**Contributors**:',self.api.issue['body'])
        self.assertIn('**Working groups and deliverables**:',self.api.issue['body'])
        self.assertNotIn('**Part of a PhD thesis**:',self.api.issue['body'])
        self.assertEqual(self.validate(),record)
        review = next(p for m,e,p in self.api.writes if e.endswith('/requested_reviewers'))
        self.assertEqual(review,{'reviewers':['aguinot'],'team_reviewers':[]})

    def test_manual_cpp_without_declaration_provenance_is_claimable(self):
        record = accepted_cpp()
        for key in ('submission','github_url','slack_channel','related_links'):
            record.pop(key,None)
        self.api = ClaimAPI(record)
        self.ctx = self.api.ctx
        self.assertEqual(self.submit(),'created')
        self.assertNotIn('submission',self.record())
        self.assertEqual(self.validate(),self.record())

    def test_nonmembers_and_claims_for_other_people_are_rejected(self):
        for changes,member,sc in (({},False,False),({'lead_github_username':'janesmith'},True,False),
                                   ({'lead':'_No response_'},True,False),({'lead_github_username':'@alice'},True,False)):
            with self.subTest(changes=changes,member=member):
                with self.assertRaises(Invalid):
                    proposal(claim_issue(self.ctx,**changes),self.ctx,organization_member=member,is_sc=sc)
        proposal(claim_issue(self.ctx,lead_github_username='janesmith'),self.ctx,organization_member=True,is_sc=True)

    def test_only_accepted_active_unassigned_cpp_targets_are_allowed(self):
        for replacement in (accepted_cpp(kind='EP',leads=[{'name':'Jane Smith','github_username':'janesmith'}]),accepted_cpp(kind='KEY'),
                            accepted_cpp(status='completed'),accepted_cpp(status='paused'),
                            accepted_cpp(leads=[{'name':'Jane Smith','github_username':'janesmith'}])):
            with self.subTest(replacement=replacement):
                api = ClaimAPI(replacement)
                with self.assertRaises(Invalid):
                    proposal(api.issue,api.ctx,organization_member=True,is_sc=False)
        for value in ('P999999 — Unknown',TARGET+', P999999', 'No CPP projects available'):
            with self.subTest(value=value),self.assertRaises(Invalid):
                proposal(claim_issue(self.ctx,claim_project=value),self.ctx,organization_member=True,is_sc=False)

    def test_slack_and_repository_replace_only_supplied_valid_values(self):
        original = self.ctx.projects[TARGET]
        for values in ({},{'slack_channel':'#member','github_url':'https://github.com/example/member'},
                       {'slack_channel':'x'*301,'github_url':'not a url'}):
            with self.subTest(values=values):
                result = proposal(claim_issue(self.ctx,**values),self.ctx,organization_member=True,is_sc=False)
                valid = values.get('slack_channel')=='#member'
                self.assertEqual(result['slack_channel'],'#member' if valid else original['slack_channel'])
                self.assertEqual(result['github_url'],'https://github.com/example/member' if valid else original['github_url'])

    def test_initial_links_and_contributors_cleanup_and_deduplication(self):
        self.api = ClaimAPI(related_links='https://example.org/sc-paper\nhttps://example.org/new\nbad',
            contributor_1_name='Jane Smith',contributor_1_github_username='janesmith',
            contributor_2_name='Incomplete',contributor_2_github_username='_No response_')
        self.ctx = self.api.ctx
        self.assertEqual(self.submit(),'created')
        self.assertEqual(self.record()['related_links'],['https://example.org/sc-paper','https://example.org/new'])
        self.assertEqual(self.record()['contributors'],[{'name':'Jane Smith','github_username':'janesmith'}])
        self.assertIn('were removed',self.api.comments[0]['body'])
        self.assertEqual(self.validate(),self.record())

    def test_merge_link_limit_keeps_existing_links_and_reports_omissions(self):
        self.api = ClaimAPI(accepted_cpp(related_links=[f'https://example.org/{i}' for i in range(100)]))
        self.ctx = self.api.ctx
        self.assertEqual(self.submit(),'created')
        self.assertEqual(len(self.record()['related_links']),100)
        self.assertIn('combined list',self.api.comments[0]['body'])
        self.assertEqual(self.validate(),self.record())

    def test_retained_commands_and_removal_of_sc_links_replay(self):
        self.submit()
        requests = ('/edit slack #claim-channel','/edit github https://github.com/example/member',
                    '/edit add links https://example.org/new',
                    '/edit remove links https://example.org/sc-paper',
                    '/edit add contributor name=Jane Smith username=janesmith',
                    '/edit remove contributor name=Jane Smith',
                    '/edit add lead name=Jane Smith username=janesmith',
                    '/edit remove lead username=janesmith', '/edit remove slack','/edit remove github')
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(self.send(text)[0],'edited')
                self.assertEqual(self.validate(),self.record())
        self.assertNotIn('https://example.org/sc-paper',self.record()['related_links'])
        self.assertEqual(self.api.issue['title'],'Claim CPP: Project')
        self.assertEqual(self.api.prs[99]['title'],'Claim CPP: Project')
        self.assertEqual(self.record()['submission'],self.ctx.projects[TARGET]['submission'])
        self.assertEqual(len(self.record()['claim']['edits']),len(requests))

    def test_protected_commands_and_last_lead_reject_without_mutation(self):
        self.submit()
        record,sha,body,statuses = self.record(),self.api.pr['head']['sha'],self.api.issue['body'],len(self.api.statuses)
        for command in ('/edit title Changed','/edit description\nChanged','/edit working-group CAS',
                        '/edit deliverables SCM-2','/edit remove deliverables','/edit phd true',
                        '/edit remove lead username=alice'):
            with self.subTest(command=command):
                self.assertEqual(self.send(command)[0],'rejected')
                self.assertEqual(self.record(),record)
                self.assertEqual(self.api.pr['head']['sha'],sha)
                self.assertEqual(self.api.issue['body'],body)
                self.assertEqual(len(self.api.statuses),statuses)
        self.assertEqual(self.validate(),record)

    def test_claim_help_shows_only_applicable_commands(self):
        self.submit()
        self.assertEqual(self.send('/help')[0],'help')
        text = self.api.comments[-1]['body']
        for key in commands.CLAIM_UNAVAILABLE_COMMANDS:
            self.assertNotIn('/'+key,text)
        self.assertIn('/edit add lead',text)
        self.assertIn('/edit remove links',text)
        self.assertEqual(self.send('/edit title help')[0],'rejected')

    def test_only_claimant_current_leads_and_sc_can_edit(self):
        self.submit()
        self.assertEqual(self.send('/edit slack #bob','bob',456)[0],'rejected')
        self.assertEqual(self.send('/edit add lead name=Jane Smith username=janesmith')[0],'edited')
        self.assertEqual(self.send('/edit slack #lead','janesmith',789)[0],'edited')
        self.assertEqual(self.send('/edit slack #sc','sc-user',654)[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_claim_retry_and_command_retry_do_not_duplicate_pr_or_receipts(self):
        self.submit()
        self.assertEqual(self.submit(),'updated')
        result,comment = self.send('/edit slack #claim')
        self.assertEqual(result,'edited')
        self.assertEqual(commands.process(self.api,self.teams,12,comment),'replayed')
        self.assertEqual(len(self.record()['claim']['edits']),1)
        self.assertEqual(len(self.api.prs),1)
        self.assertEqual(self.validate(),self.record())

    def test_two_claims_for_same_target_refuse_second_until_first_is_cancelled(self):
        self.submit()
        self.api.other_issues[13] = {**claim_issue(self.ctx),'number':13}
        self.assertEqual(issue_intake.process(self.api,self.teams,13,repo_check=lambda u:None),'invalid')
        self.assertEqual(len(self.api.prs),1)
        self.assertEqual(self.api.other_issues[13]['state'],'closed')
        self.assertIn('already has an open claim',self.api.other_comments[13][0]['body'])
        self.assertEqual(self.validate(),self.record())
        self.api.issue['state'] = 'closed'
        self.assertEqual(issue_intake.process(self.api,None,12),'cancelled')
        self.assertEqual(parse_yaml(self.api.data[BASE_SHA][PATH]),self.ctx.projects[TARGET])
        self.api.other_issues[14] = {**claim_issue(self.ctx),'number':14}
        self.assertEqual(issue_intake.process(self.api,self.teams,14,repo_check=lambda u:None),'created')

    def test_closing_only_pr_releases_reservation_but_does_not_resurrect_it(self):
        self.submit()
        self.api.pr['state'] = 'closed'
        self.api.other_issues[13] = {**claim_issue(self.ctx),'number':13}
        self.assertEqual(issue_intake.process(self.api,self.teams,13,repo_check=lambda u:None),'created')
        self.assertEqual(issue_intake.process(self.api,self.teams,12,repo_check=lambda u:None),'invalid')

    def test_claims_for_distinct_projects_can_coexist(self):
        other = accepted_cpp(project_id='P000043',name='Other CPP')
        self.api.base['projects/P000043.yaml'] = project_yaml(other)
        self.submit()
        self.api.other_issues[13] = {**claim_issue(self.ctx,claim_project='P000043 — Other CPP'),'number':13}
        self.assertEqual(issue_intake.process(self.api,self.teams,13,repo_check=lambda u:None),'created')
        self.assertEqual(len(self.api.prs),2)
        self.assertEqual(self.validate(),self.record())

    def test_main_sc_change_assignment_or_removal_blocks_stale_claim(self):
        self.submit()
        original = deepcopy(self.ctx.projects[TARGET])
        for replacement,delete in (({**original,'summary':'SC updated science'},False),
                                  ({**original,'leads':[{'name':'Jane Smith','github_username':'janesmith'}]},False),
                                  (None,True)):
            with self.subTest(delete=delete,replacement=replacement):
                self.api.change_main(replacement,delete=delete)
                with self.assertRaises(Invalid):self.validate()
        self.assertEqual(self.record()['summary'],original['summary'])

    def test_merged_claim_is_not_reoffered_even_if_issue_branch_and_lead_are_removed(self):
        self.submit()
        accepted = self.record()
        self.api.change_main(accepted)
        self.api.pr.update(state='closed',merged_at='2026-10-15T00:00:00Z')
        self.api.issue['state'] = 'closed'
        self.api.refs.pop('project-submissions/issue-12')
        self.assertEqual(issue_intake.process(self.api,None,12),'already-merged')
        self.assertFalse(claims.eligible(accepted))
        accepted['leads'] = []
        self.assertFalse(claims.eligible(accepted))
        self.api.change_main(accepted)
        data = self.api.data[self.api.refs['main']]
        ctx = load_context(data.__getitem__,paths=data)
        self.assertEqual(claims.project_options(ctx),[])
        self.api.change_main(delete=True)
        data = self.api.data[self.api.refs['main']]
        self.assertEqual(claims.project_options(load_context(data.__getitem__,paths=data)),[])

    def test_changed_applied_comment_and_unauthenticated_yaml_fail(self):
        self.submit()
        _,comment = self.send('/edit slack #claim')
        record = self.record()
        self.api.comments = [c for c in self.api.comments if c['id'] != comment['id']]
        with self.assertRaises(Invalid):self.validate()
        self.api.comments.append(comment)
        sha = self.api.pr['head']['sha']
        for key,value in (('summary','Altered'),('name','Altered'),('working_groups',[{'code':'CAS','deliverable_ids':[]}]),
                          ('submission',{'issue_number':1}),('kind','KEY'),('project_id','P000012')):
            with self.subTest(key=key):
                self.api.data[sha][PATH] = project_yaml({**record,key:value})
                with self.assertRaises(Invalid):self.validate()
        self.api.data[sha][PATH] = project_yaml(record)
        self.assertEqual(self.validate(),record)

    def test_selection_and_automatic_title_cannot_be_changed_manually(self):
        self.submit()
        title = self.api.issue['title']
        self.api.issue['title'] = 'Claim CPP: Another project'
        with self.assertRaises(Invalid):self.validate()
        self.api.issue['title'] = title
        source = submission_body(self.api.issue).replace(TARGET,'P000043')
        from project_workflow.review import formatted_issue_body
        self.api.issue['body'] = formatted_issue_body(self.record(),self.ctx,source)
        with self.assertRaises(Invalid):self.validate()

    def test_relabel_cannot_turn_claim_into_declaration(self):
        self.submit()
        self.api.issue['labels'] = [{'name':'new-cpp-project'}]
        self.assertEqual(self.send('/edit title Reclassified')[0],'rejected')
        with self.assertRaises(Invalid):self.validate()

    def test_claim_cannot_be_edited_after_acceptance(self):
        self.submit()
        self.api.pr.update(state='closed',merged_at='2026-10-15T00:00:00Z')
        self.api.issue['state'] = 'closed'
        self.assertEqual(self.send('/edit slack #after')[0],'rejected')

    def test_interrupted_claim_creation_and_edit_synchronization_recover(self):
        original_request = self.api.request
        def fail_create(method,endpoint,payload=None):
            if endpoint == 'git/trees':raise OSError('Stopped after formatting')
            return original_request(method,endpoint,payload)
        self.api.request = fail_create
        with self.assertRaises(OSError):self.submit()
        self.assertEqual(self.api.issue['title'],'Claim CPP: Project')
        self.api.request = original_request
        self.assertEqual(self.submit(),'created')
        def fail_edit(method,endpoint,payload=None):
            if method == 'PATCH' and endpoint == 'issues/12':raise OSError('Stopped before issue sync')
            return original_request(method,endpoint,payload)
        self.api.request = fail_edit
        with patch.object(commands.time,'sleep'):
            result,comment = self.send('/edit slack #recovery')
        self.assertEqual(result,'incomplete')
        self.api.request = original_request
        self.assertEqual(commands.process(self.api,self.teams,12,comment),'replayed')
        self.assertEqual(self.validate(),self.record())

    def test_member_cannot_use_general_record_update_to_bypass_claim_verification(self):
        from project_workflow.project_registry import authorize_change
        candidate = proposal(self.api.issue,self.ctx,organization_member=True,is_sc=False)
        with self.assertRaises(Invalid):
            authorize_change(self.ctx.projects[TARGET],candidate,'alice',self.ctx,organization_member=True)

    def test_main_push_revalidates_open_claims(self):
        self.submit()
        before = len(self.api.dispatches)
        claims.refresh_checks(self.api)
        self.assertEqual(len(self.api.dispatches),before+1)
        self.assertEqual(self.api.statuses[-1][1],'pending')
        self.assertEqual(self.api.dispatches[-1]['inputs']['pr_number'],'99')


    def test_independent_policy_entry_point_publishes_success_and_failure_for_stale_main(self):
        self.submit()
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / 'event.json'
            event.write_text(json.dumps({'pull_request':self.api.prs[99]}))
            env = {'GITHUB_EVENT_PATH':str(event),'GITHUB_REPOSITORY':self.api.repo,
                   'GITHUB_TOKEN':'dummy','SC_TEAM_READ_TOKEN':'dummy','POLICY_PR_NUMBER':'99'}
            with patch.dict(os.environ,env), patch.object(validate_pr,'GitHub',return_value=self.api), \
                 patch.object(validate_pr,'TeamDirectory',return_value=self.teams), \
                 patch.object(validate_pr.subprocess,'check_output',return_value=BASE_SHA), \
                 patch.object(validate_pr.sys,'argv',['validate_pr','--publish-status']):
                self.assertEqual(validate_pr.main(),0)
                self.assertEqual(self.api.statuses[-1][1],'success')
                self.api.change_main(delete=True)
                self.assertEqual(validate_pr.main(),1)
                self.assertEqual(self.api.statuses[-1][1],'failure')

    def test_reopening_a_competing_claim_blocks_both_until_one_is_closed(self):
        self.submit()
        self.api.prs[99]['state'] = 'closed'
        self.api.other_issues[13] = {**claim_issue(self.ctx),'number':13}
        self.assertEqual(issue_intake.process(self.api,self.teams,13,repo_check=lambda u:None),'created')
        self.api.prs[99]['state'] = 'open'
        with self.assertRaisesRegex(Invalid,'already has an open claim'):self.validate()
        second = self.api.prs[100]
        base,head = self.api.tree(BASE_SHA),self.api.tree(second['head']['sha'])
        with self.assertRaisesRegex(Invalid,'already has an open claim'):
            validate_bot_snapshot(base,head,self.api.reader(base),self.api.reader(head),second,
                self.api.other_issues[13],self.ctx,organization_member=True,is_sc=False,api=self.api,teams=self.teams)
        self.api.prs[100]['state'] = 'closed'
        self.assertEqual(self.validate(),self.record())

    def test_reopened_competing_pr_event_closes_old_claim_and_keeps_new_one(self):
        self.submit()
        self.api.prs[99]['state'] = 'closed'
        self.api.other_issues[13] = {**claim_issue(self.ctx),'number':13}
        self.assertEqual(issue_intake.process(self.api,self.teams,13,repo_check=lambda u:None),'created')
        self.api.prs[99]['state'] = 'open'
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / 'event.json'
            event.write_text(json.dumps({'action':'reopened','pull_request':self.api.prs[99]}))
            env = {'GITHUB_EVENT_PATH':str(event),'GITHUB_REPOSITORY':self.api.repo,
                   'GITHUB_TOKEN':'dummy','SC_TEAM_READ_TOKEN':'dummy','SUBMISSION_ISSUE_NUMBER':''}
            with patch.dict(os.environ,env), patch.object(issue_intake,'GitHub',return_value=self.api), \
                 patch.object(issue_intake,'TeamDirectory',return_value=self.teams), \
                 patch.object(issue_intake.subprocess,'check_output',return_value=BASE_SHA):
                self.assertEqual(issue_intake.main(),0)
        self.assertEqual(self.api.prs[99]['state'],'closed')
        self.assertEqual(self.api.issue['state'],'closed')
        self.assertEqual(self.api.prs[100]['state'],'open')
        self.assertNotIn('project-submissions/issue-12',self.api.refs)

    def test_interrupted_summary_with_forged_title_is_not_recovery_history(self):
        self.submit()
        old_body = self.api.issue['body']
        self.send('/edit slack #later')
        self.api.issue['body'] = old_body
        self.api.issue['title'] = 'Claim CPP: Forged title'
        with self.assertRaises(Invalid):
            commands.current_record(self.api,self.api.issue,self.ctx,self.teams,self.record(),allow_previous=True)

    def test_description_after_accepted_claim_and_branch_cleanup_uses_same_project(self):
        self.submit()
        self.api.change_main(self.record())
        self.api.prs[99].update(state='closed',merged_at='2026-10-15T00:00:00Z')
        self.api.issue['state'] = 'closed'
        self.api.refs.pop('project-submissions/issue-12')
        self.assertEqual(self.send('/description')[0],'description')
        self.assertIn('Scientific goals',self.api.comments[-1]['body'])


class ClaimAvailabilityTests(unittest.TestCase):
    def test_regeneration_tracks_add_remove_complete_and_assignment_without_empty_dropdown(self):
        data = source_data()
        ctx = load_context(data.__getitem__,paths=data)
        empty = form(ctx,'CLAIM')
        choice = next(f for f in empty['body'] if f.get('id')=='claim_project')
        self.assertEqual(choice['attributes']['options'],['No CPP projects available'])
        self.assertTrue(relevant(PATH))
        data[PATH] = project_yaml(accepted_cpp())
        ctx = load_context(data.__getitem__,paths=data)
        data.update(render(ctx))
        self.assertEqual(claims.project_options(ctx),[TARGET+' — Project'])
        self.assertNotIn(FORM_PATHS['CLAIM'],updates_for(data.__getitem__,data,ctx))
        for change in ('delete','completed','assigned'):
            updated = deepcopy(data)
            if change=='delete':updated.pop(PATH)
            else:
                record = accepted_cpp(status='completed') if change=='completed' else accepted_cpp(leads=[{'name':'Jane Smith','github_username':'janesmith'}])
                updated[PATH] = project_yaml(record)
            ctx = load_context(updated.__getitem__,paths=updated)
            self.assertEqual(claims.project_options(ctx),[])
            self.assertIn(FORM_PATHS['CLAIM'],updates_for(updated.__getitem__,updated,ctx))

    def test_unaccepted_proposal_branch_does_not_add_a_dropdown_choice(self):
        data = source_data()
        ctx = load_context(data.__getitem__,paths=data)
        self.assertEqual(claims.project_options(ctx),[])
        # Choices come only from the supplied main snapshot, never open proposal records.
        self.assertNotIn(TARGET,render(ctx)[FORM_PATHS['CLAIM']])


class ClaimMaintenanceTests(unittest.TestCase):
    def test_main_registry_changes_regenerate_claim_dropdown(self):
        from test_maintenance import MaintenanceAPI
        from project_workflow.sync_generated import synchronize_main
        api = MaintenanceAPI()
        api.base[PATH] = project_yaml(accepted_cpp())
        self.assertEqual(synchronize_main(api,checkout_sha=BASE_SHA),'updated-main')
        sha = api.refs['main']
        spec = parse_yaml(api.data[sha][FORM_PATHS['CLAIM']])
        menu = next(f for f in spec['body'] if f.get('id')=='claim_project')
        self.assertIn(TARGET+' — Project',menu['attributes']['options'])
        api.data[sha][PATH] = project_yaml(accepted_cpp(status='completed'))
        self.assertEqual(synchronize_main(api,checkout_sha=sha),'updated-main')
        spec = parse_yaml(api.data[api.refs['main']][FORM_PATHS['CLAIM']])
        menu = next(f for f in spec['body'] if f.get('id')=='claim_project')
        self.assertEqual(menu['attributes']['options'],['No CPP projects available'])
