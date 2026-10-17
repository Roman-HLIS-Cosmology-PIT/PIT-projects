# PIT Projects: Slack SC review

This is a Slack-hosted Deno app with a GitHub Actions bridge. No poll vendor or separate server is needed. A paid Slack workspace is required. Deployment is manual. The existing Issue forms, editing commands, project policy and review PRs remain authoritative.

## 1. Remove only the old dummy app

Do this in the **old** `pit-slack-dummy-vote` directory on your Mac, before installing this app. The patch cannot delete a workspace app by itself.

```sh
cd ~/Downloads/pit-slack-dummy-vote
slack app list
```

Identify the deployed **PIT Dummy Vote** app and its workspace. Record their exact `A...` and `T...` IDs. Then substitute those two IDs:

```sh
slack app delete --app A_OLD_DUMMY_APP_ID --team T_YOUR_WORKSPACE_ID
```

Read the CLI confirmation and proceed only if it identifies **PIT Dummy Vote**. Do not use `--force`, workspace-wide cleanup, or another app's ID. This permanently removes that app, its hosted functions/workflows, triggers, environment and datastores. Other workspace apps are unaffected. If `slack app list` shows a separate local-development dummy app, repeat only for that explicitly identified dummy app ID. Stop any old `slack run` process first. An uninstall alone is insufficient: Slack retains datastores after uninstalling.

Slack message history is separate from the app's datastores. If old dummy messages remain, delete only those specific **PIT Dummy Vote** messages in `test-pit-projects` using Slack's message menu and the permissions available to you. Do not clear the channel history. Once deletion is confirmed, the old local dummy directory/ZIP can be removed manually; neither is used by this app.

