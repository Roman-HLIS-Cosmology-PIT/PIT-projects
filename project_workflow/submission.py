"""Parse Issue Form answers as data and reuse registry validation/policy."""
from __future__ import annotations

import base64
import binascii
import hashlib
import zlib
import re
from urllib.parse import urlsplit

import yaml

from project_workflow.issue_form import (LABEL, BRANCH_PREFIX, BOT_LOGIN, BOT_ID,
    CONTRIBUTOR_FIELD, option_value, current_labels, label_keys, deliverable_field, form)
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
    required = {item['id'] for item in form(ctx)['body']
                if item.get('validations',{}).get('required')}
    missing = required - set(found)
    if missing:
        raise Invalid("Restore these form headings: " + ", ".join(labels.get(k, k) for k in sorted(missing)), fields=missing)
    return {key: "\n".join(found.get(key,[])).strip() for key in labels}


def optional(value):
    return "" if value.strip() in {"", "_No response_", "None"} else value.strip()


FORMATTED_MARKER = '<!-- pit-project-summary:v2 -->'
SOURCE_MARKER = re.compile(r'\n<!-- pit-project-source:([A-Za-z0-9+/=]+) -->\n?\Z')


def submission_body(issue):
    """Read the native form retained inside a formatted Issue, as untrusted data.

    No branch/database stores answers. Bounded decompression rejects oversized
    or malformed input. The visible summary is checked separately below.
    """
    body = issue.get('body')
    if not isinstance(body,str) or len(body.encode('utf-8')) > 65536:
        raise Invalid('Issue description is missing or too long (maximum 64 KiB)')
    if not body.startswith(FORMATTED_MARKER):
        return body
    match = SOURCE_MARKER.search(body)
    if not match:
        raise Invalid('The formatted submission was changed; cancel and submit a new project')
    try:
        compressed = base64.b64decode(match[1],validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed,65537)
        if len(raw) > 65536 or not decoder.eof or decoder.unused_data:
            raise ValueError('Invalid or oversized source')
        return raw.decode('utf-8')
    except (ValueError,UnicodeError,binascii.Error,zlib.error) as exc:
        raise Invalid('The formatted submission data is invalid; cancel and submit a new project') from exc


def resolved_values(issue, ctx):
    values = answers(submission_body(issue), ctx)
    values['name'] = issue.get('title') or ''
    return values


def selected_groups(value, ctx):
    selected = [option_value(x.strip()) for x in optional(value).split(',') if x.strip()]
    if len(set(selected)) != len(selected) or any(code not in ctx.working_groups for code in selected):
        raise Invalid('Working groups: choose distinct groups from the catalogue',fields={'working_groups'})
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


