"""Parse, authorize and replay comment commands as data; never execute them."""
from copy import deepcopy
import hashlib
import re
import sys
import time
import urllib.error

from project_workflow.project_registry import Invalid, USERNAME, validate_record
from project_workflow.submission import (number, proposal, submission_body, project_path,
    issue_version, is_submission_bot, FORMATTED_MARKER, labelled)
from project_workflow.review import formatted_issue_body, safe_text

# Syntax and help live together; adding a command changes this module only.
SPECS = {
    'edit title': ('/edit title New project title', 'Replace the project, Issue and PR titles.'),
    'edit description': ('/edit description\nYour complete Markdown description', 'Replace the description with all lines after the first newline.'),
    'edit deliverables': ('/edit deliverables SCM-1 SCM-2 ...', 'Replace all deliverables.'),
    'edit working-group': ('/edit working-group SCM CPIP', 'Replace working groups and remove deliverables for groups no longer selected.'),
    'edit slack': ('/edit slack #project-channel', 'Set or replace the Slack channel.'),
    'edit github': ('/edit github https://github.com/OWNER/REPOSITORY', 'Set or replace the main repository link.'),
    'edit phd': ('/edit phd true', 'Set the PhD answer: true or false.'),
    'edit add contributor': ('/edit add contributor name=Jane Smith username=janesmith', 'Add one contributor. Names may contain spaces or be quoted.'),
    'edit add lead': ('/edit add lead name=Jane Smith username=janesmith', 'Add a lead; remove that account from contributors if present.'),
    'edit add links': ('/edit add links https://example.org/a https://example.org/b', 'Append URLs or Markdown links; skip duplicates.'),
    'edit remove contributor': ('/edit remove contributor username=janesmith', 'Remove one contributor by GitHub username, or use `/edit remove contributor name=Jane Smith`. Name matching ignores capitalization; ambiguous names require a username.'),
    'edit remove lead': ('/edit remove lead username=janesmith', 'Remove one lead by GitHub username, or use `/edit remove lead name=Jane Smith`. Name matching ignores capitalization; ambiguous names require a username. At least one lead must remain.'),
    'edit remove links': ('/edit remove links https://example.org/a', 'Remove the specified related links.'),
    'edit remove slack': ('/edit remove slack', 'Clear the Slack channel.'),
    'edit remove github': ('/edit remove github', 'Clear the main repository link.'),
    'edit remove deliverables': ('/edit remove deliverables', 'Clear all deliverables; retain working groups.'),
    'deliverables': ('/deliverables SCM CPIP', 'List catalogue deliverables for the specified groups. Use /deliverables alone for all groups.'),
    'description': ('/description', 'Print the current description as copyable Markdown source.'),
}


def help_text(path=''):
    prefix = path + ' ' if path else ''
    options = [(key, syntax, effect) for key, (syntax, effect) in SPECS.items()
               if key == path or key.startswith(prefix)]
    if not options:
        raise Invalid('Unknown help topic. Use /help.')
    rows = ['**Project commands**' + (' — `' + path + '`' if path else ''),
            'One command per comment. Edits are available while the submission Issue and its PR are open. '
            'The original submitter, current leads and SC may edit; active organization membership is required.']
    for _, syntax, effect in options:
        rows.append('```text\n' + syntax + '\n```\n' + effect)
    rows.append('Help works at each level: `/help`, `/edit help`, `/edit add help`, '
                '`/edit add contributor help`. Deliverable IDs must match the catalogue exactly; use `/deliverables`. '
                'Post a new comment for every command; do not edit or delete an applied command comment.')
    return '\n\n'.join(rows)


