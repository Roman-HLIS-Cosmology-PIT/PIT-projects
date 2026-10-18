# Slack SC review

The Slack-hosted Deno app displays EP proposals and CPP claims in the configured
private SC channel. CPP declarations have no poll. A Python GitHub Actions
bridge validates proposals, refreshes messages and applies confirmed decisions;
there is no separate poll vendor or application server.

[Install or audit the integration](INSTALLATION.md) ·
[Update and recover it](MAINTENANCE.md) ·
[Implementation architecture](../project_workflow/ARCHITECTURE.md#slack-data-flow)

## Voting

The original message shows waiting/accepted/refused state, project kind, title,
groups, leads, contributors, description, PR link and votes. Accept, Refuse and
Abstain buttons update that message directly. A new choice replaces your
previous vote; Refresh repairs a temporarily stale displayed tally. No initial
duplicate information reply is posted. A valid information change posts a new
snapshot in the same thread and resets votes/overrides.

With `N` GitHub SC members, participation quorum is `floor(N/2)+1`. Abstentions
count toward participation. After quorum, an Accept majority over Refuse
accepts; a Refuse majority over Accept refuses. A tie or abstentions alone stays
waiting. More distinct voters than SC members blocks a decision. The private
channel's membership defines who may vote; there is no GitHub/Slack identity
mapping.

Ordinary clicks update Slack only. **Slack project review** checks at 00:00 UTC
or through its permanent manual `check` operation. A displayed majority may
change before checking. The bridge freezes a decision attempt while applying it
and revalidates live GitHub data. Acceptance/refusal appears only after GitHub
confirms merge/closure. Repository protections and required reviews still apply.

In the original project's thread, post exactly `!force-accept` or
`!force-refuse` to record an override and request an immediate per-PR check.
Actions startup and queue time still apply. The newest valid override for that
revision wins; deleting its Slack message is not a retraction. Commands elsewhere
are ignored. Overrides do not bypass repository protection.

## Current test mode

The checked-in channel is `test-pit-projects`, with test controls enabled in
`channel.ts`. Matching GitHub variables must be checked separately. Test buttons
set synthetic Accept, Refuse or Abstain counts to quorum. They can cause real
test PR decisions on the next check. A later real vote clears the simulation.
Disable controls in both source and GitHub configuration before SC production
use; see [channel migration](MAINTENANCE.md#changing-channels-and-test-mode).

## Code map

| Path | Responsibility |
| --- | --- |
| `manifest.ts`, `workflows.ts`, `datastores.ts` | App capabilities, workflow definitions and storage |
| `channel.ts` | Channel binding and test controls |
| `bridge.py` | Trusted GitHub validation, synchronization and decisions |
| `functions/sync.ts`, `functions/runtime.ts` | Root message, direct interactions and receipt-based delivery |
| `functions/vote.ts`, `functions/command.ts` | Ballots and thread overrides |
| `logic.ts`, `blocks.ts` | Tally reduction and rendering |
| `triggers/sync.ts`, `triggers/commands.ts` | Webhook and private-channel message-event triggers |
| `triggers/vote.ts` | Compatibility for polls created before direct buttons |
| `install-cli.sh`, `deno.jsonc` | Pinned runner tools and SDK imports |
| `tests/` | Bridge/dispatch simulations, runtime tests and shared vote scenarios |

New polls keep one sync function pending to own ordinary buttons for the review
lifetime. That pending activity is expected. Legacy polls may still need their
old vote trigger and access flow; do not delete compatibility state during an
ordinary update.
