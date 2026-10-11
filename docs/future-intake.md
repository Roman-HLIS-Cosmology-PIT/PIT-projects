# Contract for the separate creation workflow

This is the next integration boundary, not a deployed submission mechanism.
The registry and its future Wiki are in the **same repository**. All proposal
PRs are readable by the collaboration; request only appropriate information.

Before connecting ordinary-member submissions:

1. Authenticate the requester using GitHub identity. Dispatch inputs, PR bodies
   and YAML usernames are not proof of identity.
2. Verify organization membership with a trusted GitHub API lookup.
3. Read the accepted record and configuration from the current base commit.
4. Determine SC authority using `TeamDirectory.is_active_member`, with the
   team from base CODEOWNERS. Never store a duplicate member list.
5. Collect the lead's name and separate `lead_github_username`, and each
   contributor's paired `name`/`github_username`. Use usernames, never display
   names, for authorization. Require both lead fields for assignments/claims,
   or both null for an unassigned CPP/KEY. Validate with `validate_record`,
   then call `authorize_change` using verified `organization_member` and `is_sc`
   values. Creation/claim usernames must match the authenticated requester.
6. For creation, reserve an unused ID and require an absent old record. For
   claim/update, require an existing record. Enforce the requested operation,
   rechecking if the base changes. Claims cannot overwrite an assigned lead.
7. Create a PR through organization-controlled automation and bind requester,
   operation, exact data and head commit to verifiable request provenance.
8. Extend `validate_pr.py` to recognize that provenance. The initial adapter
   accepts SC-authored PRs only. Never make a bot SC as a shortcut; that would
   bypass kind restrictions, protected fields and configuration permissions.
9. Require SC approval before acceptance. Creating a PR is not acceptance.
10. Preserve trusted-base execution. Intake changes project data, not code,
    policy, credentials or authorization references.

Read access does not allow pushing central branches or running central
workflows. The separate creation workflow will provide a simple submission
path without granting members central Write access.

CLI identity flags are local simulations. They do not prove membership or
request origin. Missing/failed team lookups must reject authorization.

Wiki publication will target this repository's own `.wiki.git` remote. Its
backing Git store is separate, but no second normal GitHub repository is
needed. Publishing, project-page updates from user repos/forks, notifications
and CLI helpers remain later steps.

Central credentials remain in trusted Actions secrets or a GitHub App. Never
commit or distribute them to submitters.