def optional_fields(values, ctx, *, codes=None):
    """Keep valid optional answers in order; report omissions without blocking.

    The authoritative schema still supplies the limits. Original answers stay
    in the Issue's hidden source for deterministic independent PR validation.
    """
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(ctx.schema)
    def valid(key, value):
        return validator.evolve(schema=ctx.schema['properties'][key]).is_valid(value)
    warnings, contributors = [], []
    seen = {optional(values.get('lead_github_username','')).lower()}
    slots = sorted({int(m[1]) for key in values if (m := CONTRIBUTOR_FIELD.fullmatch(key))})
    for i in slots:
        name = optional(values.get(f'contributor_{i}_name',''))
        username = optional(values.get(f'contributor_{i}_github_username',''))
        if not name and not username:
            continue
        person = {'name':name,'github_username':username}
        if not name or not username:
            reason = 'both a name and GitHub username are needed'
        elif not valid('contributors',[person]):
            reason = 'the name or GitHub username has an invalid format or exceeds its allowed length'
        elif username.lower() in seen:
            reason = 'the username repeats the lead or an earlier contributor'
        else:
            contributors.append(person)
            seen.add(username.lower())
            continue
        warnings.append(f'Contributor {i} was removed: {reason}.')
    if not valid('contributors',contributors):
        # Supports an optional maxItems set in the single source schema.
        limit = ctx.schema['properties']['contributors'].get('maxItems')
        if limit is not None and len(contributors) > limit:
            warnings.append(f'Additional contributors were removed: keep at most {limit}.')
            contributors = contributors[:limit]
    links, seen_links = [], set()
    maximum = ctx.schema['properties']['related_links'].get('maxItems')
    for i, link in enumerate(list_text(values.get('related_links','')).splitlines(),1):
        link = link.strip()
        if not link:
            continue
        try:
            https_url(link)
            if not valid('related_links',[link]):
                raise Invalid('Invalid link')
        except Invalid:
            warnings.append(f'Related link on line {i} was removed: enter a complete HTTPS URL without embedded credentials, within the allowed length.')
            continue
        if link in seen_links:
            warnings.append(f'Related link on line {i} was removed: it duplicates an earlier link.')
            continue
        seen_links.add(link)
        if maximum is not None and len(links) >= maximum:
            warnings.append(f'Related link on line {i} was removed: keep at most {maximum} links.')
            continue
        links.append(link)
    url = optional(values.get('github_url','')) or None
    if url and (not valid('github_url',url) or url.rstrip('/').rsplit('/',1)[-1] in {'.','..'}):
        warnings.append('The main GitHub repository link was removed: use https://github.com/OWNER/REPOSITORY.')
        url = None
    slack = optional(values.get('slack_channel','')) or None
    if slack and not valid('slack_channel',slack):
        warnings.append('The Slack channel was removed: it exceeds its allowed length or has an invalid format.')
        slack = None
    groups = []
    for code in selected_groups(values.get('working_groups',''),ctx) if codes is None else codes:
        ids = []
        selections = optional(values.get(deliverable_field(code),'')).split(',')
        for i, selection in enumerate(selections,1):
            if not selection.strip():
                continue
            identifier = option_value(selection.strip())
            if identifier not in ctx.deliverables[code]:
                warnings.append(f'Deliverable selection {i} for {code} was removed: it is not in that working group’s catalogue.')
            elif identifier in ids:
                warnings.append(f'Deliverable selection {i} for {code} was removed: it repeats an earlier selection.')
            else:
                ids.append(identifier)
        groups.append({'code':code,'deliverable_ids':ids})
    return {'contributors':contributors, 'related_links':links, 'github_url':url,
            'slack_channel':slack, 'working_groups':groups}, warnings


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
    lead = optional(values['lead_github_username'])
    if lead and (len(lead) > 39 or not USERNAME.fullmatch(lead)):
        fail('Lead: enter a GitHub username without @ or a profile URL', 'lead_github_username')
    if not is_sc and lead.lower() != actor.lower():
        fail("A member must submit a project with themselves as its lead", "lead_github_username")
    try:
        codes = selected_groups(values['working_groups'],ctx)
    except Invalid as exc:
        fail(str(exc),'working_groups')
        codes = []
    if not codes:
        fail("Select at least one working group", "working_groups")
    # Values in unselected groups are intentionally ignored.
    cleaned, _ = optional_fields(values,ctx,codes=codes)
    booleans = {'True':True,'False':False}
    if values['phd_thesis'] not in booleans:
        fail("Select True or False for the PhD thesis question", 'phd_thesis')
    if errors:
        raise Invalid("\n".join(dict.fromkeys(errors)),fields=bad_fields)
    record = {
        'schema_version':ctx.schema['properties']['schema_version']['const'],
        'project_id':project_id(issue['number']), 'name':values['name'], 'kind':'EP',
        'status':ctx.schema['properties']['status'].get('default','active'),
        'lead':values['lead'], 'lead_github_username':lead, **cleaned,
        'summary':values['summary'],
        'phd_thesis':booleans[values['phd_thesis']],
        'submission':{'issue_number':issue['number'], 'submitted_by':actor,
            'submitted_at':utc_timestamp(issue.get('created_at')),
            'issue_title_sha256':hashlib.sha256(issue['title'].encode('utf-8')).hexdigest(),
            'issue_body_sha256':hashlib.sha256(submission_body(issue).encode('utf-8')).hexdigest()},
    }
    validate_record(record,project_path(issue['number']),ctx)
    if issue['body'].startswith(FORMATTED_MARKER):
        from project_workflow.review import formatted_issue_body
        if issue['body'] != formatted_issue_body(record,ctx,submission_body(issue)):
            raise Invalid('The formatted submission was changed; cancel and submit a new project')
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
