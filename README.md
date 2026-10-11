# Collaboration project registry

This repository stores searchable project YAML records and supports **New Project
Issue submissions** from organization members with Read access. GitHub Actions
validates proposals and opens PRs; Steering Committee members review and merge
them. The same repository will host its Wiki in a later step.

[**Submit a project**](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

Start with [submission setup and testing](docs/project-submissions.md), alongside
[repository permissions and branch protection](docs/setup-and-test.md). The SC
roster is maintained only in GitHub through the team identified in `config/access.yaml`.
During testing, `.github/CODEOWNERS` requests review only from `aguinot`.

All Issues, project records, reviews and logs are visible to repository readers.
Include only collaboration-appropriate content. No email field is collected.

## Current workflows

| Workflow | Trigger | Result |
| --- | --- | --- |
| **New project submission** (`new-project.yml`) | A labelled New Project Issue is created/edited/reopened/closed or labelled/unlabelled; manual retry on main | Verify the actual Issue author's membership and project policy; comment on errors; create/update one YAML proposal PR |
| **Project review** (`project-policy.yml`) | PR events targeting main; explicit dispatch for automated PRs | Validate data using trusted-base code; publish required **Project policy** status on the actual PR head |
| **Record project acceptance** (`project-accepted.yml`) | Creation PR merged into main; manual backfill/retry | Open one metadata PR storing the original merge timestamp, creation PR number and merger; never push main or auto-merge |
| **Registry health** (`registry-health.yml`) | Push to main or manual run | Validate accepted records, catalogue/form consistency, live SC team access, regression tests and machine-readable exports |

### Submission and correction

The native Issue title is the project title; no prefix is required. The form
collects kind, lead name/username, working group and deliverables with full
labels, PhD-thesis status (False by default), Slack channel, description,
optional main repository and related links. **Contributors** is the final
section, with five optional name/username pairs in numbered subsections.
Leave both boxes blank or complete both. The stored contributor list has no
five-person limit; an SC PR can add more later (member update intake is future work).
Ordinary members create EP projects with themselves as lead. SC can declare
CPP/KEY, including unassigned projects.

After Actions starts, an acknowledgment comment confirms receipt. A separate
feedback comment reports validation errors or links the PR. Correct the Issue
title or select **⋯ → Edit** on the original description, keep the field headings,
and save. Validation reruns, updates the formatted PR summary and reuses the
same PR. Invalid edits block its policy status. During testing, required Code
Owner review is from `aguinot`. Merging the creation PR closes the Issue.
The workflow never approves or merges a PR.

The merge then starts **Record project acceptance**, which opens a separate
review PR adding an optional `acceptance` object to the project YAML. Review
and merge that PR to retain the original acceptance time in the registry. The
metadata PR does not change project content and cannot create another metadata
PR. Earlier creation PRs can be backfilled with a manual run on `main`.

Working-group/dropdown choices are generated from `working_groups/`. After
changing a catalogue, run `python -m scripts.issue_form` and include the updated
form in the same SC PR. GitHub's native form does not provide cascading
working-group/deliverable dropdowns; their pairing is checked after submission.

### Review and trust

Direct SC-authored PRs remain supported. Automated proposal PRs are accepted
only from GitHub Actions on their reserved Issue branch, changing one new
project file that exactly matches the current verified Issue. A bot is never
considered an SC member. Submitted names, usernames, labels and PR descriptions
do not authenticate a requester.

The policy workflow checks out trusted base code and reads proposed Git blobs
as data. It never executes proposed scripts, tests, dependencies or workflows.
For an automated proposal it rechecks the live Issue and original author;
for a direct PR it rechecks the SC author. A passing check validates the data
and permissions; the configured Code Owners assess scientific content and acceptance.
Acceptance metadata PRs are checked against GitHub’s actual original merge,
with every other project field required to match the current accepted record.

Protect main with required **Project policy** status and Code Owner approval,
dismissed stale reviews, up-to-date branches and no bypass. The workflow job
`Validate project policy` is separate from the required head status.

### Credentials and later work

The existing **SC_TEAM_READ_TOKEN** supplies organization Members Read access
for membership lookups. It is used only with organization API endpoints.
The submission and acceptance workflows' built-in tokens write their branches/PRs/statuses (submission also writes Issue feedback)
and dispatches independent validation. The policy token reads repository data
and writes only commit statuses. Registry health has repository read access.
No external backend, email verification, fork or additional sign-in is used.

Member claims/updates for already accepted projects, Wiki publication and
notifications remain later steps. The policy engine can simulate claim/update
rules locally. See [the submission guide](docs/project-submissions.md) for
installation, security boundaries, retries and GitHub tests.

## Project records

One file per project: `projects/P001.yaml`. YAML is convenient for a long
summary and easily loaded by a computer. A stable ID separates identity from
the name, which may change. IDs are reserved when the PR is opened; use the
next unused short manual ID (`P001`, `P002`, etc.). Issue submissions reserve zero-padded IDs such as `P000012` for Issue #12. Do not reuse accepted IDs.

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
| `phd_thesis` | Boolean | Required for Issue submissions; optional in older records |
| `slack_channel` | Channel name or `null` | Optional |
| `related_links` | List of HTTPS URLs | Optional; no embedded credentials |
| `submission` | Generated Issue number, submitting login, body digest and optional title digest | Administrative provenance; verified against the live Issue during proposal review |
| `acceptance` | Original `accepted_at` UTC timestamp, creation `pull_request` number and `merged_by` username | Added after acceptance through a verified metadata PR; members cannot edit it |

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

New EP submission now uses the authenticated Issue author. Claims and updates
to accepted records remain local simulations or direct SC decisions until their
own intake flows are added. A name or username in YAML does not establish who
submitted a request.

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
PRs retain previous records and review decisions. Issue submissions include generated provenance verified against the live
Issue. Accepted history and review decisions remain in Git and the associated PR.

## Repository layout

```text
.github/
  CODEOWNERS
  pull_request_template.md
  ISSUE_TEMPLATE/
    new-project.yml            # Generated proposal form
  workflows/
    new-project.yml            # Verified Issue-to-PR submission
    project-policy.yml         # Trusted-base validation and head status
    registry-health.yml        # Validation/tests after merging into main
    project-accepted.yml       # Original merge metadata through a review PR
config/
  policy.yaml                  # Creation/claim rules and editable fields
  access.yaml                  # SC authorization team, separate from review routing
working_groups/
  SCM.yaml                     # Group code, description and deliverables
  CAS.yaml
  CPIP.yaml
schema/
  project.schema.json          # Fields, types, choices, required fields
projects/
  .gitkeep
  P000001.yaml                 # Existing records are preserved by this update
templates/
  project.yaml
  unassigned-cpp.yaml
scripts/
  project_registry.py          # Validation, policy, JSON catalogue, simulation
  validate_pr.py               # GitHub PR validation and status
  issue_form.py                # Generate form from catalogue
  submission.py                # Parse/validate Issue answers
  issue_intake.py              # Verify author and create/update proposal
  review.py                    # Human-readable PR summaries and review requests
  acceptance.py                # Original merge metadata through a review PR
tests/
  test_registry.py
  test_github_adapter.py
  test_team_directory.py
  test_working_groups.py
  test_issue_submission.py
  test_submission_updates.py
docs/
  setup-and-test.md
  project-submissions.md
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
  description and deliverables; regenerate the form with `python -m scripts.issue_form`. No schema or code change is needed.
- Add/change a deliverable: edit its working-group catalogue and regenerate the form. Removing a used
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
by that check. The policy token has Contents, Pull Requests and Issues Read,
plus Commit Statuses Write to publish the required status on the actual PR head.
It cannot change repository files or merge PRs. A separate
`SC_TEAM_READ_TOKEN` secret supplies organization Members Read permission for
team lookups; no repository write or membership write permission is needed.
Never commit its value.

SC authority comes from a **live lookup** of active GitHub team membership
for the GitHub API's PR author, or the original Issue author for bot proposals. The identifier is read from the **base**
`config/access.yaml`, not the proposed access configuration. Event senders, rerun actors, PR bodies
and YAML fields cannot establish SC identity or self-authorize a PR.

`config/access.yaml` identifies the existing SC team. Add/remove people only
in that GitHub team; no stored membership list is used. `.github/CODEOWNERS`
independently routes reviews, currently only to `aguinot` during testing.
Restore the team as Code Owner when testing finishes; the membership credential
and authorization checks stay the same. Membership is checked on each run
and again at its end. Later membership edits do not retroactively revoke
completed checks; rerun open PR checks after team changes.

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
