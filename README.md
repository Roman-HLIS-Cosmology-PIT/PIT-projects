# PIT projects

Project proposals are submitted through a GitHub Issue Form and reviewed through a pull request. Repository readers can submit Issues without write access, a fork, or another account system. Only information suitable for all repository readers belongs here.

[Submit a new EP project](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml)

[Declare a CPP (SC only)](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-cpp-project.yml)

[Claim an available CPP](https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=claim-cpp.yml)

## Slack SC review

The optional [Slack app and installation/maintenance guide](slack/README.md) publish valid new EPs and CPP claims in the private `test-pit-projects` channel. CPP declarations produce no Slack notification. Embedded Accept, Refuse and Abstain buttons update the original message. GitHub checks participation and applies decisions at midnight UTC, or through **Actions → Slack project review → Run workflow**. Thread commands `!force-accept` and `!force-refuse` request an immediate check, subject to Actions queue time and existing repository protections.

The SC size comes from the configured GitHub team; quorum is `floor(members / 2) + 1`. Valid proposal changes post a new information snapshot in the thread and reset votes. The testing version also has three clearly labeled synthetic-quorum buttons. Installation and deployment remain manual; GitHub never deploys or deletes Slack apps.

## Submission and review

1. Use the Issue title as the project title. Enter the required lead name, GitHub username, description and PhD answer. Ordinary members must submit as their own lead; SC members may submit on another person's behalf.
2. Select one or more working groups in the required multiple-selection dropdown. Each group has its own optional deliverable menu, allowing several selections. A selected group may have no deliverables. Deliverables selected for unselected groups are ignored.
3. Submit and stay on the Issue page for the automated result. This form always submits an **EP**; CPP declarations use the separate SC form below. The workflow checks the actual Issue author's live organization membership and required information. Invalid optional contributors, deliverable selections, repository links, related links and Slack entries are removed with an explanation; they do not block the project.
4. A valid submission creates one project YAML file on `project-submissions/issue-<number>` and one PR. The bot reformats the Issue body into a readable summary, links `/help` for edits, and requests review from the configured reviewer, currently **aguinot**. Retrying an unchanged submission reuses the same PR and branch.
5. Merging the PR accepts the project and closes the Issue through `Closes #<number>`. The bot posts an acceptance message on that Issue. Enable GitHub's **Automatically delete head branches** repository setting to remove merged branches.

The workflow stores the Issue's creation timestamp as `submission.submitted_at`. There is no acceptance-date update or second PR. The Issue body displays the readable project summary. Its native form answers are retained in a hidden, compressed comment in that same body for validation and retry recovery. No saved-answer database, state branch or correction form is added. Very long submissions retain their original body if formatting would exceed GitHub’s size limit.

After successful submission the bot says **Your project has been submitted**, links the PR and explains that the submitter will be notified after review and acceptance. If optional entries were removed, it lists the omissions and reasons and says they can be added later. The formatted Issue and generated YAML contain the retained values. Edits can be requested with the comment commands below while the proposal PR remains open.

Optional cleanup keeps the first valid contributor for each username, excludes the lead from contributors, removes incomplete/malformed/overlong contributors, removes unknown/duplicate deliverables within selected groups, removes malformed main repository URLs, removes invalid related links, keeps the first occurrence of duplicate links and the first 100 valid links, and removes an invalid/overlong Slack entry. A correctly formatted repository URL that cannot be verified is kept with a warning because it may be private. Empty optional fields remain absent. The original answers stay in the hidden Issue source so retries and independent policy checks reproduce the same cleaned record.

If required information or submission integrity fails validation, the bot adds `invalid`, closes the Issue automatically, explains the failure and links the corresponding EP or CPP form. Any associated unmerged PR is closed and its verified branch removed through the existing cancellation cleanup. No duplicate cancellation comment is added.

Temporary API or workflow failures before publication remain retryable and do not create a PR. Intake validates the exact candidate commit, registry, Issue integrity and live permissions, and writes a checks-passed Issue receipt before creating the PR; creation is the final required submission write. Reviewer assignment and Issue confirmation are subsequent delivery tasks: a failure is reported as pending follow-up on a successfully submitted proposal, and does not turn it into a failed submission. Slack queuing runs in a separate job after successful submission. Retry delivery with Project workflows, operation `submit`, and the same Issue number; its existing PR is reused. A lost PR-creation response is reconciled against the reserved submission branch before retrying.

