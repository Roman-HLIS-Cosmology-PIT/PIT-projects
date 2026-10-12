"""Authenticated replacement Issues and URL-prefilled forms; no submitted code."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import secrets
from urllib.parse import urlencode

from project_workflow.issue_form import (CORRECTION_FIELDS, KEEP_VALUE, current_labels, deliverable_value,
                                        option_value)
from project_workflow.project_registry import Invalid
from project_workflow.submission import (answers, correction_issue, is_submission_bot, number, optional,
                                         resolved_values)

STATE_MARKER = "<!-- pit-project-revisions:v1 -->"
ROUTE_MARKER = "<!-- pit-project-revision-root:v1 -->"
MAX_REVISIONS = 100
MAX_URL_LENGTH = 7500


def json_body(marker, payload, message):
    return marker + '\n<!-- ' + json.dumps(payload, sort_keys=True, separators=(',', ':')) + ' -->\n\n' + message


def marked_json(body, marker):
    encoded = body[len(marker):].strip().splitlines()[0]
    if not encoded.startswith('<!-- ') or not encoded.endswith(' -->'):
        raise Invalid('The authenticated automation record is malformed')
    return json.loads(encoded[5:-4])


def content_hash(issue):
    data = [issue.get("title"), issue.get("body"), issue["user"].get("id"),
            issue["user"].get("login"), issue.get("created_at")]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def same_author(left, right):
    return (type(left["user"].get("id")) is int and left["user"]["id"] == right["user"].get("id")
            and left["user"]["login"].lower() == right["user"]["login"].lower())


def state_comment(api, root_number):
    matches = [c for c in api.pages(f"issues/{number(root_number)}/comments")
               if is_submission_bot(c["user"]) and c.get("body", "").startswith(STATE_MARKER)]
    if len(matches) > 1:
        raise Invalid("Multiple revision records exist; ask SC to resolve them")
    return matches[0] if matches else None


def read_state(api, root_number):
    comment = state_comment(api, root_number)
    if comment is None:
        raise Invalid("No authenticated submission history exists; ask SC to retry the original Issue")
    try:
        state = marked_json(comment['body'], STATE_MARKER)
        history = state["history"]
        if (state["root"] != root_number or not isinstance(history, list) or not 1 <= len(history) <= MAX_REVISIONS
                or history[0]["number"] != root_number or len({e["number"] for e in history}) != len(history)):
            raise ValueError()
        for entry in history:
            number(entry["number"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid("The authenticated revision history is malformed; ask SC to inspect it") from exc
    return state


def write_state(api, state):
    comment = state_comment(api, state["root"])
    body = json_body(STATE_MARKER, state,
                     f"Latest submission: https://github.com/{api.repo}/issues/{latest_number(state)}")
    if len(body) > 64000:
        raise Invalid("Revision history exceeds GitHub's comment limit")
    if comment:
        if comment["body"] != body:
            api.request("PATCH", f"issues/comments/{comment['id']}", {"body": body})
    else:
        api.request("POST", f"issues/{state['root']}/comments", {"body": body})


def ensure_state(api, root):
    if state_comment(api, root["number"]) is None:
        write_state(api, {"root": root["number"], "author_id": root["user"]["id"],
                          "history": [{"number": root["number"]}], "request": None})
    return read_state(api, root["number"])


def references(issue, ctx):
    values = answers(issue.get("body"), ctx, correction=True, require_fields=False)
    try:
        root = number(int(values["original_issue"]))
        previous = number(int(values["previous_issue"]))
        token = values["correction_token"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise Invalid("Use the correction link provided on your existing submission") from exc
    return root, previous, token


def active_submission(api, root_number, ctx):
    """Resolve prior answers only from authenticated Issues, never guidance boxes."""
    root = api.get(f"issues/{number(root_number)}")
    if root.get("pull_request") or correction_issue(root):
        raise Invalid("Original submission must be a New Project Issue")
    state = read_state(api, root_number)
    if state.get("author_id") != root["user"].get("id"):
        raise Invalid("Original submitter identity changed")
    previous_values = None
    active = root
    for i, entry in enumerate(state["history"]):
        current = root if i == 0 else api.get(f"issues/{entry['number']}")
        if current.get("pull_request") or not same_author(root, current):
            raise Invalid("Every replacement must be authored by the original submitter")
        if entry.get("frozen_hash") and content_hash(current) != entry["frozen_hash"]:
            raise Invalid("A superseded submission was edited; ask SC to restore it and retry")
        # References are checked when the Issue enters trusted history. Later
        # edits to those text boxes cannot redirect an activated Issue.
        if i < len(state['history']) - 1:
            previous_values = resolved_values(current, ctx, previous_values, require_fields=False)
        active = current
    return root, state, active, previous_values


def routed_root(api, issue):
    comments = [c for c in api.pages(f"issues/{issue['number']}/comments")
                if is_submission_bot(c['user']) and c.get('body', '').startswith(ROUTE_MARKER)]
    if not comments:
        return None
    if len(comments) != 1:
        raise Invalid('Multiple authenticated Issue routes exist')
    try:
        return number(marked_json(comments[0]['body'], ROUTE_MARKER)['root'])
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid('The authenticated Issue route is malformed') from exc


def latest_number(state):
    return state["history"][-1]["number"]


def correction_link(api, root, state, issue, values, ctx, *, error=None):
    """Persist a version-bound reference, then prefill text values using field IDs."""
    request = state.get("request")
    mode = "correction" if error is not None else "edit"
    version = content_hash(issue)
    if not request or request["source"] != issue["number"] or request["hash"] != version or request["mode"] != mode:
        request = {"source": issue["number"], "hash": version, "mode": mode, "token": secrets.token_urlsafe(24)}
        state = deepcopy(state)
        state["request"] = request
        write_state(api, state)
    params = {"template": "correct-project.yml", "title": values.get("name", issue.get("title", "")),
              "original_issue": str(root["number"]), "previous_issue": str(issue["number"]),
              "correction_token": request["token"]}
    fields = error.fields if error else set()
    selections = error.selections if error else {}
    if "name" in fields:
        params["title"] += " 🔴"
    for item in ctx.form_source["body"]:
        key = item.get("id")
        if not key:
            continue
        value = optional(values.get(key, ""))
        if item["type"] == "dropdown":
            rows = [x.strip() for x in value.split(",") if x.strip()] or [""]
            text = []
            for row in rows:
                display = deliverable_value(row, None)[1] if key == "deliverable_ids" else option_value(row)
                bad = key in fields and (not selections.get(key) or row in selections[key])
                text.append(display + (" 🔴" if bad else ""))
            params["previous_" + key] = "Previous selection:\n" + "\n".join(text)
        else:
            if key in fields:
                if selections.get(key):
                    value = "\n".join(row + (" 🔴" if row.strip() in selections[key] else "") for row in value.splitlines())
                elif "\n" in value:
                    value = "🔴\n" + value
                else:
                    value += " 🔴" if value else "🔴"
            params[key] = value
    prefix = f"https://github.com/{api.repo}/issues/new?"
    url = prefix + urlencode(params)
    omitted = []
    # Native GitHub links have finite length. Preserve omitted answers through
    # an explicit sentinel resolved server-side against the previous Issue.
    candidates = sorted((k for k in current_labels(ctx) if k in params), key=lambda k: len(params[k]), reverse=True)
    for key in candidates:
        if len(url) <= MAX_URL_LENGTH:
            break
        if len(params[key]) <= len(KEEP_VALUE) + 4:
            continue
        params[key] = KEEP_VALUE + (" 🔴" if key in fields else "")
        omitted.append(current_labels(ctx)[key])
        url = prefix + urlencode(params)
    if len(url) > MAX_URL_LENGTH:
        raise Invalid("The correction link is too large; ask SC to inspect this submission")
    return url, omitted


def receive_revision(api, issue, root, state, ctx):
    if latest_number(state) == issue["number"] and same_author(root, issue):
        # The transition has already been authenticated, even if an author
        # subsequently edits the reference fields in the Issue description.
        return state
    root_number, previous, token = references(issue, ctx)
    if root_number != root["number"] or not same_author(root, issue):
        raise Invalid("Only the original submitter may replace this proposal")
    request = state.get("request")
    if (not request or previous != latest_number(state) or request["source"] != previous
            or not secrets.compare_digest(token, request["token"])):
        raise Invalid("This correction link is stale. Use the latest submission Issue")
    source = api.get(f"issues/{previous}")
    if source.get("state") != "open" or content_hash(source) != request["hash"]:
        raise Invalid("The previous submission changed or was closed. Request a fresh link")
    if issue.get("state") != "open" or len(state["history"]) >= MAX_REVISIONS:
        raise Invalid("This replacement cannot be activated; ask SC to inspect it")
    if issue["number"] <= previous:
        raise Invalid("A replacement must be a newer Issue")
    state = deepcopy(state)
    state["history"][-1]["frozen_hash"] = content_hash(source)
    state["history"].append({"number": issue["number"], "token": token})
    state["request"] = None
    write_state(api, state)
    return state


def set_label(api, issue, label, *, present=True):
    labels = {entry["name"] for entry in issue.get("labels", [])}
    if present and label not in labels:
        try:
            api.get(f"labels/{label}")
        except OSError as exc:
            if getattr(exc, "code", None) != 404:
                raise
            api.request("POST", "labels", {"name": label, "color": "d73a4a" if label == "invalid" else "cfd3d7"})
        api.request("POST", f"issues/{issue['number']}/labels", {"labels": [label]})
    elif not present and label in labels:
        api.request("DELETE", f"issues/{issue['number']}/labels/{label}", None)
