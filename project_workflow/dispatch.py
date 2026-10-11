"""Run independent PR validation, including during the layout migration."""


def validate_pr(api, pr_number):
    sha = api.get("git/ref/heads/main")["object"]["sha"]
    if ".github/workflows/projects.yml" in api.tree(sha):
        workflow = "projects.yml"
        inputs = {"operation": "validate", "pr_number": str(pr_number)}
    else:
        workflow = "project-policy.yml"  # One-time bridge/historical snapshots.
        inputs = {"pr_number": str(pr_number)}
    api.request("POST", f"actions/workflows/{workflow}/dispatches", {"ref": "main", "inputs": inputs})
