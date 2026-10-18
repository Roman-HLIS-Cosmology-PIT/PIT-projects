# Slack maintenance and recovery

Use the [installation guide](INSTALLATION.md) for the full setting/credential
inventory and [architecture](../project_workflow/ARCHITECTURE.md#slack-data-flow)
for state ownership. Reuse the existing app, workspace, channel, triggers,
messages and datastores during ordinary maintenance.

## Updating the installation

| Change | Required action |
| --- | --- |
| Python bridge, GitHub workflow or Markdown | Accept on main; no Slack deployment |
| Slack TypeScript, manifest, datastore definition or SDK | Run local app checks; manually deploy to the recorded app/workspace |
| Trigger definition | Update the existing recorded trigger after the compatible app deployment |
| Channel / test controls | Follow the channel migration procedure below |
| Credential | Replace only the relevant secret/app environment value; revoke the old credential after verification |

From `slack/`, for app changes:

```bash
deno task check
deno task test
slack deploy --app A_APP_ID --team T_WORKSPACE_ID
```

For an existing command-trigger definition change, substitute its recorded ID:

```bash
slack trigger update --trigger-id Ft_COMMAND_TRIGGER_ID --trigger-def triggers/commands.ts --app A_APP_ID --team T_WORKSPACE_ID
```

Do not create duplicate triggers as an update strategy. The CI job installs
pinned CLI/Deno versions for datastore access; it contains no app deployment or
deletion operation. Check SDK/hooks and archive checksums when intentionally
updating dependency versions.

## Inspecting and retrying

Inspect **Actions → Slack project review**, then app activity if needed:

```bash
slack activity --app A_APP_ID --team T_WORKSPACE_ID
```

Check app/workspace/channel bindings, private-channel membership, token expiry,
SC-team access and repository protection before retrying. Avoid copying raw
CLI responses, webhook URLs or voter data into public logs.

| Operation on main | Behavior |
| --- | --- |
| `sync`, PR number | Synchronize an individual poll and GitHub-confirmed status |
| `check`, PR number | Revalidate and apply/reconcile that proposal's decision |
| `check`, blank PR number | Process open eligible and unreconciled tracked proposals |

`sync` does not initiate a fresh ordinary quorum decision, but can resume a
previously frozen decision or pending override and reconcile terminal state.
It can update GitHub while refreshing a stale proposal. It is not a read-only
dry run. Use the pause procedure if decisions must stop.

The automatic `PIT Slack sync queued` status proves a dispatch, not delivery.
A failed dispatch leaves it retryable; a delivery failure after a successful
dispatch needs a manual Slack `sync`. Repeated synchronization of unchanged
project information reuses the root/revision rather than resetting votes or
posting another snapshot.

A lost merge/close response is reconciled from live GitHub state on the next
check. Acceptance appears only after a confirmed merge; refusal only after a
confirmed unmerged closure. Use a per-PR `check` to repair a finalized proposal
whose Issue/maintenance reconciliation was interrupted. Never infer failure
just from an API response being lost.

## Uncertain message delivery

Delivery reserves `pit_messages` receipts before posting. If a webhook call
times out or the acknowledgement is missing, inspect Slack activity and actual
messages before retrying or changing receipts. Webhook HTTP 200 is not a
delivery acknowledgement.

From `slack/`, a read-only inspection example is:

```bash
slack datastore get '{"datastore":"pit_messages","id":"pr-42"}' --app A_APP_ID --team T_WORKSPACE_ID --output json
```

Replace `42` with the affected PR. An empty `ts` means a reserved root delivery
has an uncertain result. Locate any existing message and repair only that
receipt using `slack datastore put`, with exactly `id`, `channel`, `ts` and the
actual Slack message timestamp. Record the existing item before an intentional
repair.

Revision receipts are `pr-42:FULL_REVISION_HASH`. If a revision was only partly
posted, complete missing information in its original thread and mark that
revision receipt with the root timestamp. Only when you have verified no
message was sent should you delete the individual empty receipt and retry
`sync`. Never reset a datastore or delete pending ballots to fix one poll.
Avoid overlapping manual webhook calls; the GitHub bridge serializes deliveries
and waits for explicit `ack:<delivery>` receipts.

An empty datastore is normal on first installation. CLI 4.10.0 omits `items`
when empty; the bridge treats this as an empty page and still follows cursors.
`Incomplete Slack datastore query` instead signals malformed output, an error
or a mismatched datastore and should be inspected, not repaired with dummy rows.

## Legacy polls

Polls created before direct buttons may still use the vote workflow and show
Slack's workflow access dialog. Their old sync execution has already completed;
updating text does not migrate its interaction lifecycle. Finish/close those
tests and create a fresh proposal to verify direct buttons. Do not delete roots,
votes or receipts to force conversion. An already posted duplicate reply can
be removed manually through Slack's message UI when appropriate; new polls do
not post that initial reply.

Keep `triggers/vote.ts`, its deployed trigger, its channel access and
`VOTE_TRIGGER_URL` while any old polls remain open. New installations do not
need them. Once all legacy polls finish, an administrator may remove only the
recorded legacy vote trigger. Keep sync and command triggers. Removing source
compatibility is optional and should be a separate reviewed code change.

## Changing channels and test mode

Finish or close pending test proposals first. Existing polls bind repository,
channel and original message in stored metadata and cannot be silently rerouted.

For production, set `TEST_BUTTONS=false` in `channel.ts` and
`SLACK_TEST_BUTTONS=false` in GitHub. Set the new channel ID in source and
`SLACK_CHANNEL_ID`; deploy the app, invite it to the private unshared channel,
and update the recorded command trigger's channel definition. Retained legacy
vote triggers also need the appropriate channel access if still in use.
Changing only a GitHub variable does not redeploy app code or update a trigger.

Test controls require the configured test channel and source/runtime controls.
They synthesize counts that can cause real GitHub decisions. Ordinary channel
updates reuse the same app/trigger identities; verify private-channel voter
membership before accepting new polls.

## Credentials, pause and retirement

Rotate the GitHub-side service token and webhook secret and the Slack app's
`GITHUB_DISPATCH_TOKEN` before expiry or after exposure. Recreate/update only
the affected trigger when replacing a webhook, update its secret, verify the
binding and revoke obsolete credentials. Keep actual values outside source.

To pause automatic integration, clear GitHub `SLACK_APP_ID` and disable the
recorded command trigger so overrides cannot request new runs. If a decision
job is already running, inspect/cancel that specific run before treating the
pause as complete. This does not delete the app or historical polls.

App deletion and datastore cleanup are separate administrator operations:
verify exact app/workspace IDs and retention needs before using Slack's deletion
flow. Ordinary GitHub workflows never deploy, uninstall, delete apps or reset
their datastores. Monitor datastore usage; any eventual finalized-event
retention policy needs a separate decision and implementation.

## Local checks and live verification

From the repository root:

```bash
python -m unittest discover -s slack/tests -q
```

From `slack/`:

```bash
deno task check
deno task test
```

Python and TypeScript share vote scenarios in `tests/cases.json`. Bridge tests
simulate actual proposal validation, protected merges, uncertain API responses,
claims and rejection cleanup. SDK/runtime tests cannot verify a deployed
pending-function lifecycle, workspace permissions or real organization policy.
After app deployment, intentionally verify new direct buttons with two channel
members, one changed-information revision and a manual GitHub check.
