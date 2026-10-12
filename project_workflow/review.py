"""Format proposed data for humans and route review requests from project_workflow/config.yaml."""
from __future__ import annotations

import html
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


FORMATTED_MARKER = "<!-- pit-project-summary:v1 -->"


def copy_previous_answers(values, *, max_chars=64000):
    """Complete source blocks get GitHub's native copy controls.

    Pick fences longer than every submitted backtick run so raw Markdown,
    mentions and HTML remain literal source. Never offer a partial answer as
    a copy block when the comment size limit requires a shorter message.
    """
    from project_workflow.submission import list_text, optional
    sources = {'summary': optional(values.get('summary', '')),
               'related_links': list_text(values.get('related_links', ''))}
    opening = '<details>\n<summary><strong>Copy previous description and related links</strong></summary>\n\n'
    instructions = ('Use a block’s copy icon, then paste into the matching correction field, '
                    'replacing **Keep previous value**. Leave **Keep previous value** to retain an answer.\n\n')
    closing = '\n</details>'
    labels = {'summary': '**Previous description**', 'related_links': '**Previous related links**'}
    parts = {key: ('*This answer is too long to include a complete copy here. '
                   'Leave **Keep previous value** to retain it; see the previous submission Issue to view it.*'
                   if source else '*No previous answer was provided.*') for key, source in sources.items()}
    def render():
        return opening + instructions + '\n\n'.join(labels[key] + '\n\n' + parts[key] for key in sources) + closing
    remaining = max_chars - len(render())
    blocks = {}
    for key, source in sources.items():
        if source:
            fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', source)), default=0))
            blocks[key] = fence + 'text\n' + source + '\n' + fence
    # Include short answers first, so a long description does not crowd out
    # the related links. The final display order still starts with Description.
    for key in sorted(blocks, key=lambda key: len(blocks[key])):
        cost = len(blocks[key]) - len(parts[key])
        if cost <= remaining:
            parts[key] = blocks[key]
            remaining -= cost
    text = render()
    if len(text) > max_chars:
        return ''
    return text


def summary_body(values, ctx, *, error=None):
    """One layout for Issue bodies, error feedback and PR summaries."""
    from project_workflow.issue_form import CONTRIBUTOR_FIELD, deliverable_value, option_value
    from project_workflow.project_registry import Invalid, USERNAME
    from project_workflow.submission import https_url, list_text, optional
    fields = error.fields if error else set()
    selections = error.selections if error else {}
    def value(key):
        return optional(values.get(key, ''))
    def flag(key, text):
        return text + (' 🔴' if key in fields else '')
    def person(name_key, username_key):
        name, username = value(name_key), value(username_key)
        username_text = profile(username) if username and len(username) <= 39 and USERNAME.fullmatch(username) else safe_text(username or 'Not provided')
        return flag(name_key, safe_text(name or 'Not provided')) + ' · ' + flag(username_key, username_text)
    def url_text(url):
        try:
            https_url(url)
        except Invalid:
            return safe_text(url)
        return link(url)
    kind, wg = option_value(value('kind')), option_value(value('working_group'))
    kd = ctx.policy['kinds'].get(kind, {}).get('description')
    gd = ctx.working_groups.get(wg, {}).get('description')
    rows = [flag('name', '**Project title**: ' + safe_text(value('name') or 'Not provided')),
            '**Project lead**: ' + person('lead', 'lead_github_username'),
            flag('kind', '**Project kind**: ' + safe_text(kind + (' — ' + kd if kd else '') or 'Not provided')),
            flag('working_group', '**Working group**: ' + safe_text(wg + (' — ' + gd if gd else '') or 'Not provided')),
            '**Deliverables**:' + (' 🔴' if 'deliverable_ids' in fields and not selections.get('deliverable_ids') else '')]
    selected = [x.strip() for x in value('deliverable_ids').split(',') if x.strip()]
    deliverables = []
    for selection in selected:
        group, identifier = deliverable_value(selection, wg)
        titles = {d['id']: d['title'] for d in ctx.working_groups.get(group, {}).get('deliverables', [])}
        title = titles.get(identifier)
        text = safe_text(identifier + (' — ' + title if title else ''))
        if selection in selections.get('deliverable_ids', []):
            text += ' 🔴'
        deliverables.append('- ' + text)
    rows.append('\n'.join(deliverables) or 'Not provided')
    rows.extend([flag('phd_thesis', '**Part of a PhD thesis**: ' + safe_text(value('phd_thesis') or 'Not provided')),
                 flag('slack_channel', '**Slack channel**: ' + safe_text(value('slack_channel') or 'Not provided')),
                 flag('github_url', '**Main GitHub repository**: ' + (url_text(value('github_url')) if value('github_url') else 'Not provided')),
                 '**Project description**:' + (' 🔴' if 'summary' in fields else ''),
                 safe_text(value('summary') or 'Not provided', markdown=True), '**Contributors**:'])
    slots = sorted({int(m[1]) for key in values if (m := CONTRIBUTOR_FIELD.fullmatch(key))})
    people = ['- ' + person(f'contributor_{i}_name', f'contributor_{i}_github_username') for i in slots
              if value(f'contributor_{i}_name') or value(f'contributor_{i}_github_username')
              or {f'contributor_{i}_name', f'contributor_{i}_github_username'} & fields]
    rows.append('\n'.join(people) or 'None listed')
    rows.append('**Related links**:' + (' 🔴' if 'related_links' in fields and not selections.get('related_links') else ''))
    links = [x.strip() for x in list_text(value('related_links')).splitlines() if x.strip()]
    rows.append('\n'.join('- ' + url_text(url) + (' 🔴' if url in selections.get('related_links', []) else '') for url in links) or 'None provided')
    return '\n\n'.join(rows) + '\n'


def values_from_record(record):
    values = {key: '' if value is None else str(value) for key, value in record.items()
              if key in {'name', 'kind', 'lead', 'lead_github_username', 'summary', 'working_group', 'github_url', 'slack_channel'}}
    values['deliverable_ids'] = ', '.join(record['deliverable_ids'])
    values['phd_thesis'] = 'True' if record['phd_thesis'] else 'False'
    values['related_links'] = '\n'.join(record.get('related_links', []))
    for i, person in enumerate(record['contributors'], 1):
        values[f'contributor_{i}_name'] = person['name']
        values[f'contributor_{i}_github_username'] = person['github_username']
    return values


def proposal_body(record, ctx, repo, issue_number, warning=None):
    text = f"Submitted through [Issue #{issue_number}](https://github.com/{repo}/issues/{issue_number}).\n\nCloses #{issue_number}\n\n"
    text += summary_body(values_from_record(record), ctx)
    text += f"\n**Project ID**: {record['project_id']}\n\n**Submitted at (UTC)**: {record['submission']['submitted_at']}\n"
    if warning:
        text += '\n**Repository lookup**: ' + safe_text(warning) + '\n'
    text += '\nReview the generated YAML and scientific content before accepting. Passing validation does not constitute project acceptance.\n'
    if len(text) > 64000:
        text = text[:63000] + f"\n\n*Preview shortened. The full project is in `projects/{record['project_id']}.yaml`.*\n"
    return text
