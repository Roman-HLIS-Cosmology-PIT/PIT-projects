# Repository installation and configuration audit

Use this guide for a fresh installation or to check an existing one. Source
configuration is listed below; live GitHub settings, installed apps and secret
values are not proven by their presence in source. Verify existing values before
creating replacements. Slack has its own [installation guide](../slack/INSTALLATION.md).

## 1. Repository and access

The current target is `Roman-HLIS-Cosmology-PIT/PIT-projects`, with `main` as the
default branch. Keep it private and enable Issues and Actions. Grant intended
collaboration members repository read access and SC maintainers the permissions
needed for review and administration. Members submit Issues without a fork.

Check effective organization/base/team permissions: adding a restrictive team
does not remove access already granted elsewhere. Verify visibility with an
appropriate test account. Keep sensitive discussions and credentials out of
Issues, PRs and project records.

Ensure Actions policy permits the pinned actions in `.github/workflows/` and
the jobs' declared write permissions. Enable **Allow GitHub Actions to create
and approve pull requests** where required by repository/organization policy.
The implementation creates PRs; it does not manufacture human approvals. An
organization-level restriction must be resolved by an administrator.

## 2. Checked-in configuration

| Setting | Current source/default | Audit action |
| --- | --- | --- |
| SC authorization team | `Roman-HLIS-Cosmology-PIT/steering-committee` in `config.yaml` | Confirm the team slug and active membership |
| Requested reviewers | `[aguinot]` in `config.yaml` | Retain during testing; choose `reviewers: steering_committee` when intentionally switching routing |
| Submission labels | `new-project`, `new-cpp-project`, `claim-cpp` | Create all three repository labels before using forms |
| Rejection label | `invalid` | Automation creates it if needed |
| Forms / CODEOWNERS | Three `.github/ISSUE_TEMPLATE/*.yml` files and `.github/CODEOWNERS` | Generate from source, never maintain separately |
| Working groups | Root `working_groups/*.yaml` | Check IDs, descriptions and deliverables |
| Accepted registry | Root `projects/P<digits>.yaml` | Preserve IDs, scientific content and provenance |
| Slack review channel | `test-pit-projects` in `slack/channel.ts` | Follow the separate Slack guide; do not assume live variables match |

Reviewer routing and SC authorization are independent. Changing the reviewer
list does not change who may declare a CPP or administer project records.

## 3. Organization membership credential

Create or reuse an organization-approved fine-grained GitHub token with
organization **Members: read** access. Store it as the Actions secret
`SC_TEAM_READ_TOKEN`. The token must be able to read the configured organization,
SC team and memberships; approve it and authorize it for SSO where required.
Set an expiry and record a rotation owner outside the repository.

This credential is for organization reads. Repository writes use each job's
scoped `GITHUB_TOKEN`; no manually stored repository-write token is required for
normal submission processing. Do not store an SC roster in source.

## 4. Merge settings and protection

Enable squash merging for Slack acceptance and **Automatically delete head
branches** for merged proposal branches. Unmerged rejection/cancellation uses
its own verified cleanup.

Configure protection/rulesets for `main` to require the commit status
**Project policy** and an up-to-date branch. The status is attached to the exact
candidate head. It is distinct from Actions job names and Slack queue receipts.

Choose required GitHub approvals according to the SC process. Slack votes do
not create GitHub review approvals and never bypass branch protection. If human
or CODEOWNER approval is required, satisfy it before expecting automated merge.
CODEOWNER users/teams need the repository access required by GitHub.

Generation may append native outputs to an SC PR. On main it attempts a normal
generated-file commit; if protection requires review it opens/updates only
`project-config/generated`. Do not disable protection to make that fallback
disappear.

## 5. Local environment and initial generation

Use Python 3.12, matching the Actions setup. From a trusted checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r project_workflow/requirements.txt
python -m project_workflow.generated_files
python -m project_workflow.issue_form --check
python -m project_workflow.project_registry validate
python -m project_workflow.project_registry export --output-dir build
```

Review generated changes before handing them off. A fresh installation needs
the native files accepted on the default branch before GitHub exposes the forms.
Keep `working_groups/` and `projects/` data-only. Export files stay under ignored
`build/`; maintenance publishes them as `registry-catalogues`, retained for 30 days.

## 6. Verify the GitHub installation

Run **Project maintenance** on `main` without a PR number. Confirm generation,
registry export, SC-team access and the catalogue artifact succeed. If a
generated-file PR is required, review and merge it, then check maintenance again.

Use disposable submissions with the intended test reviewer:

- A member EP produces one branch and one policy-passing PR; invalid required
  input produces no PR. An unchanged retry reuses its existing PR.
- An SC CPP declaration succeeds with empty lead/contributor lists. A non-SC
  CPP declaration is rejected before publication.
- A claim of an accepted, available CPP leaves main unchanged until merge. A
  competing claim is blocked; rejecting/cancelling the first releases it.
- Commands update the same proposal. Closing an unmerged review PR closes the
  Issue and deletes only its verified branch. Merging is acceptance, never
  rejection cleanup.

These are live integration actions; run them intentionally. Unit tests do not
confirm organization policies or repository settings.

## 7. Optional Slack integration

Complete [Slack installation](../slack/INSTALLATION.md) to deploy the app,
configure triggers, add its app environment credential and set GitHub Slack
variables/secrets. Without `SLACK_APP_ID`, Slack delivery and review jobs skip;
GitHub submission and PR review still work.

Reference: [GitHub Actions repository settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).
