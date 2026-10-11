# Collaboration project registry — stage 1

This is the initial contents of one repository readable by the collaboration.
It stores project records, validates PRs, and will host its own Wiki. It can
remain private on GitHub with collaboration Read access; it is not an
SC-confidential space. The repository starts with **no projects**.
Examples in `templates/` are not registered projects.

Start with [setup and testing](docs/setup-and-test.md). Point CODEOWNERS at your
existing SC team and configure a read-only team lookup credential. Membership
is maintained exclusively in GitHub; no duplicate SC roster is stored.

This stage supports **SC-authored PRs from branches in this repository**.
The separate workflow that authenticates ordinary members and creates PRs
will be connected later. The same repository's Wiki is reserved for that later
step; Wiki automation is not implemented yet.

Every proposal file, PR description, review comment, commit and Actions log is
visible to repository readers. Include only collaboration-appropriate content.
Handle confidential discussion outside this repository.

## Current GitHub Actions workflows

Two workflows are installed. They validate records and permissions; SC members
review and merge PRs themselves. Neither workflow creates PRs, merges changes,
publishes Wiki pages, or sends notifications.

| Workflow | File | Runs when | Result |
| --- | --- | --- | --- |
| **Project review** | `.github/workflows/project-policy.yml` | A PR targeting `main` is opened, updated, reopened, edited, or marked ready for review | The **Project policy** check passes or fails |
| **Registry health** | `.github/workflows/registry-health.yml` | A commit reaches `main`, or an administrator starts it manually (choose `main`) | The accepted registry, team access and regression tests pass or fail |

### Project review: validate a proposed change

The **Project policy** job:

1. Checks out the PR's exact base commit and installs its dependencies.
2. Fetches the PR's author and commit IDs from GitHub. Only direct SC-authored
   PRs from a branch in this repository are accepted in this stage.
3. Reads the SC team reference from the accepted CODEOWNERS file and verifies
   that the original PR author is an active member of that team.
4. Reads proposed project/configuration files through the GitHub API as data.
   It checks required fields, names and usernames, category choices,
   deliverables, project IDs, assignment rules and all proposed registry records.
5. Checks that the PR commits and the author's SC membership remain valid at
   the end of validation, then runs regression tests from the base commit.

Proposed scripts, tests, dependencies and workflows are not executed by this
check. Review infrastructure changes carefully and test their proposed code
locally before merging. A passing check means the proposal is valid; it is
not acceptance of the project and does not verify the scientific content.

To enforce the result, protect `main` with the required **Project policy**
status check and required Code Owner approval, with bypass disabled. CODEOWNERS
alone requests review; repository settings make that review mandatory.
Follow [setup and testing](docs/setup-and-test.md) for the complete settings.

### Registry health: validate the accepted state

This workflow validates every accepted project against the current schema and
deliverable catalogues, checks that the configured SC team is accessible,
runs regression tests, and builds `build/projects.json` and
`build/working_groups.yaml` on the runner.
These are temporary verification outputs: they are not committed, uploaded
as an artifact, or published. Generate it locally using the commands below
when you want to search or export the registry.

The health workflow runs accepted code after a merge. A failure reports a
problem; it does not undo the merge. It is not a required PR check because
it normally runs after changes reach `main`.

### Credentials and visibility

The built-in `GITHUB_TOKEN` has repository read permissions. Both workflows
also use the repository Actions secret **`SC_TEAM_READ_TOKEN`**, configured
with organization **Members: Read-only**. The secret is needed for team
lookups, not for granting SC people repository access. SC authority is checked
using the existing GitHub team; there is no separate membership list here.
Missing credentials, failed lookups and expired tokens fail the checks.

Keep ordinary members at Read access and trust anyone with Write/Admin access
to manage workflows and secrets. The token's organization membership access
is broader than the single SC check, so grant no unnecessary permissions.
Records and logs must contain only collaboration-appropriate information.

### Planned workflows

