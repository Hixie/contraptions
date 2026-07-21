from __future__ import annotations

import unittest
from typing import Any

from github_config.endpoint_inventory import (
    classify_path,
    unclassified_configuration_paths,
)


class EndpointInventoryTest(unittest.TestCase):
    def test_new_readable_writable_family_is_not_silently_classified(self) -> None:
        description: dict[str, Any] = {
            "paths": {
                "/orgs/{org}/new-setting": {"get": {}, "patch": {}},
            }
        }
        self.assertEqual(
            unclassified_configuration_paths(description),
            {"/orgs/{org}/new-setting"},
        )

    def test_access_collections_are_managed(self) -> None:
        description: dict[str, Any] = {
            "paths": {
                "/orgs/{org}/invitations": {"get": {}, "post": {}},
                "/orgs/{org}/outside_collaborators": {"get": {}},
                "/orgs/{org}/outside_collaborators/{username}": {"delete": {}},
                "/repos/{owner}/{repo}/invitations": {"get": {}},
                "/repos/{owner}/{repo}/invitations/{invitation_id}": {
                    "patch": {},
                    "delete": {},
                },
            }
        }
        self.assertEqual(unclassified_configuration_paths(description), set())

    def test_enterprise_property_values_and_credential_authorizations_are_managed(
        self,
    ) -> None:
        description: dict[str, Any] = {
            "paths": {
                "/organizations/{org}/org-properties/values": {
                    "get": {},
                    "patch": {},
                },
                "/orgs/{org}/credential-authorizations": {"get": {}},
                "/orgs/{org}/credential-authorizations/{credential_id}": {"delete": {}},
                "/repos/{owner}/{repo}/dismissal-requests/code-scanning": {
                    "get": {},
                    "post": {},
                },
            }
        }

        self.assertEqual(unclassified_configuration_paths(description), set())

    def test_app_installation_repository_selection_is_managed(self) -> None:
        self.assertEqual(
            classify_path(
                "/user/installations/{installation_id}/repositories/{repository_id}"
            ),
            "managed",
        )


if __name__ == "__main__":
    unittest.main()