There is no replacement form or replacement-Issue chain. Use comment commands for proposal edits; do not manually edit the Issue summary, hidden source, or an already-applied command comment. Ordinary submission edits outside the command workflow fail integrity checks. The form cannot validate every free-text answer before submission.


Closing a submission Issue before acceptance cancels it: the workflow closes its unmerged PR and removes only its verified submission branch. This cleanup works without a membership token. Reopening a cancelled submission does not resurrect it. Closing an automated review PR without merging rejects the submission: the workflow closes its linked Issue as not planned and deletes only its verified proposal branch. This applies to EP proposals, CPP declarations and CPP claims, including submissions corrected with edit commands. A merged PR is never treated as rejected. A rejected submission requires a new Issue rather than reopening its old PR. After acceptance, changes go through an SC-authored PR or an authorized direct main commit.

### Working groups and deliverables

The working-group dropdown allows multiple selections, is required, and has no default. Actions also checks that at least one valid group was selected. One **Deliverables** section contains the separate optional dropdowns for each group. Its introduction explains that deliverables for unselected groups are ignored. GitHub cannot conditionally disable these menus; they start empty so optional deliverables are not selected accidentally. A group with an empty catalogue remains selectable but has no deliverable dropdown.

## CPP declarations (SC only)

Use **Declare a CPP (SC only)** to declare an unassigned Critical Path Project. The workflow checks the actual Issue author's live active organization and steering-committee membership. The form may be visible to all repository readers; a non-SC submission is rejected and closed without a proposal branch or PR.

The CPP form shares the EP form's title, working groups, optional deliverable menus, description, Slack channel, main repository and related links. It omits all lead, contributor and PhD inputs. Generated records have `kind: CPP`, `leads: []`, and `contributors: []`; `phd_thesis` is omitted. The submitter remains in submission provenance without becoming a lead. CPP Issue and PR summaries omit these removed sections and identify the kind correctly.

CPP declarations follow the same review PR, reviewer routing (`aguinot` during testing), independent policy check, retry, cancellation and acceptance process as EP proposals. Only live SC members may edit a CPP declaration while its Issue and review PR remain open. Retained-field commands work as documented below. Lead/contributor add/remove commands and `/edit phd` are unavailable, including for SC, and are omitted from CPP command help. They are also rejected during independent command replay. Ordinary active members may still use information/help commands.

The declaration form applies only `new-cpp-project`; the EP form applies only `new-project`. Keep the original label throughout review. Both labels together are rejected; changing a label cannot convert an existing proposal. The new label must exist in the repository before the CPP form is used; see installation below. Shared wording and catalogues generate both forms from `project_workflow/config.yaml` and `working_groups/`.

CPP assignment now uses the separate claim form described below. KEY declarations use the SC YAML process; post-acceptance member editing is not supported. Existing accepted records are preserved.

## Claiming a CPP

Any active collaboration member can use **Claim a CPP** without repository write access or a fork. The Issue starts with title `Claim CPP`. Enter the required lead name and GitHub username, then select exactly one project in the required dropdown. Ordinary members must claim as themselves; SC can submit on another lead's behalf, as with EP submissions. Only active, accepted CPPs on main with no leads and no previous accepted claim are eligible. EP, KEY, paused, internal-review and completed projects cannot be claimed. A declaration under review is not yet eligible.

The rest of the form contains optional Slack channel, main GitHub repository, related links and contributor pairs. A supplied valid Slack/repository value replaces the declaration's value; a blank or discarded invalid optional value preserves it. New links append to the original list, with duplicates removed and the schema's combined-list limit respected. There is one links list: claim edits can remove links originally supplied by SC. Valid contributors are combined without repeating usernames or the lead. Initial optional cleanup and strict atomic edit validation follow the existing submission rules. No PhD answer, scientific description, working groups or deliverables are collected.

A valid submission renames its Issue and review PR to **Claim CPP: <project title>**. It updates the existing `projects/<ID>.yaml` on an Issue-scoped proposal branch, preserving project ID, status, scientific definition and original declaration provenance. Separate `claim` provenance records the claim Issue, requester, date, native-answer hash, canonical-title hash, accepted-record hash and authenticated edit receipts. Manually declared CPP records without `submission` provenance are supported. The accepted record on main is unchanged until SC merges the claim PR. Review is routed to `aguinot` during testing.