def parse(body):
    if not isinstance(body,str) or len(body.encode('utf-8')) > 65536:
        raise Invalid('Command is missing or exceeds 64 KiB.')
    lines = body.split('\n',1)
    first = lines[0].strip()
    # Existing authenticated receipts contain the old singular spelling.
    # Normalize only the header; never alter the original comment or its hash.
    first = re.sub(r'^/edit (remove )?deliverable(?=\s|$)', r'/edit \1deliverables', first)
    if not first.startswith('/'):
        return None
    words = first[1:].split()
    if not words:
        raise Invalid('Use /help to see the available commands.')
    if len(lines)>1 and words != ['edit','description'] and lines[1].strip():
        raise Invalid('Use one command per comment. Only /edit description accepts multiple lines.')
    if words == ['help']:
        return ('help','')
    if words[-1] == 'help':
        path = ' '.join(words[:-1])
        if path in SPECS or any(key.startswith(path+' ') for key in SPECS):
            return ('help',path)
    if ' '.join(words) in {'edit','edit add','edit remove'}:
        return ('help',' '.join(words))
    for key in sorted(SPECS,key=len,reverse=True):
        prefix = '/' + key
        if first == prefix or first.startswith(prefix+' '):
            value = first[len(prefix):].strip()
            if key == 'edit description':
                if value or len(lines)!=2:
                    raise Invalid('Use /edit description on the first line, followed by the description on subsequent lines.')
                value = lines[1]  # Preserve Markdown whitespace, slashes and code fences.
            return (key,value)
    raise Invalid('Unknown command. Use /help or /edit help.')


def parse_links(text):
    """Accept plain URLs and Markdown links, including balanced URL parentheses."""
    from project_workflow.submission import https_url
    result = []
    while text.strip():
        text = text.lstrip()
        if text.startswith('['):
            label_end = text.find('](')
            if label_end < 0:
                raise Invalid('Links must be HTTPS URLs or [label](https://example.org) links.')
            start, depth = label_end+2, 1
            end = start
            while end < len(text) and depth:
                if text[end]=='(': depth += 1
                elif text[end]==')': depth -= 1
                end += 1
            if depth:
                raise Invalid('Unclosed Markdown link.')
            url = text[start:end-1]
            if url.startswith('<') and url.endswith('>'): url=url[1:-1]
            text = text[end:]
            if text and not text[0].isspace():
                raise Invalid('Separate links with spaces.')
        else:
            match = re.match(r'\S+',text)
            url=match[0];text=text[match.end():]
        https_url(url)
        result.append(url)
    if not result:
        raise Invalid('Provide at least one link.')
    return list(dict.fromkeys(result))


def unquote_name(name):
    name=name.strip()
    if name.startswith(('"',"'")):
        if len(name)<2 or name[-1]!=name[0]: raise Invalid('Close the quotes around the name.')
        name=name[1:-1]
    return name


def person(text):
    match = re.fullmatch(r'name=(.+)\s+username=(\S+)',text)
    if not match:
        raise Invalid('Use name=Jane Smith username=janesmith, in that order.')
    name, username = unquote_name(match[1]),match[2]
    return {'name':name,'github_username':username}


