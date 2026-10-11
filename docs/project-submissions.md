# Submit a new project through an Issue

Organization members need only **Read** repository access. They create a
New Project Issue; GitHub Actions verifies the actual author and prepares a
project YAML PR. SC reviews and merges it. There is no email field, fork,
external service, or additional sign-in system. Wiki publication remains a
later step.

## Installation by a repository administrator

1. Copy the update files into your repository, preserving their paths. Publish
   and review this change yourself using the existing SC PR process. The old
   trusted-base validator can validate this installation PR; no project record
   is created by installing the files.
2. Keep ordinary members at **Read** and SC at the existing repository role.
   In **Settings → General → Features**, ensure **Issues** is enabled.
3. In **Issues → Labels**, create the label **`new-project`** before using the
   form. Description: `New project proposal submitted through the Issue Form`.
   The exact label name matters. Read members do not need permission to apply
   labels manually; the form applies this configured label automatically.
4. Keep the existing **SC_TEAM_READ_TOKEN** Actions secret. Its organization
   **Members: Read** permission supports both SC-team lookups and organization
   membership lookups. The token's owner must be able to read membership in
   Roman-HLIS-Cosmology-PIT. No membership roster is stored in this repository.
   Token expiration, missing credentials and API failures block validation.
5. In **Settings → Actions → General → Workflow permissions**, enable
   **Allow GitHub Actions to create and approve pull requests**. This GitHub
   checkbox is required for PR creation; these workflows never approve or
   merge PRs. The default workflow permissions can remain read-only: each
   supplied workflow requests its own scoped permissions explicitly. If the
   organization disables this setting, an organization administrator must
   allow it.
6. Protect `main` with required SC Code Owner review, dismissed stale
   approvals after new pushes, up-to-date branches, and no bypass. Require
   the **Project policy** status produced by GitHub Actions. Do **not** require
   the workflow job named `Validate project policy`: a manual dispatch runs
   on main, while the script publishes `Project policy` on the actual PR head.
   After your first test submission, confirm this status is selectable in
   the rule. Remove any duplicate/obsolete required check entries.
7. Run **Registry health** on `main` and complete the tests below before
   announcing the form.

## Direct link and future website button

After installation on `main`, use:

https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml

For a future GitHub Pages page:

```html
<a href="https://github.com/Roman-HLIS-Cosmology-PIT/PIT-projects/issues/new?template=new-project.yml">
  Submit a project
</a>
```

For a private repository, the visitor must sign in to GitHub with an account
that already has repository access. Publishing this link does not grant that
access or publish Issue contents. These files do not create or deploy a website.

## What submitters do

1. Open the link and fill out the New Project form.
2. Select one working group and one or more deliverables from that group.
   Dropdowns are static; the workflow checks the pairing after submission.
3. Enter each contributor as `Name | github_username`, one per line. Do not
   repeat the lead. Use usernames without `@`; contributors can be left blank.
4. Ordinary members select EP and enter themselves as lead. SC can also select
   CPP or KEY and leave both lead fields and contributors blank.
5. Answer Yes/No for PhD-thesis status and describe the science. Slack channel,
   the main GitHub repository and related HTTPS links are optional. No email
   or private contact information is requested.
6. Submit and wait for the automation's comment. Actions can take time to
   start; validation is asynchronous, not a live check in the form.

GitHub converts the answers into a Markdown Issue description. To correct an
error, use **⋯ → Edit** on that original description, change the answers beneath
the field headings, and **Save**. Keep every field heading, including optional
fields. An empty optional field or `_No response_` means no value. Use `Yes`
or `No` for PhD status. Deliverables may be comma-separated `SCM / SCM-1`
selections, or bare IDs such as `SCM-1, SCM-3` for the selected group.
A correction in a separate comment does not update the proposal.

The automation maintains one feedback comment. It reports specific correction
instructions or the PR link. A successful edit updates the same PR. Invalid
edits, closing the submission or removing its label fail the existing PR's
policy status. No invalid edit overwrites the last valid YAML.

Required-field checks are enforced by the workflow regardless of repository
visibility. GitHub currently documents its native required-field enforcement
as applying only to public repositories.

## PR identity, review and duplicate prevention

Issue #12 gets `projects/P000012.yaml` on `project-submissions/issue-12`.
Reserve these zero-padded, six-or-more-digit IDs for Issue submissions; SC can
continue using the existing shorter manual IDs. A collision with an accepted
record fails instead of overwriting it. The ID stays unchanged on edits.

The PR's author is GitHub Actions; authorization comes from the actual Issue
author read through GitHub's API, never the PR body or submitted usernames.
The independent policy validator rechecks active organization/SC membership,
reconstructs the proposal from the current Issue, and compares it with the
proposed YAML. Only that Issue's one new project file may differ from main.
Other project files, configuration, scripts, workflows, symlinks and submodules
cannot be added or changed through the submission adapter.

The `submission` metadata stores the Issue number, the submitting GitHub login,
and a SHA-256 digest of the submitted Issue body. This is provenance, not proof
of identity by itself. The validator verifies the live Issue separately.
Accepted old records remain valid without these new optional metadata fields.

