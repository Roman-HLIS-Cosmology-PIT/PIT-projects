"""Format proposed data for humans and route review requests from project_workflow/config.yaml."""
from __future__ import annotations

import base64
import html
import zlib
import re
from urllib.parse import quote


def safe_text(value, *, markdown=False):
    text = str(value)
    # Submitted mentions must not notify anyone through a bot-authored PR.
    # Submitted closing keywords must not close unrelated Issues on merge.
    text = text.replace("@", "&#64;")
    text = re.sub(r"#(?=[0-9])", "&#35;", text)
    text = re.sub(r"\b(close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?)\s+(?=https?://github\.com/)",
                  lambda m: f"&#{ord(m[1][0])};" + m[1][1:] + " ", text, flags=re.IGNORECASE)
    if not markdown:
        text = html.escape(text, quote=False).replace("&amp;#", "&#")
        text = re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", text).replace("\n", " ").replace("\r", " ")
    return text


def profile(username):
    return f"[{username}](https://github.com/{username})"


def link(url):
    # Explicit links keep repository underscores/punctuation out of Markdown
    # formatting. Encode characters that could terminate a destination.
    destination = quote(url, safe="/:?#%[]!$&'()*+,;=-._~")
    return f"[{safe_text(url)}](<{destination}>)"


def request_review(api, pr, ctx):
    requested_users = {p["login"].lower() for p in pr.get("requested_reviewers", [])}
    requested_teams = {t["slug"].lower() for t in pr.get("requested_teams", [])}
    desired_users = {x[1:].lower() for x in ctx.review_owners if "/" not in x}
    desired_teams = {x.split("/", 1)[1].lower() for x in ctx.review_owners if "/" in x}
    remove_users = [p["login"] for p in pr.get("requested_reviewers", []) if p["login"].lower() not in desired_users]
    remove_teams = [t["slug"] for t in pr.get("requested_teams", []) if t["slug"].lower() not in desired_teams]
    if remove_users or remove_teams:
        api.request("DELETE", f"pulls/{pr['number']}/requested_reviewers", {"reviewers": remove_users, "team_reviewers": remove_teams})
    users, teams = [], []
    for owner in ctx.review_owners:
        owner = owner[1:]
        if "/" in owner:
            org, slug = owner.split("/")
            if org.lower() != api.repo.split("/")[0].lower():
                from project_workflow.project_registry import Invalid
                raise Invalid("Review teams must belong to this repository's organization")
            if slug.lower() not in requested_teams:
                teams.append(slug)
        elif owner.lower() != pr["user"]["login"].lower() and owner.lower() not in requested_users:
            users.append(owner)
    if users or teams:
        api.request("POST", f"pulls/{pr['number']}/requested_reviewers", {"reviewers": users, "team_reviewers": teams})


def summary_body(record, ctx):
    """Readable validated submission; raw answers remain on the original Issue."""
    def person(name, username):
        return safe_text(name) + ' · ' + profile(username)
    rows = ['**Project title**: ' + safe_text(record['name'])]
    if record['kind'] == 'EP' or 'claim' in record:
        rows.append(('**Project lead**: ' + person(record['leads'][0]['name'],record['leads'][0]['github_username'])
             if len(record['leads']) == 1 else '**Project leads**:\n\n' + '\n'.join('- ' + person(p['name'],p['github_username']) for p in record['leads'])))
    kind = record['kind']
    rows.extend(['**Project kind**: ' + safe_text(kind + ' — ' + ctx.policy['kinds'][kind]['description']),
                 '**Working groups and deliverables**:'])
    for group in record['working_groups']:
        code = group['code']
        catalogue = ctx.working_groups[code]
        titles = {d['id']:d['title'] for d in catalogue['deliverables']}
        rows.append('**' + safe_text(code + ' — ' + catalogue['description']) + '**')
        rows.append('\n'.join('- ' + safe_text(i + ' — ' + titles[i]) for i in group['deliverable_ids']) or 'No deliverables selected.')
    if record['kind'] == 'EP':
        rows.append('**Part of a PhD thesis**: ' + str(record['phd_thesis']))
    rows.extend(['**Slack channel**: ' + safe_text(record.get('slack_channel') or 'Not provided'),
                 '**Main GitHub repository**: ' + (link(record['github_url']) if record.get('github_url') else 'Not provided'),
                 '**Project description**:',safe_text(record['summary'],markdown=True)])
    if record['kind'] == 'EP' or 'claim' in record:
        rows.extend(['**Contributors**:',
                     '\n'.join('- ' + person(p['name'],p['github_username']) for p in record['contributors']) or 'None listed'])
    rows.extend(['**Related links**:', '\n'.join('- ' + link(url) for url in record.get('related_links',[])) or 'None provided'])
    return '\n\n'.join(rows) + '\n'


def proposal_body(record, ctx, repo, issue_number, warning=None):
    text = f"Submitted through [Issue #{issue_number}](https://github.com/{repo}/issues/{issue_number}).\n\nCloses #{issue_number}\n\n"
    text += summary_body(record, ctx)
    from project_workflow.submission import provenance
    text += f"\n**Project ID**: {record['project_id']}\n\n**Submitted at (UTC)**: {provenance(record)['submitted_at']}\n"
    if warning:
        text += '\n**Repository lookup**: ' + safe_text(warning) + '\n'
    text += '\nReview the generated YAML and scientific content before accepting. Passing validation does not constitute project acceptance.\n'
    if len(text) > 64000:
        text = text[:63000] + f"\n\n*Preview shortened. The full project is in `projects/{record['project_id']}.yaml`.*\n"
    return text


def formatted_issue_body(record, ctx, source):
    """Display the readable summary while keeping the native answers hidden.

    These are input data, not authority or a correction/edit mechanism. PR
    validation checks both the visible rendering and the generated record.
    """
    from project_workflow.submission import FORMATTED_MARKER
    encoded = base64.b64encode(zlib.compress(source.encode('utf-8'))).decode('ascii')
    return FORMATTED_MARKER + '\n\n' + summary_body(record,ctx) + f'\n<!-- pit-project-source:{encoded} -->\n'
