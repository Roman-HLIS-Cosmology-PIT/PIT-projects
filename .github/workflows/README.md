# Workflow operations

Use **Actions → workflow name → Run workflow**, selecting `main`. After a code
fix, start a new manual run so it uses accepted current code.

| Workflow | Automatic entry points | Manual inputs |
| --- | --- | --- |
| [Project workflows](projects.yml) | Relevant Issue/command events; PR validation and unmerged submission-PR closure | `operation=submit` or `update` + `issue_number`, or `operation=validate` + `pr_number` |
| [Project maintenance](maintenance.yml) | Main pushes and daily 00:17 UTC | Optional `pr_number`; blank maintains main |
| [Slack project review](slack.yml) | Validated-submission dispatch, matching bot PR closure, 00:00 UTC schedule | `operation=sync` or `check`; optional `pr_number` |

`submit` validates a candidate before publishing its PR. Its separate `notify`
job queues Slack from that validated result. `validate` refreshes generated
files when appropriate, then checks the live PR from its exact trusted base.
No extra maintenance PR run is needed.

For a failed submission or interrupted edit, retry `submit` with its Issue
number; it reuses the verified PR and branch. The same operation finishes
rejection cleanup for a closed, unmerged review PR. For missing Slack delivery
after successful publication, use Slack `sync` with the PR number.

Slack `check` evaluates votes and may merge or refuse a proposal. `sync` does
not start a fresh ordinary quorum decision, but may resume a frozen decision
or pending override and reconcile completed changes. It is not a read-only dry
run. See [Slack operations](../../slack/MAINTENANCE.md).

Write/decision paths share `project-submission-and-policy`, with `queue: max`
and `cancel-in-progress: false`. Each job declares its own permissions;
workflow defaults grant none. GitHub-created bot events may need explicit
dispatches, as described in [event routing](../../project_workflow/ARCHITECTURE.md#event-routing).

Accepted-project updates have a separate `update` job, triggered only by the
`project-update` label or explicit manual retry. It writes accepted data after
lead/SC validation and dispatches maintenance without PRs/reviewers. Maintenance
assigns permanent WG numbers, refreshes generated forms and exports, then
publishes the accepted wiki. See [wiki setup](../../project_workflow/WIKI.md).
