# Submitting and managing a project

Use the [links on the main page](../README.md) for the submission forms. Accepted projects have a separate [update form](WIKI.md#updating-an-accepted-project) linked from their wiki page.
Repository read access and active organization membership are required; no fork
or write permission is needed. The actual authenticated Issue author is checked,
so entering somebody else's username does not grant their role.

## Choose a form

| Form | Submission and edit roles | Result |
| --- | --- | --- |
| EP | Active members submit; submitter, current leads or SC can edit | New Enhancing Project |
| CPP declaration | Active SC members submit and edit | Unassigned Critical Path Project |
| CPP claim | Active members submit; claimant, current proposed leads or SC can edit | Assignment request for one accepted, available CPP |

Ordinary members must submit themselves as the initial lead. SC may submit on
another lead's behalf. All editing roles require active organization membership.
KEY projects use the SC YAML route; there is no KEY form.

An EP collects a title, lead name and GitHub username, exactly one working group,
description and the PhD answer currently present in the form. Optional fields
include deliverables, Slack, repository, links and up to five contributor pairs.
A selected group may have no deliverables. Deliverables for unselected groups
are ignored.

A CPP declaration omits leads, contributors and PhD information. A claim asks
for lead details and one available CPP, plus optional people, Slack, repository
and links. It cannot redefine the title, description, groups or deliverables.
Valid supplied Slack/repository values replace existing ones; blanks preserve
them. Links append with deduplication.
A valid claim's Issue and PR are named `Claim CPP: <project title>`.

## Submission and review

1. Submit and stay on the Issue page for feedback. Refresh if necessary.
2. Automation checks the exact candidate and live permissions before creating
   a PR. Invalid required inputs close the Issue with an explanation and a
   resubmission link. Invalid optional entries are discarded and listed.
3. A valid submission has one Issue-number branch and one review PR. The Issue
   becomes a readable summary. Original form answers remain hidden in that
   body for verification; do not edit either representation manually.
4. Successful feedback links the PR. Reviewer assignment and Slack delivery may
   need a separate retry without making the validated submission a failure.
5. Merging accepts and closes the linked Issue. Closing an unmerged PR rejects
   it, closes the Issue and deletes only its verified proposal branch. Closing
   the Issue before acceptance cancels its proposal. Reopening a cancelled or
   rejected submission does not restart it; use a new form.

Acceptance keeps the original submission date. Accepted YAML on `main` is the
registry; an open Issue, branch or Slack message is not an accepted project.

## CPP availability

Only accepted, active CPPs with no leads and no previous accepted claim are
available. One open claim reserves a CPP. Rejection or cancellation releases
that pending reservation without changing the accepted record. An accepted
claim retains its provenance even if SC later removes its leads; this does not
automatically offer the CPP for another assignment.

The dropdown refreshes after main changes. Live checks reject unavailable
targets even if an old form still lists them. If SC changes the accepted target
during review, cancel the stale claim and submit against the updated record.

## Commands on an open submission Issue

Post one command per new comment. `/help` and nested help, such as
`/edit add contributor help`, show the authoritative syntax for the project kind.
Other active members may use information/help commands. Commands cannot edit
an accepted project.

```text
/edit title A clearer project title
```

```text
/edit description
Complete replacement description, including Markdown and URLs.
```

```text
/edit working-group SCM CPIP
```

```text
/edit deliverables SCM-1 SCM-2
```

```text
/edit add contributor name=Jane Smith username=janesmith
```

```text
/edit remove contributor name=Jane Smith
```

```text
/edit add links [Paper](https://example.org/paper) https://example.org/code
```

| Command family | Behavior |
| --- | --- |
| `/edit title`, `/edit description` | Replace the value; title also renames the Issue and PR |
| `/edit working-group`, `/edit deliverables` | Replace the single WG, or replace its deliverables; changing WG removes the previous group’s deliverables |
| `/edit add lead`, `/edit add contributor` | Require `name=` before `username=`; quotes around names are optional |
| `/edit remove lead`, `/edit remove contributor` | Use exactly one `name=` or `username=` selector; ambiguous names need a username |
| `/edit slack`, `/edit github`, `/edit phd true` | Set a field where the project kind allows it |
| `/edit add links`, `/edit remove links` | Add/remove HTTPS links; additions skip duplicates |
| `/edit remove slack`, `/edit remove github`, `/edit remove deliverables` | Clear optional values |
| `/deliverables SCM CPIP` | List exact catalogue IDs; omit groups to list all |
| `/description` | Print “Project description:” and copyable Markdown source |

Only description replacement is multiline. Deliverable IDs must match the
catalogue exactly: SCM uses `SCM-1`, while CPIP currently uses `CPI-1`.
Dot-separated aliases are not inferred. The last lead cannot be removed.
Adding a current contributor as a lead promotes that person.

CPP declarations reject people and PhD commands. Claims allow people, links,
Slack and repository changes, but reject changes to scientific definition and
PhD information, including for SC. Use `/help` for applicable commands.

Initial optional cleanup is permissive; edits are strict. One invalid value
rejects the whole command. Successful edits update the same Issue, branch, YAML
and PR with a command-specific confirmation. No-op edits retain existing values;
ordinary comments get no bot response.

Do not edit or delete an applied command comment. Its author and exact contents
are verification history. Post another command instead. If an edit was recorded
but synchronization failed, ask SC to use
[submission recovery](MAINTENANCE.md#recovery-operations).