def apply(record, command, ctx):
    """Atomic pure transformation; any error leaves the supplied record untouched."""
    key, value = command
    if not key.startswith('edit '): raise Invalid('This is not an edit command.')
    candidate = deepcopy(record)
    note = ''
    if key in {'edit title','edit description','edit slack','edit github'}:
        if not value.strip(): raise Invalid('Provide a nonempty value.')
        field={'edit title':'name','edit description':'summary','edit slack':'slack_channel','edit github':'github_url'}[key]
        if field=='name' and (any(x in value for x in '\r\n') or len(value)>256):
            raise Invalid('The project title must be a single line of at most 256 characters.')
        candidate[field]=value
    elif key=='edit phd':
        if value.lower() not in {'true','false'}:raise Invalid('Use true or false.')
        candidate['phd_thesis']=value.lower()=='true'
    elif key=='edit working-group':
        codes=list(dict.fromkeys(value.split()))
        if not codes or any(c not in ctx.working_groups for c in codes):raise Invalid('Select at least one valid working-group code. Use /deliverables to see the groups.')
        old={g['code']:g['deliverable_ids'] for g in candidate['working_groups']}
        omitted=[i for code,ids in old.items() if code not in codes for i in ids]
        candidate['working_groups']=[{'code':c,'deliverable_ids':old.get(c,[])} for c in codes]
        if omitted:note = 'Deliverables for removed groups were removed: '+', '.join(omitted)+'.'
    elif key=='edit deliverables':
        ids=list(dict.fromkeys(value.split()))
        if not ids:raise Invalid('Provide at least one deliverable, or use /edit remove deliverables to clear the list.')
        selected={g['code'] for g in candidate['working_groups']}
        groups={c:[] for c in selected}
        for identifier in ids:
            matches=[c for c in selected if identifier in ctx.deliverables[c]]
            if len(matches)!=1:raise Invalid(f'Deliverable {identifier} is unknown, ambiguous, or belongs to an unselected group. Use /deliverables and /edit working-group.')
            groups[matches[0]].append(identifier)
        for g in candidate['working_groups']:g['deliverable_ids']=groups[g['code']]
    elif key in {'edit add lead','edit add contributor'}:
        p=person(value);username=p['github_username'].lower()
        leads={p['github_username'].lower() for p in candidate['leads']}
        contributors={p['github_username'].lower() for p in candidate['contributors']}
        if username in leads or (key=='edit add contributor' and username in contributors):raise Invalid('That account is already listed as a lead or contributor.')
        if key=='edit add lead':
            candidate['leads'].append(p)
            if username in contributors:
                candidate['contributors']=[p for p in candidate['contributors'] if p['github_username'].lower()!=username]
                note = 'That account was removed from contributors.'
        else:candidate['contributors'].append(p)
    elif key in {'edit remove lead','edit remove contributor'}:
        match=re.fullmatch(r'(name|username)=(.+)',value)
        if not match or re.search(r'\s(?:name|username)=',match[2]):
            raise Invalid('Provide exactly one selector: name=Jane Smith or username=janesmith.')
        selector, selected=match[1],match[2]
        field='leads' if key=='edit remove lead' else 'contributors'
        if selector=='username':
            if len(selected)>39 or not USERNAME.fullmatch(selected):
                raise Invalid('Use the GitHub username, not the person’s name: username=janesmith. To select by name, use name=Jane Smith.')
            matches=[p for p in candidate[field] if p['github_username'].lower()==selected.lower()]
        else:
            selected=unquote_name(selected)
            if not selected.strip():raise Invalid('Provide a nonempty name: name=Jane Smith.')
            matches=[p for p in candidate[field] if p['name'].casefold()==selected.casefold()]
        if not matches:raise Invalid('No person with that '+selector+' is listed in this role. Check the project summary.')
        if len(matches)>1:raise Invalid('More than one person in this role has that name. Select one by GitHub username: username=janesmith.')
        username=matches[0]['github_username'].lower()
        retained=[p for p in candidate[field] if p['github_username'].lower()!=username]
        if field=='leads' and not retained:raise Invalid('At least one project lead must remain.')
        candidate[field]=retained
    elif key in {'edit add links','edit remove links'}:
        links=parse_links(value)
        existing=candidate.get('related_links',[])
        if key=='edit add links':candidate['related_links']=list(dict.fromkeys(existing+links))
        else:
            if any(url not in existing for url in links):raise Invalid('At least one specified link is not in the current related links.')
            candidate['related_links']=[url for url in existing if url not in links]
    elif key in {'edit remove slack','edit remove github','edit remove deliverables'}:
        if value:raise Invalid('This command takes no arguments.')
        if key=='edit remove deliverables':
            for g in candidate['working_groups']:g['deliverable_ids']=[]
        else:candidate['slack_channel' if key=='edit remove slack' else 'github_url']=None
    validate_record(candidate,project_path(candidate['submission']['issue_number']),ctx)
    return candidate,note


def receipt(comment):
    user=comment.get('user',{})
    if user.get('type')!='User' or type(user.get('id')) is not int or user['id']<1:
        raise Invalid('Commands must be posted by a human GitHub account.')
    return {'comment_id':number(comment['id']),'author_id':user['id'],
            'body_sha256':hashlib.sha256(comment.get('body','').encode('utf-8')).hexdigest()}


class Roles:
    """Cache only within one replay; subsequent checks read membership again."""
    def __init__(self,teams,ctx):self.teams,self.ctx,self.cache=teams,ctx,{}
    def get(self,login):
        if login not in self.cache:
            member=self.teams.is_org_member(self.ctx.sc_team[0],login)
            self.cache[login]=(member,self.teams.is_active_member(*self.ctx.sc_team,login) if member else False)
        return self.cache[login]


def authorize(record, issue, comment, roles):
    receipt(comment)
    login=comment['user']['login']
    member,sc=roles.get(login)
    original=comment['user']['id']==issue['user']['id']
    lead=login.lower() in {p['github_username'].lower() for p in record['leads']}
    if not member or not (sc or original or lead):
        raise Invalid('Only the original submitter, current leads, or SC may edit; active organization membership is required.')


