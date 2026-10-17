"""Request policy validation inline, or dispatch it for standalone callers."""
import os

from project_workflow.submission import number


def validate_pr(api, pr_number):
    pr_number = number(pr_number)
    if os.environ.get("POLICY_INLINE") == "true":
        # The caller's next step independently reads and validates the live PR.
        # Never dispatch a second workflow for work this job will perform itself.
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"pr_number={pr_number}\n")
        return
    api.request("POST", "actions/workflows/projects.yml/dispatches", {
        "ref": "main", "inputs": {"operation": "validate", "pr_number": str(pr_number)}})
