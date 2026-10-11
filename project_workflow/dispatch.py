"""Run independent PR validation using the project workflow."""


def validate_pr(api, pr_number):
    api.request("POST", "actions/workflows/projects.yml/dispatches", {
        "ref": "main", "inputs": {"operation": "validate", "pr_number": str(pr_number)}})