def current_record(api, issue, ctx, teams, stored=None, *, allow_previous=False):
    """Reconstruct the YAML from native answers plus authenticated command comments."""
    roles=Roles(teams,ctx)
    member,sc=roles.get(issue['user']['login'])
    original_title=(stored or {}).get('submission',{}).get('original_title')
    source_issue=deepcopy(issue)
    if original_title is not None:
        if hashlib.sha256(original_title.encode('utf-8')).hexdigest()!=stored['submission']['issue_title_sha256']:
            raise Invalid('The original Issue title does not match its submission receipt.')
        source_issue['title']=original_title
    record=proposal(source_issue,ctx,organization_member=member,is_sc=sc,verify_summary=False)
    if original_title is not None:
        record['submission']['original_title']=original_title
    source=submission_body(issue)
    edits=(stored or {}).get('submission',{}).get('edits',[])
    # A failed API update can leave the visible Issue several commands behind.
    # Accept only exact views of authenticated history, never arbitrary edits.
    previous_matches = False
    has_title_command = False
    if edits:
        comments={c['id']:c for c in api.pages(f"issues/{issue['number']}/comments")}
        seen=0
        for item in edits:
            identifier=number(item['comment_id'])
            comment=comments.get(identifier)
            if identifier<=seen or comment is None or receipt(comment)!=item:
                raise Invalid('An applied command was changed, deleted, or reordered. Its proposal cannot pass validation.')
            seen=identifier
            authorize(record,issue,comment,roles)
            if allow_previous:
                body_matches = issue['body']==formatted_issue_body(record,ctx,source) or (
                    not record['submission'].get('edits') and issue['body']==source)
                title_matches = original_title is None or issue['title'] in {record['name'],original_title}
                previous_matches = previous_matches or (body_matches and title_matches)
            command=parse(comment['body'])
            if command[0]=='edit title':has_title_command=True
            record,_=apply(record,command,ctx)
            record['submission'].setdefault('edits',[]).append(item)
        if record!=stored:
            raise Invalid('Proposal data differs from its authenticated command history.')
        if original_title is not None and not has_title_command:
            raise Invalid('Renaming the Issue requires an authenticated title command.')
    elif stored is not None and stored!=record:
        raise Invalid('The submitted proposal was changed outside the command workflow.')
    expected=formatted_issue_body(record,ctx,source)
    matches=(issue['body']==expected or (not edits and not issue['body'].startswith(FORMATTED_MARKER))) and (
        original_title is None or issue['title']==record['name'])
    if not matches and allow_previous:
        matches=previous_matches
    if not matches:raise Invalid('The rendered submission was changed outside the command workflow.')
    return record


def source_block(text):
    fence='`'*max(3,1+max((len(s) for s in re.findall(r'`+',text)),default=0))
    return fence+'text\n'+text+'\n'+fence


def confirmation(key):
    return '**'+key.capitalize()+' confirmed.**'