Only one open claim PR is allowed per CPP. The serialized intake and independent policy validator check this live, so concurrent submissions and old form pages cannot assign the same project twice. A pending target can remain visible in the static dropdown until acceptance; a second claim is rejected with the existing claim Issue identified. Closing the claim Issue cancels its PR and removes only its verified branch. Closing the claim PR without merging also closes the claim Issue and deletes its verified branch. Its reservation is released, so another member can submit a fresh claim for the still-eligible CPP. The accepted project and its available-project dropdown entry remain unchanged. Stale closure events for a currently reopened or merged PR do not trigger cleanup; rejected submissions require a new Issue. Cancellation or rejection leaves the accepted CPP unchanged.

While both the claim Issue and PR are open, the original claimant, current proposed leads and SC can edit people, Slack, main repository and related links, subject to live organization membership. Existing add/remove commands apply, including removal by name or username; the last lead cannot be removed. The selected project stays fixed. Title, description, working-group, deliverable and PhD commands are unavailable in a claim Issue, including through nested help or replay. SC changes to the scientific definition use the existing administrative route. `/description` and `/deliverables` remain informational. After acceptance, claim commands no longer edit the accepted project.

Policy checks reconstruct the exact proposed change from the accepted CPP, retained form and live comments. A claim can modify only leads, contributors, Slack, repository, related links and its claim provenance. Forged YAML, edited/deleted applied comments, label/selection/title tampering, missing records and changed requester permissions block acceptance. If SC changes the accepted target while review is pending, validation blocks the stale claim; cancel it and submit again against the updated project. Unrelated changes to main do not invalidate the target's fingerprint. Main maintenance marks pending claims for fresh independent policy checks. Require **Project policy** and an up-to-date branch under the repository's branch protection/ruleset so a stale green status cannot authorize a merge over newer main changes.

### Available-project refresh

**Project maintenance** regenerates the claim dropdown from the registry after main pushes, including accepted declarations, accepted claims, completion, assignment and deletion of a project record. It also handles registry changes on direct SC PRs. If main protection prevents a generated-file commit, its existing synchronization PR updates the form after review. Live submission and PR checks reject an unavailable project even before the generated dropdown catches up.

Accepted claims are tracked in the project record, independently of the claim Issue or branch. Deleting the branch/Issue or later removing its leads does not automatically offer that CPP again: retain `claim` provenance. Releasing an accepted claim for another assignment is outside this workflow and would require an explicit SC administrative change. A record removed from main is absent from the dropdown; retiring it with `status: completed` is the existing supported alternative to deleting registry history. If no projects are eligible, the dropdown shows **No CPP projects available** and no claim can pass validation.

## Files and routine changes

- `project_workflow/config.yaml`: form wording, schema, kind rules, reviewer routing and live SC team identifier.
- `working_groups/<CODE>.yaml`: a group's description and deliverables. This directory remains at the repository root and defines the allowed groups.
- `projects/<ID>.yaml`: accepted machine-readable records.
- `project_workflow/`: parsing, validation, PR creation and generation code; optional offline behavior tests.
- `.github/`: native GitHub forms, CODEOWNERS and two workflows.

Change the authoritative source only. **Project maintenance** regenerates all native forms and CODEOWNERS after a main commit or on an SC-authored PR. On an SC PR, generation appends the outputs to that same branch. If main protection rejects an automatic generation commit, it creates a synchronization PR containing only generated outputs. The generator executes accepted main code and reads proposed configuration as data.

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

The EP form stays fixed to EP. CPP has its own SC-only declaration form. KEY records can still be managed through the existing SC YAML process; no KEY form or KEY claim capability is added.

## Workflows and permissions

**Project workflows** processes EP/CPP submission and claim Issue events, unmerged review PR closures and cancellation, and independently validates PR data. Bot PR validation reconstructs the record from the live Issue and permits only its project file. Direct PRs require a live SC-team author. Validation publishes the required commit status **Project policy** on the actual PR head. Initial intake listens to the form's submission-label event rather than both `opened` and `labeled`. Submission and command runs use the same snapshot policy validator before publishing their candidate, then publish the required status on that exact head. They do not repeat validation after PR creation or dispatch a second project workflow. Their separate Slack delivery job verifies the live PR head's passing status and per-head dispatch receipt before queuing Slack. Human PR runs refresh generated files before checking the resulting live head. Reopening a bot submission uses the submission path alone. Changes to PR prose and unrelated Issue labels do not repeat policy processing. Explicit validation dispatch remains available for maintenance, pending-claim refreshes and standalone callers.

