> New Project Issue submissions are now implemented. Follow [the submission guide](project-submissions.md) for the additional label, Actions PR-creation setting, required head status and end-to-end tests.

# Setup and test the single repository

Run local commands from your repository root. You handle GitHub commits and
settings yourself; the supplied scripts perform no GitHub writes.

## Access model

Use one organization repository with default branch `main`. It can remain
private on GitHub while readable by the collaboration. This version has no
SC-only repository or confidential PR area.

| Group | Repository | Wiki when enabled |
| --- | --- | --- |
| Collaboration members | Read | Read |
| Existing SC team | Explicit Write | Write |
| Repository administrators / organization owners | Admin | Administrative access |
| Future publishing automation | Narrow permissions to configure later | Publish project pages |

You may keep organization-wide Read base permissions. If base permissions
are Write or higher, a repository Read grant cannot reduce them; ordinary
members must have Read, with Write reserved for SC/trusted administration.
Give the SC team explicit Write or Admin (your existing Admin grant is sufficient).
Do not grant project leads central Write
just to update a page; the later workflow will handle project-specific edits.

Enable this repository's Wiki under Settings → General → Features when ready
for the later Wiki step. Keep "Restrict editing to collaborators only" enabled
if shown. Verify that a Read-only member can view but cannot edit. Normal
`main` branch protection does not gate Wiki Git writes.

Private organization Wikis and enforced private-repository branch protection
require GitHub Team or Enterprise; Enterprise is not required.

All proposal files, history, PRs, review comments and Actions logs are visible
to readers. Include no confidential information. Actions secrets are configured
separately and never committed.

## Initial configuration

Copy the ZIP's contents, including `.github/` and `.gitignore`, into your
empty clone's root; do not nest them under another directory.

Edit the single team reference in `.github/CODEOWNERS`:

```text
* @Roman-HLIS-Cosmology-PIT/steering-committee
```

Use your existing team's org/slug from its GitHub URL:
`https://github.com/orgs/ORG/teams/TEAM-SLUG`. It must belong to this repository's
organization, be visible for CODEOWNERS, and have explicit Write or Admin access.
Keep one wildcard team rule for now: individual usernames/multiple rules are
rejected so native review and authorization use the same single identifier.

**Maintain people only in the GitHub team.** There is no SC roster file.
Active members/maintainers count; pending invitations do not. GitHub's endpoint
includes child-team members; choose team nesting deliberately.

Working groups are discovered from `working_groups/<CODE>.yaml`. Each file
requires `working_group`, `description`, and `deliverables`; the code must match
the filename. Deliverable lists can be empty, using `deliverable_ids: []`
in records. Add deliverables to the corresponding group file when available:

```yaml
working_group: SCM
description: Shape and Color Measurement
deliverables:
  - id: SCM-1
    title: Replace with the actual deliverable title
```

