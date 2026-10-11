"""Generate the GitHub form from the registry's working-group catalogue."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

import yaml

from scripts.project_registry import Invalid, load_local_context

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


def form(ctx):
    def field(key, kind="input", required=False, **attributes):
        return {"type": kind, "id": key,
                "attributes": {"label": CURRENT_FIELD_LABELS[key], **attributes},
                "validations": {"required": required}}
    def heading(text):
        return {"type": "markdown", "attributes": {"value": text}}
    kinds = list(ctx.policy["kinds"])
    body = [heading(
        "Use **Add a title** above for your project title. Submit collaboration-appropriate information only; "
        "all answers are visible to repository readers. Ordinary members may propose EP projects with themselves "
        "as lead. Only SC members may declare CPP or KEY. After submission, an acknowledgment and then a "
        "validation result will appear as comments. To correct errors, edit the Issue title or description and save. "
        "Keep the field headings. No email address is requested."),
        field("kind", "dropdown", True, options=kinds, default=kinds.index("EP") if "EP" in kinds else 0),
        field("lead", description="Required for EP."),
        field("lead_github_username", description="One GitHub username without @. Ordinary members must enter their own username."),
        field("working_group", "dropdown", True, options=list(group_options(ctx))),
        field("deliverable_ids", "dropdown", True, options=deliverable_options(ctx), multiple=True,
              description="Select deliverables from your chosen working group."),
        field("phd_thesis", "dropdown", True, options=["False", "True"], default=0),
        field("slack_channel", description="Optional existing channel name, such as #scm-shapes. Can be added later."),
        field("summary", "textarea", True, description="Describe the goals, approach and expected results. Markdown is supported."),
        field("github_url", description="Optional https://github.com/OWNER/REPOSITORY URL. Accessible repositories are checked; private/inaccessible ones are flagged for review."),
        field("related_links", "textarea", description="Optional. One HTTPS URL per line. Do not include credentials or secret sharing tokens.", render="text"),
        heading("## Contributors\n\nList up to five contributors, excluding the project lead. Leave unused contributor "
                "sections blank. Additional contributors can be added to the project later."),
    ]
    for i in range(1, CONTRIBUTOR_LIMIT + 1):
        body += [heading(f"### Contributor {i} — Optional"),
                 field(f"contributor_{i}_name", placeholder="Jane Smith"),
                 field(f"contributor_{i}_github_username", placeholder="janesmith", description="GitHub username without @. Fill both boxes or leave both blank.")]
    return {"name": "New Project", "description": "Propose a project for review.", "labels": [LABEL], "body": body}


def generated_text(ctx, *, legacy=False):
    return "# Generated by python -m scripts.issue_form; edit working_groups/ instead.\n" + yaml.safe_dump(
        legacy_form(ctx) if legacy else form(ctx), sort_keys=False, allow_unicode=True, width=110)


def matches_form(text, ctx):
    # Both exact generated versions are safe. This lets the trusted-base
    # validator review the form migration and keeps older open Issues editable.
    return text in (generated_text(ctx), generated_text(ctx, legacy=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the committed form differs from the catalogue")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        path = args.root / ".github/ISSUE_TEMPLATE/new-project.yml"
        output = generated_text(load_local_context(args.root))
        if args.check:
            if not path.is_file() or not matches_form(path.read_text(encoding="utf-8"), load_local_context(args.root)):
                raise Invalid("Regenerate the form with python -m scripts.issue_form and include it in this change")
            print("Issue form matches the working-group catalogue.")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
            print("Generated .github/ISSUE_TEMPLATE/new-project.yml")
    except (Invalid, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