**Project maintenance** handles main pushes and manual maintenance. It synchronizes generated files, checks registry and SC-team access, and provides `projects.json` and combined `working_groups.yaml` as downloadable `registry-catalogues` artifacts using one runner and one combined export command. SC PR generation belongs to **Project workflows**, so maintenance does not start a competing PR run. Manual per-PR maintenance remains available. Submission, generation, policy and Slack decisions share the existing non-cancelling queue to avoid concurrent bot writes. No unit-test suite is a mandatory workflow gate.

`project_workflow/policy/action.yml` owns the shared trusted-base checkout, validation and per-head Slack dispatch receipt. Only validated EP proposals and CPP claims queue Slack; CPP declarations do not. The Python setup action caches dependencies by the pinned requirements file and installs them once per version within each job, including when policy switches to an older trusted base. The GitHub adapter caches only immutable trees and blobs within one process; refs, Issues, PRs, comments and membership checks remain live.

Use `SC_TEAM_READ_TOKEN` with organization **Members: read**, scoped to the organization. The roster is read from `Roman-HLIS-Cosmology-PIT/steering-committee`; it is not stored in the repository. Repository writes use each job's scoped `GITHUB_TOKEN`. Both workflows execute trusted main/base code, never proposed PR code or submitted commands. They reject forks and unexpected changes in reserved branches.

During testing keep `reviewers: [aguinot]`. Switching that setting to `steering_committee` later updates review routing and CODEOWNERS automatically. Also check repository/team notification settings if people have subscribed independently.

## Comment commands

Post one command per comment on the submission Issue. Edits are allowed only while both the Issue and its existing review PR are open and unmerged. For EP, the original submitter, current project leads and SC members can edit, subject to live active organization membership. CPP declaration edits require live SC membership and cover only retained fields. CPP claim edits use the claimant/current-lead/SC rule and the restricted claim fields described above. Contributors and other active organization members can use help and information commands. These metadata roles do not grant repository write permissions.

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

Other commands include `/edit slack #channel`, `/edit github https://github.com/OWNER/REPOSITORY`, `/edit phd true|false`, `/edit remove contributor username=janesmith`, `/edit remove lead username=janesmith`, `/edit remove links URL1 URL2`, `/edit remove slack`, `/edit remove github`, and `/edit remove deliverables`. Removing the final lead is rejected. Adding a contributor already listed as a lead/contributor is rejected; adding a lead who is currently a contributor promotes them and removes their contributor entry. The EP form still accepts only one lead; the CPP declaration form accepts no people or PhD information.

`/deliverables` lists all groups; `/deliverables SCM` or `/deliverables SCM CPIP` filters that list. `/description` prints exact Markdown source in a copyable code block, including nested fences. After acceptance, this information command reads the trusted accepted record on main. Commands cannot edit accepted projects; later changes require SC review through the existing administrative process.

A valid command updates the original rendered Issue, the same proposal branch, the project YAML, the PR description, and the PR title when appropriate. The title command also renames the Issue. Its original title is retained in generated provenance for validation. Submission time and project ID are preserved. Every edit receives a command-specific confirmation, such as **Edit slack confirmed**, without repeating its help description; help/info also receive a reply. Retries reuse that command's reply and never create a second PR or duplicate edit.

Unlike permissive cleanup during initial submission, command edits are strict and atomic at validation: one invalid supplied value rejects the whole request. Rejected commands change no project data, do not close the Issue and do not invalidate the existing PR status. Adding duplicate links skips duplicates, and a request that already matches the record gets a no-change confirmation. Ordinary non-command comments get no bot response.

### Command integrity and recovery

Projects store a `leads` array of `{name, github_username}` objects. Applied commands are referenced in `submission.edits` by comment ID, numeric author ID, and body hash. No extra state branch or database is created. The independent PR check rebuilds the project from the original form and live GitHub comments, checking each command author's permission at that point in the lead history and their current organization membership. Human-edited YAML cannot invent a lead or change a project without a matching authorized command.

