# Maintenance and recovery

Use [architecture](ARCHITECTURE.md) for implementation relationships,
[installation](INSTALLATION.md) for settings, and
[Slack maintenance](../slack/MAINTENANCE.md) for app/message recovery. Start an
LLM task with [AGENTS.md](../AGENTS.md). Work on a current trusted checkout and
preserve unrelated edits. Hand off a reviewed patch; do not push automatically.

## Routine changes

| Change | Files to edit / follow-up |
| --- | --- |
| Wording, schema, kind rules, reviewer list | `config.yaml`; regenerate forms/CODEOWNERS |
| Group or deliverable | Root catalogue YAML; update affected projects if removing references; regenerate |
| Accepted project | SC administrative YAML change; retain IDs and provenance; validate |
| Command | `commands.py` syntax/help, application and authenticated replay; update submission guide |
| Submission/policy | Intake, shared snapshot validator, independent check and failure tests; retain pre-publication boundary |
| Event routing | Workflow YAML, policy action and dispatch tests; preserve one event owner and queue |
| Slack message/vote | TypeScript plus bridge when needed; shared scenarios; manual deploy only for app changes |
| Documentation | Root entry links plus the affected code-directory guides; no files in data directories |

After source-data changes, run `generated_files`, review its diff, check forms
and validate the registry. Do not hand-edit generated outputs to hide a source
error. The protected-main generation fallback is normal operation.

## Choosing checks

Run from the repository root after installing pinned Python requirements.
These are optional local maintainer checks, not an additional CI gate.

| Change area | Relevant checks |
| --- | --- |
| Documentation only | Relative links/anchors, file paths, examples against actual CLI/config, Markdown structure; ensure diff contains only `.md` |
| Registry/schema/catalogues | `test_registry`, `test_generation`, `test_maintenance`; local validate and generated-form check |
| Submission/publication | `test_issue_submission`, `test_submission_publication`, `test_review_requests`, `test_pipeline` |
| Commands | `test_commands`, plus claim/declaration restrictions |
| CPP | `test_cpp_claims`, `test_cpp_declarations`, `test_pr_rejection` |
| API and trust checks | `test_github_adapter`, `test_team_directory`, `test_validation_checkout` |
| Slack bridge/dispatch | Python `slack/tests`, especially `test_bridge` and `test_dispatch` |
| Slack app | `deno task check`, `deno task test` from `slack/`; shared vote scenarios |

Example targeted run:

```bash
PYTHONPATH=project_workflow/tests python -m unittest test_submission_publication test_pipeline -q
```

Broader checks when justified by the change:

```bash
python -m project_workflow.issue_form --check
python -m project_workflow.project_registry validate
python -m unittest discover -s project_workflow/tests -q
python -m unittest discover -s slack/tests -q
```

Use `git diff --check`. If running actionlint, note that older versions may
reject GitHub's valid `queue: max` syntax; do not remove queue semantics to
satisfy an outdated linter. Deno first resolves pinned remote imports, so check
and test need network access or an existing cache. Report checks that could
not run. Local simulations cannot verify live settings or Slack interaction
lifecycle.

## Recovery operations

Use **Actions → workflow → Run workflow**, selecting `main`. A new manual run
uses the accepted fix; rerunning an old run may reuse its old workflow definition.

| Symptom | Operation | Expected result |
| --- | --- | --- |
| Temporary submission/preparation failure | Project workflows: `submit`, Issue number | Resume/reuse the scoped branch and any verified existing PR |
| Recorded edit not fully synchronized | Project workflows: `submit`, Issue number | Replay recorded commands and repair the same Issue/PR |
| Independent PR check needs retry | Project workflows: `validate`, PR number | Regenerate if eligible, validate the current head from its trusted base |
| Interrupted unmerged-PR rejection | Project workflows: `submit`, Issue number | Close linked Issue and remove only its verified proposal branch |
| Stale generated forms/catalogue exports | Project maintenance, blank PR number | Maintain main; protected writes may produce the one generation PR |
| Regenerate a particular SC PR | Project maintenance, that PR number | Append outputs to that existing branch, then request policy |
| Missing Slack message or failed queue/delivery | Slack project review: `sync`, PR number | Retry synchronization; inspect uncertain receipts first |
| Evaluate votes / repair terminal state | Slack project review: `check`, optional PR number | Validate live state and apply/reconcile decisions |

Slack `sync` can resume previously frozen decisions or pending overrides; it
is not a read-only diagnostic. See [Slack maintenance](../slack/MAINTENANCE.md).

## Failure interpretation

An invalid new declaration/claim is rejected before PR creation. Optional-field
cleanup alone is not a rejection. A temporary preparation/API error is
retryable. Once publication succeeds, reviewer or confirmation errors are
delivery warnings, and Slack delivery is a separate job. Do not add a rollback
step that creates then closes the PR for a failed new submission.

For an existing proposal, manual Issue/form/provenance tampering can invalidate
and cancel it. Rejected command input does not close the proposal. An applied
command that was edited/deleted blocks validation: restore the exact comment
where possible or cancel and resubmit. Never fabricate receipts or manually
copy an unverified summary into the Issue.

A staged edit that fails synchronization remains pending/blocked; rerun submit.
Commands are ordered by authenticated comment history. Reposting an older
command as a new comment may be necessary if processing arrived out of order.

An unexpectedly changed branch/history requires inspection, not a forced
overwrite or broad deletion. A missing ref can be handled by verifying the
retained PR head during rejection. Merged PRs and stale close events for a
currently reopened PR must never enter rejection cleanup.

For a stale claim target, cancel and resubmit against the accepted current CPP.
Do not erase accepted `claim` metadata to make a project available again.
Unrelated main changes do not alter a target's content fingerprint.

## Live acceptance checks

Use disposable Issues/PRs and the configured test channel intentionally. Check
one EP, an SC and non-SC CPP declaration, a valid/competing CPP claim, an
unchanged retry, a valid/invalid command, rejection and acceptance. Verify
invalid new submissions create no PR, each valid retry keeps one PR, and claim
rejection leaves accepted main unchanged. For Slack, verify no initial duplicate
reply, direct buttons without access dialogs, changed-information vote reset,
quorum/override behavior and a protection-blocked merge.

In the handoff, identify changed files, behavior, checks, skipped live checks
and any deployment requirement. Documentation and Python/workflow changes do
not require Slack app redeployment; deployed TypeScript/manifest changes do.
