"""Data validation and policy engine. No GitHub writes or network requests."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Callable, Iterable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class Invalid(ValueError):
    def __init__(self, message, *, fields=(), selections=None, corrections=None):
        super().__init__(message)
        self.fields = set(fields)
        self.selections = selections or {}
        self.corrections = corrections or {}


class StrictLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise Invalid("YAML mapping keys must be strings")
        if key in result:
            raise Invalid(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_yaml(text: str):
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise Invalid("YAML file exceeds the 1 MiB limit")
    try:
        depth = 0
        for event in yaml.parse(text):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None):
                raise Invalid("YAML anchors and aliases are not supported")
            if isinstance(event, yaml.events.CollectionStartEvent):
                depth += 1
                if depth > 40:
                    raise Invalid("YAML nesting exceeds 40 levels")
            elif isinstance(event, yaml.events.CollectionEndEvent):
                depth -= 1
        return yaml.load(text, Loader=StrictLoader)
    except yaml.YAMLError as exc:
        raise Invalid("Invalid YAML syntax or unsupported YAML tag") from exc


def utc_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise Invalid("Submission date must be an ISO 8601 UTC timestamp ending in Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise Invalid("Submission date is not a valid calendar date/time") from exc
    return value


USERNAME = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")
PROJECT_PATH = re.compile(r"projects/(P[0-9]{3,})\.yaml\Z")
GROUP_PATH = re.compile(r"working_groups/([A-Z][A-Z0-9_-]*)\.yaml\Z")
PROTECTED = {"project_id", "schema_version", "kind", "lead_github_username", "submission"}


def usernames(values, label):
    if not isinstance(values, list) or any(
        not isinstance(x, str) or len(x) > 39 or not USERNAME.fullmatch(x) for x in values
    ):
        raise Invalid(f"{label} must be a list of GitHub usernames without @")
    lowered = [x.lower() for x in values]
    if len(lowered) != len(set(lowered)):
        raise Invalid(f"{label} contains repeated usernames (case insensitive)")
    return set(lowered)


@dataclass
class Context:
    schema: dict
    policy: dict
    sc_team: tuple[str, str]
    deliverables: dict[str, set[str]]
    working_groups: dict[str, dict]
    review_owners: tuple[str, ...]
    form_source: dict


CONFIG_PATH = "project_workflow/config.yaml"


def load_context(read_text: Callable[[str], str], *, paths: Iterable[str]) -> Context:
    """Read settings directly from the single workflow config and WG catalogues."""
    paths = set(paths)
    if CONFIG_PATH not in paths:
        raise Invalid("Missing project_workflow/config.yaml")
    config = parse_yaml(read_text(CONFIG_PATH))
    if not isinstance(config, dict) or set(config) != {"steering_committee", "reviewers", "policy", "schema", "form"}:
        raise Invalid("Workflow config requires steering_committee, reviewers, policy, schema and form")
    if not isinstance(config["schema"], dict) or not isinstance(config["form"], dict):
        raise Invalid("schema and form must be mappings")
    policy, schema = config["policy"], config["schema"]
    if not isinstance(policy, dict) or set(policy) != {"kinds", "lead_editable_fields", "contributor_editable_fields"} or not isinstance(policy["kinds"], dict) or not policy["kinds"]:
        raise Invalid("Invalid policy configuration keys or kinds")
    def bind_choices(value):
        if isinstance(value, dict):
            if "enum_from" in value:
                if value.pop("enum_from") != "project_kinds" or "enum" in value:
                    raise Invalid("Schema enum_from must reference project_kinds without a second enum")
                value["enum"] = list(policy["kinds"])
            for item in value.values():
                bind_choices(item)
        elif isinstance(value, list):
            for item in value:
                bind_choices(item)
    bind_choices(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise Invalid("Invalid project JSON Schema") from exc

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value and not value["$ref"].startswith("#/"):
                raise Invalid("Schema references must be local; remote references are disabled")
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(schema)
    required = {"schema_version", "project_id", "name", "lead", "lead_github_username", "contributors", "summary",
                "kind", "status", "working_group", "deliverable_ids"}
    if schema.get("additionalProperties") is not False or not required <= set(schema.get("required", [])):
        raise Invalid("The schema must reject unknown fields and require all core fields")
    props = schema["properties"]
    if "default" in props["status"] and not Draft202012Validator(props["status"]).is_valid(props["status"]["default"]):
        raise Invalid("Initial project status must satisfy its schema constraints")
    if not isinstance(policy["kinds"], dict) or set(policy["kinds"]) != set(props["kind"]["enum"]):
        raise Invalid("Schema kinds and policy kinds must agree")
    rule_keys = {"member_can_create", "member_can_claim", "allow_unassigned"}
    for rules in policy["kinds"].values():
        if not isinstance(rules, dict) or not rule_keys <= set(rules) or set(rules) - rule_keys - {"description"}:
            raise Invalid("Every kind requires creation, claim and unassigned rules; description is optional")
        if any(type(rules[key]) is not bool for key in rule_keys):
            raise Invalid("Kind policy values must be booleans")
        if "description" in rules and (not isinstance(rules["description"], str) or not rules["description"].strip()):
            raise Invalid("Project-kind description must be nonempty text")
        if rules["member_can_claim"] and not rules["allow_unassigned"]:
            raise Invalid("A claimable kind must allow an unassigned lead")
    for label in ("lead_editable_fields", "contributor_editable_fields"):
        fields = policy[label]
        if not isinstance(fields, list) or any(not isinstance(x, str) for x in fields):
            raise Invalid(f"{label} must be a list of field names")
        if len(set(fields)) != len(fields) or not set(fields) <= set(props) or set(fields) & PROTECTED:
            raise Invalid(f"Invalid or protected field in {label}")
    if "contributors" in policy["contributor_editable_fields"]:
        raise Invalid("Contributors cannot manage project membership")
    if not set(policy["contributor_editable_fields"]) <= set(policy["lead_editable_fields"]):
        raise Invalid("Contributor fields must also be lead-editable")
    # Review routing and SC authorization are independent. GitHub owns the roster.
    owner_pattern = r"@[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)?"
    team = config["steering_committee"]
    if not isinstance(team, str) or not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", team):
        raise Invalid("Invalid steering_committee organization/team-slug")
    sc_team = tuple(team.split("/"))
    owners = config["reviewers"]
    if owners == "steering_committee":
        owners = ["/".join(sc_team)]
    if not isinstance(owners, list) or not owners or any(not isinstance(x, str) for x in owners):
        raise Invalid("reviewers must be a nonempty user/team list or steering_committee")
    review_owners = tuple("@" + x.removeprefix("@") for x in owners)
    if len(set(x.lower() for x in review_owners)) != len(review_owners) or any(not re.fullmatch(owner_pattern, x) for x in review_owners):
        raise Invalid("Review owners must be unique valid GitHub users or teams")
    if any("/" in x and x[1:].split("/")[0].lower() != sc_team[0].lower() for x in review_owners):
        raise Invalid("Review teams must belong to the SC organization")
    form_source = config["form"]
    group_paths = sorted(p for p in paths if p.startswith("working_groups/"))
    if not group_paths:
        raise Invalid("working_groups/ must contain at least one working-group YAML file")
    if "enum" in props["working_group"]:
        raise Invalid("Remove the working_group schema enum; working_groups/ defines the allowed groups")
    definitions = {}
    for path in group_paths:
        match = GROUP_PATH.fullmatch(path)
        if match is None:
            raise Invalid("working_groups/ must contain only <UPPERCASE-CODE>.yaml files")
        wg = match[1]
        data = parse_yaml(read_text(path))
        if not isinstance(data, dict) or set(data) != {"working_group", "description", "deliverables"} or data["working_group"] != wg:
            raise Invalid("Working-group definition must match its filename and contain description and deliverables")
        if not isinstance(data["description"], str) or not data["description"].strip():
            raise Invalid("Working-group description must be a nonempty string")
        definitions[wg] = data
    deliverables = {}
    for wg, data in definitions.items():
        if not isinstance(data["deliverables"], list):
            raise Invalid("Deliverables must be a list")
        ids = set()
        for item in data["deliverables"]:
            if not isinstance(item, dict) or set(item) != {"id", "title"} or any(
                not isinstance(item[key], str) or not item[key].strip() for key in ("id", "title")
            ):
                raise Invalid("Every deliverable requires a nonempty id and title")
            if item["id"] in ids:
                raise Invalid(f"Duplicate deliverable ID in {wg}")
            ids.add(item["id"])
        deliverables[wg] = ids
    return Context(schema, policy, sc_team, deliverables, definitions, review_owners, form_source)


def validate_record(record: dict, path: str, ctx: Context):
    match = PROJECT_PATH.fullmatch(path)
    if not match:
        raise Invalid("Project records must use projects/P<digits>.yaml")
    errors = list(Draft202012Validator(ctx.schema).iter_errors(record))
    if errors:
        # Do not echo invalid content into Actions logs or interpret it as commands.
        error = errors[0]
        location = ".".join(str(x) for x in error.absolute_path) or "record"
        raise Invalid(f"{path}: {location} fails schema constraint {error.validator}")
    if record["project_id"] != match[1]:
        raise Invalid("Project ID must match its filename")
    if not record["name"].strip() or not record["summary"].strip():
        raise Invalid("Name and summary cannot be whitespace")
    lead = record["lead_github_username"]
    if (lead is None) != (record["lead"] is None):
        raise Invalid("Lead name and GitHub username must both be set or both be null")
    if lead is not None:
        if not record["lead"].strip():
            raise Invalid("Lead name cannot be whitespace")
        usernames([lead], "Lead")
    if lead is None and not ctx.policy["kinds"][record["kind"]]["allow_unassigned"]:
        raise Invalid("This project kind requires a lead")
    if any(not person["name"].strip() for person in record["contributors"]):
        raise Invalid("Contributor names cannot be whitespace")
    roster = usernames([person["github_username"] for person in record["contributors"]], "Contributors")
    if lead is not None and lead.lower() in roster:
        raise Invalid("The lead is implicitly a project member; do not repeat them as a contributor")
    if record["working_group"] not in ctx.working_groups:
        raise Invalid("Working group must exist in working_groups/")
    unknown = set(record["deliverable_ids"]) - ctx.deliverables[record["working_group"]]
    if unknown:
        raise Invalid("Deliverable IDs must exist in the selected working group's catalogue")
    from project_workflow.submission import https_url
    for link in record.get("related_links", []):
        https_url(link)
    if isinstance(record.get("slack_channel"), str) and not record["slack_channel"].strip():
        raise Invalid("Slack channel cannot be whitespace")
    if "submission" in record:
        utc_timestamp(record["submission"]["submitted_at"])
    url = record.get("github_url")
    if url is not None and url.rstrip("/").rsplit("/", 1)[-1] in {".", ".."}:
        raise Invalid("GitHub URL must refer to a repository")


def authorize_change(old, new, actor: str, ctx: Context, *, organization_member: bool, is_sc: bool = False):
    """actor/member are trusted inputs from the caller, NEVER record fields.

    is_sc must come from a trusted live GitHub team lookup, not record content.
    The CLI only simulates these values. The GitHub adapter accepts
    direct SC-authored PRs and verified Issue submissions. The adapter must
    authenticate the original requester before calling this function.
    """
    actor = actor.lower()
    if new is None:
        raise Invalid("Keep project records; use status: completed instead of deletion")
    if old is not None and old["project_id"] != new["project_id"]:
        raise Invalid("Project ID is immutable")
    if is_sc:
        return
    if not organization_member:
        raise Invalid("Requester must be a verified organization member")
    if old is None:
        if not ctx.policy["kinds"][new["kind"]]["member_can_create"]:
            raise Invalid("Only SC members can declare this project kind")
        if new["lead_github_username"] is None or new["lead_github_username"].lower() != actor:
            raise Invalid("A member must submit a new project with themselves as its lead")
        return
    fields = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    if old["lead_github_username"] is None:
        rules = ctx.policy["kinds"][old["kind"]]
        if not rules["member_can_claim"] or new["lead_github_username"] is None or new["lead_github_username"].lower() != actor:
            raise Invalid("Only a claimable unassigned project can be claimed by a member")
        # Claiming does just one thing. Subsequent content changes are separate.
        expected_contributors = [x for x in old["contributors"] if x["github_username"].lower() != actor]
        if fields - {"lead", "lead_github_username", "contributors"} or new["contributors"] != expected_contributors:
            raise Invalid("A claim may only assign the lead and remove that person from contributors")
        return
    if old["lead_github_username"].lower() == actor:
        allowed = set(ctx.policy["lead_editable_fields"])
    elif actor in {x["github_username"].lower() for x in old["contributors"]}:
        allowed = set(ctx.policy["contributor_editable_fields"])
    else:
        raise Invalid("Requester is not the lead, a contributor, or an SC member")
    if fields - allowed:
        raise Invalid("This change modifies fields the requester cannot edit")


def local_reader(root: Path):
    root = root.resolve()
    def read(path):
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise Invalid("Data paths must remain inside the repository")
        target = root / relative
        if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise Invalid("Symlinked data files/directories are not supported")
        return target.read_text(encoding="utf-8")
    return read


def local_records(root: Path, ctx: Context):
    records = {}
    if not (root / "projects").is_dir() or (root / "projects").is_symlink():
        raise Invalid("projects/ must be a regular directory")
    for target in sorted((root / "projects").rglob("*")):
        path = target.relative_to(root).as_posix()
        if path == "projects/.gitkeep":
            continue
        if target.is_dir() or target.is_symlink() or not PROJECT_PATH.fullmatch(path):
            raise Invalid("projects/ must contain only ordinary project YAML files and .gitkeep")
        record = parse_yaml(local_reader(root)(path))
        validate_record(record, path, ctx)
        records[path] = record
    return records


def load_local_context(root: Path) -> Context:
    paths = [CONFIG_PATH] if (root / CONFIG_PATH).exists() else []
    directory = root / "working_groups"
    if directory.is_symlink():
        raise Invalid("Symlinked catalogue directories are not supported")
    if directory.exists():
        if not directory.is_dir():
            raise Invalid("working_groups/ must be a regular directory")
        paths.extend(p.relative_to(root).as_posix() for p in directory.rglob("*"))
    return load_context(local_reader(root), paths=paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    catalog = sub.add_parser("catalog")
    catalog.add_argument("--output", type=Path)
    groups = sub.add_parser("working-groups", help="Export one combined YAML catalogue from the working-group files")
    groups.add_argument("--output", type=Path)
    simulate = sub.add_parser("simulate", help="Local policy simulation; never authenticates anyone")
    simulate.add_argument("--actor", required=True)
    simulate.add_argument("--member", action="store_true", help="Assume verified org membership for simulation")
    simulate.add_argument("--sc", action="store_true", help="Assume active SC team membership for simulation only")
    simulate.add_argument("--old", type=Path)
    simulate.add_argument("--new", type=Path, required=True)
    args = parser.parse_args()
    try:
        ctx = load_local_context(args.root)
        if args.command == "simulate":
            new = parse_yaml(args.new.read_text())
            old = parse_yaml(args.old.read_text()) if args.old else None
            validate_record(new, f"projects/{new['project_id']}.yaml", ctx)
            if old is not None:
                validate_record(old, f"projects/{old['project_id']}.yaml", ctx)
            authorize_change(old, new, args.actor, ctx, organization_member=args.member, is_sc=args.sc)
            print("Allowed by policy (simulation only; no GitHub changes).")
        elif args.command == "working-groups":
            output = yaml.safe_dump({"working_groups": list(ctx.working_groups.values())}, sort_keys=False, allow_unicode=True)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output, encoding="utf-8")
            else:
                print(output, end="")
        else:
            records = local_records(args.root, ctx)
            if args.command == "validate":
                print(f"Registry valid: {len(records)} project(s).")
            else:
                output = json.dumps({"schema_version": 1, "working_groups": list(ctx.working_groups.values()), "projects": list(records.values())}, indent=2, ensure_ascii=False) + "\n"
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(output, encoding="utf-8")
                else:
                    print(output, end="")
    except (Invalid, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