Ordinary-member submission, authenticated claims and updates, PR creation,
Wiki publication, and notifications are later steps. The policy engine already
supports local simulations of member rules, but this starter does not expose
a member submission form or authenticate bot-created PRs. See
[the future intake contract](docs/future-intake.md).

## Project records

One file per project: `projects/P001.yaml`. YAML is convenient for a long
summary and easily loaded by a computer. A stable ID separates identity from
the name, which may change. IDs are reserved when the PR is opened; use the
next unused `P001`, `P002`, etc. Do not reuse an accepted project's ID.

| Field | Representation | Rules |
| --- | --- | --- |
| `schema_version` | Integer, currently `1` | Administrative format version |
| `project_id` | String, e.g. `P001` | Stable, unique filename; cannot be changed |
| `name` | String | Required, nonempty |
| `lead` | One person's name or `null` | Required field; EP requires a named lead |
| `lead_github_username` | One GitHub username or `null` | Required field; both lead fields must be set together or both null |
| `contributors` | List of `{name, github_username}` entries | May be empty; both fields are required for each person; the lead is not repeated |
| `summary` | Long Markdown string | Required; use a YAML `\|` literal block |
| `kind` | `EP`, `CPP`, `KEY` | Enhancing / critical path / key project |
| `status` | `active`, `paused`, `IR`, `completed` | `IR` means internal review of the work |
| `working_group` | A code from `working_groups/<CODE>.yaml` | One working group per project; allowed codes come from directory contents |
| `deliverable_ids` | List of strings | May be empty; IDs must exist in that group's catalogue |
| `github_url` | Repository HTTPS URL or `null` | Optional; can be omitted and added later |

Names are human-readable labels and may contain spaces and accented characters.
GitHub usernames are separate, **without `@`**, and are used for authorization
and duplicate checks. Identity comparisons are case insensitive. Different
people may have the same name, but the same account cannot appear twice or
as both lead and contributor. Each contributor's name and username stay in
one entry, avoiding mismatches between separate parallel lists.

Username syntax is checked; this stage does not look up whether the project
lead/contributor accounts exist, belong to the organization, or match their
declared names. SC reviewers must confirm those details. SC authorization
for the PR author is separately verified through the live team lookup.
Do not include email addresses or other private personal information.

```yaml
schema_version: 1
project_id: P001
name: Weak lensing shape validation
lead: Alice Example
lead_github_username: alice
contributors:
  - name: Bob Example
    github_username: bob
summary: |
  Validate the shape-measurement pipeline against controlled simulations.

  This description may contain paragraphs, Markdown lists, and equations.
kind: EP
status: active
working_group: SCM
deliverable_ids: []
github_url: null
```

The 1 MiB file limit is an operational limit, not a short-summary requirement.
Only known fields are allowed, so a typo cannot silently create a new category.

## Working groups and deliverables

Each file in `working_groups/` defines one working group, its description,
and its deliverables. For example, `working_groups/SCM.yaml`:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Calibration
  - id: SCM-2
    title: Image pre-processing
