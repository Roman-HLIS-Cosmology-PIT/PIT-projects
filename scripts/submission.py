"""Parse Issue Form answers as data and reuse registry validation/policy."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

import yaml

from scripts.issue_form import FIELD_LABELS, LABEL, BRANCH_PREFIX, BOT_LOGIN, BOT_ID, CURRENT_FIELD_LABELS, CONTRIBUTOR_LIMIT, deliverable_choices, group_options
from scripts.project_registry import Invalid, USERNAME, authorize_change, validate_record


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
    # updated_at includes comments/labels, which do not change the proposal.
    return (issue.get("title") or "", issue.get("body") or "", issue["user"]["login"], issue["user"].get("id"),
            issue.get("state"), labelled(issue))


def answers(body):
    if not isinstance(body, str) or len(body.encode("utf-8")) > 65536:
        raise Invalid("Issue description is missing or too long (maximum 64 KiB)")
    labels = {**FIELD_LABELS, **CURRENT_FIELD_LABELS}
    reverse = {label: key for key, label in labels.items()}
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
                raise Invalid(f"Repeated field heading: {labels[current]}. Keep each form heading once.")
            found[current] = []
        elif current is not None:
            found[current].append(line)
        elif line.strip():
            raise Invalid("Keep the original form headings; unexpected text before the first field")
    expected = FIELD_LABELS if "name" in found else CURRENT_FIELD_LABELS
    if "name" not in found and "contributors" in found:
        raise Invalid("Do not mix the old contributor list with the new contributor boxes")
    missing = set(expected) - set(found)
    if missing:
        raise Invalid("Restore these form headings: " + ", ".join(expected[k] for k in expected if k in missing))
    return {key: "\n".join(value).strip() for key, value in found.items()}


def optional(value):
    return "" if value.strip() in {"", "_No response_", "None"} else value.strip()


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
        raise Invalid("Submission Issue must be open and labelled new-project")
    if not organization_member:
        raise Invalid("The Issue author must be an active organization member")
    actor = issue["user"]["login"]
    values = answers(issue.get("body"))
    legacy = "name" in values
    if not legacy:
        values["name"] = issue.get("title") or ""
    errors = []
    for key in ("name", "kind", "working_group", "summary", "phd_thesis", "deliverable_ids"):
        values[key] = optional(values[key])
        if not values[key]:
            errors.append(f"{FIELD_LABELS[key]}: an answer is required")
    contributors = []
    if legacy:
        for line in list_text(values["contributors"]).splitlines():
            if not line.strip():
                continue
            pair = [x.strip() for x in line.split("|")]
            if len(pair) != 2 or not all(pair):
                errors.append("Contributors: use Name | github_username, one person per line")
            else:
                contributors.append({"name": pair[0], "github_username": pair[1]})
    else:
        for i in range(1, CONTRIBUTOR_LIMIT + 1):
            name = optional(values[f"contributor_{i}_name"])
            username = optional(values[f"contributor_{i}_github_username"])
            if bool(name) != bool(username):
                errors.append(f"Contributor {i}: fill both name and GitHub username, or leave both blank")
            elif name:
                contributors.append({"name": name, "github_username": username})
    links = [x.strip() for x in list_text(values["related_links"]).splitlines() if x.strip()]
    for link in links:
        try:
            https_url(link)
        except Invalid as exc:
            errors.append(str(exc))
    if len(set(links)) != len(links):
        errors.append("Related links: remove repeated URLs")
    lead_username = optional(values["lead_github_username"])
    if lead_username and (len(lead_username) > 39 or not USERNAME.fullmatch(lead_username)):
        errors.append("Project lead GitHub username: enter one valid GitHub username without @ or a profile URL")
    if any(len(p["github_username"]) > 39 or not USERNAME.fullmatch(p["github_username"]) for p in contributors):
        errors.append("Contributors: each GitHub username must be valid, without @ or a profile URL")
    github_url = optional(values["github_url"])
    if github_url and not re.fullmatch(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+/?", github_url):
        errors.append("Main GitHub repository: use https://github.com/OWNER/REPOSITORY without a branch/file path, or leave it blank")
    booleans = {"Yes": True, "No": False} if legacy else {"True": True, "False": False}
    if values["phd_thesis"] not in booleans:
        errors.append("Part of your PhD thesis?: enter " + " or ".join(booleans))
    wg = group_options(ctx).get(values["working_group"], values["working_group"])
    choices = deliverable_choices(ctx)
    aliases = {f"{group} / {item['id']}": (group, item["id"])
               for group, definition in ctx.working_groups.items() for item in definition["deliverables"]}
    selected = [x.strip() for x in values["deliverable_ids"].split(",") if x.strip()]
    ids = []
    for selection in selected:
        pair = choices.get(selection) or aliases.get(selection) or aliases.get(f"{wg} / {selection}")
        if pair is None or pair[0] != wg:
            errors.append("Deliverables: select IDs from the chosen working group. Valid choices: " +
                          ", ".join(sorted(ctx.deliverables.get(wg, set()))))
        else:
            ids.append(pair[1])
    if wg not in ctx.working_groups:
        errors.append("Working group: choose " + ", ".join(sorted(ctx.working_groups)))
    if errors:
        raise Invalid("\n".join(dict.fromkeys(errors)))
    record = {
        "schema_version": 1, "project_id": project_id(issue["number"]),
        "name": values["name"], "kind": values["kind"], "status": "active",
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
    if not legacy:
        record["submission"]["issue_title_sha256"] = hashlib.sha256(issue["title"].encode("utf-8")).hexdigest()
    if any(c in record["name"] for c in "\r\n"):
        raise Invalid("Project title must be a single line")
    validate_record(record, project_path(issue["number"]), ctx)
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


def validate_bot_snapshot(base_tree, head_tree, base_read, head_read, pr, issue, ctx, *, organization_member, is_sc):
    if not is_submission_bot(pr["user"]) or branch_issue(pr["head"]["ref"]) != issue["number"]:
        raise Invalid("Automated PR must be authored by GitHub Actions and bound to its submission Issue")
    path = project_path(issue["number"])
    if path in base_tree:
        raise Invalid("This project's ID already exists; a new submission cannot overwrite it")
    if changed_files(base_tree, head_tree) != {path}:
        raise Invalid("Automated PR may add only the single project file associated with its Issue")
    expected = proposal(issue, ctx, organization_member=organization_member, is_sc=is_sc)
    from scripts.project_registry import parse_yaml
    if parse_yaml(head_read(path)) != expected:
        raise Invalid("PR data differs from the current Issue. Save the Issue to regenerate the proposal")
    validate_record(expected, path, ctx)
    return expected