Do not edit or delete an applied command comment: its receipt will no longer match, and the PR policy check will fail. Post a new command instead. Restoring the exact original comment can repair an edit; a deleted applied comment normally requires cancelling and resubmitting. Historical command authors leaving the organization also cause validation to fail closed and require SC inspection.

GitHub does not provide a single transaction spanning a branch, Issue and PR. A valid edit is first recorded on the existing branch with a pending policy status, then the Issue/PR are synchronized and independent validation runs in the same job. Brief synchronization failures are retried automatically without applying the command again. If synchronization still fails, the Actions run fails and logs the error category. The recorded edit remains blocked; run **Project workflows → Run workflow** on **main**, choose **submit**, and enter its Issue number to finish synchronization from all recorded receipts. This also recovers an Issue summary several recorded edits behind. Recovery accepts only exact summaries from authenticated history; independent PR validation still requires the final summary. After publishing a workflow fix, use this new manual run instead of rerunning an old workflow version. No extra confirmation step is required. If several comments arrive out of processing order, an older comment after a newer applied edit is rejected; post it again as a new comment.

## Rejection cleanup and recovery

Closing an automated review PR without merging terminates its submission. To finish cleanup after an interrupted or older closure, use **Project workflows → Run workflow → main → operation `submit` → its Issue number** to run the same rejection cleanup. This retry also works without a membership token. No registry record or available-project list needs a manual edit after rejection.

The workflow re-reads live PR state, verifies bot identity, repository, target branch, Issue binding and the scoped proposal commit before closing the Issue. It deletes only the verified reserved branch. A missing ref is supported by verifying the retained PR head; retries finish interrupted deletion without duplicating the cancellation comment. Cleanup does not require SC membership lookup. Unrelated/human/fork PRs, merged PRs and stale close events for reopened PRs are excluded. If a scoped branch contains unrelated changes or advances unexpectedly, cleanup fails safely and SC must inspect it.

## Test on GitHub

- Submit and correct an EP, CPP declaration and CPP claim. Close each review PR without merging. Expect a closed Issue, cancellation feedback and deletion of only that proposal branch.
- After rejecting a claim, submit a new claim as a different member for the same CPP. The accepted record must remain unchanged until the new claim is merged.
- Merge a proposal instead: normal acceptance must apply, with no rejection feedback or rejection-based branch deletion.
- Retry cleanup and try a PR whose branch was manually deleted. The Issue must still close without duplicate comments or changes to other proposals. Membership-token availability must not affect rejection cleanup.

- As an ordinary member, claim one accepted unassigned CPP. Confirm lead fields first, a single project dropdown, canonical Issue/PR titles, reviewer `aguinot`, original declaration metadata preserved, and main unchanged before merge.
- Supply replacement Slack/repository values, additional links and contributors. Try blank optional fields too. Remove an SC-declared link with a claim command.
- Try title/description/group/deliverable/PhD edits: reject without data/status changes. Add/remove co-leads and contributors; block removal of the final lead.
- Submit two claims for one CPP. Only one PR may remain open. Cancel the first and submit a fresh claim: the reservation is released.
- Change the accepted CPP through SC while a claim is pending. Maintenance must revalidate it; the stale claim must not overwrite the changed record. Try a removed or completed target and an old form page too.
- Merge a claim and wait for maintenance. The target must leave the dropdown and remain unavailable after branch cleanup or subsequent removal of its leads while claim provenance is retained. Remove/retire another eligible CPP and confirm it leaves the dropdown.

- Submit a CPP as an SC member. Confirm no lead/contributor/PhD form sections, correct CPP summary, empty people lists, omitted `phd_thesis`, and one review PR requesting `aguinot`.
- Submit the same CPP form as an ordinary member. Expect an explanatory failure, `invalid`, a closed Issue, and no proposal branch/PR.
- On a CPP declaration, edit each retained field as SC. Try people/PhD commands as SC and a retained-field edit as an ordinary member: all must reject without changing the proposal. CPP `/help` must list only applicable commands.
- Retry a CPP submission/edit, close another CPP Issue to cancel, and merge a third to accept. Confirm the existing EP behavior below is preserved.

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
python -m project_workflow.project_registry export --output-dir build
python -m unittest discover -s project_workflow/tests
python -m unittest discover -s slack/tests
```
