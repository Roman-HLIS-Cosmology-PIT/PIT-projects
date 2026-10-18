# AI maintenance entry point

Read [architecture](project_workflow/ARCHITECTURE.md) before changing workflow
behavior and [maintenance](project_workflow/MAINTENANCE.md) before choosing checks
or a recovery action. Setup is documented in
[repository installation](project_workflow/INSTALLATION.md) and
[Slack installation](slack/INSTALLATION.md). Source code and workflow YAML are
authoritative if documentation diverges.

## Working agreement

- Prepare local changes and downloadable patches. Do not push changes.
- Do not merge/close live PRs or Issues, deploy Slack, send test notifications,
  change settings or rotate credentials unless the maintainer explicitly
  requests that action. Simulated tests need no external writes.
- Preserve unrelated changes. Inspect the current base and worktree before edits.
- Never put tokens, webhook URLs, membership rosters or individual ballots in
  documentation, fixtures, commit messages or logs.
- Keep the root README short. Keep detailed Markdown alongside code under
  `project_workflow/`, `slack/` and `.github/`. Keep `projects/` and
  `working_groups/` free of documentation and other non-data files.

## Sources of truth

| Change | Authoritative source |
| --- | --- |
| Schema, form wording, kind policy, SC team and reviewers | `project_workflow/config.yaml` |
| Working groups and deliverables | `working_groups/<CODE>.yaml` |
| Accepted projects | `projects/<ID>.yaml` on `main`; permanent `project_number` allocated after acceptance |
| Number history | `projects/number_registry.yaml`; append allocations, never reuse numbers |
| Wiki and automatic updates | `project_workflow/accepted.py`, `updates.py`, `wiki.py`; see `WIKI.md` |
| Commands, syntax, help and replay | `project_workflow/commands.py` |
| Event routing and job permissions | `.github/workflows/*.yml` |
| Slack channel and test controls | `slack/channel.ts` plus matching GitHub variables |
| Slack scopes, storage and handlers | `slack/manifest.ts`, `datastores.ts`, `functions/` |

Generate forms and CODEOWNERS with `python -m project_workflow.generated_files`.
Do not maintain another copy by hand. Keep `working_groups/` at the root.

## Invariants to preserve

1. Execute trusted `main`/exact PR-base code. Proposed code, submitted Markdown,
   commands and workflow artifacts are data, never executable authority.
2. Validate the exact candidate and live requester permissions before creating a
   new submission PR. Preparation failure must never create then close a PR.
   Reviewer/Issue/Slack delivery errors after publication are separate follow-up
   failures. Reconcile uncertain creation responses before retrying.
3. One Issue owns one `project-submissions/issue-<number>` branch and associated
   bot PR. Check history, scope and live SHA before updating or deleting a ref.
4. Reconstruct edits from retained form answers and authenticated live comments.
   Stored YAML, rendered summaries and labels alone cannot authorize changes.
5. CPP declarations require SC. CPP claims preserve the accepted scientific
   definition, check target fingerprints and reserve one open claim per CPP.
6. Merging accepts; closing an unmerged proposal rejects and cleans up only its
   verified branch. Merged work must never be treated as rejected.
7. Keep one non-cancelling `project-submission-and-policy` queue. Initial Issue
   intake uses the submission-label event. Do not add duplicate `opened` intake,
   maintenance PR triggers or another post-publication intake policy pass.
8. Slack notifies EPs and CPP claims only. Decisions revalidate live GitHub data
   and merge with an exact head SHA. Channel membership is the voter trust
   boundary; SC roster size is a count, not an identity mapping.
9. Preserve immutable ballot events, delivery receipts and legacy vote support
   while old polls remain. Never reset datastores to repair one message.
10. Do not add a mandatory full unit-test CI gate or automatic Slack deployment.
    Run relevant optional tests locally and document verification limits.

## Before handing off

Use the [test matrix](project_workflow/MAINTENANCE.md#choosing-checks), check
generated outputs after source-data changes, and update affected guides and
architecture tables. Documentation-only edits need link/path and interface
checks. Do not add runtime changes just to accommodate documentation.

PR policy executes the accepted base implementation. A newly supported schema
or data layout may require merging validator support first, then rebasing its
dependent content PR. Never execute proposed validator code to make its own PR
pass. Describe this dependency in the handoff if it applies.

## Accepted project publication and updates

See [wiki architecture and setup](project_workflow/WIKI.md). Enforce exactly one
WG. Permanent WG numbers are allocated only from accepted main, atomically with
an append-only allocation ledger. Internal IDs remain stable for claim replay.
Author updates use live lead/SC authorization on the exact selected accepted
project; recheck Issue/main freshness and roles immediately before publication.
Do not create PRs or request human reviews for these automatic updates. Preserve
at least one lead and protect open CPP assignment through the SC claim process.
Retain update receipts and distinguish refusal from retryable delivery failure.
The wiki is a projection, never the source of author-maintained information.