```

The filename must match the group code. Use uppercase letters, digits,
underscores or hyphens, starting with a letter. Descriptions may be long YAML
literal blocks. Each deliverable needs a nonempty, unique `id` within its group
and a nonempty `title`; IDs do not need to start with the group code.
`deliverables: []` is allowed. Different groups may reuse an ID: the project's
`working_group` identifies which catalogue applies.

Projects store the selected group code and its deliverable IDs, not a copied
list of groups. The validator discovers all group files from the proposed
repository directory and checks every project against them. There is no
hard-coded list of working groups in the project schema or Python code.

Adding a group requires only a new file such as `working_groups/NEW.yaml`;
adding a deliverable requires only another entry in that group's file.
SC reviews these administrative changes. Removing/renaming a group or used
deliverable requires migrating affected projects in the same PR.

Generate a single combined YAML file when needed:

```bash
python -m scripts.project_registry working-groups --output build/working_groups.yaml
```

The per-group files are the source. The combined YAML is generated, so there
is no second hand-maintained catalogue. The JSON project export also includes
the group descriptions and deliverables in a top-level `working_groups` list.
CAS and CPIP currently use their codes as descriptions; fill in their official
descriptions in the corresponding files when available.

See [working-group configuration and migration](docs/working-groups.md).

## Authorization rules

| Operation | Ordinary organization member | Project lead | Contributor | SC |
| --- | --- | --- | --- | --- |
| Declare EP | Yes, with themselves as lead | Yes | Yes | Yes, may name another lead |
| Declare CPP/KEY | No | No | No | Yes, including both lead fields null and `contributors: []` |
| Claim unassigned CPP | Yes, as themselves | Yes | Yes | Yes |
| Assign a KEY lead | No | No | No | Yes |
| Change project kind or transfer lead | No | No | No | Yes |
| Add/remove contributors | No | Yes | No | Yes |
| Correct the lead's display name | No | Yes | No | Yes |
| Edit summary, status, deliverables, GitHub URL | No | Yes | Yes | Yes |
| Edit project name or working group | No | Yes | No | Yes |
| Change schema, working-group definitions, deliverables, or policy | No | No | No | Yes |

The member-facing editing rules are defaults inferred from the
overall design. Their editable field lists are configurable in
`config/policy.yaml`. This implements the requested ability to extend the
contributor list while protecting lead assignment and project classification.

A **claim** fills both lead fields for an unassigned CPP, using the
authenticated requester's GitHub username and their supplied display name.
If that person is already a contributor, they are removed from the contributor
list because the lead is implicit. A claim does not rewrite the proposal or
add other people. Other updates happen separately. Claimed CPPs cannot be
claimed again. KEY projects require SC lead assignment.

The engine implements these rules now, but ordinary-member actions are only
**local policy simulations** until the authenticated submission bridge exists.
In the current GitHub workflow, SC-authored PRs are SC decisions, including
when SC enters a proposal on behalf of someone else. A name or username in YAML
does not establish who submitted a request.

## Acceptance and rejection

- Open PR: proposed new project, claim, update, or administrative change.
- Merge into `main`: accept the proposal/change and store its current record.
- Close without merging: reject/withdraw; the PR remains collaboration-readable.
- `projects/` on `main`: accepted registry, including unassigned SC declarations.
- An unassigned CPP can have `status: active`; availability is indicated by
  `lead: null` and `lead_github_username: null`, not a second use of the status field.
- Keep completed project records. Deletion is rejected to retain history.

Proposal review is distinct from `status: IR`. Do not add `pending` or
`rejected` to the project status to represent the PR decision. Git history and
PRs retain previous records and review decisions. Acceptance dates/submission
provenance are not fabricated in editable YAML; the future intake/acceptance
workflow can store them as trusted administrative information.

## Repository layout

```text
.github/
  CODEOWNERS
  pull_request_template.md
  workflows/
    project-policy.yml         # Trusted-base, read-only PR validation
    registry-health.yml        # Validation/tests after merging into main
config/
  policy.yaml                  # Creation/claim rules and editable fields
working_groups/
  SCM.yaml                     # Group code, description and deliverables
  CAS.yaml
  CPIP.yaml
schema/
  project.schema.json          # Fields, types, choices, required fields
projects/
  .gitkeep                    # No projects registered initially
templates/
  project.yaml
  unassigned-cpp.yaml
scripts/
  project_registry.py          # Validation, policy, JSON catalogue, simulation
  validate_pr.py               # GitHub read-only adapter
tests/
  test_registry.py
  test_github_adapter.py
  test_team_directory.py
  test_working_groups.py
docs/
  setup-and-test.md
  future-intake.md
  working-groups.md
