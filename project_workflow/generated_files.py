"""Render repository projections from their authoritative data sources."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from project_workflow.issue_form import FORM_PATHS, generated_text
from project_workflow.project_registry import CONFIG_PATH, Invalid, load_local_context, parse_yaml

OUTPUTS = set(FORM_PATHS.values()) | {".github/CODEOWNERS"}


def relevant(path):
    return path == CONFIG_PATH or path in OUTPUTS or path.startswith("working_groups/")


def render(ctx):
    return {
        **{path: generated_text(ctx,kind) for kind,path in FORM_PATHS.items()},
        ".github/CODEOWNERS": "# Generated from project_workflow/config.yaml; do not edit this output.\n* " + " ".join(ctx.review_owners) + "\n",
    }


def equivalent(path, actual, expected):
    if path.endswith("CODEOWNERS"):
        def rules(text):
            rows = [line.split("#", 1)[0].split() for line in text.splitlines()]
            return [row for row in rows if row]
        return rules(actual) == rules(expected)
    return parse_yaml(actual) == parse_yaml(expected)


def updates_for(read, paths, ctx):
    updates = {}
    for path, expected in render(ctx).items():
        same = False
        if path in paths:
            actual = read(path)  # Reject symlinks/executables, even when repairing output.
            try:
                same = equivalent(path, actual, expected)
            except (Invalid, ValueError, TypeError):
                pass
        if not same:
            updates[path] = expected
    return updates


def validate_outputs(read, paths, ctx):
    if CONFIG_PATH in paths and updates_for(read, paths, ctx):
        raise Invalid("Generated files need refreshing; maintenance updates this PR automatically")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        for path, text in render(load_local_context(args.root)).items():
            target = args.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print("Generated " + path)
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
