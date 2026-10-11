# Submit a new project through an Issue

Organization members need only **Read** repository access. GitHub verifies the
actual Issue author and prepares a project YAML PR. During testing, review is
requested only from **aguinot**. SC authorization still comes from the existing
GitHub team. There is no email field, fork, backend or additional sign-in.

When upgrading the existing repository, first follow
[the staged update and notification instructions](review-and-acceptance-update.md).
No accepted project or deliverable catalogue is overwritten by that update.

## Administrator setup

1. Enable Issues and Actions. Keep ordinary members at Read.
2. Create the **new-project** label if missing. The form applies it automatically;
   the title does not identify the workflow. Removing `[New project]` is safe.
3. Keep the existing **SC_TEAM_READ_TOKEN** secret, with organization **Members:
   Read** permission and visibility of the organization/team. Never commit it.
4. Enable **Allow GitHub Actions to create and approve pull requests** at the
   repository and, if required, organization level. Keep default token permissions
   read-only. Individual supplied workflows request the permissions they need;
   none contains an approval or merge operation. Consider the other repositories
   affected before changing organization settings.
5. Protect main with required PR and Code Owner approval, dismissed stale reviews,
   up-to-date branches, and no bypass. Require the **Project policy** commit status
   from GitHub Actions, not the separate `Validate project policy` workflow job.
6. Run Registry health on main and complete the live tests below.

Authorization configuration:

```yaml
# config/access.yaml
steering_committee: Roman-HLIS-Cosmology-PIT/steering-committee
```

Test review routing:

```text
# .github/CODEOWNERS
* @aguinot
```

Both files require administrative review. Changing reviewers does not change who
can declare CPP/KEY projects. GitHub maintains the only SC membership roster.

## Direct form link