Official references: [Delete an app](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_app_delete/) and [download/delete datastore data](https://slack.com/help/articles/15945329427859-Download-or-delete-an-apps-datastore).

## 2. Apply the patch

From a clean checkout of `Roman-HLIS-Cosmology-PIT/PIT-projects`, based on main commit `7b666b93c16125ec79259d51b972ea0d2eaa0c42`:

```sh
git apply --check /path/to/pit-slack-review.patch
git apply /path/to/pit-slack-review.patch
```

Review and merge these changes into main through your normal process. Workflow dispatches and the schedule use main. All app and bridge implementation lives in `slack/`; `.github/workflows/slack.yml` is the Actions entry point. The existing project workflows are not replaced. Before configuring `SLACK_APP_ID`, the new Actions job skips automatically.

## 3. Configure the private test channel and deploy

Open **test-pit-projects → channel name → About** and copy its channel ID. The channel must be private and unshared. Edit `slack/channel.ts`, replacing `CHANNEL_ID` with that ID. Keep `CHANNEL_NAME = "test-pit-projects"` and `TEST_BUTTONS = true` for now. Never put a channel name in place of the ID.

From the repository:

```sh
cd slack
deno task check
deno task test
slack login
slack deploy
slack app list
```

Select your workspace when prompted. This creates the new **PIT Projects** app, not an update to an unrelated app. Record its deployed app ID and workspace ID. Subsequent commands below use those explicit IDs. If your shell cannot find Slack CLI on macOS, add `$HOME/.local/bin` and `$HOME/.slack/bin` to PATH in `~/.zshrc` and open a new terminal.

Invite **PIT Projects** to `test-pit-projects` using the channel's Integrations → Add apps. Only that configured channel receives proposals. Inviting the app elsewhere does not enable votes or notifications there.

## 4. Create the three triggers once

Run these from `slack/`, substituting the new app/workspace IDs:

```sh
slack trigger create --trigger-def triggers/vote.ts --app A_NEW_APP_ID --team T_WORKSPACE_ID
slack trigger create --trigger-def triggers/sync.ts --app A_NEW_APP_ID --team T_WORKSPACE_ID
slack trigger create --trigger-def triggers/commands.ts --app A_NEW_APP_ID --team T_WORKSPACE_ID
```

Record all three trigger IDs. Keep the **vote shortcut URL** (`https://slack.com/shortcuts/...`) and the **sync webhook URL** (`https://hooks.slack.com/triggers/...`). Treat the webhook URL as a secret. Do not add these URLs to source control, issues or logs.

Grant members of the private test channel access to the vote trigger:

```sh
slack trigger access --trigger-id Ft_VOTE_TRIGGER_ID --grant --channels C_TEST_CHANNEL_ID --app A_NEW_APP_ID --team T_WORKSPACE_ID
slack trigger access --trigger-id Ft_VOTE_TRIGGER_ID --info --app A_NEW_APP_ID --team T_WORKSPACE_ID
```

Use the actual copied channel ID, including its original prefix. Do not grant everyone access. The app also checks the actual interaction channel, original message timestamp and current proposal revision; editable button parameters are not authorization.

Set the vote URL on the deployed app. These commands prompt for values, avoiding secrets in shell history:

```sh
slack env set VOTE_TRIGGER_URL --app A_NEW_APP_ID --team T_WORKSPACE_ID
slack env set GITHUB_DISPATCH_TOKEN --app A_NEW_APP_ID --team T_WORKSPACE_ID
```

For `GITHUB_DISPATCH_TOKEN`, create a fine-grained GitHub token owned/approved by the repository organization, restricted to **PIT-projects**, with **Actions: Read and write** plus automatic Metadata read. It only dispatches `slack.yml`; Slack does not receive a token able to merge PRs or edit repository contents. Set an expiry and rotate it before expiration. Organization approval/SSO policies may apply. The merge/close happens with the GitHub Actions token in the trusted repository job.

If your CLI reports a webhook URL under a different official Slack host, check the current Slack documentation before changing the bridge's explicit host validation.

## 5. Configure GitHub

From `slack/`, generate a CLI service token for CI using the official flow:

```sh
slack auth token --team T_WORKSPACE_ID
```

Use the same account/workspace that can manage the new app. Follow Slack's challenge instructions. Store the resulting token in Actions secrets, not in the repository. This is a developer CLI credential, not an app bot token. The bridge always supplies the new app ID and workspace ID; it contains no deploy, uninstall or delete operations. See [Slack's official CI/CD guide](https://docs.slack.dev/tools/slack-cli/guides/setting-up-ci-cd-with-the-slack-cli/).

In **GitHub repository → Settings → Secrets and variables → Actions**, configure:

| Type | Name | Value |
| --- | --- | --- |
| Variable | `SLACK_APP_ID` | New PIT Projects deployed `A...` ID |
| Variable | `SLACK_TEAM_ID` | Slack workspace `T...` ID |
| Variable | `SLACK_CHANNEL_ID` | Exactly the same channel ID as `slack/channel.ts` |
| Variable | `SLACK_TEST_BUTTONS` | `true` while testing |
| Secret | `SLACK_SERVICE_TOKEN` | CLI service token generated above |
| Secret | `SLACK_SYNC_WEBHOOK` | Secret sync trigger URL |
| Existing secret | `SC_TEAM_READ_TOKEN` | Organization Members: Read access to the configured SC team |

The SC team is read from `project_workflow/config.yaml`, currently `Roman-HLIS-Cosmology-PIT/steering-committee`. The bridge paginates its members, includes maintainers, deduplicates logins, and stores only their count. An empty/inaccessible team blocks checking; there is no guessed fallback quorum. No GitHub/Slack identity mapping is used. Keep the private channel limited to the intended SC voters: every member can vote and issue an override. Slack IDs are stored internally to allow one current ballot per person and shown only through Slack interactions, never exported to repository files or workflow logs.

Ensure Actions can create/update project PRs as already required by this repository. Enable **squash merging** and **Automatically delete head branches**. Existing branch protection and required approvals still apply to automated acceptance. An override bypasses voting, not repository protections.

## 6. Test the integration

1. Submit a valid EP through the existing form. After **Project workflows** succeeds, **Slack project review** synchronizes it. Expect one root message headed **🟠 Waiting**, type **New EP**, title, working groups, leads, contributors, description and review PR link. A full snapshot appears in its thread. Very long root fields are shortened to fit Slack; full text is split across thread messages without truncation.
2. Submit a CPP declaration: no Slack message. A subsequent valid claim on an accepted, available CPP produces its own **CPP claim** message.
3. Click Accept, Refuse or Abstain. The original message updates its tally. A new choice replaces your previous ballot; simultaneous voters use separate event records. Use Refresh if concurrent display updates briefly show an older tally. Decisions use stored events, never the displayed count.
4. Try **TEST: Accept quorum**, **TEST: Refuse quorum**, and **TEST: Abstain quorum** on separate test proposals. Each sets that option to the current quorum and the other counts to zero, with a visible TEST label. A later real vote clears the simulation and restores the real ballots. **These test counts can merge or close the real test PR at the next check.** Abstain quorum remains waiting.
5. In **Actions → Slack project review → Run workflow**, choose main, operation `check`, and optionally the PR number. Leaving it empty checks all pending projects. Choose `sync` to refresh messages without starting new decisions. This manual check is a permanent feature.
6. In a waiting project's **original thread**, post exactly `!force-accept` or `!force-refuse`. The app records the request and immediately dispatches a per-PR check. GitHub Actions startup/queue time still applies; this is not a zero-second merge. Dispatch failures leave the request for the next nightly/manual check and appear in app activity logs. Commands elsewhere are ignored. The most recent valid override for the current revision wins; deleting its message is not a retraction.
7. Make a valid edit with the existing GitHub comment commands. Expect a new full snapshot in the same thread, an updated root and reset votes/overrides. No-op/help/invalid edits do not create a new version. Old buttons on stale revisions are rejected.
8. Check concurrent proposals, acceptance, refusal and a blocked merge. Accepted appears only after GitHub confirms merging. Refused appears only after it confirms closure without merging; the existing rejection cleanup closes the Issue and removes only the verified proposal branch. Both preserve the root Slack message. A blocked decision stays orange, closes voting temporarily, and retries on a later check; inspect the Actions log and satisfy the required checks/reviews.

## Voting rules and scheduling

SC count is refreshed for each valid synchronization/check. With `N` members, participation quorum is `floor(N / 2) + 1` (six members → four voters). Abstentions count toward participation but not the Accept/Refuse majority. Once participation reaches quorum, Accept > Refuse accepts and Refuse > Accept refuses; a tie or abstentions alone stays waiting. More distinct voters than SC members blocks the decision and requires inspecting channel membership. Counts do not prove identity: channel membership is the agreed trust boundary.

The schedule is **00:00 UTC** in `.github/workflows/slack.yml`; change its cron for another schedule. GitHub may delay scheduled runs or disable schedules in inactive public repositories. The manual trigger remains available. Vote clicks only update Slack, so a passing majority can still change before checking. A decision freezes its cutoff and SC count while GitHub applies it. Later ordinary ballots do not affect that frozen attempt. A newer thread override can replace a blocked pending decision until GitHub confirms merging/closure. A changed proposal starts a new vote instead.

All bridge runs share the existing `project-submission-and-policy` concurrency queue, using GitHub's [documented `queue: max` setting](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency). Some actionlint releases do not yet recognize that setting. The bridge reads only trusted main code, replays the existing policy against live GitHub data, and merges using an exact head-SHA precondition. Proposed files are data, never executed. It explicitly requests project maintenance after merging because a `GITHUB_TOKEN` merge does not trigger another push workflow. Restoring an earlier project version still creates a fresh voting revision; it never restores its old ballots.

## Maintenance and recovery

- Inspect **Actions → Slack project review** and `slack activity --app A_NEW_APP_ID --team T_WORKSPACE_ID`. Errors redact credentials and voter identities. Check credentials, private channel membership, trigger access, branch protection and SC token access before retrying.
- After code changes, run the checks below and manually `slack deploy --app A_NEW_APP_ID --team T_WORKSPACE_ID`. Normal GitHub runs never deploy. Keep trigger IDs/URLs in your installation notes. Use `slack trigger update --trigger-id Ft_ID --trigger-def triggers/FILE.ts --app A_NEW_APP_ID --team T_WORKSPACE_ID` when a trigger definition changes, rather than creating duplicates.
- Separate `pit_projects` metadata, immutable `pit_ballots` events and `pit_messages` receipts prevent one voter overwriting another. Historical events/receipts are retained for recovery; datastore usage should be monitored. Do not delete pending ballots. A later retention policy can purge finalized projects after your desired audit period.
- A merge/close response can be lost after GitHub applied it. The next check reads GitHub before taking another decision and repairs the Slack status and Issue cleanup. Terminal records remain eligible for recovery until reconciliation completes. For a specific finalized project, run a manual check with its PR number.
- Delivery reserves a receipt before posting, to avoid silently reposting after an uncertain Slack response. If synchronization times out, inspect app activity first. `slack datastore get '{"datastore":"pit_messages","id":"pr-42"}' --app A_NEW_APP_ID --team T_WORKSPACE_ID --output json` shows the root receipt. If its `ts` is empty, find the existing PIT Projects message and set that receipt's `ts` to its actual Slack timestamp using `slack datastore put`. The item must contain exactly `id`, `channel` and `ts`. Revision receipts use `pr-42:FULL_REVISION_HASH`. If a reserved revision was only partly posted, complete its missing information manually in the original thread and mark its receipt with the root timestamp. If you verify no message was sent, delete **only that individual empty receipt** and run `sync` again. Never clear a whole datastore to fix one proposal. Avoid overlapping manual webhook invocations; the GitHub bridge waits for an explicit acknowledgement and serializes its own deliveries.
- Rotate `SLACK_SERVICE_TOKEN`, `SLACK_SYNC_WEBHOOK` (recreate only its trigger), and `GITHUB_DISPATCH_TOKEN` when needed. Revoke obsolete credentials. The installed Deno/CLI versions are pinned in `slack/install-cli.sh`; update and recheck them together with the SDK imports. The Linux CLI archive is checksum-verified.
- To pause integration, clear the GitHub variable `SLACK_APP_ID` and disable/delete this app's command trigger by its recorded ID. To retire the new app, use `slack app delete` with **its** explicit ID/workspace as in step 1. This never calls deletion against another app.

Before moving to the real SC channel: finish pending tests; set `TEST_BUTTONS = false` in `channel.ts` and GitHub variable `SLACK_TEST_BUTTONS=false`; change the channel ID in both places; deploy; update the command trigger's channel and the vote trigger's channel access. Existing test polls cannot be silently rerouted because their channel is bound in stored metadata. Close/finish test submissions first. Ordinary updates in the same channel reuse the app, triggers, datastores and original messages.

## Local checks

```sh
# Repository root
python -m pip install -r project_workflow/requirements.txt
python -m unittest discover -s project_workflow/tests -q
python -m unittest discover -s slack/tests -q
cd slack
deno task check
deno task test
```

Python and TypeScript use the same voting scenarios in `tests/cases.json`. Bridge tests exercise real submission validation with in-memory GitHub/Slack adapters, including CPP exclusion, claims, protected merges, lost merge responses and rejection cleanup. Live deployment, Slack permissions, organization token approval and repository branch protection still need the channel test above.