Keep `projects/` empty for the bootstrap; templates contain example names and
usernames. For an assigned lead, fill both `lead` (person's name) and
`lead_github_username` (account without `@`). Every contributor entry must have
both `name` and `github_username`. For an unassigned CPP/KEY, set both lead
fields to `null`; `contributors: []` is allowed.

## Read-only membership credential

The normal repository-scoped `GITHUB_TOKEN` does not supply organization
Members Read permission. The starter uses a separate read-only credential:

1. A trusted SC administrator opens their user Settings → Developer settings
   → Personal access tokens → Fine-grained tokens.
2. Create a token with the collaboration organization as Resource owner.
3. Set **Organization permissions → Members: Read-only**. No Members Write,
   repository Contents Write, or PR Write permission is required. Choose an expiry.
4. Complete organization approval if required. The account must be able to
   see the team; an SC maintainer is suitable, including for a secret team.
5. In the repository's Settings → Secrets and variables → Actions, add the
   repository secret **`SC_TEAM_READ_TOKEN`** containing the token value.

It reads team existence and the PR author's membership, cannot change anything,
and is never distributed to submitters. Missing/expired/inaccessible credentials
or API failures reject the check. No stored roster is used as a fallback.
If your organization blocks PATs, a GitHub App with Members Read can supply
an installation token instead; App token generation is not included here.

## Local checks and bootstrap

Use Python 3.12 or compatible Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.project_registry validate
python -m unittest discover -s tests -v
python -m scripts.project_registry catalog --output build/projects.json
python -m scripts.project_registry working-groups --output build/working_groups.yaml
```

Expected: `Registry valid: 0 project(s).`, all tests pass, and the catalogue
contains `"projects": []` and the working-group metadata. The combined YAML
contains every group's code, description and deliverables. Local checks use
offline fixtures; no credential is
needed. Production PR checks do live membership lookups.

Commit/push the bootstrap to `main` yourself before enabling protection.
In Settings → Actions → General, allow GitHub-owned `actions/checkout` and
`actions/setup-python` and their dependencies if allowlisted. Keep permissions
read-only by default, and enable "Allow GitHub Actions to create and approve pull requests" for the submission workflow. The supplied workflow only creates PRs; it never approves or merges them. Use the supplied hosted runners.

**Registry health** should pass, including its live check that the SC team is
accessible. It can also run manually on `main`.

## First PR and branch protection

As an active SC member, create a branch in this repository, copy
`templates/project.yaml` to `projects/P001.yaml`, fill in project information
and real names/GitHub usernames for the lead and each contributor, and open
a PR to `main`. Expected: **Project policy** passes,
Code Owner review is requested where applicable, and P001 is absent from
`main` until merged. An ordinary member can read the PR but cannot push or merge.

In Settings → Branches, protect `main` with:

- Required PR, at least one approving review, and required Code Owner review.
- Stale approvals dismissed after new commits, approval of the most recent
  reviewable push, and conversation resolution.
- Required **Project policy** check from GitHub Actions. Run the first test
  PR if it has not yet appeared in GitHub's selection list.
- Branches required to be up to date before merge.
- "Do not allow bypassing the above settings", including administrators.
- SC-only push restriction where offered, with force push/deletion disabled.

Use a second SC account for approval; proposers cannot approve their own PR.
No merge queue is supported yet. Do not require **Registry health** on PRs,
since it runs on `main`. Check that approval cannot bypass a failed policy check.

## Test records and live membership

Push each invalid change in a test PR, verify failed check and blocked merge,
then fix it and verify success:

| Change | Expected |
| --- | --- |
| Valid EP with one lead name and GitHub username | Pass |
| Missing lead GitHub username, lead is a list, or EP lead is null | Fail |
| Only one of the lead name/username fields is null | Fail |
| Contributor missing name or GitHub username; blank person name | Fail |
| SC CPP/KEY declaration with both lead fields null and empty contributors | Pass |
| Unknown kind, status, group, or field | Fail |
| Unknown deliverable or ID from another working group | Fail |
| New correctly formatted group file with a project referencing it | Pass without schema/code edits |
| Working-group filename/code mismatch, blank description, duplicate ID or unknown catalogue field | Fail |
| Removing a group/deliverable still used by a project | Fail; migrate the affected project in the same PR |
| Missing/null GitHub URL | Pass |
| Non-GitHub URL or repository subpage URL | Fail |
| Repeated contributor GitHub username (even with different names), or lead username repeated in contributors | Fail |
| Different accounts with the same person's display name | Pass; reviewers confirm their identities |
| ID different from filename | Fail |
| Duplicate YAML keys, aliases or Python tags | Fail |

To verify GitHub is the only membership source:

1. Run a designated test SC account's PR check while active in the team: pass.
2. Remove it from the team and rerun its existing PR check as a remaining SC
   administrator: the original PR author must be rejected.
3. Add it back, ensure active membership, and rerun: pass without changing files.
4. Verify a pending invitation grants no SC authority.
5. Test a missing/invalid secret in a temporary repository: fail closed.

Use a test account: removal also removes access inherited from the team.
Later team edits do not retroactively revoke completed checks; rerun open PR
checks after changes. Proposed CODEOWNERS edits cannot self-authorize a PR:
the lookup uses the reference on the trusted base commit.

After approval and passing checks, merge the valid PR. P001 should be on
`main`, visible to members, and Registry health should pass. Wiki automation
does not run yet. Deleting an accepted record fails; use `status: completed`.
Closing without merging leaves the proposal outside the accepted registry,
but the closed PR remains visible to the collaboration.

## Simulate ordinary-member rules

Copy `templates/project.yaml` to `/tmp/test-ep.yaml`, set `lead: Alice Example`
and `lead_github_username: alice`, and run:

```bash
python -m scripts.project_registry simulate --actor alice --member --new /tmp/test-ep.yaml
```

Expected: allowed. Change kind to CPP/KEY: denied. Add `--sc` to simulate SC:
allowed. These flags are simulation assumptions, not authentication.

For a claim, copy `templates/unassigned-cpp.yaml` to `/tmp/cpp-old.yaml`, copy
again to `/tmp/cpp-new.yaml`, and set the new lead fields to
`lead: Alice Example` and `lead_github_username: alice`:

```bash
python -m scripts.project_registry simulate --actor alice --member --old /tmp/cpp-old.yaml --new /tmp/cpp-new.yaml
```

Expected: allowed. KEY in both files or an already assigned old CPP: denied.
The separate creation workflow will connect these member policies later.

## Evolution and troubleshooting

For this directory change on an existing repository, follow the two-step
[migration instructions](working-groups.md) so trusted-base PR validation can
read the new layout before you move the files.

Add fields/statuses/groups through an SC PR as described in README. The whole
proposed registry is checked; migrate records for incompatible changes.
Run proposed code tests locally; the PR check executes trusted base code only.

- Team 403/404: verify slug, token resource owner, Members Read permission,
  organization approval and team visibility to the credential's account.
- Team 401: replace expired/revoked credentials in the Actions secret.
- SC denied: check the PR author's membership, not the rerun actor.
- No Code Owner review: verify explicit team Write access and visibility.
- No check: workflows must be on `main`, Actions enabled, PR targeting `main`,
  and the initial adapter using a branch in this repository.
- PR changed during validation: rerun; the validator fails closed.
- Invalid record mergeable: fix required checks/admin bypass settings.
- Ordinary member can edit Wiki: check Write grants/Wiki settings; branch
  protection does not control Wiki writes.

References: [team lookup](https://docs.github.com/en/rest/teams/members#get-team-membership-for-a-user),
[tokens](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token),
[Wiki access](https://docs.github.com/en/communities/documenting-your-project-with-wikis/changing-access-permissions-for-wikis).
