# Collaboration project registry

This repository stores searchable project records and accepts proposals through
GitHub Issues. Organization members need only Read access. Actions checks each
proposal and opens a PR; a human reviews and merges it. The same repository can
host the collaboration Wiki later.

[**Submit a project**](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

All repository readers can see records, Issues, PRs and logs. Submit only
collaboration-appropriate information. No email address is collected.

## Files to maintain

| Location | Purpose | Edit directly? |
| --- | --- | --- |
| `project_workflow/config.yaml` | SC team, reviewers, project schema, policy and Issue Form wording/layout | Yes: the one routine workflow configuration file |
| `working_groups/<CODE>.yaml` | Working-group description and deliverables | Yes: one catalogue per group, reusable outside this workflow |
| `projects/P<digits>.yaml` | Accepted project records | Through proposal PRs or an SC administrative change |
| `.github/ISSUE_TEMPLATE/new-project.yml` and `correct-project.yml` | New-project and replacement forms sharing the same fields | Generated automatically |
| `.github/CODEOWNERS` | Native GitHub review routing | Generated automatically |
| `.github/workflows/projects.yml` | Issue intake and PR validation | Only for changes to automation behavior |
| `.github/workflows/maintenance.yml` | Generation, registry health and downloadable exports | Only for changes to automation behavior |
| `project_workflow/` | Implementation, dependencies, shared setup and optional developer checks | Only for implementation changes |

The root contains just `.github/`, `project_workflow/`, `projects/`,
`working_groups/`, this README and `.gitignore`. There is no separate schema,
configuration, scripts, tests, templates or documentation directory at the root.
The project schema is loaded directly from `config.yaml`; there is no compiled
schema file to keep synchronized. The combined working-group YAML and project
JSON are export artifacts, not additional sources to edit.

## Changing configuration

Edit `project_workflow/config.yaml` once. You can either commit to `main` when
repository rules allow it, or submit an SC-authored PR targeting `main`.

* **PR:** maintenance reads the proposed configuration as data, checks the
  author's live SC membership, and appends any needed form/CODEOWNERS update to
  the same branch. It then dispatches validation for the updated PR head. Wait
  for the final **Project policy** status before merging.
* **Direct commit:** maintenance validates the accepted configuration and all
  projects, then commits only needed generated files to `main`. If protection
  requires a PR, it instead opens or refreshes one synchronization PR on
  `project-config/generated`. Review and merge that PR to finish synchronization.
* **No output change:** there is no generation commit. Existing formatting and
  comments are retained when the generated files mean the same thing.

Generated commits never change source configuration or project records, and
automation never approves or merges a PR. Do not use the reserved bot branch
names for human work. A direct source commit cannot be rolled back by validation;
use a PR when you want checks before accepting an incompatible change.

Examples of changes requiring only one source edit:

| Change | Edit |
| --- | --- |
| Shorten help text, rename a label, reorder form sections | `form` in `config.yaml`; keep control IDs stable |
| Add a sixth contributor slot | Add input controls `contributor_6_name` and `contributor_6_github_username` under `form.body` |
| Change initial status or allowed statuses | `schema.properties.status.default` / `enum` |
| Add a project kind | Add its three explicit rules under `policy.kinds`; schema and form use `enum_from: project_kinds` / `options_from: project_kinds` |
| Change reviewers | `reviewers`; both CODEOWNERS and runtime review requests follow it |
| Change the SC team | `steering_committee`; membership continues to come from GitHub |
| Add a working group | Add `working_groups/<CODE>.yaml`; dropdowns discover it automatically |
| Add a deliverable or change its title | The relevant working-group YAML |

The `new-project` label is the fixed intake marker and must remain in
`form.labels`. Answer headings come from the labels in the current config.
If you rename an answer label, update that heading in any open submission
Issue before revalidating it. Contributor slots are optional; adding a slot
does not require existing submitters to fill it in.

An optional new project property can be defined under `schema.properties`
without another schema file. Adding a completely new input type or behavior to
the Issue parser still requires an implementation change; schema editing alone
does not teach the parser to collect that property. Required-field or schema
version changes must migrate existing records as part of the same change.
Removing a used group/deliverable likewise requires updating affected projects.

Working groups and deliverables are expandable. Each catalogue has this shape:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Calibration
  - id: SCM-2
    title: Image pre-processing
```

The filename must match the group code. Group codes start with an uppercase
letter and then use uppercase letters, digits, underscores or hyphens.
Deliverable IDs used in the form contain letters, digits, dots, underscores or
hyphens. A catalogue may have no deliverables, but the complete registry must
have at least one to render the submission form. No group list is hard-coded in
the project schema. Different groups may reuse a deliverable ID.

## Current workflows

| Workflow / job | Trigger | Result |
| --- | --- | --- |
| Project workflows / submission | New-project/correction Issue events, `/edit` comments or manual `submit` retry on main | Authenticate, format, validate and create/update the same proposal PR across replacement Issues |
| Project workflows / validation | Open PR events targeting main or manual `validate` on main | Independently publish **Project policy** on the current proposed head |
| Project maintenance / generation | Main push, open PR events, or manual run on main | Synchronize both forms and CODEOWNERS when needed |
| Project maintenance / registry | Main push or manual main run without a PR number | Validate accepted records, check SC team access and upload `registry-catalogues` exports |

Routine Actions runs do not run unit tests. Data validation, identity checks and
bot-write scope checks remain because removing them could break intake or grant
incorrect permissions. Form presentation is not frozen byte-for-byte.

### Submission and corrections

The native Issue title is the project name. No prefix is needed. The form asks
for kind, lead name and GitHub username, working group, deliverables, PhD-thesis
status (False by default), Slack channel, description, optional main repository
and related links. Five optional contributor name/username pairs appear in
numbered subsections at the end. Complete both boxes or leave both blank.
The stored contributor list has no five-person limit.

After submission, stay on the Issue page until the workflow posts next-step
instructions. You may need to refresh the page; this can take a few minutes.
There is no separate acknowledgment or latest-submission message. The workflow
reformats the native field-by-field Issue body into the same readable summary
used in the PR: bold field labels, names followed by clickable usernames, and
one contributor list.

If validation fails, one feedback comment repeats the complete summary with
**🔴** only at the problematic fields or entries, then provides a correction
link. The **Correct or Edit Project** form starts with one plain-text **Changes
needed** box containing only concise instructions, for example:

```text
Deliverables: CAS-1 does not belong to SCM. Select a deliverable from your chosen working group.
Contributor 2 GitHub username: Fill both contributor name and GitHub username, or leave both blank.
```

Text answers are prefilled where the native form supports it. Every dropdown
must be selected again; its description says **Select again for this
submission**. There are no per-dropdown previous-selection boxes, no keep-choice
option, and no Issue-number inputs. Guidance text does not supply project data
or grant permission. Clearing an optional text field clears that answer.

GitHub's native forms have no hidden inputs. A short routing reference is
therefore appended as an HTML comment at the end of the prefilled description.
Keep that comment when changing the description. It is visible in the form's
text editor, but is removed from the rendered Issue, PR summary and project
YAML. It carries no credential. Its contents are never trusted as authority:
the workflow checks the issued reference, source version, live organization
membership and original author's numeric GitHub identity. Removing or changing
it rejects an unactivated correction instead of redirecting another proposal.

To edit a valid proposal, its original author comments **`/edit`** on the latest
submission Issue while its PR is open. The bot posts a form link with no emoji
or error list. Opening the link leaves the current proposal and status unchanged.
A valid correction reference activates a new Issue, labels the previous one
**superseded**, links it to the replacement and closes it. A previously invalid
Issue retains **invalid**; a valid superseded Issue does not acquire that label.

The original project ID, date, branch and PR stay unchanged. A valid replacement
updates the YAML and formatted PR summary. An invalid replacement blocks
**Project policy** and leaves the last valid YAML in the branch; its feedback
provides another correction link. Merging closes the latest Issue. Require
**Project policy** in branch protection to enforce the merge block. Stale links,
another author's corrections and corrections after a PR is merged are rejected.
Opening the correction form directly without an issued reference closes the
resulting Issue and points to the New Project form.

The formatted Issue is a display of authenticated saved answers. Editing its
body manually triggers revalidation and blocks the PR if the display no longer
matches those answers. Use the provided replacement form to change answers.
Editing a title alone is supported; revalidation refreshes the same PR.
Do not edit superseded Issues: their frozen content protects revision history.

Both forms share `form.body` and the working-group catalogues. Change shared
wording, labels, contributor slots or the closing notice in the single config.
Native GitHub checks simple required fields where supported; membership, schema
and catalogue validation run afterward. There are no cascading dropdowns.
Long text that cannot fit in a correction link is replaced by **Keep previous
value**, which retains its complete saved answer. Replace that sentinel, while
keeping the appended reference for a description, to change the answer.
Very large displays may be shortened to fit GitHub; saved answers remain complete.

Internal revision state and answer receipts live on the bot-managed branch
`project-workflow/state`, never in main's project/configuration directories and
never in extra Issue comments or state PRs. The workflow creates it automatically
using its existing repository token and appends non-forced updates. Leave that
branch in place and do not edit or merge it. State has the same visibility as the
repository and contains submitted project information, not secrets.
Submission and policy jobs share a queued concurrency group to avoid conflicting
state updates or stale statuses. No new token, login or external backend is used.
A closed, unmerged PR is not replaced: ask SC to reopen it to continue.


Ordinary organization members may create EP projects with themselves as lead.
Only SC members may declare CPP/KEY, including unassigned projects with both
lead fields null. Claims/updates by members for already accepted projects are
future intake work; the underlying policy supports those rules. SC can update
accepted projects through administrative PRs.

The optional main repository URL is checked through GitHub's repository API.
Private/inaccessible repositories may be impossible to verify and are flagged
for human review rather than assumed not to exist. Username syntax is checked;
reviewers must confirm contributor identities and accounts.

### Submission date and acceptance

The initial project PR includes GitHub's original Issue creation timestamp:

```yaml
submission:
  issue_number: 12
  submitted_by: alice
  submitted_at: '2026-10-11T06:00:00Z'
  # Workflow-generated body/title digests are also stored here.
```

The date is generated from the authenticated GitHub API response; submitters
neither enter it nor supply it in an Issue answer. It stays unchanged when the
Issue is corrected or the workflow is retried, and appears in the formatted PR
summary. This is the proposal submission date, not its later acceptance date.

Merging the project PR accepts the project and closes its linked Issue. There
is no follow-up acceptance-metadata PR and no automatic project rewrite after
merge. The acceptance time remains visible in the original PR's merge history.
The submitted branch may be deleted after merging.

Issue submission metadata requires the original submission date and both
body/title digests. The removed `acceptance` YAML field is no longer allowed.
The workflow reads only `project_workflow/config.yaml`, the root
`working_groups/` catalogues and the current Issue form with paired contributor
fields. Earlier directory layouts and Issue formats are not supported.
Manual SC-created projects without a submission Issue omit the `submission`
object.

## Project records and exports

One YAML file per project, for example:

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
kind: EP
status: active
working_group: SCM
deliverable_ids: []
github_url: null
```

Names and GitHub usernames are separate; usernames omit `@`. A lead is a single
person, cannot also appear as a contributor, and contributor usernames cannot
repeat. Lead name and username are either both set or both null. Status is
`active`, `paused`, `IR` (internal review), or `completed`. Keep completed records;
the PR policy rejects deletion to preserve history.

Optional fields include `phd_thesis`, `slack_channel`, `related_links`,
`github_url`, and workflow-generated `submission` metadata.
Issue submissions reserve `P000012` for Issue #12; manual SC creation can use
short IDs such as `P001`. IDs and filenames must agree and must not be reused.
Summaries support long Markdown text within the operational 1 MiB file limit.

From the repository root, using Python 3.12:

```bash
python -m pip install -r project_workflow/requirements.txt
python -m project_workflow.project_registry validate
python -m project_workflow.project_registry catalog --output build/projects.json
python -m project_workflow.project_registry working-groups --output build/working_groups.yaml
```

The JSON export contains `projects` and `working_groups` arrays. Maintenance
uploads both exports as the `registry-catalogues` artifact, retained for 30 days.
Download it from the relevant Actions run. `build/` is not committed.

If you want to preview generated native files locally:

```bash
python -m project_workflow.generated_files
```

That command changes the two local forms and CODEOWNERS only; it does not contact
GitHub. Normally the maintenance workflow handles this for you.

## GitHub configuration and security

* Create the **project-correction** Issue label once before using correction links.
  Keep the existing **new-project** label. The bot creates **invalid** and
  **superseded** labels when first needed.
* Organization members keep Read access. SC needs Write access to create
  administrative PRs (your existing SC Admin access is sufficient).
* Retain the repository secret **SC_TEAM_READ_TOKEN** with organization Members
  Read access for live membership lookups. Never put its value in a file.
  The existing team is `Roman-HLIS-Cosmology-PIT/steering-committee`; no roster
  is stored here.
* Retain **Allow GitHub Actions to create and approve pull requests**, subject
  to the organization's policy. These workflows create PRs and request human
  reviews; they do not approve PRs. The read-only default token setting is fine:
  individual jobs request the permissions they need.
* During testing, `reviewers: [aguinot]` requests only `aguinot`. To switch to
  the SC team later, set `reviewers: steering_committee` in the single config.
  Existing review requests on older PRs may need manual removal; changing the
  config does not cancel them. Scientific authorization still uses the SC team
  even while review notifications go only to `aguinot`.
* For production protection, require **Project policy** (the commit status)
  and human Code Owner approval, dismiss stale reviews, require an up-to-date
  branch and avoid bypass. Do not require every workflow job: manual validation
  runs are attached to main while the explicit status is on the actual PR head.
  If old health/test check names are currently required, replace those obsolete
  requirements when installing this update.

PR checks execute trusted base/main code and read proposed Git blobs as data.
They never run proposed scripts, workflows, dependencies or tests. SC authority
comes from the API's actual PR/Issue author and live membership in the accepted
SC team, never from a supplied lead name or a proposed team setting. A bot is
not an SC member. Each supported bot PR has independently verified identity,
provenance and allowed file/content scope. Generation writes only the two native forms and CODEOWNERS. Main/SC-branch updates are non-forced; only verified reserved bot
branches can be rebased.

GitHub suppresses most new workflow triggers for writes using `GITHUB_TOKEN`.
Automation therefore explicitly dispatches PR validation, and the current
maintenance run builds exports after its own generation commit. When a stale
main/branch check or transient API error stops a run, retry against current
main. Do not change tokens or loosen policy to work around a stale run.

## Installing this update

Apply the patch from the repository root and publish it through your usual
main commit or SC-authored PR. It is based on main commit
`b1863043bb8dd6993f04c050f7cab126ccb618ca`. The accepted test project is unchanged.

```bash
git apply --check /path/to/PIT-projects-submission-presentation.patch
git apply /path/to/PIT-projects-submission-presentation.patch
python -m project_workflow.project_registry validate
python -m project_workflow.issue_form --check
```

After publishing, run **Project maintenance** on **main**, with no PR number,
to ensure the forms are generated by the newly installed code. An installation
PR runs the previously accepted generator: let it finish its generated-file
commit and policy check, merge, then run maintenance on main. If main protection
requires a generated-file synchronization PR, review and merge that PR before
using the new form. Routine configuration edits still require just one source
file and generate outputs automatically on main or the same SC PR.

Retain the `new-project` and `project-correction` labels, membership secret and
Actions settings. Reviews still go only to **aguinot**. The state branch is
created automatically; no manual setup or new secret is needed.
Use fresh test submissions for this implementation update. Previous correction
links/reference inputs are retired; finish or close old test PRs/Issues before
testing. Accepted project YAML needs no migration. This update does not delete
old bot comments from previously created test Issues.

## Testing after installation

1. Run the local `validate` command above. In GitHub, manually run **Project
   maintenance** on `main`, leaving `pr_number` empty. Check generation, team
   access and the two catalogue exports.
2. **Direct commit path:** if main currently permits it, shorten `form.description`
   in `config.yaml` and commit just that file. Confirm maintenance appends only
   the form update and succeeds without another manual source edit.
3. **PR path:** from your SC account, change a form description or add a test
   deliverable in a branch. Open a PR. Confirm generated updates are appended
   to that same branch and **Project policy** passes on its final head. Merge.
4. **Protected-main fallback:** on a test installation with protection active,
   have main contain a stale generated file (for example after enabling
   protection between a source merge and maintenance). Run maintenance. Confirm
   exactly one synchronization PR, only native output changes, review requested
   from `aguinot`, and a passing policy status. Repeat the run; it must reuse
   the same PR. Review and merge it.
5. Submit a valid EP Issue from an organization member with Read access,
   entering their own username as lead. Check the readable Issue body, one feedback
   comment, formatted PR,
   human review request, linked Issue and one YAML addition. Comment `/edit`,
   verify that the form has no error list or 🔴, select every dropdown again,
   then submit a replacement and
   verify that the same PR updates and the previous Issue is closed.
6. Test a working-group/deliverable mismatch or a half-filled contributor pair.
   Check an **invalid** label and the correction link. Confirm the feedback
   repeats the full summary and marks only the incorrect selection with 🔴.
   Check the form starts with short correction instructions, has no Issue-number
   inputs or previous-selection boxes, and asks you to select dropdowns again. Submit a replacement with corrected answers and
   verify recovery without a duplicate PR. Also confirm an ordinary
   member's CPP/KEY submission is rejected.
7. Confirm the initial project YAML and PR summary contain the original Issue
   creation time under `submission.submitted_at`. Submit a replacement and check
   that this date and project ID stay unchanged. Merge the proposal; confirm Issue closure
   and that no second project/metadata PR is created.
8. Before merging, request another `/edit` and choose an invalid working-group /
   deliverable combination. Confirm the same PR is blocked by **Project policy**,
   its old YAML remains, and the latest Issue offers a correction link. Correct
   it and confirm recovery. Opening an old correction link must not produce a
   duplicate PR or change the active submission.
9. Open the correction form directly without a bot-issued reference. Confirm
   that its Issue is closed and directs you to the New Project form. Test another
   member trying your correction link: it must not affect your PR.
10. Revert temporary wording/catalogue test changes through your usual commit/PR
   route. Keep accepted test project history, or use a separate test repository
   if disposable project records are needed.

Offline developer checks are optional and are not maintenance gates:

```bash
python -m unittest discover -s project_workflow/tests
```

They cover identity/policy boundaries, parsing, narrow bot writes and retries.
Run proposed implementation changes locally before review; PR validation does
not execute proposed code. New model behaviors, member-update intake and Wiki
publication remain implementation work rather than configuration-only changes.

Official GitHub documentation: [protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches),
[token-trigger behavior](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow),
[team membership API](https://docs.github.com/en/rest/teams/members#get-team-membership-for-a-user).