[Submit a project](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

The same URL can be used by a future GitHub Pages button. This update does not
create a website or alter repository visibility. A private repository still
requires access through the visitor's GitHub account.

## Filling and correcting the form

- Use the native **Add a title** box as the project title. There is no separate
  project-title field or required prefix. A title edit revalidates the proposal.
- Ordinary members choose EP and enter themselves as lead. SC can also declare
  CPP or KEY and leave both lead fields blank for an unassigned project. The
  lead-name guidance reads **Required for EP.**
- Choose a working group and deliverables. Choices include catalogue descriptions
  and deliverable titles. Dropdowns do not cascade: the workflow checks pairing.
- PhD-thesis status offers **False / True**, with **False** preselected.
- Slack channel, the main GitHub repository and related HTTPS links are optional.
- The last section, **Contributors**, contains **Contributor 1** through
  **Contributor 5** subsections. Each has separate name and GitHub username boxes.
  Fill both boxes or leave both blank. Names may contain spaces; usernames must
  not contain @. Do not repeat the lead or a contributor account.

The initial form has five contributor slots; the stored list is expandable.
Additional contributors can currently be added through an SC-authored project
update PR. A Read-member update/claim intake remains future work.

GitHub displays the controls vertically, with Markdown headings for the section
and subsections; it has no side-by-side layout or add-another-contributor button.

After Actions starts, an acknowledgment comment confirms submission. A separate
feedback comment gives corrections or the proposal PR link. Actions can queue,
so these comments are asynchronous. Receipt and validation are not acceptance.
The acknowledgment is posted once; the latest validation feedback is updated
on reruns rather than duplicated.

To correct an error, edit the Issue title or choose **⋯ → Edit** on its original
body, change answers beneath the existing headings, and **Save**. GitHub edits
the submitted Markdown, not the original dropdown form. Keep all numbered
contributor headings, including unused slots. Empty optional values and
`_No response_` mean no value. Use `False` or `True` for the new form. Working-group codes and bare deliverable IDs such as `SCM-1, SCM-3` are also accepted
when correcting Markdown. A separate comment does not change the proposal.

Earlier Issues created with the old form remain supported: they retain their
Project title body field, contributor list and Yes/No answers. Do not mix old
and new fields in one Issue. Existing accepted records are not regenerated from
closed Issues, even if someone edits those Issues later.

Invalid edits, closing an unmerged submission Issue or removing its label fail
an existing PR's policy status; they do not overwrite the last valid YAML.
Missing required fields are checked by the workflow in private and public repos.

## Proposal PR and review

Issue #12 generates `projects/P000012.yaml` on `project-submissions/issue-12`.
The ID remains stable on edits. Collisions fail instead of overwriting accepted
projects. Shorter IDs remain available for manual SC creations.

The bot-authored PR description contains the project metadata, lead, contributor
name/username table, deliverable IDs and titles, related links and description.
Corrections refresh this summary. User-supplied mentions and Issue-closing
references are escaped so the generated summary does not ping others or close
unrelated Issues. GitHub's PR-description limit can require shortening a very
long preview; the full content remains in the YAML.

The independent policy check re-reads the actual original Issue author, active
organization/SC membership, and current answers, including the title for new
forms. Only that Issue's single new project file may change. It must exactly
match the reconstructed data. Editable PR descriptions and provenance do not
establish authorization. Proposed scripts, workflows, symlinks or submodules
cannot be executed or introduced by this adapter.

The `submission` object includes the Issue number, submitting login, body digest
and, for new forms, title digest. Membership is looked up live; the bot is not
considered an SC member. Proposed access configuration cannot self-authorize.

CODEOWNERS controls review routing. During tests, only `aguinot` is requested.
Refreshing a generated proposal or metadata PR removes outstanding review
requests that are no longer in CODEOWNERS. This cannot retract notifications
already delivered or prevent ordinary notifications to repository watchers.

Repeated events reuse the same branch and PR, including a branch left after an
interrupted run. A closed, unmerged PR is not replaced; an administrator can
reopen that same PR and retry. Edit the Issue to correct proposed data rather
than manually editing generated YAML. Merge the approved proposal into main
to accept the project and automatically close the Issue.

If main advances, run **New project submission → Run workflow**, select main,
and enter the Issue number, or edit/save the Issue to rebuild its branch. Avoid
GitHub's Update branch merge button; the adapter maintains one-parent commits.
Run a fresh Project review dispatch on main for the current PR number. Existing
checks do not automatically revoke themselves after team membership changes;
rerun checks for open proposals when changing membership.

## Acceptance timestamp

When the creation PR is merged, **Record project acceptance** opens a separate
metadata PR, using `project-acceptances/pr-<CREATION-PR-NUMBER>`. It adds:

```yaml
acceptance:
  accepted_at: "2026-10-11T05:30:00Z"
  pull_request: 42
  merged_by: aguinot
```

These are example values. `accepted_at` is GitHub's actual original merge time
in UTC. `pull_request` identifies the creation PR; `merged_by` is its merger,
not a list of approving reviewers. The acceptance time is not the later merge
of the metadata PR. Absence of the field does not mean a project on main is
unaccepted; its metadata PR may still be awaiting review.

Review and merge the metadata PR to store these values in the committed YAML.
The required Project policy check verifies the original PR is merged to main,
its author is the genuine submission bot or an active SC member, the creation
file matches the actual merge commit, and only exact acceptance metadata is
added to the current accepted project. Later accepted science edits are
preserved. Conflicting existing metadata is rejected rather than overwritten.
Members cannot supply or edit acceptance fields.

Manual SC creation PRs can also be recorded, including multiple new project
files in one PR. Updates to existing projects do not reset acceptance dates.
Closing without merging does not count as acceptance. Merging the metadata PR
cannot start another metadata PR. No workflow approves, merges, or pushes main.

For an earlier creation PR, or a retry after main changes, manually run
**Record project acceptance** on main and enter the original creation PR number.
The same metadata PR is reused. If it was closed without merging, reopen it
before rerunning. If the manual creation author is no longer in SC, automatic
backfill fails closed; an active SC member must inspect the historical merge
and record the correction through a reviewed administrative PR.

A direct push that creates a record has no creation-PR merge to reference;
it cannot be automatically assigned a PR acceptance date. Use the PR process.

## Catalogue changes and URL checks

Working groups and deliverables remain defined in `working_groups/<CODE>.yaml`.
Descriptions appear beside group codes; titles appear beside deliverable IDs.
CAS/CPIP currently have placeholder descriptions in your catalogue; edit their
`description` values when their full definitions are available. Then run:

```bash
python -m scripts.issue_form
```

Publish the regenerated form with the catalogue change. New groups/deliverables
require no hard-coded enum update. Deliverable IDs in the form use letters,
digits, dots, underscores or hyphens. ASCII commas in titles are displayed as
semicolons only in multi-select labels, because GitHub joins selections with
commas; original catalogue titles are preserved in project summaries.

The optional main-repository link is checked anonymously for public accessibility.
A GitHub 404 means private/inaccessible or nonexistent, so it is a review warning,
not a claim that the repo does not exist. Related links are syntax-checked only.
No organization credential is sent to other repositories or submitted links.

## Local verification

Use Python 3.12 from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.project_registry validate
python -m scripts.issue_form --check
python -m unittest discover -s tests -v
python -m scripts.project_registry catalog --output build/projects.json
python -m scripts.project_registry working-groups --output build/working_groups.yaml
```

Tests simulate GitHub and perform no network requests, comments or repository
writes. Never run the live adapters locally with production credentials merely
as a test.

## Tests after you publish on GitHub

| Test | Expected result |
| --- | --- |
| Open an ordinary Issue without the submission label | Submission job skipped; no PR |
| Submit EP using only the native Issue title and no prefix | Receipt comment, then validation result and one proposal PR |
| Inspect the end of the form | Contributors section, five subsections and ten optional boxes |
| Leave all contributor boxes blank | Empty contributor list accepted |
| Fill only Contributor 3 name | Correction identifies Contributor 3; no PR |
| Fill all five pairs | Five contributors retained; duplicates and lead repetition rejected |
| Choose a deliverable from another group | Correction; no PR |
| Leave PhD status at its default | YAML contains `phd_thesis: false` |
| Edit title, description or contributor boxes in Issue Markdown | Same PR refreshed; summary and provenance updated |
| Inspect Reviewers | Only aguinot (account spelled `aguinot`); no SC-team request |
| Ordinary member selects CPP/KEY or another lead | Policy failure; no unauthorized update |
| SC submits unassigned CPP/KEY | Both lead fields null; proposal accepted for review |
| Rerun a valid submission | Same PR and ID; no duplicate receipt comment |
| Merge a valid proposal | Project on main; Issue closes; one acceptance metadata PR opens |
| Compare acceptance object with original PR | Exact original merge time, PR number and merger |
| Rerun Record project acceptance using the original PR number | Same metadata PR |
| Try changing science or metadata in the generated metadata PR | Project policy fails |
| Merge the metadata PR | Acceptance object stored; no further metadata PR |
| Close an unmerged creation PR | No acceptance metadata recorded |
| Backfill an earlier valid creation PR | Original date recorded; current project content preserved |
| Inspect Registry health and exports | Existing and newly accepted records validate; acceptance appears in JSON |

Use real test data only if it is appropriate for repository readers. Accepted
records are retained: do not merge disposable projects unless you intend to
keep them. The existing `P000001` is preserved by this update and can be backfilled.

For failures, inspect New project submission, Project review, Record project
acceptance and Registry health in Actions. Check labels, permissions, membership
secret, branch restrictions and whether main advanced during a run. A pending,
missing or failed Project policy status must block merging. Retry with a fresh
manual dispatch on main and the existing Issue/PR number; do not create a duplicate.

## Official references

- [Issue Form syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
- [Form controls and Markdown headings](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)
- [Code Owners and automatic reviews](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
- [Workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [PR merge metadata](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request)
- [GITHUB_TOKEN event restrictions](https://docs.github.com/en/actions/concepts/security/github_token)