requirements.txt
README.md
```

## Search and export

From the repository root:

```bash
python -m scripts.project_registry validate
python -m scripts.project_registry catalog --output build/projects.json
python -m scripts.project_registry working-groups --output build/working_groups.yaml
```

`build/projects.json` is generated, not committed. It contains a `projects`
array with all accepted project records and a `working_groups` array with
descriptions and deliverables. It supports ordinary JSON tools:

```bash
jq '.projects[] | select(.working_group == "SCM" and .status == "active")' build/projects.json
jq '.projects[] | select(.kind == "CPP" and .lead == null)' build/projects.json
jq '.projects[] | select(.deliverable_ids | index("SCM-1"))' build/projects.json
jq '.projects[] | select((.lead_github_username // "" | ascii_downcase) == "alice" or any(.contributors[]; (.github_username | ascii_downcase) == "alice"))' build/projects.json
```

`jq` is optional. Python can read either the YAML files or the JSON export.
The catalogue has the repository's collaboration visibility. Publish outside
the collaboration only if you intentionally choose to make the content public.

## Changing categories later

Use an SC administrative PR. The PR validates **all projects** against the
proposed schema/catalogues, so incompatible changes must migrate records in
the same PR.

- Add/change a status: edit `properties.status.enum` in the schema.
- Add a working group: add `working_groups/<GROUP>.yaml` with its code,
  description and deliverables. No schema or code change is needed.
- Add/change a deliverable: edit its working-group catalogue. Removing a used
  ID requires updating the affected project records in the same PR.
- Add a kind: edit `properties.kind.enum` and define its three explicit rules
  under `kinds` in the policy. Unknown kinds have no implicit permission.
- Add a field: add a property to the JSON Schema. Start with an optional field
  so existing records still validate. Add it to the relevant editable field
  lists if members should be able to change it.
- Make a field required: add it to `required` and populate every existing
  record in the same PR. The fixed core fields remain required.
- Change schema format version: change the schema's `schema_version.const`
  and migrate every record in an SC PR. Project IDs remain immutable.

For example, add this property to support optional project tags:

```json
"tags": {"type": "array", "uniqueItems": true, "items": {"type": "string"}}
```

Add `tags` to `lead_editable_fields` if leads should maintain it. Changes of
this kind do not require rewriting the validator. More complex new behavior,
such as multiple working groups or a different identity system, requires an
explicit policy/model change.

## Trust boundary

The `Project policy` workflow executes only code and dependencies from the
PR's exact base commit. Proposed YAML/JSON are obtained as Git blobs through
the GitHub API and inspected as data. Proposed Python/workflows are never run
by that check. The normal Actions token has only Contents Read and Pull Requests
Read; it cannot merge, change files, or publish anything. A separate
`SC_TEAM_READ_TOKEN` secret supplies organization Members Read permission for
team lookups; no repository write or membership write permission is needed.
Never commit its value.

SC authority comes from a **live lookup** of active GitHub team membership
for the GitHub API's PR author. The identifier is read from the **base**
CODEOWNERS rule, not the proposed rule. Event senders, rerun actors, PR bodies
and YAML fields cannot establish SC identity or self-authorize a PR.

The same single CODEOWNERS reference drives native GitHub review and the live
team check. Add/remove people in the existing GitHub team; no repository edits
are needed. Membership is checked on each run and again at its end. Later
membership edits do not retroactively revoke completed checks; rerun open PR
checks after team changes.

For infrastructure/code PRs, run the proposed tests locally and review the
changes before merging: this PR workflow runs the **base** regression tests,
not the proposed code. The health workflow runs accepted code after merge.
SC members with Write access and organization owners remain trusted repository
administrators; YAML validation cannot constrain an administrator who changes
repository settings or bypasses protection.

Official GitHub references:

- [Organization base permissions](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/setting-base-permissions-for-an-organization)
- [Protected branches and plan availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [Secure use of pull_request_target](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)
- [Required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
- [Team lookup and Members Read permission](https://docs.github.com/en/rest/teams/members#get-team-membership-for-a-user)