def process(api, teams, issue_number, event_comment, *, action='created'):
    """Handle one comment. Rejected edits never touch the proposal or its status."""
    from project_workflow.issue_intake import associated_pr, branch_snapshot, feedback, process as submit
    from project_workflow.project_registry import load_context, parse_yaml
    from project_workflow.submission import project_yaml, submission_branch
    from project_workflow.review import proposal_body
    from project_workflow.dispatch import validate_pr as dispatch
    issue=api.get(f'issues/{number(issue_number)}')
    if issue.get('pull_request') or not labelled(issue):return 'ignored'
    identifier=number(event_comment['id'])
    marker=f'<!-- pit-project-command:{identifier} -->'
    staged=False
    pr=None
    try:
        sha,commit,stored=branch_snapshot(api,issue)
        applied=next((r for r in (stored or {}).get('submission',{}).get('edits',[]) if r['comment_id']==identifier),None)
        if action!='created':
            if applied is None:return 'ignored'
            if action=='edited':
                live=api.get(f'issues/comments/{identifier}')
                if receipt(live)==applied:return 'ignored'
            pr=associated_pr(api,issue_number)
            if pr and pr.get('state')=='open':
                api.status(pr['head']['sha'],'failure','An applied command was edited or deleted')
                dispatch(api,pr['number'])
            feedback(api,issue_number,'**An applied command was changed or deleted.** The PR cannot pass validation. '
                     'Restore the original comment if possible, or cancel and resubmit. Post future edits as new comments.',marker=marker)
            return 'changed-command'
        comment=api.get(f'issues/comments/{identifier}')
        if is_submission_bot(comment.get('user',{})):return 'ignored'
        if comment.get('issue_url')!=f'https://api.github.com/repos/{api.repo}/issues/{issue_number}':
            raise Invalid('This comment does not belong to the submission Issue.')
        if comment.get('body')!=event_comment.get('body') or comment['user'].get('id')!=event_comment['user'].get('id'):
            raise Invalid('The command comment changed before processing. Post a new command comment.')
        command=parse(comment['body'])
        if command is None:return 'ignored'
        if teams is None:raise Invalid('Membership access is unavailable; ask SC to fix the automation.')
        base_sha=api.get('git/ref/heads/main')['object']['sha']
        base_tree=api.tree(base_sha)
        read=api.reader(base_tree)
        ctx=load_context(read,paths=base_tree)
        if ctx.sc_team[0].lower()!=api.repo.split('/')[0].lower():raise Invalid('SC team must belong to this repository organization.')
        roles=Roles(teams,ctx)
        member,_=roles.get(comment['user']['login'])
        if not member:raise Invalid('Commands require active organization membership.')
        key,value=command
        if key=='help':
            feedback(api,issue_number,help_text(value),marker=marker)
            return 'help'
        if key=='deliverables':
            codes=list(dict.fromkeys(value.split())) or sorted(ctx.working_groups)
            if any(c not in ctx.working_groups for c in codes):raise Invalid('Unknown working group. Use /deliverables for all groups.')
            rows=[]
            for code in codes:
                g=ctx.working_groups[code]
                rows.append('**'+safe_text(code+' — '+g['description'])+'**\n\n'+
                    ('\n'.join('- `'+safe_text(d['id'])+'` — '+safe_text(d['title']) for d in g['deliverables']) or 'No deliverables defined.'))
            text='\n\n'.join(rows)
            if len(text)>63000:raise Invalid('The catalogue is too large for one reply; request one group at a time.')
            feedback(api,issue_number,text,marker=marker)
            return 'deliverables'
        pr=associated_pr(api,issue_number)
        if key=='description':
            if value:raise Invalid('/description takes no arguments.')
            path=project_path(issue_number)
            if path in base_tree:
                current=parse_yaml(read(path));validate_record(current,path,ctx)
            elif stored:
                current=current_record(api,issue,ctx,teams,stored,allow_previous=True)
            else:
                raise Invalid('There is no active proposal or accepted project to display.')
            text=source_block(current['summary'])
            if len(text)>64000:raise Invalid('The description exceeds the reply limit; view its complete source in the project YAML.')
            feedback(api,issue_number,text,marker=marker)
            return 'description'
        if issue.get('state')!='open' or not pr or pr.get('state')!='open' or pr.get('merged_at'):
            raise Invalid('Edits require an open submission Issue and its open, unmerged review PR.')
        if (pr['base']['ref']!='main' or pr['base']['repo']['full_name']!=api.repo
                or pr['head']['repo']['full_name']!=api.repo):
            raise Invalid('The review PR must target this repository’s main branch.')
        if sha!=pr['head']['sha']:raise Invalid('Proposal branch changed; retry this workflow.')
        current=current_record(api,issue,ctx,teams,stored,allow_previous=True)
        if applied:
            if receipt(comment)!=applied:raise Invalid('An applied command was modified; it cannot be reused.')
            staged=True
            result=submit(api,teams,issue_number,command_sync=True)
            if result not in {'created','updated'}:raise Invalid('The edit could not finish synchronization.')
            feedback(api,issue_number,confirmation(key)+' The Issue summary and the same PR are synchronized.',marker=marker)
            return 'replayed'
        authorize(current,issue,comment,roles)
        history=current['submission'].get('edits',[])
        if history and identifier<=history[-1]['comment_id']:
            raise Invalid('This comment predates the latest applied edit. Post the command in a new comment.')
        candidate,note=apply(current,command,ctx)
        if key=='edit title':
            # Keep the original submission input before changing its Issue title.
            candidate['submission'].setdefault('original_title',issue['title'])
        if candidate==current:
            feedback(api,issue_number,confirmation(key)+' No change was needed; the requested values are already present.',marker=marker)
            return 'unchanged'
        candidate['submission'].setdefault('edits',[]).append(receipt(comment))
        rendered=formatted_issue_body(candidate,ctx,submission_body(issue))
        if len(rendered.encode('utf-8'))>65536 or len(project_yaml(candidate).encode('utf-8'))>1024*1024:
            raise Invalid('The edit exceeds the Issue or project file size limit. Use a shorter value.')
        validate_record(candidate,project_path(issue_number),ctx)
        def still_current():
            live_issue=api.get(f'issues/{issue_number}')
            live_pr=api.get(f"pulls/{pr['number']}")
            live_comment=api.get(f'issues/comments/{identifier}')
            if issue_version(live_issue)!=issue_version(issue) or receipt(live_comment)!=receipt(comment):
                raise Invalid('Issue or command changed during processing; retry with a new comment.')
            if (live_pr.get('state')!='open' or live_pr.get('merged_at') or live_pr['head']['sha']!=sha
                    or live_pr['base']!=pr['base']
                    or api.get('git/ref/heads/'+submission_branch(issue_number))['object']['sha']!=sha
                    or api.get('git/ref/heads/main')['object']['sha']!=base_sha):
                raise Invalid('Proposal or main changed during processing; retry.')
            # Recheck all history and the new requester with fresh live membership.
            fresh_record=current_record(api,live_issue,ctx,teams,stored,allow_previous=True)
            authorize(fresh_record,live_issue,live_comment,Roles(teams,ctx))
            if fresh_record!=current:raise Invalid('Proposal changed during processing; retry.')
        still_current()
        base_commit=api.get('git/commits/'+base_sha)
        tree=api.request('POST','git/trees',{'base_tree':base_commit['tree']['sha'],'tree':[
            {'path':project_path(issue_number),'mode':'100644','type':'blob','content':project_yaml(candidate)}]})
        head=api.request('POST','git/commits',{'message':f'Project submission {issue_number}',
            'tree':tree['sha'],'parents':[base_sha]})['sha']
        still_current()
        api.status(head,'pending','Command edit recorded; synchronizing and validating')
        api.request('PATCH','git/refs/heads/'+submission_branch(issue_number),{'sha':head,'force':True})
        staged=True
        # The normal writer repairs interrupted synchronization from the receipt.
        # It reads GitHub comments again, never trusting the proposed YAML as authority.
        # GitHub's PR head can briefly lag behind a successful ref update.
        # Synchronization is idempotent; retry without applying the edit again.
        for attempt in range(3):
            try:
                result=submit(api,teams,issue_number,command_sync=True)
                break
            except (Invalid,OSError,ValueError,KeyError,TypeError):
                if attempt==2:raise
                time.sleep(1)
        if result not in {'created','updated'}:raise Invalid('The edit could not finish synchronization; ask SC to inspect the workflow.')
        feedback(api,issue_number,confirmation(key)+(' '+safe_text(note) if note else '')+'\n\nThe Issue summary and [review PR](https://github.com/'+api.repo+f"/pull/{pr['number']}) have been updated.",marker=marker)
        return 'edited'
    except (Invalid,OSError,ValueError,KeyError,TypeError) as exc:
        if staged:
            # Never print HTTP response bodies, request objects or credentials.
            reason = ('GitHub API error (HTTP '+str(exc.code)+')' if isinstance(exc,urllib.error.HTTPError)
                      else str(exc) if isinstance(exc,Invalid) else type(exc).__name__)
            print('Command synchronization failed: '+reason.replace('\n',' ')[:500],file=sys.stderr)
            text='**The edit was recorded, but synchronization could not complete.** Ask SC to run Project workflows on main with operation `submit` and this Issue number. The PR remains blocked until validation passes.'
        else:
            text='**Command ignored.** '+safe_text(str(exc))+'\n\nUse `/help` or the relevant help command, such as `/edit add contributor help`.'
        feedback(api,issue_number,text,marker=marker)
        return 'incomplete' if staged else 'rejected'
