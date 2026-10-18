# Project automation and documentation

This Python package parses Issue forms, enforces registry policy, creates and
updates proposal PRs, replays comment commands and regenerates GitHub files.

| Task | Guide |
| --- | --- |
| Submit, claim, edit or withdraw a project | [Submission guide](SUBMISSIONS.md) |
| Set up or audit GitHub, including existing settings | [Repository installation](INSTALLATION.md) |
| Understand implementation and workflow architecture | [Architecture](ARCHITECTURE.md) |
| Change, test, troubleshoot or recover the system | [Maintenance](MAINTENANCE.md) |
| Install the optional Slack integration | [Slack installation](../slack/INSTALLATION.md) |
| Start an LLM maintenance task | [AGENTS.md](../AGENTS.md) |

Related directory guides: [GitHub integration](../.github/OVERVIEW.md),
[workflow operations](../.github/workflows/README.md), [Slack](../slack/README.md).
Data formats are documented below and in architecture; `projects/` and
`working_groups/` contain data only.

## Configuration and generated files

Edit [config.yaml](config.yaml) for schema, form wording, project-kind rules,
reviewer routing and SC team identifier. Edit root `working_groups/<CODE>.yaml`
for catalogues. Accepted project records live in root `projects/<ID>.yaml`.

From the repository root:

```bash
python -m pip install -r project_workflow/requirements.txt
python -m project_workflow.generated_files
python -m project_workflow.issue_form --check
python -m project_workflow.project_registry validate
python -m project_workflow.project_registry export --output-dir build
```

`generated_files` writes all three native Issue forms and CODEOWNERS. The export
command validates once and writes `build/projects.json` and
`build/working_groups.yaml`. `issue_form --check` checks generated form content;
it does not publish anything. The schema uses local references only.

## Runtime entry points

| Entry point | Purpose |
| --- | --- |
| `issue_intake` | Submission, command dispatch, cancellation and rejection |
| `validate_pr --publish-status` | Check live PR data from its exact trusted base and publish `Project policy` |
| `validate_pr --check-team` | Check configured SC-team access |
| `sync_generated` | Regenerate main or an authorized SC PR; use a generated-file PR if main protection requires it |
| `generated_files` | Local generation without GitHub writes |
| `project_registry` | Local validation and catalogue exports |

The API entry points are intended for Actions with scoped credentials and event
context. Use [manual workflow operations](../.github/workflows/README.md) for
live maintenance rather than invoking them from an arbitrary checkout.

[setup/action.yml](setup/action.yml) selects Python 3.12 and caches pinned
dependencies. [policy/action.yml](policy/action.yml) handles exact-base PR
validation or a prevalidated submission's Slack handoff. The latter still checks
the live head's policy status before dispatching.

## Data formats

A working-group catalogue has `working_group`, `description` and `deliverables`:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Calibration
```

The example illustrates the format; existing files define the actual scientific
catalogue. The uppercase filename must match the group code. IDs are explicit:
the CPIP catalogue currently uses `CPI-...` IDs. `deliverables: []` is valid.
Adding a group requires its catalogue, not a parallel parser enum. Removing an
in-use group or deliverable requires updating affected projects in the same change.

Project YAML must satisfy the schema in `config.yaml`. Its stable `project_id`
matches the filename. Core fields include kind, status, working-group links,
people and Markdown summary. Kinds are `EP`, `CPP`, `KEY`; statuses are `active`,
`paused`, `IR`, `completed`. `IR` describes the project's work, not proposal
review. EP requires at least one lead; unassigned CPP/KEY may have none.
People are `{name, github_username}` objects. Optional fields cover Slack,
repository, related links and PhD information where applicable.

Generated `submission` and `claim` provenance must be retained. An accepted
claim remains recorded even if SC later removes its leads. Do not invent or
erase provenance to bypass policy. Prefer `status: completed` when retaining
retired project history. Accepted changes require the SC administrative route.

Tests live in [tests/](tests/). They are optional maintainer checks rather than
an additional mandatory CI suite. Select relevant coverage using the
[test matrix](MAINTENANCE.md#choosing-checks).
