"""Build and publish accepted project pages as ordinary GitHub Wiki Markdown."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
import urllib.error

from project_workflow.project_registry import Invalid, load_local_context
from project_workflow.review import link, profile, safe_text

STATUS = {'open': ('Open', 'white'), 'active': ('Active', 'blue'),
          'IR': ('Internal review', 'orange'), 'completed': ('Completed', 'green'),
          'paused': ('Paused', 'grey')}
MANIFEST = '.pit-project-pages.json'


def badge(status):
    label, color = STATUS[status]
    query = urlencode({'label': 'status', 'message': label, 'color': color})
    return f'![{label}](https://img.shields.io/static/v1?{query})'


def navigation(ctx, repo):
    root = f'https://github.com/{repo}/wiki'
    links = [('All projects', 'Home'), ('CPP', 'CPP'), ('EP', 'EP')]
    links += [(code, 'WG-' + code) for code in sorted(ctx.working_groups)]
    return ' · '.join(f'[{safe_text(label)}]({root}/{page})' for label, page in links)


def order(project):
    group = project['working_groups'][0]['code']
    return group, int(project['project_number'][len(group):])


def table(projects, repo):
    rows = ['| Project number | Project status | Working group | Project kind | Project lead | Project title |',
            '| --- | --- | --- | --- | --- | --- |']
    for project in sorted(projects, key=order):
        identifier = project['project_number']
        page = f'https://github.com/{repo}/wiki/{identifier}'
        lead = project['leads'][0] if project['leads'] else None
        rows.append('| ' + ' | '.join([
            f'[{identifier}]({page})', safe_text(STATUS[project['status']][0]),
            safe_text(project['working_groups'][0]['code']), safe_text(project['kind']),
            safe_text(lead['name']) + ' · ' + profile(lead['github_username']) if lead else '—',
            f'[{safe_text(project["name"])}]({page})']) + ' |')
    return '\n'.join(rows) + ('\n' if len(rows) > 2 else '\n\nNo projects listed.\n')


def snapshots(ctx, token, *, api_factory=None):
    """Public issues only. API visibility never implies wiki-reader visibility."""
    from project_workflow.validate_pr import GitHub
    api_factory = api_factory or GitHub
    result = {}
    for url in sorted({p.get('github_url') for p in ctx.projects.values() if p.get('github_url')}):
        target = url.removeprefix('https://github.com/').rstrip('/')
        try:
            api = api_factory(target, token)
            metadata = api.get('')
            if metadata.get('private') is not False or metadata.get('visibility', 'public') != 'public':
                result[url] = {'state': 'restricted'}
                continue
            if metadata.get('has_issues') is False:
                result[url] = {'state': 'disabled'}
                continue
            issues = [i for i in api.pages('issues?state=open&sort=updated&direction=desc') if 'pull_request' not in i]
            result[url] = {'state': 'ok', 'issues': issues}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, Invalid):
            # Do not retain an old mirror after access disappears.
            result[url] = {'state': 'unavailable'}
    return result


def issue_section(url, snapshot, now):
    rows = ['## Open repository issues', '', f'[View issues on GitHub]({url.rstrip("/")}/issues)', '']
    state = (snapshot or {}).get('state')
    if state == 'ok':
        rows += [f'Last refreshed: {now} (UTC).', '', '| Issue | Title |', '| --- | --- |']
        for issue in snapshot['issues']:
            # Construct links from validated repository URL and numeric ID.
            if type(issue.get('number')) is not int or issue['number'] < 1:
                raise Invalid('Unexpected repository issue identifier')
            rows.append(f'| [#{issue["number"]}]({url.rstrip("/")}/issues/{issue["number"]}) | {safe_text(issue["title"])} |')
        if not snapshot['issues']:
            rows += ['', 'No open issues.']
    elif state == 'restricted':
        rows += ['Issue details remain in the private repository. Open the link using your GitHub account.']
    elif state == 'disabled':
        rows += ['Issues are disabled for this repository.']
    else:
        rows += ['Issue preview is unavailable. Open the repository link to check access and current issues.']
    return '\n'.join(rows)


def project_page(project, ctx, repo, nav, issues, now):
    identifier = project['project_number']
    update = f'https://github.com/{repo}/issues/new?' + urlencode({
        'template': 'update-project.yml', 'project_number': identifier, 'title': f'Update {identifier}'})
    rows = [nav, '', f'# {identifier} — {safe_text(project["name"])}', '', badge(project['status']), '',
            f'[Update this project]({update})', '']
    if project['status'] == 'open' and project['kind'] == 'CPP' and not project['leads']:
        rows += [f'[Claim a CPP](https://github.com/{repo}/issues/new?template=claim-cpp.yml)', '']
    rows += [f'**Project kind:** {safe_text(project["kind"])}', '']
    if 'phd_thesis' in project:
        rows += ['**Part of a PhD thesis:** ' + str(project['phd_thesis']), '']
    rows += ['**Project leads:**', '']
    rows += ['- ' + safe_text(p['name']) + ' · ' + profile(p['github_username']) for p in project['leads']] or ['None assigned.']
    group = project['working_groups'][0]
    catalogue = ctx.working_groups[group['code']]
    titles = {d['id']: d['title'] for d in catalogue['deliverables']}
    rows += ['', '**Working group:** ' + safe_text(group['code'] + ' — ' + catalogue['description']), '', '**Deliverables:**', '']
    rows += ['- ' + safe_text(i + ' — ' + titles[i]) for i in group['deliverable_ids']] or ['None selected.']
    rows += ['', '**Main GitHub repository:** ' + (link(project['github_url']) if project.get('github_url') else 'Not provided'),
             '', '**Slack channel:** ' + safe_text(project.get('slack_channel') or 'Not provided'),
             '', '## Project description', '', safe_text(project['summary'], markdown=True),
             '', '## Additional information', '', safe_text(project.get('additional_information') or 'No additional information provided.', markdown=True),
             '', '## Contributors', '']
    rows += ['- ' + safe_text(p['name']) + ' · ' + profile(p['github_username']) for p in project['contributors']] or ['None listed.']
    rows += ['', '## Related links', '']
    rows += ['- ' + link(url) for url in project.get('related_links', [])] or ['None provided.']
    if project.get('github_url'):
        rows += ['', issue_section(project['github_url'], issues.get(project['github_url']), now)]
    return '\n'.join(rows).rstrip() + '\n'


def render(ctx, repo, *, issues=None, now=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Invalid('Invalid wiki repository identifier')
    now = now or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    nav = navigation(ctx, repo)
    projects = [p for p in ctx.projects.values() if p.get('project_number') and p['kind'] in {'CPP', 'EP'}]
    # Only numbered accepted records are published; proposals are never read.
    def index(title, selected, kinds=('CPP', 'EP')):
        parts = [nav, '', '# ' + title]
        for kind in kinds:
            parts += ['', '## ' + kind + ' projects', '', table([p for p in selected if p['kind'] == kind], repo)]
        return '\n'.join(parts).rstrip() + '\n'
    pages = {'Home.md': index('Projects', projects), 'CPP.md': index('Critical Path Projects', projects, ('CPP',)),
             'EP.md': index('Enhancing Projects', projects, ('EP',))}
    for code in sorted(ctx.working_groups):
        pages['WG-' + code + '.md'] = index(code + ' — ' + safe_text(ctx.working_groups[code]['description']),
                                           [p for p in projects if p['working_groups'][0]['code'] == code])
    for project in projects:
        pages[project['project_number'] + '.md'] = project_page(project, ctx, repo, nav, issues or {}, now)
    return pages


def managed_path(path):
    return isinstance(path, str) and bool(re.fullmatch(r'(?:Home|CPP|EP|WG-[A-Z][A-Z0-9_-]*|[A-Z][A-Z0-9_-]*[1-9][0-9]*)\.md', path))


def write_pages(directory, pages):
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / MANIFEST
    previous = json.loads(manifest.read_text()) if manifest.exists() else []
    if not isinstance(previous, list) or not all(managed_path(p) for p in previous) or not all(managed_path(p) for p in pages):
        raise Invalid('Invalid generated wiki page manifest')
    for path in set(previous) - set(pages):
        (directory / path).unlink(missing_ok=True)
    for path, text in pages.items():
        (directory / path).write_text(text, encoding='utf-8')
    manifest.write_text(json.dumps(sorted(pages), indent=2) + '\n')


def publish(repo, directory, token):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise Invalid('Invalid wiki repository identifier')
    if not token:
        raise Invalid('Set WIKI_WRITE_TOKEN to publish the wiki; the generated preview is still available')
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_CONFIG_', 'GIT_TRACE', 'GIT_CURL_'))}
    credential = base64.b64encode(('x-access-token:' + token).encode()).decode()
    environment.update({'GIT_CONFIG_COUNT': '3', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
                        'GIT_CONFIG_VALUE_0': 'AUTHORIZATION: basic ' + credential,
                        'GIT_CONFIG_KEY_1': 'credential.helper', 'GIT_CONFIG_VALUE_1': '',
                        'GIT_CONFIG_KEY_2': 'http.followRedirects', 'GIT_CONFIG_VALUE_2': 'false',
                        'GIT_TERMINAL_PROMPT': '0'})
    def git(*args, cwd=None):
        result = subprocess.run(['git', *args], cwd=cwd, env=environment, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Suppress command output so credentials can never enter logs.
            raise Invalid('Wiki Git operation failed. Create the initial wiki page and check WIKI_WRITE_TOKEN access; retry maintenance.')
        return result.stdout.strip()
    with tempfile.TemporaryDirectory(prefix='pit-wiki-') as temporary:
        checkout = Path(temporary) / 'wiki'
        git('clone', '--quiet', '--depth=1', f'https://github.com/{repo}.wiki.git', str(checkout))
        files = json.loads((directory / MANIFEST).read_text())
        pages = {p: (directory / p).read_text() for p in files}
        # Reject symlinks from the remote wiki before writing any managed files.
        if any((checkout / p).is_symlink() for p in [MANIFEST, *files]):
            raise Invalid('Generated wiki paths must not be symlinks')
        write_pages(checkout, pages)
        git('config', 'user.name', 'github-actions[bot]', cwd=checkout)
        git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com', cwd=checkout)
        git('add', '--all', cwd=checkout)
        if not git('status', '--porcelain', cwd=checkout):
            return 'unchanged'
        git('commit', '--quiet', '-m', 'Refresh accepted project wiki', cwd=checkout)
        git('push', '--quiet', 'origin', 'HEAD', cwd=checkout)
    return 'published'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--output', type=Path, default=Path('build/wiki'))
    parser.add_argument('--fetch-issues', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--publish-only', action='store_true', help='Publish the already generated preview without fetching twice')
    args = parser.parse_args()
    try:
        if args.publish_only:
            from project_workflow.validate_pr import GitHub
            api = GitHub(args.repo, os.environ.get('GITHUB_TOKEN', ''))
            checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
            if api.get('git/ref/heads/main')['object']['sha'] != checkout:
                raise Invalid('Accepted main changed after wiki generation; rerun maintenance')
            print('Wiki: ' + publish(args.repo, args.output, os.environ.get('WIKI_WRITE_TOKEN', '')))
            return 0
        ctx = load_local_context(args.root)
        issues = snapshots(ctx, os.environ.get('GITHUB_TOKEN', '')) if args.fetch_issues else {}
        pages = render(ctx, args.repo, issues=issues)
        write_pages(args.output, pages)
        print(f'Generated {len(pages)} wiki page(s).')
        if args.publish:
            print('Wiki: ' + publish(args.repo, args.output, os.environ.get('WIKI_WRITE_TOKEN', '')))
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
