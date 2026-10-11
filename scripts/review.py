"""Format proposed data for humans and route review requests from CODEOWNERS."""
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
                from scripts.project_registry import Invalid
                raise Invalid("Review teams must belong to this repository's organization")
            if slug.lower() not in requested_teams:
                teams.append(slug)
        elif owner.lower() != pr["user"]["login"].lower() and owner.lower() not in requested_users:
            users.append(owner)
    if users or teams:
        api.request("POST", f"pulls/{pr['number']}/requested_reviewers", {"reviewers": users, "team_reviewers": teams})


def proposal_body(record, ctx, repo, issue_number, warning=None):
    group = ctx.working_groups[record["working_group"]]
    kinds = {"EP": "Enhancing project", "CPP": "Critical path project", "KEY": "Key project"}
    lead = (safe_text(record["lead"]) + " · " + profile(record["lead_github_username"])) if record["lead"] else "Unassigned"
    body = [f"## {safe_text(record['name'])}",
            f"Submitted through [Issue #{issue_number}](https://github.com/{repo}/issues/{issue_number}).",
            f"Closes #{issue_number}",
            "| Field | Value |", "| --- | --- |",
            f"| Project ID | {record['project_id']} |",
            f"| Kind | {record['kind']} — {kinds.get(record['kind'], record['kind'])} |",
            f"| Initial status | {record['status']} |",
            f"| Working group | {record['working_group']} — {safe_text(group['description'])} |",
            f"| Project lead | {lead} |",
            f"| Part of a PhD thesis | {'True' if record['phd_thesis'] else 'False'} |",
            f"| Slack channel | {safe_text(record['slack_channel']) if record['slack_channel'] else 'Not provided'} |",
            f"| Main repository | {link(record['github_url']) if record['github_url'] else 'Not provided'} |",
            "\n### Deliverables\n"]
    titles = {x["id"]: x["title"] for x in group["deliverables"]}
    body.extend(f"- **{safe_text(identifier)}** — {safe_text(titles[identifier])}" for identifier in record["deliverable_ids"])
    body.append("\n### Contributors\n")
    if record["contributors"]:
        body.extend(["| Name | GitHub username |", "| --- | --- |"])
        body.extend(f"| {safe_text(p['name'])} | {profile(p['github_username'])} |" for p in record["contributors"])
    else:
        body.append("No additional contributors listed.")
    body.append("\n### Related links\n")
    body.extend("- " + link(url) for url in record["related_links"])
    if not record["related_links"]:
        body.append("None provided.")
    if warning:
        body.append("\n**Repository lookup:** " + safe_text(warning))
    body.extend(["\n### Description\n", safe_text(record["summary"], markdown=True),
                 "\n### Review\n", "Review the generated YAML and scientific content before accepting. "
                 "Passing validation does not constitute project acceptance."])
    text = "\n\n".join(body[:3]) + "\n\n" + "\n".join(body[3:])
    if len(text) > 64000:
        text = text[:63000] + f"\n\n*Preview shortened to fit GitHub's PR description limit. The full submission is in `projects/{record['project_id']}.yaml`.*"
    return text + "\n"
