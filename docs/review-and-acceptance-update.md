# Install the form, review-routing and acceptance update

The downloadable package has **stage-1/** and **stage-2/** directories. Copy
only the contents of the indicated stage to your repository root, preserving
paths. These are files for you to publish; the assistant has not changed GitHub.

## Publish in two stages

Your current trusted-base validator requires an SC-team wildcard CODEOWNERS
rule and the old generated form. Changing those files first would fail policy.
The upgrade avoids disabling that check.

1. **Stage 1: install the compatible code and access configuration.** Copy
   `stage-1/` into your clone. Keep your existing CODEOWNERS and Issue Form.
   Publish this stage and wait for it to reach main. The old validator can
   review these proposed files; after merge, the new validator recognizes both
   generated form versions and separates SC authorization from review routing.
2. **Stage 2: activate test-only reviews and the new form.** Copy `stage-2/`
   into your clone and publish it after stage 1 is on main. This stage replaces
   `.github/CODEOWNERS` and `.github/ISSUE_TEMPLATE/new-project.yml`. The new
   trusted-base validator can review the activation change.
3. After stage 2 reaches main, run Registry health, then submit a test Issue.
   Follow the live test table in [project-submissions.md](project-submissions.md).

If you publish directly to main yourself and your repository settings allow it,
apply the stages in the same order. If publishing through PRs, the installation
PRs still use the **old main-branch CODEOWNERS**, so GitHub may request the SC team
until stage 2 is merged. The proposed CODEOWNERS cannot silence notifications
for the PR that installs it. Draft PRs defer Code Owner requests, but marking
ready requests the old owners. Previously delivered notifications cannot be
retracted. This is a GitHub limitation, not a missing workflow setting.

There are no deleted files and no changed accepted records or working-group
catalogues. Do not copy a whole starter repository over your current records.
Optional stage patches are included; they target the repository version noted
in the package's APPLY.md. Review differences if main has changed since then.

## What you must change manually for notifications

The provided stage-2 CODEOWNERS already contains:

```text
* @aguinot
```

**You must publish that file to main.** Just editing the workflow reviewer list
would not prevent GitHub's automatic team requests. Confirm aguinot's account
(`aguinot`) has explicit Write or Admin repository access. No SC team membership
or organization role change is needed; SC authorization remains in
`config/access.yaml` as `Roman-HLIS-Cosmology-PIT/steering-committee`.

Keep required Code Owner review and Project policy. During tests, Code Owner
approval means aguinot's approval. The bot creates proposal/metadata PRs, so
aguinot can approve those. For human-authored configuration PRs, authors cannot
approve their own PRs: if aguinot is the sole Code Owner, another active SC
member must author the configuration PR. Plan the same way when restoring
production review routing.

For existing open bot proposal/metadata PRs, edit/save their Issue or rerun the
corresponding workflow after stage 2. The adapter removes outstanding reviewers
that are not in the current CODEOWNERS and requests the configured reviewer.
You can also remove old review requests manually in the PR sidebar. Neither
operation stops ordinary notifications to repository watchers or unsubscribes
people who already follow an Issue/PR. The workflows do not post SC @mentions.

If you have an organization ruleset or integration that separately requests
SC review, adjust its test routing separately; this package changes CODEOWNERS
and the supplied workflows, not external organization configuration.

After testing, restore this wildcard rule through a reviewed administrative
change:

```text
* @Roman-HLIS-Cosmology-PIT/steering-committee
```

The team must be visible and have explicit Write access for GitHub's review
routing. Keep the access configuration and membership secret as they are.

## Acceptance setup and backfill

No new secret or token is required. The supplied Record project acceptance
workflow requests scoped repository permissions and reuses SC_TEAM_READ_TOKEN.
The existing Actions-create-PR checkbox is also needed for its metadata PRs.
No automatic approval, merge, or direct main-branch write is introduced.

Merging the creation PR accepts the project. A second PR adds the original
GitHub merge timestamp, PR number and merger to the committed YAML. Review and
merge that metadata PR to retain the date. Its required Project policy check
allows only verified acceptance fields and preserves current project data.

To record the already accepted `P000001`, find its original creation PR number
and manually run **Record project acceptance** on **main** using that number.
Do not enter the Issue number unless it happens to equal the PR number. The
workflow reads the historical merge; it does not reparse the closed Issue or
replace the accepted project. Direct pushes cannot be backfilled as PR acceptance.

If main advances while a metadata PR is open, dispatch Record project acceptance
again with the original creation PR number to rebuild the same branch. Then
run a fresh Project review dispatch on main with the metadata PR number if
needed. Avoid Update branch merge commits on generated branches.

## Limits and maintenance

All five contributor pairs are optional, but half-filled pairs fail validation.
The project YAML list can grow beyond five through an SC project update PR;
a Read-member update intake is not yet implemented. The GitHub controls are
vertical, and Markdown supplies Contributors/Contributor 1–5 headings.

CAS and CPIP descriptions in your current catalogue remain placeholders. Edit
those descriptions when their expanded names are known, then regenerate the
form with `python -m scripts.issue_form`. Catalogue titles/IDs are preserved.
Older Issues remain editable with their original fields. Ordinary members cannot
edit acceptance metadata. These changes do not create the Wiki/Pages website.
