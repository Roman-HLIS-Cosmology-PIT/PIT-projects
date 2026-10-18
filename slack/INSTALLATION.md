# Slack installation

For an existing installation, audit the settings below and reuse the recorded
app, workspace, channel and trigger IDs. Do not create another app or duplicate
triggers just to apply a code/documentation update. Follow
[maintenance](MAINTENANCE.md) for deployment updates and legacy polls.

## Prerequisites

Complete [GitHub setup](../project_workflow/INSTALLATION.md) first. You need a
paid Slack workspace (or a suitable Slack developer sandbox), permission to
install/deploy the app, a private unshared channel, Slack CLI and Deno. The
repository's runner uses Slack CLI **4.10.0** and Deno **2.2.12**, installed with
archive checksums by `install-cli.sh`. SDK imports are pinned in `deno.jsonc`;
the Deno hook is pinned in `.slack/hooks.json`. Use these tested versions when
reproducing the runner environment. See Slack's
[installation instructions](https://docs.slack.dev/tools/slack-cli/).

The app uses `commands`, `chat:write`, `datastore:read`, `datastore:write`,
`groups:read` and `groups:history`, declared in `manifest.ts`. It calls GitHub
only through the manifest's allowed outgoing domain `api.github.com`.

## 1. Verify the channel

Copy the channel ID from its Slack details. Names are not routing identifiers.
The current source configuration in [channel.ts](channel.ts) is:

| Value | Checked-in setting |
| --- | --- |
| `CHANNEL_ID` | `C0C9Y1M0P6W` |
| `CHANNEL_NAME` | `test-pit-projects` |
| `TEST_BUTTONS` | `true` |

For a different installation, set the intended channel ID before deployment and
keep the GitHub `SLACK_CHANNEL_ID` variable identical. Keep the channel private
and unshared, limited to intended voters. Every member can vote and request an
override; the SC GitHub roster supplies quorum size but does not authenticate
individual Slack voters.

## 2. Validate and deploy the app

From the repository root:

```bash
cd slack
deno task check
deno task test
slack login
```

For a first installation only, deploy interactively and record the deployed
app ID (`A...`) and workspace ID (`T...`):

```bash
slack deploy
slack app list
```

For an existing installation, use its recorded IDs instead:

```bash
slack deploy --app A_APP_ID --team T_WORKSPACE_ID
```

Replace the illustrative IDs in every command with the recorded values. A local
development app from `slack run` is not the deployed app. Invite **PIT Projects**
to the configured channel using **Integrations → Add apps**. Inviting the app
elsewhere does not authorize notifications or votes there.

## 3. Create triggers once

From `slack/`, for the selected deployed app:

```bash
slack trigger create --trigger-def triggers/sync.ts --app A_APP_ID --team T_WORKSPACE_ID
slack trigger create --trigger-def triggers/commands.ts --app A_APP_ID --team T_WORKSPACE_ID
```

Record both `Ft...` trigger IDs. Save the sync webhook URL securely: it is a
credential. The bridge accepts `https://hooks.slack.com/triggers/...` and refuses
redirects. Check official Slack changes before altering that host restriction.

New installations need no vote shortcut trigger, trigger access grants or
`VOTE_TRIGGER_URL`. Direct buttons run in the existing message's pending function
execution. For old polls only, preserve their vote trigger and environment
variable as explained in [legacy compatibility](MAINTENANCE.md#legacy-polls).

Use `slack trigger update` with the recorded trigger ID when a definition changes;
deploying source does not replace an existing trigger definition automatically.

## 4. Configure the Slack-to-GitHub credential

Create a fine-grained GitHub token restricted to this repository, with **Actions:
read and write** and automatic metadata access. Obtain organization approval/SSO
authorization where required. Record its expiry and rotation owner securely.

Set it on the deployed Slack app with a prompt, avoiding a value in shell
history:

```bash
slack env set GITHUB_DISPATCH_TOKEN --app A_APP_ID --team T_WORKSPACE_ID
```

The implementation uses this credential to dispatch `slack.yml` on `main` for
thread overrides. Actions-write permission is repository-wide, not limited by
the token to one workflow. It has no Contents/PR write grant for merging; the
trusted Actions job performs GitHub decisions with its own scoped token.

## 5. Configure GitHub-to-Slack access

Generate a Slack CLI service credential using the account/workspace that can
manage the selected app:

```bash
slack auth token --team T_WORKSPACE_ID
```

Follow the challenge flow. This is a developer CLI service credential, not an
app bot token. Store it only in the Actions secret below. No extra bot token or
signing secret is used by this bridge. The Slack SDK supplies the app runtime's
Slack API access.

### Credentials and settings

Configure **Repository Settings → Secrets and variables → Actions**:

| Location/type | Name | Required value / purpose |
| --- | --- | --- |
| GitHub variable | `SLACK_APP_ID` | Selected deployed app `A...`; empty disables automatic Slack integration |
| GitHub variable | `SLACK_TEAM_ID` | Matching workspace `T...` |
| GitHub variable | `SLACK_CHANNEL_ID` | Exact ID from `channel.ts` |
| GitHub variable | `SLACK_TEST_BUTTONS` | `true` for current testing, `false` for production |
| GitHub secret | `SLACK_SERVICE_TOKEN` | CLI service credential for app datastore access |
| GitHub secret | `SLACK_SYNC_WEBHOOK` | Secret URL from the selected sync trigger |
| Existing GitHub secret | `SC_TEAM_READ_TOKEN` | Organization Members-read credential for the configured SC team |
| Slack app environment | `GITHUB_DISPATCH_TOKEN` | Repository-scoped Actions-write credential above |
| Legacy Slack environment only | `VOTE_TRIGGER_URL` | Preserve only while old workflow-button polls remain |

The bridge supplies explicit app/workspace IDs on CLI calls, never deploys or
deletes apps, and suppresses raw CLI output in failures to avoid credential or
ballot leakage. Do not publish real tokens, trigger URLs or installation notes
containing secrets in Issues or source control.

## 6. Verify end to end

Use disposable test proposals and the intended test channel:

1. Submit a new valid EP. Expect **Project submission** to pass, followed by
   **Slack delivery after successful submission** and **Slack project review**.
   Expect one waiting root and no initial duplicate thread snapshot. A CPP
   declaration produces no poll; an accepted CPP's valid claim does.
2. Have two channel members click direct buttons. No workflow-access popup
   should appear on a new poll. Changing a choice replaces the prior ballot.
   A pending sync execution in Slack activity is expected while voting is open.
3. Make a valid GitHub command edit. Expect one new information revision in the
   same thread, with votes reset. Help/no-op/invalid commands do not reset it.
4. Test **Slack project review → main → check**, optionally with the PR number.
   Leaving it blank checks pending projects. Confirm a blocked protected merge
   stays pending and never falsely reports acceptance.
5. Test `!force-accept`/`!force-refuse` in an original test thread. Verify the
   recorded request and per-PR GitHub check; queue time is expected.
6. If using synthetic quorum controls, use separate disposable test PRs. These
   counts can merge or close real test proposals; Abstain quorum stays waiting.

Do not clear datastores to make a test pass. Empty initial datastores are normal.
See [maintenance and receipt recovery](MAINTENANCE.md) for delivery failures,
channel migration, credentials and retiring legacy support.

Official references: [deployment](https://docs.slack.dev/tools/deno-slack-sdk/guides/deploying-to-slack),
[CLI authorization](https://docs.slack.dev/tools/slack-cli/guides/authorizing-the-slack-cli),
[service tokens](https://docs.slack.dev/tools/slack-cli/reference/commands/slack_auth_token/),
[CI/CD](https://docs.slack.dev/tools/slack-cli/guides/setting-up-ci-cd-with-the-slack-cli/),
and [interactive messages](https://docs.slack.dev/tools/deno-slack-sdk/guides/creating-an-interactive-message/).
