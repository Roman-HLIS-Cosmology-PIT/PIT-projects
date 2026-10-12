"""Parse Issue Form answers as data and reuse registry validation/policy."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

import yaml

from project_workflow.issue_form import (LABEL, CORRECTION_LABEL, KEEP_VALUE, BRANCH_PREFIX,
    BOT_LOGIN, BOT_ID, CONTRIBUTOR_FIELD, option_value, deliverable_value, current_labels, label_keys)
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
    return bool({LABEL, CORRECTION_LABEL} & {x["name"] for x in issue.get("labels", [])})


def correction_issue(issue):
    return CORRECTION_LABEL in {x["name"] for x in issue.get("labels", [])}


def issue_version(issue):
    # updated_at includes comments/labels, which do not change the proposal.
    return (issue.get("title") or "", issue.get("body") or "", issue["user"]["login"], issue["user"].get("id"),
            issue.get("state"), labelled(issue), correction_issue(issue), issue.get("created_at"))


def answers(body, ctx, *, correction=False, require_fields=True):
    if not isinstance(body, str) or len(body.encode("utf-8")) > 65536:
        raise Invalid("Issue description is missing or too long (maximum 64 KiB)")
    labels = current_labels(ctx)
    reverse = label_keys(ctx, correction=correction)
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
    if missing and require_fields:
        raise Invalid("Restore these form headings: " + ", ".join(labels.get(k, k) for k in sorted(missing)), fields=missing)
    return {key: "\n".join(value).strip() for key, value in found.items()}


def optional(value):
    return "" if value.strip() in {"", "_No response_", "None"} else value.strip()


def resolved_values(issue, ctx, previous_values=None, *, require_fields=True):
    correction = correction_issue(issue)
    values = answers(issue.get("body"), ctx, correction=correction, require_fields=require_fields)
    if correction:
        if previous_values is None:
            raise Invalid("Use a correction link provided on your existing submission")
        for item in ctx.form_source['body']:
            key = item.get('id')
            if key in values and item.get('type') in {'input', 'textarea'} and values[key] == KEEP_VALUE:
                values[key] = previous_values.get(key, '')
    values["name"] = issue.get("title") or ""
    return values


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


def proposal(issue, ctx, *, organization_member, is_sc, original_issue=None, previous_values=None, input_values=None):
    if issue.get("pull_request") or issue.get("state") != "open" or not labelled(issue):
        raise Invalid("Submission Issue must be open and have its submission label")
    if not organization_member:
        raise Invalid("The Issue author must be an active organization member")
    actor = issue["user"]["login"]
    if input_values is None:
        values = resolved_values(issue, ctx, previous_values)
    else:
        values = dict(input_values)
        values['name'] = issue.get('title') or ''
        expected = set(current_labels(ctx)) - {key for key in current_labels(ctx) if CONTRIBUTOR_FIELD.fullmatch(key)}
        missing = expected - set(values)
        if missing:
            raise Invalid('Complete the missing fields', fields=missing)
    original_issue = original_issue or issue
    root_number = original_issue["number"]
    labels = {"name": "Project title", **current_labels(ctx)}
    errors, bad_fields, bad_selections = [], set(), {}
    corrections = {}
    def fail(message, *keys):
        errors.append(message)
        bad_fields.update(keys)
        for key in keys:
            corrections.setdefault(key, []).append(message)
    for key in ("name", "kind", "working_group", "summary", "phd_thesis", "deliverable_ids"):
        values[key] = optional(values[key])
        if not values[key]:
            fail(f"{labels[key]}: an answer is required", key)
    contributors, contributor_slots = [], []
    slots = sorted({int(m[1]) for key in values if (m := CONTRIBUTOR_FIELD.fullmatch(key))})
    for i in slots:
        pair = {f'contributor_{i}_name', f'contributor_{i}_github_username'}
        if pair - set(values):
            raise Invalid('Complete the contributor name/username pair', fields=pair - set(values))
        name = optional(values[f"contributor_{i}_name"])
        username = optional(values[f"contributor_{i}_github_username"])
        if bool(name) != bool(username):
            fail(f"Fill both contributor name and GitHub username, or leave both blank",
                 f"contributor_{i}_github_username" if name else f"contributor_{i}_name")
        elif name:
            contributors.append({"name": name, "github_username": username})
            contributor_slots.append(i)
    links = [x.strip() for x in list_text(values["related_links"]).splitlines() if x.strip()]
    for link in links:
        try:
            https_url(link)
        except Invalid as exc:
            fail(str(exc), "related_links")
            bad_selections.setdefault("related_links", []).append(link)
    if len(set(links)) != len(links):
        fail("Related links: remove repeated URLs", "related_links")
        bad_selections.setdefault('related_links', []).extend(x for x in links if links.count(x) > 1)
    lead_username = optional(values["lead_github_username"])
    if lead_username and (len(lead_username) > 39 or not USERNAME.fullmatch(lead_username)):
        fail("Project lead GitHub username: enter one valid GitHub username without @ or a profile URL", "lead_github_username")
    for i in slots:
        username = optional(values[f"contributor_{i}_github_username"])
        if username and (len(username) > 39 or not USERNAME.fullmatch(username)):
            fail("Contributors: each GitHub username must be valid, without @ or a profile URL", f"contributor_{i}_github_username")
        if username and (username.lower() == lead_username.lower() or sum(p["github_username"].lower() == username.lower() for p in contributors) > 1):
            fail("Do not repeat the lead or a contributor", f"contributor_{i}_github_username")
    github_url = optional(values["github_url"])
    if github_url and not re.fullmatch(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+/?", github_url):
        fail("Main GitHub repository: use https://github.com/OWNER/REPOSITORY without a branch/file path, or leave it blank", "github_url")
    booleans = {"True": True, "False": False}
    if values["phd_thesis"] not in booleans:
        fail("Part of your PhD thesis?: enter " + " or ".join(booleans), "phd_thesis")
    wg = option_value(values["working_group"])
    selected = [x.strip() for x in values["deliverable_ids"].split(",") if x.strip()]
    ids = []
    for selection in selected:
        pair = deliverable_value(selection, wg)
        if pair[0] != wg or pair[1] not in ctx.deliverables.get(wg, set()):
            fail(f"{pair[1]} does not belong to {wg}. Select a deliverable from your chosen working group.", "deliverable_ids")
            bad_selections.setdefault("deliverable_ids", []).append(selection)
        else:
            ids.append(pair[1])
    if wg not in ctx.working_groups:
        fail("Working group: choose " + ", ".join(sorted(ctx.working_groups)), "working_group")
    kind = option_value(values["kind"])
    if kind not in ctx.policy["kinds"]:
        fail("Unknown project kind", "kind")
    elif not is_sc and not ctx.policy["kinds"][kind]["member_can_create"]:
        fail("Only SC members can declare this project kind", "kind")
    if bool(optional(values["lead"])) != bool(lead_username):
        fail("Fill both lead name and GitHub username", "lead", "lead_github_username")
    if kind in ctx.policy["kinds"] and not ctx.policy["kinds"][kind]["allow_unassigned"] and not lead_username:
        fail("This project kind requires a lead", "lead", "lead_github_username")
    if not is_sc and lead_username.lower() != actor.lower():
        fail("A member must submit a new project with themselves as its lead", "lead_github_username")
    if len(set(ids)) != len(ids):
        fail("Remove repeated deliverables", "deliverable_ids")
        bad_selections.setdefault('deliverable_ids', []).extend(s for s in selected if ids.count(deliverable_value(s, wg)[1]) > 1)
    if errors:
        raise Invalid("\n".join(dict.fromkeys(errors)), fields=bad_fields, selections=bad_selections, corrections=corrections)
    record = {
        "schema_version": ctx.schema["properties"]["schema_version"]["const"], "project_id": project_id(root_number),
        "name": values["name"], "kind": option_value(values["kind"]),
        "status": ctx.schema["properties"]["status"].get("default", "active"),
        "lead": optional(values["lead"]) or None,
        "lead_github_username": lead_username or None,
        "contributors": contributors, "summary": values["summary"],
        "working_group": wg, "deliverable_ids": ids,
        "phd_thesis": booleans[values["phd_thesis"]],
        "slack_channel": optional(values["slack_channel"]) or None,
        "github_url": github_url or None,
        "related_links": links,
        "submission": {"issue_number": issue["number"], "submitted_by": actor,
                       "issue_body_sha256": hashlib.sha256(issue["body"].encode("utf-8")).hexdigest()},
    }
    record["submission"]["issue_title_sha256"] = hashlib.sha256(issue["title"].encode("utf-8")).hexdigest()
    record["submission"]["submitted_at"] = utc_timestamp(original_issue.get("created_at"))
    if root_number != issue["number"]:
        record["submission"]["original_issue_number"] = root_number
    if any(c in record["name"] for c in "\r\n"):
        raise Invalid("Project title must be a single line", fields={"name"})
    from jsonschema import Draft202012Validator
    for error in Draft202012Validator(ctx.schema).iter_errors(record):
        path = list(error.absolute_path)
        key = path[0] if path else "name"
        if key == "contributors" and len(path) > 2:
            key = f"contributor_{contributor_slots[path[1]]}_{path[2]}"
        guidance = {'minLength': 'Enter a non-empty value.',
                    'maxLength': f'Use at most {error.validator_value} characters.',
                    'maxItems': f'Use at most {error.validator_value} entries.',
                    'uniqueItems': 'Remove repeated entries.',
                    'enum': 'Choose one of the offered options.',
                    'pattern': 'Use a value in the required format.',
                    'type': 'Use a value in the required format.',
                    'anyOf': 'Use a value in the required format.'}.get(error.validator, 'Complete or correct this answer.')
        fail(guidance, key)
    if errors:
        raise Invalid("\n".join(dict.fromkeys(errors)), fields=bad_fields, corrections=corrections)
    validate_record(record, project_path(root_number), ctx)
    authorize_change(None, record, actor, ctx, organization_member=organization_member, is_sc=is_sc)
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


def validate_bot_snapshot(base_tree, head_tree, base_read, head_read, pr, issue, ctx, *, organization_member, is_sc,
                          original_issue=None, previous_values=None, input_values=None):
    root = original_issue or issue
    if not is_submission_bot(pr["user"]) or branch_issue(pr["head"]["ref"]) != root["number"]:
        raise Invalid("Automated PR must be authored by GitHub Actions and bound to its submission Issue")
    path = project_path(root["number"])
    if path in base_tree:
        raise Invalid("This project's ID already exists; a new submission cannot overwrite it")
    if changed_files(base_tree, head_tree) != {path}:
        raise Invalid("Automated PR may add only the single project file associated with its Issue")
    expected = proposal(issue, ctx, organization_member=organization_member, is_sc=is_sc,
                        original_issue=root, previous_values=previous_values, input_values=input_values)
    from project_workflow.project_registry import parse_yaml
    if parse_yaml(head_read(path)) != expected:
        raise Invalid("PR data differs from the current Issue. Save the Issue to regenerate the proposal")
    validate_record(expected, path, ctx)
    return expected
