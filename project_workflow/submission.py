"""Parse Issue Form answers as data and reuse registry validation/policy."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

import yaml

from project_workflow.issue_form import (LABEL, BRANCH_PREFIX, BOT_LOGIN, BOT_ID,
    CONTRIBUTOR_FIELD, option_value, current_labels, label_keys, deliverable_field)
from project_workflow.project_registry import Invalid, USERNAME, authorize_change, utc_timestamp, validate_record


def number(value):
    if type(value) is not int or not 1 <= value < 10**12:
        raise Invalid("Invalid Issue/PR number")
    return value


def project_id(issue_number):
    return f"P{number(issue_number):06d}"


def project_path(issue_number):
    return f"projects/{project_id(issue_number)}.yaml"


def submission_branch(issue_number):
    return BRANCH_PREFIX + str(number(issue_number))


def branch_issue(branch):
    match = re.fullmatch(re.escape(BRANCH_PREFIX) + r"([1-9][0-9]*)", branch or "")
    if not match:
        raise Invalid("Automated proposal has an unsupported branch name")
    return number(int(match[1]))


def is_submission_bot(user):
    return user.get("login") == BOT_LOGIN and user.get("id") == BOT_ID and user.get("type") == "Bot"


def labelled(issue):
    return LABEL in {x["name"] for x in issue.get("labels", [])}


def issue_version(issue):
    return (issue.get("title") or "", issue.get("body") or "", issue["user"],
            issue.get("state"), labelled(issue), issue.get("created_at"))


def answers(body, ctx):
    if not isinstance(body, str) or len(body.encode("utf-8")) > 65536:
        raise Invalid("Issue description is missing or too long (maximum 64 KiB)")
    labels = current_labels(ctx)
    reverse = label_keys(ctx)
    found, current, fence = {}, None, None
    for line in body.replace("\r\n", "\n").splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not marker[2].strip():
                fence = None
        heading = re.fullmatch(r"### (.+?)\s*", line) if fence is None and not marker else None
        if heading and heading[1] in reverse:
            current = reverse[heading[1]]
            if current in found:
                raise Invalid(f"Repeated field heading: {heading[1]}. Keep each form heading once.", fields={current})
            found[current] = []
        elif current is not None:
            found[current].append(line)
        elif line.strip():
            raise Invalid("Keep the original form headings; unexpected text before the first field")
    required = {key for key in labels if not CONTRIBUTOR_FIELD.fullmatch(key)}
    for key in found:
        match = CONTRIBUTOR_FIELD.fullmatch(key)
        if match:
            required.update({f"contributor_{match[1]}_name", f"contributor_{match[1]}_github_username"})
    missing = required - set(found)
    if missing:
        raise Invalid("Restore these form headings: " + ", ".join(labels.get(k, k) for k in sorted(missing)), fields=missing)
    return {key: "\n".join(value).strip() for key, value in found.items()}


def optional(value):
    return "" if value.strip() in {"", "_No response_", "None"} else value.strip()


def resolved_values(issue, ctx):
    values = answers(issue.get("body"), ctx)
    values["name"] = issue.get("title") or ""
    return values


def selected_groups(value, ctx):
    selected = []
    for line in value.splitlines():
        if re.fullmatch(r"- \[ \] .+", line):
            continue
        match = re.fullmatch(r"- \[[xX]\] (.+)", line)
        if not match:
            if optional(line):
                raise Invalid("Working groups: keep the checkbox format", fields={"working_groups"})
            continue
        code = option_value(match[1])
        if code not in ctx.working_groups or code in selected:
            raise Invalid("Working groups: choose each catalogue group at most once", fields={"working_groups"})
        selected.append(code)
    return selected


def list_text(value):
    value = optional(value)
    lines = value.splitlines()
    if lines and re.fullmatch(r"```(?:text)?", lines[0]) and lines[-1] == "```":
        return optional("\n".join(lines[1:-1]))
    return value


def https_url(value):
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
        parsed.port  # Validate malformed ports too.
    except ValueError:
        valid = False
    if not valid or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise Invalid("Related links must be complete HTTPS URLs without embedded credentials, one per line")


def proposal(issue, ctx, *, organization_member, is_sc):
    if issue.get("pull_request") or issue.get("state") != "open" or not labelled(issue):
        raise Invalid("Submission Issue must be open and have the new-project label")
    if not organization_member:
        raise Invalid("The Issue author must be an active organization member")
    actor = issue["user"]["login"]
    values = resolved_values(issue, ctx)
    labels = {"name":"Project title", **current_labels(ctx)}
    errors, bad_fields = [], set()
    def fail(message, *fields):
        errors.append(message)
        bad_fields.update(fields)
    for key in ("name", "lead", "lead_github_username", "summary", "phd_thesis"):
        values[key] = optional(values[key])
        if not values[key]:
            fail(f"{labels[key]}: an answer is required", key)
    if any(c in values["name"] for c in "\r\n"):
        fail("Project title must be a single line", "name")
    contributors = []
    slots = sorted({int(m[1]) for key in values if (m := CONTRIBUTOR_FIELD.fullmatch(key))})
    for i in slots:
        name_key, user_key = f"contributor_{i}_name", f"contributor_{i}_github_username"
        name, username = optional(values[name_key]), optional(values[user_key])
        if bool(name) != bool(username):
            fail("Fill both contributor name and GitHub username, or leave both blank", name_key, user_key)
        elif name:
            contributors.append({"name":name, "github_username":username})
    lead = optional(values["lead_github_username"])
    if lead and (len(lead) > 39 or not USERNAME.fullmatch(lead)):
        fail("Lead: enter a GitHub username without @ or a profile URL", "lead_github_username")
    seen = {lead.lower()}
    for i, person in zip([i for i in slots if optional(values[f'contributor_{i}_name']) and optional(values[f'contributor_{i}_github_username'])], contributors):
        username = person['github_username']
        if len(username) > 39 or not USERNAME.fullmatch(username):
            fail("Contributor: enter a GitHub username without @ or a profile URL", f"contributor_{i}_github_username")
        if username.lower() in seen:
            fail("Do not repeat the lead or a contributor", f"contributor_{i}_github_username")
        seen.add(username.lower())
    if not is_sc and lead.lower() != actor.lower():
        fail("A member must submit a project with themselves as its lead", "lead_github_username")
    groups = []
    try:
        codes = selected_groups(values['working_groups'],ctx)
    except Invalid as exc:
        fail(str(exc),'working_groups')
        codes = []
    if not codes:
        fail("Select at least one working group", "working_groups")
    for code in codes:
        key = deliverable_field(code)
        selections = [x.strip() for x in optional(values.get(key,'' )).split(',') if x.strip()]
        ids = [option_value(x) for x in selections]
        if set(ids) - ctx.deliverables[code] or len(set(ids)) != len(ids):
            fail(f"{code}: choose distinct deliverables from its catalogue", key)
        groups.append({'code':code,'deliverable_ids':ids})
    # Values in unselected groups are intentionally ignored, even if malformed.
    links = [x.strip() for x in list_text(values['related_links']).splitlines() if x.strip()]
    for link in links:
        try:
            https_url(link)
        except Invalid as exc:
            fail(str(exc),'related_links')
    if len(set(links)) != len(links):
        fail("Remove repeated related links", 'related_links')
    url = optional(values['github_url'])
    if url and not re.fullmatch(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+/?",url):
        fail("Main repository: use https://github.com/OWNER/REPOSITORY or leave blank", 'github_url')
    booleans = {'True':True,'False':False}
    if values['phd_thesis'] not in booleans:
        fail("Select True or False for the PhD thesis question", 'phd_thesis')
    if errors:
        raise Invalid("\n".join(dict.fromkeys(errors)),fields=bad_fields)
    record = {
        'schema_version':ctx.schema['properties']['schema_version']['const'],
        'project_id':project_id(issue['number']), 'name':values['name'], 'kind':'EP',
        'status':ctx.schema['properties']['status'].get('default','active'),
        'lead':values['lead'], 'lead_github_username':lead, 'contributors':contributors,
        'summary':values['summary'], 'working_groups':groups,
        'phd_thesis':booleans[values['phd_thesis']],
        'slack_channel':optional(values['slack_channel']) or None,
        'github_url':url or None, 'related_links':links,
        'submission':{'issue_number':issue['number'], 'submitted_by':actor,
            'submitted_at':utc_timestamp(issue.get('created_at')),
            'issue_title_sha256':hashlib.sha256(issue['title'].encode('utf-8')).hexdigest(),
            'issue_body_sha256':hashlib.sha256(issue['body'].encode('utf-8')).hexdigest()},
    }
    validate_record(record,project_path(issue['number']),ctx)
    authorize_change(None,record,actor,ctx,organization_member=organization_member,is_sc=is_sc)
    return record


class Dumper(yaml.SafeDumper):
    pass


def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


Dumper.add_representer(str, _string)


def project_yaml(record):
    return yaml.dump(record, Dumper=Dumper, sort_keys=False, allow_unicode=True)


def changed_files(base_tree, head_tree):
    def entries(tree):
        return {p: (v["type"], v["mode"], v["sha"]) for p, v in tree.items() if v["type"] != "tree"}
    base, head = entries(base_tree), entries(head_tree)
    return {p for p in set(base) | set(head) if base.get(p) != head.get(p)}


def validate_bot_snapshot(base_tree, head_tree, base_read, head_read, pr, issue, ctx, *, organization_member, is_sc):
    if not is_submission_bot(pr['user']) or branch_issue(pr['head']['ref']) != issue['number']:
        raise Invalid('Automated PR must be authored by GitHub Actions and bound to its submission Issue')
    path = project_path(issue['number'])
    if path in base_tree:
        raise Invalid('A new submission cannot overwrite an existing project ID')
    if changed_files(base_tree,head_tree) != {path}:
        raise Invalid('Automated PR may add only its single project YAML file')
    expected = proposal(issue,ctx,organization_member=organization_member,is_sc=is_sc)
    from project_workflow.project_registry import parse_yaml
    if parse_yaml(head_read(path)) != expected:
        raise Invalid('PR data differs from the submission Issue; ask SC to inspect it')
    return expected
