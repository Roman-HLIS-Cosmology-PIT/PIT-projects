# PIT projects

Project proposals are submitted through a GitHub Issue Form and reviewed through a pull request. Repository readers can submit Issues without write access, a fork, or another account system. Only information suitable for all repository readers belongs here.

[Submit a new EP project](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

## Submission and review

1. Use the Issue title as the project title. Enter the required lead name, GitHub username, description and PhD answer. Ordinary members must submit as their own lead; SC members may submit on another person's behalf.
2. Select one or more working groups in the required multiple-selection dropdown. Each group has its own optional deliverable menu, allowing several selections. A selected group may have no deliverables. Deliverables selected for unselected groups are ignored.
3. Submit and stay on the Issue page for the automated result. Every form submission is an **EP**. The workflow checks the actual Issue author's live organization membership and required information. Invalid optional contributors, deliverable selections, repository links, related links and Slack entries are removed with an explanation; they do not block the project.
4. A valid submission creates one project YAML file on `project-submissions/issue-<number>` and one PR. The bot reformats the Issue body into a readable summary, links `/help` for edits, and requests review from the configured reviewer, currently **aguinot**. Retrying an unchanged submission reuses the same PR and branch.
5. Merging the PR accepts the project and closes the Issue through `Closes #<number>`. The bot posts an acceptance message on that Issue. Enable GitHub's **Automatically delete head branches** repository setting to remove merged branches.

The workflow stores the Issue's creation timestamp as `submission.submitted_at`. There is no acceptance-date update or second PR. The Issue body displays the readable project summary. Its native form answers are retained in a hidden, compressed comment in that same body for validation and retry recovery. No saved-answer database, state branch or correction form is added. Very long submissions retain their original body if formatting would exceed GitHub’s size limit.

After successful submission the bot says **Your project has been submitted**, links the PR and explains that the submitter will be notified after review and acceptance. If optional entries were removed, it lists the omissions and reasons and says they can be added later. The formatted Issue and generated YAML contain the retained values. Edits can be requested with the comment commands below while the proposal PR remains open.

Optional cleanup keeps the first valid contributor for each username, excludes the lead from contributors, removes incomplete/malformed/overlong contributors, removes unknown/duplicate deliverables within selected groups, removes malformed main repository URLs, removes invalid related links, keeps the first occurrence of duplicate links and the first 100 valid links, and removes an invalid/overlong Slack entry. A correctly formatted repository URL that cannot be verified is kept with a warning because it may be private. Empty optional fields remain absent. The original answers stay in the hidden Issue source so retries and independent policy checks reproduce the same cleaned record.

If required information or submission integrity fails validation, the bot adds `invalid`, closes the Issue automatically, explains the failure and links the normal New Project form. Any associated unmerged PR is closed and its verified branch removed through the existing cancellation cleanup. No duplicate cancellation comment is added. Temporary API or workflow failures remain retryable; they are reported separately from validation failures.

There is no replacement form or replacement-Issue chain. Use comment commands for proposal edits; do not manually edit the Issue summary, hidden source, or an already-applied command comment. Ordinary submission edits outside the command workflow fail integrity checks. The form cannot validate every free-text answer before submission.


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

CPP and KEY are absent from the submission form. SC can manage their YAML records separately; the schema still allows both, including an unassigned project with `leads: []`. Automated CPP/KEY claiming is not implemented.

## Workflows and permissions

**Project workflows** processes new-project Issue events and cancellation, and independently validates PR data. Bot PR validation reconstructs the record from the live Issue and permits only its project file. Direct PRs require a live SC-team author. Validation publishes the required commit status **Project policy** on the actual PR head. Bot-created PRs explicitly dispatch this check because `GITHUB_TOKEN` writes do not reliably trigger PR workflows.

**Project maintenance** synchronizes generated files, checks registry and SC-team access, and provides `projects.json` and combined `working_groups.yaml` as downloadable `registry-catalogues` artifacts. Source edits support both main commits and SC PRs. No unit-test suite is a mandatory workflow gate.

Use `SC_TEAM_READ_TOKEN` with organization **Members: read**, scoped to the organization. The roster is read from `Roman-HLIS-Cosmology-PIT/steering-committee`; it is not stored in the repository. Repository writes use each job's scoped `GITHUB_TOKEN`. Both workflows execute trusted main/base code, never proposed PR code or submitted commands. They reject forks and unexpected changes in reserved branches.

During testing keep `reviewers: [aguinot]`. Switching that setting to `steering_committee` later updates review routing and CODEOWNERS automatically. Also check repository/team notification settings if people have subscribed independently.

## Comment commands

Post one command per comment on the submission Issue. Edits are allowed only while both the Issue and its existing review PR are open and unmerged. The original submitter, current project leads and SC members can edit, subject to live active organization membership. Contributors and other active organization members can use help and information commands. These metadata roles do not grant repository write permissions.

Use `/help`, `/edit help`, `/edit add help`, `/edit add contributor help`, or the same help pattern for any other command. Syntax and help are defined together in `project_workflow/commands.py`, the authoritative command reference. Example comments:

```text
/edit title A new project title
```

```text
/edit description
Complete replacement description, with Markdown.

See https://example.org/method/a/b for details.
```

```text
/edit working-group SCM CPIP
```

```text
/edit deliverables SCM-1 SCM-2 ...
```

```text
/edit add contributor name=Jane Smith username=janesmith
```

```text
/edit add lead name="Jane Smith" username=janesmith
```

```text
/edit add links [Paper](https://example.org/paper) https://example.org/code
```

Names may be unquoted or quoted; when adding people, `name=` comes before `username=`. For removal, provide exactly one selector: `/edit remove lead name=Jane Smith` or `/edit remove lead username=janesmith`; the same selectors work for contributors. Names match without regard to capitalization. If several people in that role share a name, use a GitHub username to select one. Command paths are recognized only on the first line. The description is everything after the first newline, preserving whitespace, Markdown, URLs and command-like lines. Other commands are single-line. Slashes inside names, titles, descriptions and URLs are data, not additional commands.

Deliverable replacement takes any number of IDs, keeps each ID once, and replaces the complete list. IDs must exactly match the catalogues: the current SCM catalogue uses `SCM-1`, while CPIP uses `CPI-1`. Dot-separated aliases are not inferred. Groups must already be selected; unknown, ambiguous or unselected-group deliverables reject the whole edit. Group replacement removes deliverables belonging to removed groups and mentions that in the confirmation. Empty deliverable lists remain valid.

Other commands include `/edit slack #channel`, `/edit github https://github.com/OWNER/REPOSITORY`, `/edit phd true|false`, `/edit remove contributor username=janesmith`, `/edit remove lead username=janesmith`, `/edit remove links URL1 URL2`, `/edit remove slack`, `/edit remove github`, and `/edit remove deliverables`. Removing the final lead is rejected. Adding a contributor already listed as a lead/contributor is rejected; adding a lead who is currently a contributor promotes them and removes their contributor entry. The initial form still accepts only one lead.

`/deliverables` lists all groups; `/deliverables SCM` or `/deliverables SCM CPIP` filters that list. `/description` prints exact Markdown source in a copyable code block, including nested fences. After acceptance, this information command reads the trusted accepted record on main. Commands cannot edit accepted projects; later changes require SC review through the existing administrative process.

A valid command updates the original rendered Issue, the same proposal branch, the project YAML, the PR description, and the PR title when appropriate. The title command also renames the Issue. Its original title is retained in generated provenance for validation. Submission time and project ID are preserved. Every edit receives a command-specific confirmation, such as **Edit slack confirmed**, without repeating its help description; help/info also receive a reply. Retries reuse that command's reply and never create a second PR or duplicate edit.

Unlike permissive cleanup during initial submission, command edits are strict and atomic at validation: one invalid supplied value rejects the whole request. Rejected commands change no project data, do not close the Issue and do not invalidate the existing PR status. Adding duplicate links skips duplicates, and a request that already matches the record gets a no-change confirmation. Ordinary non-command comments get no bot response.

### Command integrity and recovery

Projects store a `leads` array of `{name, github_username}` objects. Applied commands are referenced in `submission.edits` by comment ID, numeric author ID, and body hash. No extra state branch or database is created. The independent PR check rebuilds the project from the original form and live GitHub comments, checking each command author's permission at that point in the lead history and their current organization membership. Human-edited YAML cannot invent a lead or change a project without a matching authorized command.

Do not edit or delete an applied command comment: its receipt will no longer match, and the PR policy check will fail. Post a new command instead. Restoring the exact original comment can repair an edit; a deleted applied comment normally requires cancelling and resubmitting. Historical command authors leaving the organization also cause validation to fail closed and require SC inspection.

GitHub does not provide a single transaction spanning a branch, Issue and PR. A valid edit is first recorded on the existing branch with a pending policy status, then the Issue/PR are synchronized and independent validation is dispatched. Brief synchronization failures are retried automatically without applying the command again. If synchronization still fails, the Actions run fails and logs the error category. The recorded edit remains blocked; run **Project workflows → Run workflow** on **main**, choose **submit**, and enter its Issue number to finish synchronization from all recorded receipts. This also recovers an Issue summary several recorded edits behind. Recovery accepts only exact summaries from authenticated history; independent PR validation still requires the final summary. After publishing a workflow fix, use this new manual run instead of rerunning an old workflow version. No extra confirmation step is required. If several comments arrive out of processing order, an older comment after a newer applied edit is rejected; post it again as a new comment.

## Install this update

This patch is based on main commit `28bf14aae08be504f9086a0650c516a6c7f764b2`. Existing proposals and their recorded commands remain valid. Use `/edit deliverables` and `/edit remove deliverables` for new commands; the old singular spelling remains readable for existing command receipts.

1. Apply the patch and review `git diff`. Keep existing proposals open.
2. Publish the update to main through your usual direct commit or SC PR process.
3. Run Project maintenance on main to refresh the form. It also runs after the publication push. No new secret or permission is required; the existing submission job handles `issue_comment` events with trusted main code.
4. Review routing remains `aguinot`. Live test the commands below before production.
5. For a title command recorded before this update, post `/edit title Your project title` again as a new comment to rename its Issue too. Do not change the previous command comment.

## Test on GitHub

- Open the New Project form and try submitting with no working groups selected. The required dropdown must block submission. Actions also rejects a missing selection if the Issue body is altered manually.
- Submit a valid EP with SCM and CAS selected, SCM deliverables selected, and CAS deliverables empty. Select a CPIP deliverable while leaving CPIP unselected; it must be absent from the generated YAML. Check the formatted Issue body, original submission date, reviewer `aguinot`, and successful **Project policy** status.
- Retry through **Project workflows → Run workflow → submit**, entering the Issue number. It must reuse its PR and branch.
- Try `/help` and help at each nested level. Use `/deliverables SCM CPIP` to see actual IDs and `/description` to copy the Markdown source.
- Add a co-lead, then post a command as that account. Try the same as a contributor: the edit must be rejected without touching the proposal. Removing the last lead must also be rejected.
- Change title, description, working groups, deliverables, links, Slack and PhD answer. Check that a title edit updates the Issue title along with its rendered body, YAML and same PR.
- Include a URL with slashes in a multiline description and add links in both plain and Markdown form. An invalid link mixed with a valid one must reject the whole command.
- Rerun a successful command Actions run: one receipt, one confirmation reply and the same PR/branch must remain. Editing/deleting an applied comment must block Project policy.
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
