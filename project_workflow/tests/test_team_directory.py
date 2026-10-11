import unittest
import urllib.error

from project_workflow.project_registry import Invalid
from project_workflow.validate_pr import TeamDirectory


class TeamDirectoryTests(unittest.TestCase):
    def lookup(self, response=None, error=None, team_error=None):
        api = TeamDirectory("dummy-team-token")
        calls = []

        def get(endpoint):
            calls.append(endpoint)
            if "/memberships/" not in endpoint:
                if team_error:
                    raise urllib.error.HTTPError("https://api.github.com", team_error, "test", {}, None)
                return {"slug": "sc"}
            if error:
                raise urllib.error.HTTPError("https://api.github.com", error, "test", {}, None)
            return response

        api.get = get
        return api, calls

    def test_active_member_and_maintainer_allowed(self):
        for role in ("member", "maintainer"):
            api, calls = self.lookup({"state": "active", "role": role})
            self.assertTrue(api.is_active_member("test-org", "sc", "alice"))
            self.assertEqual(calls, ["test-org/teams/sc", "test-org/teams/sc/memberships/alice"])

    def test_pending_invitation_denied(self):
        api, _ = self.lookup({"state": "pending", "role": "member"})
        self.assertFalse(api.is_active_member("test-org", "sc", "alice"))

    def test_nonmember_404_denied(self):
        api, _ = self.lookup(error=404)
        self.assertFalse(api.is_active_member("test-org", "sc", "alice"))

    def test_invalid_or_hidden_team_fails_closed(self):
        api, _ = self.lookup(team_error=404)
        with self.assertRaises(urllib.error.HTTPError):
            api.is_active_member("test-org", "sc", "alice")

    def test_expired_token_or_forbidden_lookup_fails_closed(self):
        for status in (401, 403, 429, 500):
            api, _ = self.lookup(error=status)
            with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError):
                api.is_active_member("test-org", "sc", "alice")

    def test_missing_credential_rejected(self):
        with self.assertRaises(Invalid):
            TeamDirectory("")

    def test_membership_changes_observed_without_cache(self):
        api, _ = self.lookup({"state": "active", "role": "member"})
        self.assertTrue(api.is_active_member("test-org", "sc", "alice"))
        api.get = lambda endpoint: {} if "/memberships/" not in endpoint else {"state": "pending", "role": "member"}
        self.assertFalse(api.is_active_member("test-org", "sc", "alice"))

    def test_bot_or_injected_identity_not_a_human_sc_member(self):
        api, calls = self.lookup({"state": "active", "role": "member"})
        for identity in ("app[bot]", "../alice", "alice?scope=admin"):
            self.assertFalse(api.is_active_member("test-org", "sc", identity))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
