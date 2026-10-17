"""Offline command transactions, replay and authorization; no live GitHub writes."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from project_workflow import commands,issue_intake
from project_workflow.project_registry import Invalid,load_context,parse_yaml
from project_workflow.submission import project_path,validate_bot_snapshot
from test_issue_submission import FakeAPI,Teams,BASE_SHA
from test_registry import source_data


class CommandTeams(Teams):
    def __init__(self,blocked=(),sc=()):self.blocked=set(blocked);self.sc_users=set(sc)
    def is_org_member(self,org,actor):return actor not in self.blocked
    def is_active_member(self,org,slug,actor):return actor in self.sc_users


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeAPI();self.teams=CommandTeams()
        issue_intake.process(self.api,self.teams,12)
        data=source_data();self.ctx=load_context(data.__getitem__,paths=data)
        self.next_id=1000

    def record(self):return parse_yaml(self.api.data[self.api.pr['head']['sha']][project_path(12)])

    def comment(self,text,login='alice',user_id=123):
        self.next_id+=1
        c={'id':self.next_id,'body':text,'user':{'login':login,'id':user_id,'type':'User'},
            'issue_url':'https://api.github.com/repos/test-org/registry/issues/12'}
        self.api.comments.append(c)
        return deepcopy(c)

    def send(self,text,login='alice',user_id=123):
        c=self.comment(text,login,user_id)
        return commands.process(self.api,self.teams,12,c),c

    def validate(self):
        base=self.api.tree(BASE_SHA);head=self.api.tree(self.api.pr['head']['sha'])
        return validate_bot_snapshot(base,head,self.api.reader(base),self.api.reader(head),self.api.pr,self.api.issue,self.ctx,
            organization_member=True,is_sc=False,api=self.api,teams=self.teams)

    def test_title_updates_issue_summary_pr_and_yaml(self):
        title=self.api.issue['title']
        result,c=self.send('/edit title New project title')
        self.assertEqual(result,'edited')
        self.assertEqual(self.record()['name'],'New project title')
        self.assertEqual(self.api.issue['title'],'New project title')
        self.assertEqual(self.record()['submission']['original_title'],title)
        self.assertIn('New project title',self.api.issue['body'])
        self.assertEqual(self.api.pr['title'],'New project: New project title')
        self.assertEqual(self.validate(),self.record())
        self.assertEqual(self.record()['submission']['submitted_at'],'2026-10-11T06:00:00Z')

    def test_repeated_title_changes_and_later_commands_preserve_submission(self):
        original=deepcopy(self.record()['submission'])
        self.assertEqual(self.send('/edit title First title')[0],'edited')
        self.assertEqual(self.send('/edit title Second title')[0],'edited')
        self.assertEqual(self.send('/edit slack #project-channel')[0],'edited')
        self.assertEqual(self.send('/description')[0],'description')
        self.assertEqual(self.api.issue['title'],'Second title')
        self.assertEqual(self.api.pr['title'],'New project: Second title')
        for key,value in original.items():self.assertEqual(self.record()['submission'][key],value)
        self.assertEqual(self.validate(),self.record())

    def test_title_sync_interruption_after_issue_rename_recovers(self):
        request=self.api.request
        def interrupted(method,endpoint,payload=None):
            if method=='PATCH' and endpoint==f"pulls/{self.api.pr['number']}":raise OSError('PR update interrupted')
            return request(method,endpoint,payload)
        self.api.request=interrupted
        with patch.object(commands.time,'sleep'):
            result,c=self.send('/edit title Renamed before interruption')
        self.assertEqual(result,'incomplete')
        self.assertEqual(self.api.issue['title'],'Renamed before interruption')
        self.api.request=request
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        self.assertEqual(len(self.record()['submission']['edits']),1)
        self.assertEqual(self.validate(),self.record())

    def test_external_issue_title_change_after_command_is_rejected(self):
        self.send('/edit title Authorized title')
        self.api.issue['title']='Unrelated manual title'
        self.assertEqual(self.send('/edit slack #channel')[0],'rejected')
        with self.assertRaises(Invalid):self.validate()

    def test_remove_people_requires_username_and_preserves_names_with_spaces(self):
        self.send('/edit add lead name=Jane Smith username=janesmith')
        self.assertEqual(self.send('/edit remove lead username=Jane Smith')[0],'rejected')
        self.assertIn('GitHub username, not the person’s name',self.api.comments[-1]['body'])
        self.assertEqual(self.send('/edit remove lead username=JANESMITH')[0],'edited')
        self.send('/edit add contributor name=Jane Smith username=janesmith')
        self.assertEqual(self.send('/edit remove contributor username=Jane Smith')[0],'rejected')
        self.assertEqual(self.send('/edit remove contributor username=janesmith')[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_remove_by_name_handles_spaces_quotes_case_and_role_scope(self):
        self.send('/edit add lead name=Jane Smith username=janesmith')
        self.send('/edit add contributor name=Jane Smith username=janesmith2')
        self.assertEqual(self.send('/edit remove lead name=jAnE sMiTh')[0],'edited')
        self.assertEqual(len(self.record()['contributors']),2)
        self.assertEqual(self.send('/edit remove contributor name="Jane Smith"')[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_ambiguous_name_is_atomic_and_username_disambiguates(self):
        for role in ('lead','contributor'):
            with self.subTest(role=role):
                self.send(f'/edit add {role} name=Jane Smith username=janesmith')
                self.send(f'/edit add {role} name=Jane Smith username=janesmith2')
                before=deepcopy(self.record());head=self.api.pr['head']['sha'];body=self.api.issue['body']
                self.assertEqual(self.send(f'/edit remove {role} name=Jane Smith')[0],'rejected')
                self.assertIn('More than one person',self.api.comments[-1]['body'])
                self.assertEqual(self.record(),before)
                self.assertEqual(self.api.pr['head']['sha'],head)
                self.assertEqual(self.api.issue['body'],body)
                self.assertEqual(self.send(f'/edit remove {role} username=janesmith')[0],'edited')
                self.assertEqual(self.send(f'/edit remove {role} name=Jane Smith')[0],'edited')
                self.assertEqual(self.validate(),self.record())

    def test_name_selector_rejects_missing_both_unknown_and_last_lead(self):
        record=deepcopy(self.record())
        for value in ('name=', 'name=""', 'name="Jane Smith', 'name=Jane Smith username=janesmith',
                      'username=janesmith name=Jane Smith', 'name=Missing Person', 'username=janesmith'):
            with self.subTest(value=value):
                self.assertEqual(self.send('/edit remove lead '+value)[0],'rejected')
                self.assertEqual(self.record(),record)
        last=record['leads'][0]['name']
        self.assertEqual(self.send('/edit remove lead name='+last)[0],'rejected')
        self.assertIn('At least one project lead must remain',self.api.comments[-1]['body'])
        self.assertEqual(self.record(),record)

    def test_command_confirmations_omit_help_effect_even_on_retry(self):
        result,c=self.send('/edit slack #project-channel')
        self.assertEqual(result,'edited')
        reply=next(row for row in self.api.comments if row['body'].startswith(f"<!-- pit-project-command:{c['id']} -->"))
        self.assertIn('**Edit slack confirmed.**',reply['body'])
        self.assertNotIn(commands.SPECS['edit slack'][1],reply['body'])
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        reply=next(row for row in self.api.comments if row['body'].startswith(f"<!-- pit-project-command:{c['id']} -->"))
        self.assertIn('**Edit slack confirmed.**',reply['body'])
        self.assertNotIn(commands.SPECS['edit slack'][1],reply['body'])

    def test_old_recorded_deliverable_spelling_still_replays_without_appearing_in_help(self):
        self.assertEqual(self.send('/edit deliverable SCM-1 SCM-2')[0],'edited')
        self.assertEqual(self.send('/edit deliverables SCM-2')[0],'edited')
        help_body=commands.help_text()
        self.assertIn('/edit deliverables SCM-1 SCM-2 ...',help_body)
        self.assertNotIn('/edit deliverable ',help_body)
        self.assertNotIn('[SCM CPIP]',help_body)
        self.assertNotIn('Use catalogue IDs; their groups must already be selected.',help_body)
        self.assertIn('name=Jane Smith username=janesmith',help_body)
        self.assertEqual(self.validate(),self.record())

    def test_description_keeps_markdown_slashes_and_command_like_lines(self):
        text='  ## Approach\nhttps://example.org/a/b?x=1\n```python\n/edit title literal content\n```\n/help\n'
        result,_=self.send('/edit description\n'+text)
        self.assertEqual(result,'edited');self.assertEqual(self.record()['summary'],text)
        self.assertEqual(self.validate(),self.record())

    def test_deliverables_replace_all_and_preserve_empty_groups(self):
        self.send('/edit working-group SCM CPIP')
        result,_=self.send('/edit deliverables SCM-2 SCM-2 CPI-1')
        self.assertEqual(result,'edited')
        self.assertEqual(self.record()['working_groups'],[{'code':'SCM','deliverable_ids':['SCM-2']},{'code':'CPIP','deliverable_ids':['CPI-1']}])
        self.assertEqual(self.validate(),self.record())

    def test_invalid_edit_is_atomic_and_does_not_change_status(self):
        before=deepcopy(self.record());head=self.api.pr['head']['sha'];body=self.api.issue['body'];statuses=len(self.api.statuses)
        result,_=self.send('/edit deliverables SCM-2 CPI-1')
        self.assertEqual(result,'rejected');self.assertEqual(self.record(),before)
        self.assertEqual(self.api.pr['head']['sha'],head);self.assertEqual(self.api.issue['body'],body)
        self.assertEqual(len(self.api.statuses),statuses);self.assertEqual(self.api.issue['state'],'open')
        self.assertEqual(self.api.pr['state'],'open')

    def test_multiple_leads_and_promotion_from_contributors(self):
        result,_=self.send('/edit add lead name=Bob Example username=bob')
        self.assertEqual(result,'edited')
        self.assertEqual(self.record()['leads'][1],{'name':'Bob Example','github_username':'bob'})
        self.assertEqual(self.record()['contributors'],[])
        result,_=self.send('/edit slack #new-channel','bob',456)
        self.assertEqual(result,'edited');self.assertEqual(self.record()['slack_channel'],'#new-channel')
        self.assertEqual(self.validate(),self.record())

    def test_last_lead_cannot_be_removed(self):
        self.assertEqual(self.send('/edit remove lead username=alice')[0],'rejected')
        self.assertEqual(len(self.record()['leads']),1)

    def test_removed_lead_loses_edit_permission_but_original_submitter_keeps_it(self):
        self.send('/edit add lead name=Carol username=carol')
        self.send('/edit remove lead username=alice','carol',789)
        self.assertEqual(self.send('/edit title Original submitter may still edit')[0],'edited')
        self.send('/edit add lead name=Alice username=alice')
        self.send('/edit remove lead username=carol')
        self.assertEqual(self.send('/edit title Not allowed','carol',789)[0],'rejected')

    def test_contributor_add_unquoted_and_quoted_names_and_remove(self):
        self.assertEqual(self.send('/edit add contributor name=Jane Smith username=janesmith')[0],'edited')
        self.assertEqual(self.record()['contributors'][-1]['name'],'Jane Smith')
        self.assertEqual(self.send('/edit add contributor name="Jane Smith" username=jane')[0],'edited')
        self.assertEqual(self.send('/edit remove contributor username=janesmith')[0],'edited')
        self.assertEqual([p['github_username'] for p in self.record()['contributors']],['bob','jane'])
        self.assertEqual(self.validate(),self.record())

    def test_invalid_optional_edits_are_rejected_without_submission_cleanup(self):
        for text in ('/edit add contributor name=Invalid username=@bad','/edit github http://example.org',
                     '/edit add links https://example.org/good javascript:bad','/edit slack '+'x'*301):
            before=self.record()
            with self.subTest(text=text):
                self.assertEqual(self.send(text)[0],'rejected');self.assertEqual(self.record(),before)

    def test_links_plain_markdown_balanced_parentheses_duplicates_and_remove(self):
        result,_=self.send('/edit add links [A paper](https://example.org/a/b) https://example.org/a/b [Other](https://example.org/path/(section))')
        self.assertEqual(result,'edited')
        self.assertEqual(self.record()['related_links'],['https://example.org/paper','https://example.org/a/b','https://example.org/path/(section)'])
        self.assertEqual(self.send('/edit remove links https://example.org/a/b')[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_info_and_help_at_all_levels_are_read_only(self):
        head=self.api.pr['head']['sha'];statuses=len(self.api.statuses)
        for text in ('/help','/edit help','/edit add help','/edit add contributor help','/edit remove lead help',
                     '/edit description help','/deliverables','/deliverables SCM CPIP','/description'):
            with self.subTest(text=text):self.assertIn(self.send(text,'bob',456)[0],{'help','deliverables','description'})
        self.assertEqual(self.api.pr['head']['sha'],head);self.assertEqual(len(self.api.statuses),statuses)
        self.assertIn('Measure shear calibration',self.api.comments[-1]['body'])

    def test_contributor_and_nonmember_cannot_edit_sc_can(self):
        self.assertEqual(self.send('/edit title Not allowed','bob',456)[0],'rejected')
        self.teams.blocked.add('alice')
        self.assertEqual(self.send('/edit title Not allowed')[0],'rejected')
        self.teams.blocked.clear();self.teams.sc_users.add('sc-user')
        self.assertEqual(self.send('/edit title SC change','sc-user',999)[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_two_commands_are_rejected_except_description_content(self):
        self.assertEqual(self.send('/edit slack #x\n/edit title second')[0],'rejected')
        self.assertEqual(self.send('/edit title contains /slashes')[0],'edited')

    def test_retries_reuse_command_receipt_pr_branch_and_reply(self):
        result,c=self.send('/edit title Retried command')
        self.assertEqual(result,'edited');head=self.api.pr['head']['sha']
        count=len([x for x in self.api.writes if x[1]=='git/commits'])
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        self.assertEqual(self.api.pr['head']['sha'],head)
        self.assertEqual(len(self.record()['submission']['edits']),1)
        self.assertEqual(len([x for x in self.api.writes if x[1]=='git/commits']),count)
        self.assertEqual(len(self.api.prs),1)
        replies=[x for x in self.api.comments if x['body'].startswith(f'<!-- pit-project-command:{c["id"]} -->')]
        self.assertEqual(len(replies),1)

    def test_deleted_or_edited_applied_comments_block_policy(self):
        _,c=self.send('/edit title Applied')
        row=next(x for x in self.api.comments if x['id']==c['id']);row['body']='/edit title Tampered'
        self.assertEqual(commands.process(self.api,self.teams,12,row,action='edited'),'changed-command')
        self.assertEqual(self.api.statuses[-1][1],'failure')
        with self.assertRaises(Invalid):self.validate()
        self.api.comments.remove(row)
        with self.assertRaises(Invalid):self.validate()

    def test_receipt_or_data_forgery_is_rejected_by_replay(self):
        self.send('/edit title Applied')
        head=self.api.pr['head']['sha'];record=self.record();record['name']='Forged'
        import yaml
        self.api.data[head][project_path(12)]=yaml.safe_dump(record)
        with self.assertRaises(Invalid):self.validate()

    def test_working_group_removal_and_clear_commands(self):
        self.send('/edit working-group SCM CPIP');self.send('/edit deliverables SCM-1 CPI-1')
        self.assertEqual(self.send('/edit working-group SCM')[0],'edited')
        self.assertEqual(self.record()['working_groups'],[{'code':'SCM','deliverable_ids':['SCM-1']}])
        self.assertEqual(self.send('/edit remove deliverables')[0],'edited')
        self.send('/edit github https://github.com/example/repository')
        self.assertEqual(self.send('/edit remove github')[0],'edited')
        self.send('/edit slack #x');self.assertEqual(self.send('/edit remove slack')[0],'edited')
        self.assertEqual(self.send('/edit phd false')[0],'edited')
        self.assertEqual(self.validate(),self.record())

    def test_closed_or_merged_proposal_rejects_edits(self):
        self.api.pr['state']='closed'
        self.assertEqual(self.send('/edit title Not allowed')[0],'rejected')
        self.api.pr.update(state='closed',merged_at='2026-10-13T00:00:00Z')
        self.assertEqual(self.send('/edit title Not allowed')[0],'rejected')

    def test_interrupt_after_branch_publish_recovers_on_retry(self):
        c=self.comment('/edit title Recoverable edit');request=self.api.request
        def interrupt(method,endpoint,payload=None):
            if endpoint=='issues/12' and method=='PATCH' and payload.get('body'):raise OSError('Interrupted formatting')
            return request(method,endpoint,payload)
        self.api.request=interrupt
        self.assertEqual(commands.process(self.api,self.teams,12,c),'incomplete')
        self.assertEqual(self.api.issue['state'],'open')
        self.assertEqual(self.record()['name'],'Recoverable edit')
        self.api.request=request
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        self.assertIn('Recoverable edit',self.api.issue['body'])
        self.assertEqual(self.validate(),self.record())

    def test_two_staged_edits_recover_original_summary_on_manual_submit(self):
        original=self.api.issue['body']
        with patch.object(issue_intake,'process',side_effect=OSError('Interrupted')), patch.object(commands.time,'sleep'):
            self.assertEqual(self.send('/edit add contributor name=Axel P username=aguinottest')[0],'incomplete')
            self.assertEqual(self.send('/edit add links https://example.org/a https://example.org/b')[0],'incomplete')
        self.assertEqual(self.api.issue['body'],original)
        self.assertEqual(len(self.record()['submission']['edits']),2)
        self.assertEqual(self.send('/description')[0],'description')
        self.assertEqual(issue_intake.process(self.api,self.teams,12),'updated')
        self.assertIn('aguinottest',self.api.issue['body'])
        self.assertIn('https://example.org/b',self.api.pr['body'])
        self.assertEqual(len(self.api.prs),1)
        self.assertEqual(self.validate(),self.record())

    def test_stale_summary_recovery_still_rejects_arbitrary_body_changes(self):
        with patch.object(issue_intake,'process',side_effect=OSError('Interrupted')), patch.object(commands.time,'sleep'):
            self.send('/edit title First')
            self.send('/edit title Second')
        self.api.issue['body']=self.api.issue['body'].replace('**Project title**:', '**Changed title**:')
        with self.assertRaisesRegex(Invalid,'rendered submission'):
            commands.current_record(self.api,self.api.issue,self.ctx,self.teams,self.record(),allow_previous=True)

    def test_transient_pr_head_lag_retries_sync_without_duplicate_edit(self):
        submit=issue_intake.process
        calls=[]
        def delayed(*args,**kwargs):
            calls.append(None)
            if len(calls)<3:raise Invalid('Proposal branch changed; retry')
            return submit(*args,**kwargs)
        with patch.object(issue_intake,'process',side_effect=delayed) as mocked, patch.object(commands.time,'sleep'):
            result,_=self.send('/edit title Retry safely')
        self.assertEqual(result,'edited')
        self.assertEqual(mocked.call_count,3)
        self.assertEqual(len(self.record()['submission']['edits']),1)
        self.assertIn('Retry safely',self.api.issue['body'])
        self.assertIn('Retry safely',self.api.pr['body'])
        self.assertEqual(self.validate(),self.record())

    def test_user_comment_changed_before_processing_is_not_applied(self):
        c=self.comment('/edit title Original request')
        next(x for x in self.api.comments if x['id']==c['id'])['body']='/edit title Altered request'
        self.assertEqual(commands.process(self.api,self.teams,12,c),'rejected')
        self.assertNotEqual(self.record()['name'],'Altered request')

    def test_description_source_fences_preserve_mentions_and_nested_code(self):
        text='```python\nprint("@someone")\n```\n</details>\n'
        self.send('/edit description\n'+text)
        result,c=self.send('/description')
        self.assertEqual(result,'description')
        reply=next(x['body'] for x in self.api.comments if x['body'].startswith(f'<!-- pit-project-command:{c["id"]} -->'))
        self.assertIn('````text\n'+text,reply)

    def test_command_does_not_evaluate_shell_or_python_text(self):
        self.assertEqual(self.send('/edit description\n$(touch /tmp/should-not-exist) /path; import os')[0],'edited')
        self.assertIn('$(touch',self.record()['summary'])
        self.assertEqual(self.validate(),self.record())

    def test_self_removed_lead_can_retry_same_comment_but_cannot_post_new_edit(self):
        self.send('/edit add lead name=Bob username=bob')
        result,c=self.send('/edit remove lead username=bob','bob',456)
        self.assertEqual(result,'edited')
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        self.assertEqual(self.send('/edit title Not allowed','bob',456)[0],'rejected')

    def test_membership_revocation_blocks_history_validation(self):
        self.send('/edit add lead name=Bob username=bob')
        self.send('/edit slack #bob-change','bob',456)
        self.teams.blocked.add('bob')
        with self.assertRaises(Invalid):self.validate()

    def test_comment_from_another_issue_cannot_authorize_an_edit(self):
        c=self.comment('/edit title Forged route')
        next(x for x in self.api.comments if x['id']==c['id'])['issue_url']='https://api.github.com/repos/test-org/registry/issues/13'
        self.assertEqual(commands.process(self.api,self.teams,12,c),'rejected')

    def test_receipt_author_forgery_is_rejected(self):
        self.send('/edit title Applied')
        r=self.record();r['submission']['edits'][0]['author_id']=999
        import yaml
        self.api.data[self.api.pr['head']['sha']][project_path(12)]=yaml.safe_dump(r)
        with self.assertRaises(Invalid):self.validate()

    def test_native_issue_retry_after_command_keeps_all_edits(self):
        self.send('/edit title Kept title');self.send('/edit description\nKept /description')
        head=self.api.pr['head']['sha']
        self.assertEqual(issue_intake.process(self.api,self.teams,12),'updated')
        self.assertEqual(self.api.pr['head']['sha'],head)
        self.assertEqual(self.record()['name'],'Kept title')
        self.assertEqual(self.validate(),self.record())

    def test_valid_command_sync_failure_does_not_close_issue(self):
        c=self.comment('/edit title Staged')
        with patch.object(issue_intake,'process',side_effect=Invalid('Simulated synchronization failure')):
            self.assertEqual(commands.process(self.api,self.teams,12,c),'incomplete')
        self.assertEqual(self.api.issue['state'],'open');self.assertEqual(self.api.pr['state'],'open')
        self.assertEqual(self.api.statuses[-1][1],'pending')
        self.assertEqual(commands.process(self.api,self.teams,12,c),'replayed')
        self.assertEqual(self.validate(),self.record())

    def test_comment_webhook_runs_command_handler_using_api_author(self):
        from contextlib import redirect_stdout,redirect_stderr
        import io,json,os,tempfile
        from pathlib import Path
        c=self.comment('/edit title Webhook title')
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'event.json';p.write_text(json.dumps({'action':'created','issue':self.api.issue,'comment':c,'sender':{'login':'sc-user'}}))
            env={'GITHUB_EVENT_PATH':str(p),'GITHUB_REPOSITORY':self.api.repo,'GITHUB_TOKEN':'dummy',
                 'SC_TEAM_READ_TOKEN':'dummy','SUBMISSION_ISSUE_NUMBER':''}
            with patch.dict(os.environ,env),patch.object(issue_intake,'GitHub',return_value=self.api),\
                 patch.object(issue_intake,'TeamDirectory',return_value=self.teams),\
                 patch.object(issue_intake.subprocess,'check_output',return_value=BASE_SHA+'\n'),\
                 redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(issue_intake.main(),0)
                with patch.object(commands,'process',return_value='incomplete'):
                    self.assertEqual(issue_intake.main(),1)
        self.assertEqual(self.record()['name'],'Webhook title')
        self.assertEqual(self.validate(),self.record())

    def test_pr_policy_adapter_replays_command_comments_and_publishes_success(self):
        from project_workflow import validate_pr
        from contextlib import redirect_stdout,redirect_stderr
        import io,json,os,tempfile,sys
        from pathlib import Path
        self.send('/edit title Validated through adapter')
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'event.json';p.write_text(json.dumps({'pull_request':deepcopy(self.api.pr)}))
            env={'GITHUB_EVENT_PATH':str(p),'GITHUB_REPOSITORY':self.api.repo,'GITHUB_TOKEN':'dummy',
                 'SC_TEAM_READ_TOKEN':'dummy','POLICY_PR_NUMBER':''}
            output=io.StringIO()
            with patch.dict(os.environ,env),patch.object(sys,'argv',['validate_pr','--publish-status']),\
                 patch.object(validate_pr,'GitHub',return_value=self.api),\
                 patch.object(validate_pr,'TeamDirectory',return_value=self.teams),\
                 patch.object(validate_pr.subprocess,'check_output',return_value=BASE_SHA+'\n'),\
                 redirect_stdout(output),redirect_stderr(output):
                self.assertEqual(validate_pr.main(),0,output.getvalue())
        self.assertEqual(self.api.statuses[-1][1],'success')

    def test_pr_base_change_is_rejected_before_edit(self):
        self.api.pr['base']['ref']='another-branch'
        head=self.api.pr['head']['sha']
        self.assertEqual(self.send('/edit title Not allowed')[0],'rejected')
        self.assertEqual(self.api.pr['head']['sha'],head)
