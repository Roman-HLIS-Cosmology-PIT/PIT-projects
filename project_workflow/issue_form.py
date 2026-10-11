"""Render the form source with catalogue choices and shared parser labels."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import re
import sys

import yaml

from project_workflow.project_registry import Invalid, load_local_context, parse_yaml

LABEL = "new-project"
BRANCH_PREFIX = "project-submissions/issue-"
BOT_LOGIN = "github-actions[bot]"
BOT_ID = 41898282
FIELD_LABELS = {
    "name": "Project title",
    "kind": "Project kind",
    "lead": "Project lead name",
    "lead_github_username": "Project lead GitHub username",
    "contributors": "Contributors",
    "working_group": "Working group",
    "deliverable_ids": "Deliverables",
    "phd_thesis": "Part of your PhD thesis?",
    "slack_channel": "Slack channel",
    "summary": "Description",
    "github_url": "Main GitHub repository",
    "related_links": "Related links",
}


def legacy_deliverable_options(ctx):
    options = []
    for group, definition in sorted(ctx.working_groups.items()):
        for item in definition["deliverables"]:
            # GitHub joins multiple dropdown answers with commas. Keep values
            # unambiguous; human-readable titles appear in the field help.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item["id"]):
                raise Invalid("Issue-form deliverable IDs must use letters, digits, dots, underscores or hyphens")
            options.append(f"{group} / {item['id']}")
    if not options:
        raise Invalid("Add at least one deliverable before generating the submission form")
    return options


def legacy_form(ctx):
    def field(key, kind="input", required=False, **attributes):
        return {"type": kind, "id": key,
                "attributes": {"label": FIELD_LABELS[key], **attributes},
                "validations": {"required": required}}

    group_help = "; ".join(f"{code}: {g['description']}" for code, g in sorted(ctx.working_groups.items()))
    deliverable_help = "Select only deliverables from your working group. " + "; ".join(
        f"{code} / {d['id']}: {d['title']}"
        for code, g in sorted(ctx.working_groups.items()) for d in g["deliverables"])
    kinds = list(ctx.policy["kinds"])
    return {
        "name": "New Project", "description": "Propose a project for Steering Committee review.",
        "title": "[New project] ", "labels": [LABEL],
        "body": [
            {"type": "markdown", "attributes": {"value":
                "Submit collaboration-appropriate information only. All answers are visible to repository readers. "
                "Ordinary members can propose EP projects with themselves as lead. Only SC members can declare CPP or KEY. "
                "After submission, validation will comment here. To correct errors, edit this Issue's description, "
                "keep the field headings, and save. No email address is requested."}},
            field("name", required=True, placeholder="A short, descriptive project title"),
            field("kind", "dropdown", True, options=kinds, default=kinds.index("EP") if "EP" in kinds else 0),
            field("lead", description="Required for EP. SC may leave both lead fields blank for an unassigned CPP/KEY."),
            field("lead_github_username", description="One GitHub username without @. Ordinary members must enter their own username."),
            field("contributors", "textarea", description="Optional. One Name | github_username pair per line; omit the lead.",
                  placeholder="Bob Example | bob-example\nCarol Example | carol-example", render="text"),
            field("working_group", "dropdown", True, options=sorted(ctx.working_groups), description=group_help),
            field("deliverable_ids", "dropdown", True, options=legacy_deliverable_options(ctx), multiple=True, description=deliverable_help),
            field("phd_thesis", "dropdown", True, options=["Yes", "No"]),
            field("slack_channel", description="Optional existing channel name, such as #scm-shapes. Can be added later."),
            field("summary", "textarea", True, description="Describe the goals, approach and expected results. Markdown is supported."),
            field("github_url", description="Optional https://github.com/OWNER/REPOSITORY URL. Accessible repositories are checked; private/inaccessible ones are flagged for review."),
            field("related_links", "textarea", description="Optional. One HTTPS URL per line. Do not include credentials or secret sharing tokens.", render="text"),
        ]}


CONTRIBUTOR_LIMIT = 5
CURRENT_FIELD_LABELS = {k: v for k, v in FIELD_LABELS.items() if k not in {"name", "contributors"}}
for i in range(1, CONTRIBUTOR_LIMIT + 1):
    CURRENT_FIELD_LABELS[f"contributor_{i}_name"] = f"Contributor {i} name"
    CURRENT_FIELD_LABELS[f"contributor_{i}_github_username"] = f"Contributor {i} GitHub username"


def group_options(ctx):
    return {f"{code} — {' '.join(g['description'].split())}": code for code, g in sorted(ctx.working_groups.items())}


def kind_options(ctx):
    return [f"{code} - {' '.join(rules['description'].split())}" if "description" in rules else code
            for code, rules in ctx.policy["kinds"].items()]


def deliverable_choices(ctx):
    result = {}
    for code, group in sorted(ctx.working_groups.items()):
        for item in group["deliverables"]:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item["id"]):
                raise Invalid("Issue-form deliverable IDs must use letters, digits, dots, underscores or hyphens")
            # GitHub joins multiple answers with ASCII commas. Display commas
            # as semicolons only in this label; the original title is retained.
            title = " ".join(item["title"].split()).replace(",", ";")
            result[f"{code} / {item['id']} — {title}"] = (code, item["id"])
    if not result:
        raise Invalid("Add at least one deliverable before generating the submission form")
    return result


def deliverable_options(ctx):
    return list(deliverable_choices(ctx))


CONTRIBUTOR_FIELD = re.compile(r"contributor_([1-9][0-9]*)_(name|github_username)\Z")


def current_labels(ctx=None):
    if ctx is None or ctx.form_source is None:
        return dict(CURRENT_FIELD_LABELS)
    source = ctx.form_source
    if not isinstance(source, dict) or not isinstance(source.get("body"), list):
        raise Invalid("Form source must contain a body list")
    core = {key for key in CURRENT_FIELD_LABELS if not CONTRIBUTOR_FIELD.fullmatch(key)}
    labels, slots = {}, {}
    for item in source["body"]:
        if not isinstance(item, dict):
            raise Invalid("Form source fields must be mappings")
        if item.get("type") == "markdown":
            continue
        key = item.get("id")
        match = CONTRIBUTOR_FIELD.fullmatch(key) if isinstance(key, str) else None
        if (key not in core and match is None) or key in labels:
            raise Invalid("Keep canonical field IDs; new data fields require handler changes")
        if match:
            slots.setdefault(match[1], set()).add(match[2])
        attributes = item.get("attributes")
        label = attributes.get("label") if isinstance(attributes, dict) else None
        if not isinstance(label, str) or not label.strip() or "\n" in label:
            raise Invalid("Every answer field needs a nonempty single-line label")
        labels[key] = label
    if not core <= set(labels) or any(pair != {"name", "github_username"} for pair in slots.values()):
        raise Invalid("Form source requires each core field and complete contributor name/username pairs")
    return labels


def label_keys(ctx=None):
    reverse = {}
    def add(key, label):
        if label in reverse and reverse[label] != key:
            raise Invalid("Form labels and aliases must not identify different fields")
        reverse[label] = key
    for key, label in FIELD_LABELS.items():
        add(key, label)
    for key, label in CURRENT_FIELD_LABELS.items():
        add(key, label)
    for key, label in current_labels(ctx).items():
        add(key, label)
    if ctx is not None and ctx.form_source is not None:
        for item in ctx.form_source["body"]:
            aliases = item.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(x, str) or not x.strip() or "\n" in x for x in aliases):
                raise Invalid("Field aliases must be a list of previous labels")
            if aliases and item.get("type") == "markdown":
                raise Invalid("Only answer fields can have aliases")
            for label in aliases:
                add(item["id"], label)
    return reverse


def form(ctx):
    if ctx.form_source is None:
        raise Invalid("Set the form section in project_workflow/config.yaml before generating the form")
    label_keys(ctx)
    spec = deepcopy(ctx.form_source)
    choices = {"project_kinds": kind_options(ctx),
               "working_groups": list(group_options(ctx)),
               "deliverables": deliverable_options(ctx)}
    bindings = {"kind": "project_kinds", "working_group": "working_groups", "deliverable_ids": "deliverables"}
    for item in spec["body"]:
        item.pop("aliases", None)
        source = item.pop("options_from", None)
        if item.get("id") in bindings and source != bindings[item["id"]]:
            raise Invalid("Kind, working group and deliverable options must come from their authoritative sources")
        if source is not None:
            if bindings.get(item.get("id")) != source or "options" in item.get("attributes", {}):
                raise Invalid("options_from must reference the field's catalogue without duplicate options")
            item["attributes"]["options"] = choices[source]
    validate_form(yaml.safe_dump(spec), ctx)
    return spec


def generated_text(ctx, *, legacy=False):
    return "# Generated from project_workflow/config.yaml and working_groups/; do not edit this output.\n" + yaml.safe_dump(
        legacy_form(ctx) if legacy else form(ctx), sort_keys=False, allow_unicode=True, width=110)


def option_value(value):
    """Read the stable code/ID before an optional display description."""
    return value.split("—", 1)[0].split(" - ", 1)[0].strip()


def deliverable_value(value, working_group):
    value = option_value(value)
    if "/" in value:
        group, identifier = (part.strip() for part in value.split("/", 1))
        return group, identifier
    return working_group, value


def validate_form(text, ctx):
    """Check only the fields and values needed to parse a submission.

    This is a functional check, not a frozen generated-file comparison. Source
    text, Markdown sections, placeholders, defaults and order are editable.
    Codes and IDs, not display descriptions, determine answer meaning.
    """
    spec = parse_yaml(text)
    if not isinstance(spec, dict) or not isinstance(spec.get("body"), list):
        raise Invalid("Submission form must contain a YAML body list")
    labels = spec.get("labels", [])
    if isinstance(labels, str):
        labels = [label.strip() for label in labels.split(",")]
    if not isinstance(labels, list) or LABEL not in labels:
        raise Invalid("Submission form must apply the new-project label")
    fields, identifiers = {}, set()
    reverse = label_keys(ctx)
    for item in spec["body"]:
        if not isinstance(item, dict):
            raise Invalid("Submission form fields must be mappings")
        if item.get("type") == "markdown":
            continue
        identifier = item.get("id")
        if identifier is not None:
            if not isinstance(identifier, str) or identifier in identifiers:
                raise Invalid("Submission form control IDs must be unique strings")
            identifiers.add(identifier)
        attributes = item.get("attributes")
        label = attributes.get("label") if isinstance(attributes, dict) else None
        key = reverse.get(label) if isinstance(label, str) else None
        if key is None or key in fields:
            raise Invalid("Submission form must keep each parser field label exactly once")
        fields[key] = item
    expected = FIELD_LABELS if "name" in fields else current_labels(ctx)
    if set(fields) != set(expected):
        raise Invalid("Submission form fields must match the parser; update its field definitions when adding/removing fields")
    for key, item in fields.items():
        attributes = item.get("attributes")
        if item.get("type") not in {"input", "textarea", "dropdown"}:
            raise Invalid(f"Submission field {key} must produce a text answer")
        if item["type"] != "dropdown":
            continue
        options = attributes.get("options")
        if not isinstance(options, list) or not options or any(not isinstance(x, str) for x in options):
            raise Invalid(f"Dropdown {key} needs text options")
        default = attributes.get("default")
        if default is not None and (type(default) is not int or not 0 <= default < len(options)):
            raise Invalid(f"Dropdown {key} has an invalid default index")
        for value in options:
            if key == "working_group" and option_value(value) not in ctx.working_groups:
                raise Invalid(f"Unknown working-group option: {value}")
            if key == "deliverable_ids":
                if "," in value:
                    raise Invalid("Deliverable options cannot contain commas; GitHub uses them to separate selections")
                group, identifier = deliverable_value(value, None)
                valid = identifier in ctx.deliverables.get(group, set()) if group else any(identifier in ids for ids in ctx.deliverables.values())
                if not valid:
                    raise Invalid(f"Unknown deliverable option: {value}")
            if key == "kind" and option_value(value) not in ctx.policy["kinds"]:
                raise Invalid(f"Unknown project-kind option: {value}")
            booleans = {"Yes", "No"} if "name" in fields else {"True", "False"}
            if key == "phd_thesis" and value not in booleans:
                raise Invalid(f"Unsupported PhD-thesis option: {value}")
    return spec


def matches_form(text, ctx):
    """Compatibility wrapper; validity no longer means identical formatting."""
    try:
        validate_form(text, ctx)
    except (Invalid, ValueError, TypeError):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check fields and option IDs needed by submission; ignore display text and layout")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        path = args.root / ".github/ISSUE_TEMPLATE/new-project.yml"
        ctx = load_local_context(args.root)
        if args.check:
            if not path.is_file():
                raise Invalid("Submission form is missing")
            validate_form(path.read_text(encoding="utf-8"), ctx)
            print("Issue form has the fields and values needed by submission.")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated_text(ctx), encoding="utf-8")
            print("Generated .github/ISSUE_TEMPLATE/new-project.yml")
    except (Invalid, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
