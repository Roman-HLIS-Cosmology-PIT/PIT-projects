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
CORRECTION_LABEL = "project-correction"
KEEP_SELECTION = "Keep previous selection"
KEEP_VALUE = "Keep previous value"
CORRECTION_FIELDS = {"original_issue": "Original submission Issue", "previous_issue": "Previous submission Issue",
                     "correction_token": "Correction reference"}
BRANCH_PREFIX = "project-submissions/issue-"
BOT_LOGIN = "github-actions[bot]"
BOT_ID = 41898282
CORE_FIELDS = {"kind", "lead", "lead_github_username", "working_group", "deliverable_ids",
               "phd_thesis", "slack_channel", "summary", "github_url", "related_links"}


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


def current_labels(ctx):
    source = ctx.form_source
    if not isinstance(source, dict) or not isinstance(source.get("body"), list):
        raise Invalid("Form source must contain a body list")
    core = CORE_FIELDS
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


def label_keys(ctx, *, correction=False):
    labels = current_labels(ctx)
    if correction:
        labels.update(CORRECTION_FIELDS)
        labels.update({"previous_" + item["id"]: "Previous selection — " + item["attributes"]["label"]
                       for item in ctx.form_source["body"] if item.get("type") == "dropdown"})
    if len(set(labels.values())) != len(labels):
        raise Invalid("Form answer labels must be unique")
    return {label: key for key, label in labels.items()}


def form(ctx, *, correction=False):
    label_keys(ctx)
    spec = deepcopy(ctx.form_source)
    choices = {"project_kinds": kind_options(ctx),
               "working_groups": list(group_options(ctx)),
               "deliverables": deliverable_options(ctx)}
    bindings = {"kind": "project_kinds", "working_group": "working_groups", "deliverable_ids": "deliverables"}
    for item in spec["body"]:
        source = item.pop("options_from", None)
        if item.get("id") in bindings and source != bindings[item["id"]]:
            raise Invalid("Kind, working group and deliverable options must come from their authoritative sources")
        if source is not None:
            if bindings.get(item.get("id")) != source or "options" in item.get("attributes", {}):
                raise Invalid("options_from must reference the field's catalogue without duplicate options")
            item["attributes"]["options"] = choices[source]
    if correction:
        spec["name"] = "Correct or Edit Project"
        spec["description"] = "Replace a submission using the link provided by the automation."
        spec["labels"] = [CORRECTION_LABEL]
        controls = [{"type": "input", "id": key, "attributes": {"label": label},
                     "validations": {"required": True}} for key, label in CORRECTION_FIELDS.items()]
        body = [{"type": "markdown", "attributes": {"value":
            "Use the correction link from your submission. Keep the three reference fields unchanged. "
            "For dropdowns, keep the previous selection or choose replacement values. "
            "An empty optional text field clears that answer. A field containing **Keep previous value** "
            "retains its earlier answer. Previous-selection boxes are for reference only."}}, *controls]
        for item in spec["body"]:
            if item.get("type") == "markdown" and item is spec["body"][0]:
                continue
            if item.get("type") == "dropdown":
                key = item["id"]
                body.append({"type": "textarea", "id": "previous_" + key,
                             "attributes": {"label": "Previous selection — " + item["attributes"]["label"]},
                             "validations": {"required": False}})
                item["attributes"]["options"].insert(0, KEEP_SELECTION)
                item["attributes"]["default"] = 0
            body.append(item)
        spec["body"] = body
    validate_form(yaml.safe_dump(spec), ctx, correction=correction)
    return spec


def generated_text(ctx, *, correction=False):
    return "# Generated from project_workflow/config.yaml and working_groups/; do not edit this output.\n" + yaml.safe_dump(
        form(ctx, correction=correction), sort_keys=False, allow_unicode=True, width=110)


def option_value(value):
    """Read the stable code/ID before an optional display description."""
    return value.split("—", 1)[0].split(" - ", 1)[0].strip()


def deliverable_value(value, working_group):
    value = option_value(value)
    if "/" in value:
        group, identifier = (part.strip() for part in value.split("/", 1))
        return group, identifier
    return working_group, value


def validate_form(text, ctx, *, correction=False):
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
    required_label = CORRECTION_LABEL if correction else LABEL
    if not isinstance(labels, list) or required_label not in labels:
        raise Invalid("Submission form must apply the " + required_label + " label")
    fields, identifiers = {}, set()
    reverse = label_keys(ctx, correction=correction)
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
    expected = {key: label for label, key in reverse.items()}
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
            if correction and value == KEEP_SELECTION:
                continue
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
            booleans = {"True", "False"}
            if key == "phd_thesis" and value not in booleans:
                raise Invalid(f"Unsupported PhD-thesis option: {value}")
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check fields and option IDs needed by submission; ignore display text and layout")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        ctx = load_local_context(args.root)
        for filename, correction in (("new-project.yml", False), ("correct-project.yml", True)):
            path = args.root / ".github/ISSUE_TEMPLATE" / filename
            if args.check:
                if not path.is_file():
                    raise Invalid("Submission form is missing")
                validate_form(path.read_text(encoding="utf-8"), ctx, correction=correction)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(generated_text(ctx, correction=correction), encoding="utf-8")
                print("Generated " + str(path.relative_to(args.root)))
        if args.check:
            print("Both Issue forms have the fields and values needed by submission.")
    except (Invalid, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