Runs for the same Issue are serialized. Repeated events reuse the branch/PR,
including a branch left behind by an interrupted run. Merged proposals cannot
create a second PR. If SC closes a PR without merging, later edits do not
create a replacement: SC must explicitly reopen that PR or decide how to
handle the proposal. Before deleting a rejected submission branch, decide
whether that proposal should remain editable.

SC reviewers should edit the **Issue** to change proposal information. Direct
edits to generated YAML must match the Issue or the policy check fails. New
commits dismiss previous approvals when branch protection is configured as
above. The PR includes `Closes #12`; merging into `main` closes the Issue.

If main advances, run **New project submission → Run workflow**, choose `main`
and enter the Issue number. This rebuilds its branch from current main. Avoid
GitHub's **Update branch** merge button for these generated branches: their
one-parent history is maintained by the submission workflow. Ordinary authors
can also edit/save their Issue to regenerate it. Rerun open proposal checks
after organization or SC membership changes; membership changes do not
retroactively invalidate already completed GitHub statuses.

## Repository URL checks

An optional main-repository URL must be a root HTTPS GitHub repository URL,
not a branch/file URL. The workflow anonymously requests public GitHub
repository metadata. A successful response verifies accessibility. A 404 can
mean either a missing repository or an inaccessible private repository; it is
shown as a warning in both the PR and Issue for SC review. Lookup outages and
rate limits are also warnings. No organization token is sent to that lookup.
Related links are syntax-checked only; the workflow does not fetch their URLs.

## Extending groups and deliverables

Edit/add files in `working_groups/`, then run:

```bash
python -m scripts.issue_form
```

Commit the regenerated `.github/ISSUE_TEMPLATE/new-project.yml` in the same
SC PR. The form generator derives choices and help text from the catalogues;
there is no separately maintained group/deliverable list. Deliverable IDs used
by the form must contain only letters, digits, dots, underscores and hyphens.
Titles may contain normal punctuation, including commas. A group with no
deliverables can exist in the catalogue, but a submission must choose at least
one deliverable. Newly added arbitrary schema fields still need an explicit
form/parser mapping if you want to collect them through Issues.

## Local checks (no GitHub credentials or writes)

Run from your repository root with Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.project_registry validate
python -m scripts.issue_form --check
python -m unittest discover -s tests -v
python -m scripts.project_registry catalog --output build/projects.json
```

The submission tests simulate GitHub completely: they do not create Issues,
branches, comments, statuses or PRs in a real repository.

## Tests you run on GitHub after publishing

Use appropriate test data visible to repository readers. Do not merge test
projects unless you intend to retain them in the registry.

| Test | Expected result |
| --- | --- |
| Open an ordinary Issue with no `new-project` label | Submission job skipped; no PR |
| Submit a valid EP using a Read member account as its lead | One bot PR; `Project policy` passes on its head; SC review required |
| Choose SCM with a CAS deliverable, or omit a contributor username | Correction comment; no PR |
| Edit the original description to fix those errors | Same Issue becomes valid; one PR created |
| Save the same valid Issue again, or rerun its workflow | Same project ID/PR; no duplicate |
| Change an existing EP proposal to KEY as an ordinary member | PR policy fails; no new PR or unauthorized YAML update |
| Restore EP and save | Same PR gets revalidated; policy can pass again |
| Submit unassigned CPP/KEY from an SC account | PR created with both lead fields null and an empty contributor list |
| Try to submit from a nonmember (possible only if the test account can access the repo) | Membership rejected; no PR |
| Use an invalid repository URL | Validation error; no PR |
| Use a syntactically valid but inaccessible private repository URL | Warning for SC; no false claim that it does not exist |
| Close an unmerged proposal Issue or remove its submission label | Its open PR gets a failing policy status |
| Close the PR without merging, then edit the Issue | No duplicate PR; message asks SC to reopen the original PR |
| Merge a real approved proposal | YAML reaches main; associated Issue closes; Registry health passes |
| Add a working group/deliverable and regenerate the form | New choices appear after merge; validation uses the new catalogue |

Inspect **Actions → New project submission** and **Project review** if no
comment/PR appears. Common causes: missing label, disabled Issues/Actions,
expired membership token, Actions PR creation disabled, a restrictive branch
ruleset, or main changing during a run. SC can retry either workflow manually
on `main` with the existing Issue/PR number. A pending, missing or failing
`Project policy` status must prevent merging.

## Official references

- [Issue Form syntax and Markdown conversion](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
- [Form controls and validations](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)
- [Editing an Issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/editing-an-issue)
- [Organization membership API](https://docs.github.com/en/rest/orgs/members#get-organization-membership-for-a-user)
- [Workflow-token event restrictions](https://docs.github.com/en/actions/concepts/security/github_token)
- [Commit statuses](https://docs.github.com/en/rest/commits/statuses#create-a-commit-status)
- [Closing Issues through PRs](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)
