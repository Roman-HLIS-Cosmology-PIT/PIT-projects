# Intake integration boundary

New-project intake is implemented through the New Project Issue Form. See
[setup, submission, correction and tests](project-submissions.md).

The requester is the original Issue author obtained from GitHub. The workflow
checks active organization membership and the SC team from trusted CODEOWNERS,
then calls the existing record validator and authorization engine. Bot PRs are
bound to one live Issue, one reserved branch, and one project file. Their entire
proposed record must equal a reconstruction of the current Issue answers.
Labels, PR bodies and editable provenance alone never establish identity.
Proposed code is never executed with a membership credential.

Future flows for claiming CPPs and updating accepted records must use the same
identity boundary, but call authorize_change with the actual accepted record
as old rather than treating the request as a new project. They must define their
own stable request/PR mapping and allowed file changes. Wiki synchronization
and Slack notifications remain separate future work. The existing submission
flow does not automatically publish project details or contact information.
