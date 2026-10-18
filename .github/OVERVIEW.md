# GitHub integration

| Path | Role | Edit directly? |
| --- | --- | --- |
| `ISSUE_TEMPLATE/new-project.yml` | EP form | No; generated |
| `ISSUE_TEMPLATE/new-cpp-project.yml` | SC CPP declaration form | No; generated |
| `ISSUE_TEMPLATE/claim-cpp.yml` | Available-CPP claim form | No; generated |
| `ISSUE_TEMPLATE/update-project.yml` | Automatic lead/SC project updates | No; generated |
| `CODEOWNERS` | Reviewer routing | No; generated |
| `pull_request_template.md` | Human PR review checklist | Yes |
| `workflows/` | Actions triggers, permissions and routing | Yes |

Forms and CODEOWNERS come from
[workflow configuration](../project_workflow/config.yaml), root catalogues and,
for the claim dropdown, accepted project records. Run
`python -m project_workflow.generated_files` from the repository root after
changing those sources.

CODEOWNERS routes reviews. SC authorization is a live GitHub-team check;
repository rules determine whether a requested review is also a required
approval. Slack voter authorization uses private-channel membership.

See [workflow operations](workflows/README.md),
[architecture](../project_workflow/ARCHITECTURE.md) and
[installation](../project_workflow/INSTALLATION.md). Do not add another workflow
that duplicates an existing Issue, PR or generation path.
