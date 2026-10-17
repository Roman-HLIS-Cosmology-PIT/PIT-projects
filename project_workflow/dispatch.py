"""Request policy validation inline, or dispatch it for standalone callers."""
import os

from project_workflow.submission import number


def validate_pr(api, pr_number, *, slack_review=None):
    pr_number = number(pr_number)
    if os.environ.get("POLICY_INLINE") == "true":
        # Intake has already checked the exact candidate before PR publication.
        # Other callers still ask their next step to validate the live PR.
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"pr_number={pr_number}\n")
            if slack_review is not None:
                output.write(f"slack_review={str(slack_review).lower()}\n")
        return
    api.request("POST", "actions/workflows/projects.yml/dispatches", {
        "ref": "main", "inputs": {"operation": "validate", "pr_number": str(pr_number)}})
