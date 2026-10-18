# Accepted project wiki and automatic updates

## Reading projects

The wiki is generated from accepted `projects/P<digits>.yaml` records on `main`.
Unmerged proposals are never read or published. Each project belongs to exactly
one working group. Permanent display identifiers are `SCM1`, `SCM2`, etc.; the
internal `project_id` and filename stay stable for submission/claim replay.

Home lists CPPs first and EPs second. Each WG page has the same two tables.
Separate CPP and EP pages list their respective kind. Every table has these
columns, in order: project number, project status, working group, project kind,
first lead, title. The number and title link to the project's page. Navigation
links appear at the top of every generated page. Browser Find works on the
current page; there are no custom JavaScript filters.

Project pages show the full lead/contributor lists, catalogue deliverables,
repository, Slack channel, description, additional information, and related
links. Status badges are static Shields images with only status text/colour
in the URL; no project details or credentials are sent to that service.

| Stored status | Badge | Meaning |
| --- | --- | --- |
| `open` | Open, white | Accepted CPP awaiting a lead |
| `active` | Active, blue | Work in progress |
| `IR` | Internal review, orange | Internal review of the project's work |
| `completed` | Completed, green | Work completed |
| `paused` | Paused, grey | Work paused |

Open CPPs have a general **Claim a CPP** link. It opens `claim-cpp.yml` without
prefilling a project. Assignment still requires the existing SC claim review;
acceptance changes the CPP to active and retains its permanent identifier.

## Number allocation

`accepted.py` runs on trusted accepted main before form generation and wiki
publication. It assigns only accepted records that do not yet have a number.
CPP and EP share one sequence per group. Within a newly accepted batch, records
are ordered by original submission UTC timestamp, then internal ID for ties.
Across separate acceptances, existing numbers never move: an older submission
accepted later gets the next unused number. Strict global submission ordering
would require reserving numbers before acceptance or later renumbering.

`projects/number_registry.yaml` records every allocation, submission date and
allocation date. Record updates and ledger additions commit atomically, using
one non-forced main commit. Retries do not allocate another number. Allocation
history is retained even if a record disappears; numbers are never reused.
A numbered project's working group and number cannot change. New submissions
use the existing temporary internal key during review and consume no WG number.

The installation patch deliberately removes the five existing test records and
starts with an empty allocation ledger. It does not modify live Issues or PRs.

## Updating an accepted project

Each project page has **Update this project**, opening `update-project.yml`
with `project_number` and title prefilled. GitHub cannot make that input read
only. The workflow instead resolves the supplied number against accepted main
and verifies the actual Issue author is a current lead of that exact project or
an active member of the configured SC team. Other users are refused.

Only the number is required. Available changes are:

- Add one lead, supplying both name and GitHub username.
- Remove one lead by GitHub username; at least one lead must remain. Adding a
  replacement and removing a former lead in the same request is supported.
- Add one contributor, supplying both name and GitHub username.
- Remove one contributor by GitHub username.
- Choose Active, Internal review, Completed or Paused from a dropdown; No change
  is the default. Open CPPs retain Open until an SC-approved claim is accepted.
- Append HTTPS related links; duplicates are ignored and existing links retained.
- Replace the main GitHub repository URL.
- Replace the Additional information section with Markdown.

Blank answers preserve existing values. Identical values produce no project
commit or audit receipt. All requested changes validate together; one invalid
operation refuses the whole update. People cannot appear in both roles, but
moving between roles is possible using the corresponding add/remove fields in
one transaction. Project title, description, kind, WG and deliverables are not
editable through this form.

Authorization and Issue freshness are checked again immediately before the
non-forced main write. The record retains an audit receipt with Issue number,
author identity, body hash and application time. A lost response can be retried
without repeating additions/removals. Editing an already applied request never
replays it. A new request is needed for another update.

Updates create no PRs, requested reviews, assignees, Slack review polls or extra
comment notifications. The request closes with an Applied or No changes title.
Invalid input/unauthorized authors get a closed Refused request and explanation.
GitHub's ordinary subscription notifications still follow each user's settings.
Transient API/conflict/protection failures remain retryable; no human approval
fallback is created.

## Repository issue previews

A repository issue preview is a generated Markdown table, not an iframe or live
widget. It shows all open issues, excluding PRs, with links and a refresh time.
It refreshes after maintenance, accepted changes, or the daily 00:17 UTC run.
A manual maintenance run refreshes it on demand. No source-repository webhook
or per-project workflow installation is required.

Only public repositories are mirrored. Private/inaccessible repositories get a
link and explanatory text; private titles are never copied into the wiki.
Automation access to a repository does not imply that every wiki reader may
read it. A new unavailable response replaces any earlier preview rather than
retaining stale details. Nothing is fetched from arbitrary hosts: the existing
scoped GitHub API client accepts validated GitHub repository URLs and refuses
redirects.

## Installation

1. Apply and accept the patch on main. This introduces validator/schema support
   and deletes test records; a PR checked by the older base validator cannot
   validate the new layout. Use the maintainer's existing direct-main update
   route or merge validator support first before dependent data changes.
2. Enable the repository Wiki and create its initial page once through GitHub.
   The separate `<repository>.wiki.git` repository does not exist before that.
3. Set the Actions secret `WIKI_WRITE_TOKEN` to a wiki-capable HTTPS credential.
   A classic personal access token can use `public_repo` for a public repository,
   or `repo` for a private repository, with the required repository write access
   and organization/SSO approval. Installation tokens such as `GITHUB_TOKEN`
   do not support wiki Git access. Keep this secret limited to the publish step.
4. Ensure the main automation actor can append validated project data. Normally
   the existing job `GITHUB_TOKEN` does this. If main rules block direct writes,
   an administrator must configure an approved automation actor for the data
   writes and provide its Contents read/write credential as `PROJECT_DATA_TOKEN`.
   This optional token is used only for project-data writes, not Issue handling
   or workflow dispatch. Repository rules still apply; code never forces main
   or bypasses a refusal. Without an authorized direct-write route, fully
   automatic updates cannot work under mandatory human-approval rules.
5. Reuse `SC_TEAM_READ_TOKEN` for live SC checks. Run **Project maintenance** on
   main with no PR number. It creates the `project-update` label if missing,
   assigns accepted IDs, refreshes claim choices, exports, and publishes.
6. If `WIKI_WRITE_TOKEN` is unset, generation still succeeds and a warning links
   the missing configuration to the generated preview in the catalogue artifact.
   With a configured but invalid credential, publishing fails visibly; fix the
   credential and run maintenance again.

Generated wiki pages are owned by automation, including Home, CPP, EP, WG pages
and project-number pages. Do not hand-edit them. Other wiki pages are preserved.
A manifest tracks only generated paths for scoped cleanup; traversal/symlinks
are rejected. User-maintained information belongs in the accepted source record
through the update form, so regeneration cannot erase it.

Local generation requires no GitHub writes:

```bash
python -m project_workflow.generated_files
python -m project_workflow.wiki --repo Roman-HLIS-Cosmology-PIT/PIT-projects --output build/wiki
PYTHONPATH=project_workflow/tests python -m unittest test_accepted_wiki_updates -q
```

The daily workflow uses the same serialized project queue as intake, policy,
number allocation and updates. Bot-authored main commits do not normally start
another run, so updates explicitly dispatch maintenance; Slack acceptance already
uses that dispatch path. Human merges trigger maintenance through main pushes.
Publication retries normally by a fresh clone and non-forced wiki push.

An accepted project or update remains accepted even if wiki delivery fails.
Rerun maintenance to repair the display; do not resubmit or undo accepted data.
