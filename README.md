# PIT projects

Project proposals are submitted through a GitHub Issue Form and reviewed through a pull request. Repository readers can submit Issues without write access, a fork, or another account system. Only information suitable for all repository readers belongs here.

[Submit a new EP project](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

## Submission and review

1. Use the Issue title as the project title. Enter the required lead name, GitHub username, description and PhD answer. Ordinary members must submit as their own lead; SC members may submit on another person's behalf.
2. Select one or more working groups in the required multiple-selection dropdown. Each group has its own optional deliverable menu, allowing several selections. A selected group may have no deliverables. Deliverables selected for unselected groups are ignored.
3. Submit and stay on the Issue page for the automated result. Every form submission is an **EP**. The workflow checks the actual Issue author's live organization membership, required information, usernames, contributor pairs, working groups, deliverables and URL syntax.
4. A valid submission creates one project YAML file on `project-submissions/issue-<number>` and one PR. The bot reformats the Issue body into a readable summary and requests review from the configured reviewer, currently **aguinot**. Retrying an unchanged submission reuses the same PR and branch.
5. Merging the PR accepts the project and closes the Issue through `Closes #<number>`. Enable GitHub's **Automatically delete head branches** repository setting to remove merged branches.

The workflow stores the Issue's creation timestamp as `submission.submitted_at`. There is no acceptance-date update or second PR. The Issue body displays the readable project summary. Its native form answers are retained in a hidden, compressed comment in that same body for validation and retry recovery. No saved-answer database, state branch, correction form or edit command is added. Very long submissions retain their original body if formatting would exceed GitHub’s size limit.

If validation fails, the bot adds `invalid` and explains the errors. Close the Issue and use the New Project form again. There is no correction form, replacement-Issue chain, clipboard workaround or `/edit` command. Once the proposal branch exists, its submitted answers are fixed. Manual Issue edits are checked and block the PR if they change the proposal; they do not edit its YAML. Cancel and resubmit to change a proposal. The form cannot validate every free-text answer before submission.

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

This update replaces the checkbox form. Existing submissions made with that form are not migrated; close obsolete test Issues/PRs before installing, then submit through the new form.

1. Apply the patch and review `git diff`. It is based on main commit `8b98d7fd62548a1cd5ffc5e5d3f75c80e9b65231`.
2. Publish the update to main. The previously installed generator assumes checkboxes, so use an authorized direct main commit for this code update, or an administrator-assisted installation merge. Routine configuration/catalogue updates afterward still support both main commits and SC PRs.
3. Run **Project maintenance** on main and check the generated form. It also runs automatically after your publication push.
4. Use the live tests below. Review requests remain directed to `aguinot`; no new secret or permission is required.

## Test on GitHub

- Open the New Project form and try submitting with no working groups selected. The required dropdown must block submission. Actions also rejects a missing selection if the Issue body is altered manually.
- Submit a valid EP with SCM and CAS selected, SCM deliverables selected, and CAS deliverables empty. Select a CPIP deliverable while leaving CPIP unselected; it must be absent from the generated YAML. Check the formatted Issue body, original submission date, reviewer `aguinot`, and successful **Project policy** status.
- Retry through **Project workflows → Run workflow → submit**, entering the Issue number. It must reuse its PR and branch.
- Submit another valid project before merging the first. It must have its own Issue-number branch and PR.
- Submit an invalid username or incomplete contributor pair. Expect one error comment, `invalid`, and no proposal branch/PR. Close the Issue and resubmit through the normal form.
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
