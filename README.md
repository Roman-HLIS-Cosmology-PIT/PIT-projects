# PIT projects

Project proposals are submitted through a GitHub Issue Form and reviewed through a pull request. Repository readers can submit Issues without write access, a fork, or another account system. Only information suitable for all repository readers belongs here.

[Submit a new EP project](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

## Submission and review

1. Use the Issue title as the project title. Enter the required lead name, GitHub username, description and PhD answer. Ordinary members must submit as their own lead; SC members may submit on another person's behalf.
2. Select one or more working groups in the required multiple-selection dropdown. Each group has its own optional deliverable menu, allowing several selections. A selected group may have no deliverables. Deliverables selected for unselected groups are ignored.
3. Submit and stay on the Issue page for the automated result. Every form submission is an **EP**. The workflow checks the actual Issue author's live organization membership and required information. Invalid optional contributors, deliverable selections, repository links, related links and Slack entries are removed with an explanation; they do not block the project.
4. A valid submission creates one project YAML file on `project-submissions/issue-<number>` and one PR. The bot reformats the Issue body into a readable summary and requests review from the configured reviewer, currently **aguinot**. Retrying an unchanged submission reuses the same PR and branch.
5. Merging the PR accepts the project and closes the Issue through `Closes #<number>`. The bot posts an acceptance message on that Issue. Enable GitHub's **Automatically delete head branches** repository setting to remove merged branches.

The workflow stores the Issue's creation timestamp as `submission.submitted_at`. There is no acceptance-date update or second PR. The Issue body displays the readable project summary. Its native form answers are retained in a hidden, compressed comment in that same body for validation and retry recovery. No saved-answer database, state branch, correction form or edit command is added. Very long submissions retain their original body if formatting would exceed GitHub’s size limit.

After successful submission the bot says **Your project has been submitted**, links the PR and explains that the submitter will be notified after review and acceptance. If optional entries were removed, it lists the omissions and reasons and says they can be added later. The formatted Issue and generated YAML contain the retained values. Editing is not implemented yet.

Optional cleanup keeps the first valid contributor for each username, excludes the lead from contributors, removes incomplete/malformed/overlong contributors, removes unknown/duplicate deliverables within selected groups, removes malformed main repository URLs, removes invalid related links, keeps the first occurrence of duplicate links and the first 100 valid links, and removes an invalid/overlong Slack entry. A correctly formatted repository URL that cannot be verified is kept with a warning because it may be private. Empty optional fields remain absent. The original answers stay in the hidden Issue source so retries and independent policy checks reproduce the same cleaned record.

If required information or submission integrity fails validation, the bot adds `invalid`, closes the Issue automatically, explains the failure and links the normal New Project form. Any associated unmerged PR is closed and its verified branch removed through the existing cancellation cleanup. No duplicate cancellation comment is added. Temporary API or workflow failures remain retryable; they are reported separately from validation failures.

There is no correction form, replacement-Issue chain, clipboard workaround or `/edit` command. Once the proposal branch exists, its submitted answers are fixed. Manual edits that change the proposal fail validation, close the submission and do not edit its YAML. The form cannot validate every free-text answer before submission.


Closing a submission Issue before acceptance cancels it: the workflow closes its unmerged PR and removes only its verified submission branch. This cleanup works without a membership token. Reopening a cancelled submission does not resurrect it. Closing a PR alone keeps its branch; SC can reopen that PR. After acceptance, changes go through an SC-authored PR or an authorized direct main commit.

### Working groups and deliverables

The working-group dropdown allows multiple selections, is required, and has no default. Actions also checks that at least one valid group was selected. One **Deliverables** section contains the separate optional dropdowns for each group. Its introduction explains that deliverables for unselected groups are ignored. GitHub cannot conditionally disable these menus; they start empty so optional deliverables are not selected accidentally. A group with an empty catalogue remains selectable but has no deliverable dropdown.

## Files and routine changes

- `project_workflow/config.yaml`: form wording, schema, kind rules, reviewer routing and live SC team identifier.
- `working_groups/<CODE>.yaml`: a group's description and deliverables. This directory remains at the repository root and defines the allowed groups.
- `projects/<ID>.yaml`: accepted machine-readable records.
- `project_workflow/`: parsing, validation, PR creation and generation code; optional offline behavior tests.
- `.github/`: native GitHub form, CODEOWNERS and two workflows.

Change the authoritative source only. **Project maintenance** regenerates the native form and CODEOWNERS after a main commit or on an SC-authored PR. On an SC PR, generation appends the outputs to that same branch. If main protection rejects an automatic generation commit, it creates a synchronization PR containing only generated outputs. The generator executes accepted main code and reads proposed configuration as data.

Add a working group by adding its catalogue, for example:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Calibration
```

Add deliverables by editing that same file. No parser, schema enum or form file needs a parallel edit. An empty `deliverables: []` is allowed. Removing a group or deliverable still used by an accepted project requires updating those project records in the same change.

A project's group associations are stored as:

```yaml
kind: EP
working_groups:
  - code: SCM
    deliverable_ids: [SCM-1, SCM-2]
  - code: CAS
    deliverable_ids: []
```

CPP and KEY are absent from the submission form. SC can manage their YAML records separately; the schema still allows both, including an unassigned lead with both lead fields null. Automated CPP/KEY claiming is not implemented.

## Workflows and permissions

**Project workflows** processes new-project Issue events and cancellation, and independently validates PR data. Bot PR validation reconstructs the record from the live Issue and permits only its project file. Direct PRs require a live SC-team author. Validation publishes the required commit status **Project policy** on the actual PR head. Bot-created PRs explicitly dispatch this check because `GITHUB_TOKEN` writes do not reliably trigger PR workflows.

**Project maintenance** synchronizes generated files, checks registry and SC-team access, and provides `projects.json` and combined `working_groups.yaml` as downloadable `registry-catalogues` artifacts. Source edits support both main commits and SC PRs. No unit-test suite is a mandatory workflow gate.

Use `SC_TEAM_READ_TOKEN` with organization **Members: read**, scoped to the organization. The roster is read from `Roman-HLIS-Cosmology-PIT/steering-committee`; it is not stored in the repository. Repository writes use each job's scoped `GITHUB_TOKEN`. Both workflows execute trusted main/base code, never proposed PR code or submitted commands. They reject forks and unexpected changes in reserved branches.

During testing keep `reviewers: [aguinot]`. Switching that setting to `steering_committee` later updates review routing and CODEOWNERS automatically. Also check repository/team notification settings if people have subscribed independently.

## Install this update

1. Apply the patch and review `git diff`. It is based on main commit `336d40688c9cabd4ae8bce2c146d46cade01cadd`.
2. Publish the update through an authorized main commit or SC PR. No new secret, permission or form regeneration is required for these handling changes. Existing test proposals should be cancelled and submitted again if they contain optional errors that the old workflow rejected.
3. Use the live tests below. Review requests remain directed to `aguinot`.

## Test on GitHub

- Open the New Project form and try submitting with no working groups selected. The required dropdown must block submission. Actions also rejects a missing selection if the Issue body is altered manually.
- Submit a valid EP with SCM and CAS selected, SCM deliverables selected, and CAS deliverables empty. Select a CPIP deliverable while leaving CPIP unselected; it must be absent from the generated YAML. Check the formatted Issue body, original submission date, reviewer `aguinot`, and successful **Project policy** status.
- Retry through **Project workflows → Run workflow → submit**, entering the Issue number. It must reuse its PR and branch.
- Submit another valid project before merging the first. It must have its own Issue-number branch and PR.
- Submit a valid lead with an incomplete/invalid contributor, malformed repository URL, invalid/duplicate related links and an overlong Slack value. Expect an open submission with a PR, cleaned YAML and one message listing the removals. Retry: the same values, branch and PR must be retained.
- Submit an invalid lead username or a different lead as an ordinary member. Expect one failure comment, `invalid`, an automatically closed Issue and no new proposal branch/PR. Use the provided link to submit a new project.
- Close an unmerged valid submission Issue. Its PR must close and its branch disappear. Merge another valid proposal: the Issue closes, the accepted file remains, and automatic branch deletion applies if enabled.
- Change one catalogue through an SC PR or direct main commit. Maintenance must generate the updated form automatically. Adding a new group must add a dropdown option and its deliverable menu.

Optional local checks:

```bash
python -m pip install -r project_workflow/requirements.txt
python -m project_workflow.generated_files
python -m project_workflow.issue_form --check
python -m project_workflow.project_registry validate
python -m unittest discover -s project_workflow/tests
```

No website or wiki integration is added in this patch.
