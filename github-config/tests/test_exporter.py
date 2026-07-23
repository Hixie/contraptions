from __future__ import annotations

import unittest
from typing import Any

from github_config.api import GraphqlData
from github_config.exporter import (
    Exporter,
    Snapshot,
    _normalize_environment,
    _normalize_pattern_configurations,
    _unique_key,
)

from .fakes import FakeApi


def _has_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_has_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_has_key(child, key) for child in value)
    return False


class ExporterTest(unittest.TestCase):
    def test_environment_without_branch_restrictions_exports_null_policy(self) -> None:
        normalized = _normalize_environment(
            {"protection_rules": [], "deployment_branch_policy": None}
        )

        self.assertIsNone(normalized["settings"]["deployment_branch_policy"])

    def test_payment_gated_optional_settings_are_recorded_as_unavailable(self) -> None:
        api = FakeApi(default_status=402)
        exporter = Exporter(api)
        self.assertIsNone(exporter._optional_get("/paid/setting"))
        self.assertIsNone(exporter._optional_list("/paid/settings"))
        self.assertEqual(
            exporter.unavailable,
            ["/paid/setting (402)", "/paid/settings (402)"],
        )

    def test_allow_forking_is_exported_only_for_non_public_repositories(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme"): {
                    "id": 1,
                    "login": "acme",
                    "members_can_fork_private_repositories": True,
                },
                ("GET", "/repos/acme/public-repo"): {
                    "id": 10,
                    "name": "public-repo",
                    "visibility": "public",
                    "allow_forking": False,
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
                ("GET", "/repos/acme/private-repo"): {
                    "id": 11,
                    "name": "private-repo",
                    "visibility": "private",
                    "allow_forking": True,
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
                ("GET", "/repos/acme/internal-repo"): {
                    "id": 12,
                    "name": "internal-repo",
                    "visibility": "internal",
                    "allow_forking": False,
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
            },
            lists={
                "/orgs/acme/repos?type=all&sort=full_name": [
                    {"id": 10, "name": "public-repo"},
                    {"id": 11, "name": "private-repo"},
                    {"id": 12, "name": "internal-repo"},
                ]
            },
        )

        repositories = Exporter(api).export("acme").config["repositories"]["items"]

        self.assertNotIn("allow_forking", repositories["public-repo"]["settings"])
        self.assertIs(repositories["private-repo"]["settings"]["allow_forking"], True)
        self.assertIs(repositories["internal-repo"]["settings"]["allow_forking"], False)

    def test_allow_forking_is_not_exported_when_organization_policy_is_off(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme"): {
                    "id": 1,
                    "login": "acme",
                    "members_can_fork_private_repositories": False,
                },
                ("GET", "/repos/acme/private-repo"): {
                    "id": 10,
                    "name": "private-repo",
                    "visibility": "private",
                    "allow_forking": False,
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
            },
            lists={
                "/orgs/acme/repos?type=all&sort=full_name": [
                    {"id": 10, "name": "private-repo"}
                ]
            },
        )

        repository = (
            Exporter(api).export("acme").config["repositories"]["items"]["private-repo"]
        )

        self.assertNotIn("allow_forking", repository["settings"])

    def test_has_projects_true_is_omitted_only_when_organization_disables_it(
        self,
    ) -> None:
        for label, organization_settings, exported in (
            ("disabled", {"has_repository_projects": False}, False),
            ("unavailable", {}, True),
        ):
            with self.subTest(label=label):
                api = FakeApi(
                    responses={
                        ("GET", "/orgs/acme"): {
                            "id": 1,
                            "login": "acme",
                            **organization_settings,
                        },
                        ("GET", "/repos/acme/widget"): {
                            "id": 10,
                            "name": "widget",
                            "visibility": "public",
                            "has_projects": True,
                            "archived": False,
                            "fork": False,
                            "is_template": False,
                            "topics": [],
                        },
                    },
                    lists={
                        "/orgs/acme/repos?type=all&sort=full_name": [
                            {"id": 10, "name": "widget"}
                        ]
                    },
                )

                repository = (
                    Exporter(api)
                    .export("acme")
                    .config["repositories"]["items"]["widget"]
                )

                if exported:
                    self.assertIs(repository["settings"]["has_projects"], True)
                else:
                    self.assertNotIn("has_projects", repository["settings"])

    def test_allow_forking_is_preserved_when_organization_policy_is_unavailable(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme"): {"id": 1, "login": "acme"},
                ("GET", "/repos/acme/widget"): {
                    "id": 10,
                    "name": "widget",
                    "visibility": "private",
                    "allow_forking": True,
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
            },
            lists={
                "/orgs/acme/repos?type=all&sort=full_name": [
                    {"id": 10, "name": "widget"}
                ]
            },
        )

        repository = (
            Exporter(api).export("acme").config["repositories"]["items"]["widget"]
        )

        self.assertIs(repository["settings"]["allow_forking"], True)

    def test_duplicate_display_names_use_stable_organization_local_ids(self) -> None:
        items: dict[str, object] = {}
        first = _unique_key(items, "duplicate", 17, duplicate=True)
        items[first] = {}
        second = _unique_key(items, "duplicate", 928374, duplicate=True)
        self.assertEqual(first, "duplicate#github-id-17")
        self.assertEqual(second, "duplicate#github-id-928374")

    def test_exports_core_settings_when_optional_sections_are_forbidden(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme"): {
                    "id": 1,
                    "login": "acme",
                    "name": "Acme",
                    "default_repository_permission": None,
                    "members_can_create_repositories": None,
                    "members_can_fork_private_repositories": None,
                },
                ("GET", "/repos/acme/widget"): {
                    "id": 10,
                    "name": "widget",
                    "visibility": "private",
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "delete_branch_on_merge": True,
                    "topics": ["service"],
                    "security_and_analysis": {
                        "advanced_security": {"status": "enabled"},
                        "dependabot_security_updates": {"status": "enabled"},
                    },
                },
                ("GET", "/orgs/acme/private-registries/NPM_REGISTRY_SECRET"): {
                    "name": "NPM_REGISTRY_SECRET",
                    "registry_type": "npm_registry",
                    "url": "https://registry.example",
                    "visibility": "selected",
                    "selected_repository_ids": [10],
                },
            },
            lists={
                "/orgs/acme/repos?type=all&sort=full_name": [
                    {"id": 10, "name": "widget"},
                ],
                "/orgs/acme/private-registries": [
                    {
                        "name": "NPM_REGISTRY_SECRET",
                        "registry_type": "npm_registry",
                        "url": "https://registry.example",
                        "visibility": "selected",
                    }
                ],
                "/orgs/acme/invitations": [
                    {
                        "id": 77,
                        "login": "alice",
                        "email": None,
                        "role": "direct_member",
                    }
                ],
                "/orgs/acme/invitations/77/teams": [{"slug": "platform"}],
                "/orgs/acme/outside_collaborators?filter=all": [
                    {"id": 22, "login": "outside"}
                ],
                "/repos/acme/widget/invitations": [
                    {
                        "id": 88,
                        "invitee": {"id": 23, "login": "pending"},
                        "permissions": "write",
                    }
                ],
                "/repos/acme/widget/rulesets?includes_parents=false": [],
            },
        )
        snapshot = Exporter(api).export("acme")
        organization_settings = snapshot.config["organization"]["settings"]
        self.assertIsNone(organization_settings["default_repository_permission"])
        self.assertIsNone(organization_settings["members_can_create_repositories"])
        self.assertIsNone(
            organization_settings["members_can_fork_private_repositories"]
        )
        repository = snapshot.config["repositories"]["items"]["widget"]
        self.assertEqual(repository["settings"]["name"], "widget")
        self.assertEqual(repository["settings"]["visibility"], "private")
        self.assertEqual(
            repository["settings"]["security_and_analysis"],
            {"advanced_security": {"status": "enabled"}},
        )
        self.assertEqual(repository["topics"], ["service"])
        self.assertGreater(len(snapshot.unavailable), 10)
        self.assertFalse(_has_key(snapshot.config, "id"))
        self.assertEqual(snapshot.ids[("repositories", "widget")], 10)
        registry = snapshot.config["organization"]["private_registries"]["items"][
            "NPM_REGISTRY_SECRET"
        ]
        self.assertEqual(registry["selected_repositories"], ["widget"])
        organization = snapshot.config["organization"]
        self.assertEqual(
            organization["invitations"]["items"]["alice"]["teams"],
            ["platform"],
        )
        self.assertEqual(
            list(organization["outside_collaborators"]["items"]), ["outside"]
        )
        self.assertEqual(
            repository["collaborator_invitations"]["items"],
            {"pending": "write"},
        )
        self.assertIn(
            ("GET_ALL", "/repos/acme/widget/rulesets?includes_parents=false", None),
            api.requests,
        )

    def test_pattern_configuration_response_is_converted_to_patch_shape(self) -> None:
        normalized = _normalize_pattern_configurations(
            {
                "pattern_config_version": "version-1",
                "provider_pattern_overrides": [
                    {
                        "token_type": "TOKEN",
                        "setting": "enabled",
                        "alert_total": 12,
                    }
                ],
                "custom_pattern_overrides": [
                    {
                        "token_type": "cp_2",
                        "custom_pattern_version": "pattern-version",
                        "setting": "disabled",
                    }
                ],
            }
        )
        self.assertEqual(normalized["_pattern_config_version"], "version-1")
        self.assertEqual(
            normalized["provider_pattern_settings"],
            [{"token_type": "TOKEN", "push_protection_setting": "enabled"}],
        )
        self.assertEqual(
            normalized["custom_pattern_settings"],
            [
                {
                    "token_type": "cp_2",
                    "custom_pattern_version": "pattern-version",
                    "push_protection_setting": "disabled",
                }
            ],
        )

    def test_inherited_runner_groups_are_exported_as_read_only(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/actions/runner-groups": [
                    {"id": 1, "name": "Enterprise", "inherited": True},
                    {
                        "id": 2,
                        "name": "Local",
                        "inherited": False,
                        "network_configuration_id": "network-1",
                    },
                ],
                "/orgs/acme/actions/runner-groups/1/runners": [
                    {"id": 9, "name": "enterprise-runner"}
                ],
                "/orgs/acme/actions/runner-groups/1/repositories": [
                    {"id": 10, "name": "api"}
                ],
                "/orgs/acme/actions/runner-groups/2/repositories": [],
                "/orgs/acme/actions/runner-groups/2/runners": [],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)
        exporter._export_runner_groups(target, "acme")
        items = target["actions"]["runner_groups"]["items"]
        self.assertEqual(list(items), ["Enterprise", "Local"])
        self.assertEqual(items["Enterprise"]["repositories"], ["api"])
        self.assertEqual(items["Enterprise"]["runners"], ["enterprise-runner"])
        self.assertNotIn("network_configuration", items["Local"]["settings"])
        reason = exporter.read_only_items[
            ("organization", "actions", "runner_groups", "Enterprise")
        ]
        self.assertIn("enterprise", reason)
        self.assertEqual(
            exporter.read_only_identities[
                ("organization", "actions", "runner_groups", "Enterprise")
            ],
            reason,
        )
        snapshot = Snapshot(
            config={},
            read_only_items=exporter.read_only_items,
        )
        self.assertIn(
            (
                "organization",
                "actions",
                "runner_groups",
                "items",
                "Enterprise",
            ),
            snapshot.comment_read_only_fields,
        )
        self.assertIn(
            "enterprise", exporter.read_only_runner_group_runners["enterprise-runner"]
        )
        self.assertEqual(exporter.ids[("self_hosted_runners", "enterprise-runner")], 9)

    def test_unreadable_inherited_runner_assignments_are_tracked_separately(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/actions/runner-groups": [
                    {"id": 1, "name": "Enterprise", "inherited": True}
                ]
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_runner_groups(target, "acme")

        self.assertIn(
            "Enterprise",
            target["actions"]["runner_groups"]["items"],
        )
        self.assertEqual(
            exporter.unreadable_inherited_runner_assignments,
            ["/orgs/acme/actions/runner-groups/1/runners"],
        )
        self.assertNotIn(
            ("organization", "actions", "runner_groups"),
            exporter.unavailable_collections,
        )
        self.assertEqual(exporter.unavailable, [])

    def test_nullable_hosted_runner_image_is_omitted(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/actions/hosted-runners": [
                    {
                        "id": 7,
                        "name": "image-builder",
                        "image_details": None,
                        "machine_size_details": {"id": "large"},
                        "maximum_runners": 2,
                    }
                ]
            }
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_hosted_runners(target, "acme")

        runner = target["actions"]["hosted_runners"]["items"]["image-builder"]
        self.assertNotIn("image", runner)
        self.assertEqual(runner["maximum_runners"], 2)

    def test_workflow_pages_without_a_source_omits_the_source_key(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/repos/acme/api/pages"): {
                    "build_type": "workflow",
                    "source": None,
                    "https_enforced": True,
                }
            }
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_pages(target, "acme", "api")

        self.assertEqual(
            target["pages"],
            {"enabled": True, "build_type": "workflow", "https_enforced": True},
        )

    def test_denied_ruleset_detail_omits_the_authoritative_collection(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme"): {"id": 1, "login": "acme"},
                ("GET", "/repos/acme/widget"): {
                    "id": 10,
                    "name": "widget",
                    "visibility": "private",
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                },
            },
            lists={
                "/orgs/acme/repos?type=all&sort=full_name": [
                    {"id": 10, "name": "widget"}
                ],
                "/repos/acme/widget/rulesets?includes_parents=false": [
                    {"id": 99, "name": "Protected"}
                ],
            },
        )
        snapshot = Exporter(api).export("acme")
        repository = snapshot.config["repositories"]["items"]["widget"]
        self.assertNotIn("rulesets", repository)
        self.assertEqual(
            snapshot.unavailable_collections[
                ("repositories", "items", "widget", "rulesets")
            ],
            "/repos/acme/widget/rulesets/99",
        )

    def test_exports_personal_access_token_grants_with_repository_names(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/personal-access-tokens": [
                    {
                        "id": 31,
                        "owner": {"login": "alice"},
                        "token_name": "automation",
                        "repository_selection": "subset",
                        "permissions": {"repository": {"contents": "read"}},
                        "token_expired": False,
                    }
                ],
                "/orgs/acme/personal-access-tokens/31/repositories": [
                    {"name": "widget"}
                ],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)
        exporter._export_personal_access_tokens(target, "acme")
        grant = target["personal_access_tokens"]["items"]["alice:automation"]
        self.assertEqual(grant["repositories"], ["widget"])
        self.assertEqual(
            exporter.ids[("personal_access_tokens", "alice:automation")], 31
        )

    def test_exports_saml_credential_authorizations_as_one_way_collection(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/credential-authorizations": [
                    {
                        "login": "alice",
                        "credential_id": 42,
                        "credential_type": "SSH key",
                        "credential_authorized_at": "2026-01-01T00:00:00Z",
                        "credential_accessed_at": None,
                        "authorized_credential_id": 9,
                        "fingerprint": "SHA256:example",
                        "scopes": [],
                    }
                ]
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_credential_authorizations(target, "acme")

        key = "alice:SSH key:SHA256:example"
        authorization = target["credential_authorizations"]["items"][key]
        self.assertEqual(authorization["login"], "alice")
        self.assertNotIn("credential_id", authorization)
        self.assertEqual(exporter.ids[("credential_authorizations", key)], 42)
        self.assertIn(
            (
                "organization",
                "credential_authorizations",
                "items",
                key,
                "credential_type",
            ),
            exporter.read_only_fields,
        )

    def test_exports_custom_property_values_assigned_to_organization(self) -> None:
        api = FakeApi(
            lists={
                "/organizations/acme/org-properties/values": [
                    {"property_name": "cost_center", "value": "engineering"},
                    {"property_name": "regions", "value": ["us", "ca"]},
                ]
            }
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_organization_custom_property_values(target, "acme")

        self.assertEqual(
            target["custom_property_values"],
            {
                "mode": "exact",
                "items": {
                    "cost_center": "engineering",
                    "regions": ["us", "ca"],
                },
            },
        )

    def test_exports_repository_hash_algorithm_as_read_only_setting(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/repos/acme/api/hash-algorithm"): {"hash_algorithm": "sha256"}
            }
        )
        target: dict[str, Any] = {"settings": {}}
        exporter = Exporter(api)

        exporter._export_repository_hash_algorithm(target, "acme", "api")

        self.assertEqual(target["settings"]["hash_algorithm"], "sha256")
        self.assertIn(
            ("repositories", "items", "api", "settings", "hash_algorithm"),
            exporter.read_only_fields,
        )

    def test_incomplete_graphql_collections_export_in_merge_mode(self) -> None:
        api = FakeApi(
            responses={
                ("GRAPHQL", "OrganizationIpAllowList"): {
                    "organization": {
                        "id": "O_acme",
                        "ipAllowListEnabledSetting": "ENABLED",
                        "ipAllowListForInstalledAppsEnabledSetting": "DISABLED",
                        "ipAllowListEntries": {
                            "nodes": [
                                {
                                    "id": "I_office",
                                    "allowListValue": "192.0.2.0/24",
                                    "isActive": True,
                                    "name": "office",
                                }
                            ],
                            "pageInfo": {
                                "endCursor": None,
                                "hasNextPage": True,
                            },
                        },
                    }
                },
                ("GRAPHQL", "OrganizationDomains"): {
                    "organization": {
                        "id": "O_acme",
                        "domains": {
                            "nodes": [
                                {
                                    "id": "D_example",
                                    "domain": "example.com",
                                    "isApproved": True,
                                    "isVerified": True,
                                }
                            ],
                            "pageInfo": {
                                "endCursor": None,
                                "hasNextPage": True,
                            },
                        },
                    }
                },
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_ip_allow_list(target, "acme")
        exporter._export_domains(target, "acme")

        self.assertEqual(target["ip_allow_list"]["entries"]["mode"], "merge")
        self.assertEqual(target["domains"]["mode"], "merge")
        self.assertIn(
            ("organization", "ip_allow_list", "entries"),
            exporter.comment_caveats,
        )
        self.assertIn(("organization", "domains"), exporter.comment_caveats)

    def test_collection_exports_remove_response_only_nested_metadata(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/issue-fields": [
                    {
                        "id": 1,
                        "name": "Service",
                        "data_type": "single_select",
                        "options": [
                            {
                                "id": 2,
                                "name": "API",
                                "color": "blue",
                                "priority": 1,
                                "created_at": "2026-01-01T00:00:00Z",
                                "updated_at": "2026-01-02T00:00:00Z",
                            }
                        ],
                    }
                ],
                "/orgs/acme/code-security/configurations": [
                    {
                        "id": 3,
                        "name": "Default",
                        "secret_scanning_delegated_bypass_options": {
                            "reviewers": [
                                {
                                    "reviewer_id": 7,
                                    "reviewer_type": "TEAM",
                                    "mode": "ALWAYS",
                                    "security_configuration_id": 3,
                                }
                            ]
                        },
                    }
                ],
            }
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_organization_collections(target, "acme")

        option = target["issue_fields"]["items"]["Service"]["options"][0]
        self.assertNotIn("created_at", option)
        self.assertNotIn("updated_at", option)
        reviewer = target["code_security"]["configurations"]["items"]["Default"][
            "secret_scanning_delegated_bypass_options"
        ]["reviewers"][0]
        self.assertNotIn("security_configuration_id", reviewer)
        self.assertEqual(reviewer["reviewer_type"], "TEAM")

    def test_denied_assignment_reads_are_marked_unavailable(self) -> None:
        api = FakeApi()
        exporter = Exporter(api)
        exporter.ids[
            ("organization_collections", "code_security.configurations", "Default")
        ] = 4
        target: dict[str, Any] = {
            "code_security": {
                "configurations": {
                    "mode": "exact",
                    "items": {"Default": {"name": "Default"}},
                }
            }
        }
        exporter._export_code_security_assignments(target, "acme")
        exporter._export_copilot_seats(target, "acme")
        self.assertIn(
            (
                "organization",
                "code_security",
                "configurations",
                "items",
                "Default",
                "repositories",
            ),
            exporter.unavailable_collections,
        )
        self.assertIn(
            (
                "organization",
                "code_security",
                "configurations",
                "items",
                "Default",
                "default_for_new_repos",
            ),
            exporter.unavailable_collections,
        )
        self.assertIn(
            ("organization", "copilot", "seats"),
            exporter.unavailable_collections,
        )

    def test_exports_public_membership_for_every_visible_member(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/user"): {"id": 1, "login": "alice"},
                ("GET", "/orgs/acme/memberships/alice"): {
                    "role": "admin",
                    "direct_membership": True,
                },
                ("GET", "/orgs/acme/memberships/bob"): {
                    "role": "member",
                    "direct_membership": True,
                },
            },
            lists={
                "/orgs/acme/members?role=admin": [{"id": 1, "login": "alice"}],
                "/orgs/acme/members?role=member": [{"id": 2, "login": "bob"}],
                "/orgs/acme/public_members": [{"id": 2, "login": "bob"}],
            },
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_members(target, "acme")

        self.assertEqual(
            target["members"]["items"],
            {
                "alice": {"role": "admin", "public": False},
                "bob": {"role": "member", "public": True},
            },
        )

    def test_exports_indirect_organization_members_as_read_only(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("GET", "/orgs/acme/memberships/alice"): {
                    "role": "member",
                    "direct_membership": True,
                },
                ("GET", "/orgs/acme/memberships/inherited"): {
                    "role": "member",
                    "direct_membership": False,
                },
            },
            lists={
                "/orgs/acme/members?role=admin": [],
                "/orgs/acme/members?role=member": [
                    {"id": 1, "login": "alice"},
                    {"id": 2, "login": "inherited"},
                ],
                "/orgs/acme/public_members": [],
            },
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_members(target, "acme")

        self.assertEqual(list(target["members"]["items"]), ["alice", "inherited"])
        self.assertEqual(exporter.ids[("users", "inherited")], 2)
        self.assertIn(
            ("organization", "members", "inherited"), exporter.read_only_items
        )

    def test_omits_members_when_direct_membership_cannot_be_observed(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/members?role=admin": [],
                "/orgs/acme/members?role=member": [{"id": 1, "login": "alice"}],
                "/orgs/acme/public_members": [],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_members(target, "acme")

        self.assertNotIn("members", target)
        self.assertIn(("organization", "members"), exporter.unavailable_collections)

    def test_exports_team_default_repository_permission(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/teams": [
                    {
                        "id": 3,
                        "slug": "platform",
                        "name": "Platform",
                        "permission": "maintain",
                        "parent": None,
                    }
                ],
                "/orgs/acme/teams/platform/members?role=all": [],
                "/orgs/acme/teams/platform/members?role=maintainer": [],
                "/orgs/acme/teams/platform/repos": [],
            }
        )
        target: dict[str, Any] = {}
        Exporter(api)._export_teams(target, "acme", [])
        settings = target["teams"]["items"]["platform"]["settings"]
        self.assertEqual(settings["permission"], "maintain")

    def test_exports_enterprise_teams_as_read_only(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/teams": [
                    {
                        "id": 3,
                        "slug": "platform",
                        "name": "Platform",
                        "type": "organization",
                        "parent": None,
                    },
                    {
                        "id": 4,
                        "slug": "enterprise-security",
                        "name": "Enterprise Security",
                        "type": "enterprise",
                        "parent": None,
                    },
                ],
                "/orgs/acme/teams/platform/members?role=all": [],
                "/orgs/acme/teams/platform/members?role=maintainer": [],
                "/orgs/acme/teams/platform/repos": [],
                "/orgs/acme/teams/enterprise-security/members?role=all": [],
                "/orgs/acme/teams/enterprise-security/members?role=maintainer": [],
                "/orgs/acme/teams/enterprise-security/repos": [],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_teams(target, "acme", [])

        self.assertEqual(
            list(target["teams"]["items"]),
            ["enterprise-security", "platform"],
        )
        self.assertEqual(exporter.ids[("teams", "enterprise-security")], 4)
        self.assertIn(
            ("organization", "teams", "enterprise-security"),
            exporter.read_only_items,
        )
        self.assertIn(
            ("organization", "teams", "Enterprise Security"),
            exporter.read_only_identities,
        )
        self.assertIn(
            (
                "GET_ALL",
                "/orgs/acme/teams/enterprise-security/members?role=all",
                None,
            ),
            api.requests,
        )

    def test_exports_inherited_team_members_as_read_only(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/teams": [
                    {
                        "id": 3,
                        "slug": "platform",
                        "name": "Platform",
                        "parent": None,
                    }
                ],
                "/orgs/acme/teams/platform/members?role=all": [
                    {
                        "id": 1,
                        "login": "alice",
                        "role": "maintainer",
                        "inherited": False,
                    },
                    {
                        "id": 2,
                        "login": "inherited",
                        "role": "member",
                        "inherited": True,
                    },
                ],
                "/orgs/acme/teams/platform/members?role=maintainer": [
                    {
                        "id": 1,
                        "login": "alice",
                        "inherited": False,
                    }
                ],
                "/orgs/acme/teams/platform/repos": [],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_teams(target, "acme", [])

        members = target["teams"]["items"]["platform"]["members"]["items"]
        self.assertEqual(
            members,
            {"alice": "maintainer", "inherited": "member"},
        )
        self.assertEqual(exporter.ids[("users", "inherited")], 2)
        self.assertIn(
            ("organization", "teams", "platform", "members", "inherited"),
            exporter.read_only_items,
        )

    def test_exports_inherited_team_repository_assignments_as_read_only(self) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/teams": [
                    {
                        "id": 1,
                        "slug": "parent",
                        "name": "Parent",
                        "parent": None,
                    },
                    {
                        "id": 2,
                        "slug": "child",
                        "name": "Child",
                        "parent": {"slug": "parent"},
                    },
                ],
                "/orgs/acme/teams/parent/members?role=all": [],
                "/orgs/acme/teams/parent/members?role=maintainer": [],
                "/orgs/acme/teams/parent/repos": [
                    {
                        "name": "shared",
                        "permissions": {"push": True},
                    }
                ],
                "/orgs/acme/teams/child/members?role=all": [],
                "/orgs/acme/teams/child/members?role=maintainer": [],
                "/orgs/acme/teams/child/repos": [
                    {
                        "name": "shared",
                        "permissions": {"push": True},
                    },
                    {
                        "name": "child-only",
                        "permissions": {"maintain": True},
                    },
                ],
                "/repos/acme/shared/teams": [
                    {
                        "slug": "parent",
                        "type": "organization",
                        "access_source": "direct",
                    }
                ],
                "/repos/acme/child-only/teams": [
                    {
                        "slug": "child",
                        "type": "organization",
                        "access_source": "direct",
                    }
                ],
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_teams(
            target,
            "acme",
            [{"name": "shared"}, {"name": "child-only"}],
        )

        teams = target["teams"]["items"]
        self.assertEqual(teams["parent"]["repositories"]["items"], {"shared": "push"})
        self.assertEqual(
            teams["child"]["repositories"]["items"],
            {"child-only": "maintain", "shared": "push"},
        )
        self.assertIn(
            ("organization", "teams", "child", "repositories", "shared"),
            exporter.read_only_items,
        )

    def test_omits_ambiguous_nested_repositories_when_repository_teams_are_hidden(
        self,
    ) -> None:
        exporter = Exporter(FakeApi())

        repositories = exporter._direct_team_repositories(
            "acme",
            [{"name": "shared"}, {"name": "stronger"}],
            {
                "parent": {"shared": "push", "stronger": "push"},
                "child": {"shared": "push", "stronger": "maintain"},
            },
            {"parent": None, "child": "parent"},
        )

        self.assertEqual(
            repositories,
            {
                "parent": {"shared": "push", "stronger": "push"},
                "child": None,
            },
        )
        self.assertIn(
            (
                "organization",
                "teams",
                "items",
                "child",
                "repositories",
            ),
            exporter.unavailable_collections,
        )
        self.assertTrue(
            any("direct assignment" in entry for entry in exporter.unavailable)
        )

    def test_derives_a_stronger_nested_repository_permission(self) -> None:
        exporter = Exporter(FakeApi())

        repositories = exporter._direct_team_repositories(
            "acme",
            [{"name": "service"}],
            {
                "parent": {"service": "push"},
                "child": {"service": "maintain"},
            },
            {"parent": None, "child": "parent"},
        )

        self.assertEqual(
            repositories,
            {
                "parent": {"service": "push"},
                "child": {"service": "maintain"},
            },
        )

    def test_repository_context_identifies_direct_public_team_access(self) -> None:
        exporter = Exporter(
            FakeApi(
                lists={
                    "/repos/acme/public-service/teams": [
                        {"slug": "parent", "type": "organization"},
                        {"slug": "child", "type": "organization"},
                    ]
                }
            )
        )

        repositories = exporter._direct_team_repositories(
            "acme",
            [{"name": "public-service", "visibility": "public"}],
            {
                "parent": {"public-service": "push"},
                "child": {"public-service": "push"},
            },
            {"parent": None, "child": "parent"},
        )

        self.assertEqual(
            repositories,
            {
                "parent": {"public-service": "push"},
                "child": {"public-service": "push"},
            },
        )

    def test_exports_graphql_organization_settings_and_read_only_metadata(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("GRAPHQL", "OrganizationIpAllowList"): {
                    "organization": {
                        "id": "O_acme",
                        "ipAllowListEnabledSetting": "ENABLED",
                        "ipAllowListForInstalledAppsEnabledSetting": "DISABLED",
                        "ipAllowListEntries": {
                            "nodes": [
                                {
                                    "id": "I_office",
                                    "allowListValue": "192.0.2.0/24",
                                    "isActive": True,
                                    "name": "office",
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                },
                ("GRAPHQL", "OrganizationDomains"): {
                    "organization": {
                        "id": "O_acme",
                        "domains": {
                            "nodes": [
                                {
                                    "id": "D_example",
                                    "domain": "example.com",
                                    "isApproved": True,
                                    "isVerified": False,
                                    "verificationToken": "token",
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                },
                ("GRAPHQL", "OrganizationConfiguration"): {
                    "organization": {
                        "id": "O_acme",
                        "notificationDeliveryRestrictionEnabledSetting": "ENABLED",
                        "samlIdentityProvider": {"issuer": "https://idp.example"},
                        "pinnedItems": {
                            "nodes": [
                                {
                                    "__typename": "Repository",
                                    "id": "R_docs",
                                    "nameWithOwner": "acme/docs",
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                },
            }
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {}

        exporter._export_organization_graphql(target, "acme")

        self.assertIs(target["notification_restriction_enabled"], True)
        self.assertEqual(
            target["ip_allow_list"]["entries"]["items"]["192.0.2.0/24"]["name"],
            "office",
        )
        self.assertIs(target["domains"]["items"]["example.com"]["approved"], True)
        self.assertIn(
            ("organization", "saml_identity_provider"),
            exporter.read_only_fields,
        )
        self.assertIn(
            (
                "organization",
                "domains",
                "items",
                "example.com",
                "verification_token",
            ),
            exporter.read_only_fields,
        )

    def test_app_installation_export_includes_writable_repository_selection(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/installations": [
                    {
                        "id": 9,
                        "app_slug": "deploy",
                        "repository_selection": "selected",
                        "permissions": {"contents": "read"},
                        "events": ["push"],
                    }
                ],
                "/user/installations/9/repositories": [
                    {"id": 10, "name": "api"},
                ],
            }
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {}

        exporter._export_app_installations(target, "acme")

        installation = target["app_installations"]["items"]["deploy"]
        self.assertEqual(installation["selected_repositories"], ["api"])
        self.assertEqual(exporter.ids[("app_installations", "deploy")], 9)
        self.assertEqual(exporter.ids[("repositories", "api")], 10)
        self.assertIn(
            (
                "organization",
                "app_installations",
                "items",
                "deploy",
                "repository_selection",
            ),
            exporter.read_only_fields,
        )
        self.assertNotIn(
            (
                "organization",
                "app_installations",
                "items",
                "deploy",
                "selected_repositories",
            ),
            exporter.read_only_fields,
        )

    def test_exports_graphql_repository_settings_and_wildcard_rules(self) -> None:
        api = FakeApi(
            responses={
                ("GRAPHQL", "RepositoryConfiguration"): {
                    "repository": {
                        "id": "R_api",
                        "hasDiscussionsEnabled": True,
                        "hasSponsorshipsEnabled": False,
                        "issueCreationPolicy": "COLLABORATORS_ONLY",
                        "openGraphImageUrl": "https://example.test/preview.png",
                        "usesCustomOpenGraphImage": True,
                        "environments": {
                            "nodes": [
                                {
                                    "id": "E_production",
                                    "name": "production",
                                    "isPinned": True,
                                    "pinnedPosition": 0,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                        "discussionCategories": {
                            "nodes": [
                                {
                                    "id": "C_questions",
                                    "name": "Questions",
                                    "slug": "questions",
                                    "isAnswerable": True,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        },
                    }
                },
                ("GRAPHQL", "RepositoryBranchProtectionRules"): {
                    "repository": {
                        "branchProtectionRules": {
                            "nodes": [
                                {
                                    "id": "B_release",
                                    "pattern": "release/*",
                                    "allowsDeletions": False,
                                    "requiredStatusChecks": [
                                        {
                                            "context": "test",
                                            "app": {
                                                "id": "A_actions",
                                                "slug": "actions",
                                            },
                                        }
                                    ],
                                    "pushAllowances": {
                                        "nodes": [
                                            {
                                                "actor": {
                                                    "__typename": "Team",
                                                    "id": "T_release",
                                                    "slug": "release",
                                                }
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                    "bypassForcePushAllowances": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                    "bypassPullRequestAllowances": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                    "reviewDismissalAllowances": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
            }
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {
            "settings": {},
            "environments": {
                "mode": "exact",
                "items": {"production": {"settings": {}}},
            },
        }

        exporter._export_repository_graphql(target, "acme", "api")
        exporter._export_branch_protection_rules(target, "acme", "api")

        self.assertIs(target["settings"]["has_discussions"], True)
        self.assertEqual(
            target["settings"]["issue_creation_policy"], "collaborators_only"
        )
        self.assertIs(target["environments"]["items"]["production"]["pinned"], True)
        rule = target["branch_protection_rules"]["items"]["release/*"]
        self.assertEqual(rule["push_actors"], ["team:release"])
        self.assertEqual(
            rule["required_status_checks"],
            [{"context": "test", "app": "actions"}],
        )
        self.assertEqual(exporter.ids[("apps", "actions", "node_id")], "A_actions")
        self.assertEqual(
            exporter.ids[("branch_protection_actors", "team", "release")],
            "T_release",
        )

    def test_graphql_branch_rules_are_the_canonical_export(self) -> None:
        api = FakeApi(
            responses={
                ("GET", "/repos/acme/api"): {
                    "id": 10,
                    "name": "api",
                    "visibility": "private",
                    "archived": False,
                    "fork": False,
                    "is_template": False,
                    "topics": [],
                },
                ("GRAPHQL", "RepositoryBranchProtectionRules"): {
                    "repository": {
                        "branchProtectionRules": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                },
            }
        )

        repository = Exporter(api)._export_repository("acme", "api")

        self.assertEqual(
            repository["branch_protection_rules"],
            {"mode": "exact", "items": {}},
        )
        self.assertNotIn("branch_protections", repository)
        self.assertFalse(
            any(
                "/branches?protected=true" in endpoint
                for _, endpoint, _ in api.requests
            )
        )

    def test_branch_rule_export_caveats_an_ambiguous_status_check_app(
        self,
    ) -> None:
        empty_connection = {
            "nodes": [],
            "pageInfo": {"hasNextPage": False},
        }
        api = FakeApi(
            responses={
                ("GRAPHQL", "RepositoryBranchProtectionRules"): {
                    "repository": {
                        "branchProtectionRules": {
                            "nodes": [
                                {
                                    "id": "B_main",
                                    "pattern": "main",
                                    "requiredStatusChecks": [
                                        {"context": "test", "app": None}
                                    ],
                                    "bypassForcePushAllowances": empty_connection,
                                    "bypassPullRequestAllowances": empty_connection,
                                    "pushAllowances": empty_connection,
                                    "reviewDismissalAllowances": empty_connection,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            }
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {}

        exporter._export_branch_protection_rules(target, "acme", "api")

        self.assertEqual(
            target["branch_protection_rules"]["items"]["main"][
                "required_status_checks"
            ],
            [{"context": "test", "app": None}],
        )
        self.assertIn(
            (
                "repositories",
                "items",
                "api",
                "branch_protection_rules",
                "items",
                "main",
                "required_status_checks",
            ),
            exporter.comment_caveats,
        )

    def test_custom_property_export_preserves_enterprise_source_and_regex(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/properties/schema": [
                    {
                        "property_name": "service",
                        "value_type": "string",
                        "source_type": "enterprise",
                        "require_explicit_values": True,
                    }
                ]
            },
            responses={
                ("GRAPHQL", "OrganizationCustomProperties"): {
                    "organization": {
                        "repositoryCustomProperties": {
                            "nodes": [
                                {
                                    "id": "P_service",
                                    "propertyName": "service",
                                    "regex": "^[a-z]+$",
                                    "requireExplicitValues": True,
                                }
                            ],
                            "pageInfo": {"hasNextPage": False},
                        }
                    }
                }
            },
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {}

        exporter._export_custom_property_schema(target, "acme")

        item = target["custom_properties"]["items"]["service"]
        self.assertEqual(item["source_type"], "enterprise")
        self.assertEqual(item["regex"], "^[a-z]+$")
        self.assertIn(
            ("organization", "custom_properties", "service"),
            exporter.read_only_items,
        )

    def test_partial_custom_property_query_omits_regex_instead_of_clearing_it(
        self,
    ) -> None:
        api = FakeApi(
            lists={
                "/orgs/acme/properties/schema": [
                    {
                        "property_name": "service",
                        "value_type": "string",
                    }
                ]
            },
            responses={
                ("GRAPHQL", "OrganizationCustomProperties"): GraphqlData(
                    {
                        "organization": {
                            "repositoryCustomProperties": {
                                "nodes": [
                                    {
                                        "id": "P_service",
                                        "propertyName": "service",
                                        "regex": None,
                                    }
                                ],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    },
                    ["The regex field could not be read"],
                )
            },
        )
        exporter = Exporter(api)
        target: dict[str, Any] = {}

        exporter._export_custom_property_schema(target, "acme")

        item = target["custom_properties"]["items"]["service"]
        self.assertNotIn("regex", item)
        self.assertIn(
            (
                "organization",
                "custom_properties",
                "items",
                "service",
                "regex",
            ),
            exporter.unavailable_collections,
        )

    def test_branch_protection_actor_connections_are_fully_paginated(self) -> None:
        first_actor = {
            "actor": {
                "__typename": "User",
                "id": "U_alice",
                "login": "alice",
            }
        }
        second_actor = {
            "actor": {
                "__typename": "User",
                "id": "U_bob",
                "login": "bob",
            }
        }
        empty_connection = {
            "nodes": [],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }
        api = FakeApi(
            responses={
                ("GRAPHQL", "RepositoryBranchProtectionRules"): {
                    "repository": {
                        "branchProtectionRules": {
                            "nodes": [
                                {
                                    "id": "B_main",
                                    "pattern": "main",
                                    "requiredStatusChecks": [],
                                    "bypassForcePushAllowances": empty_connection,
                                    "bypassPullRequestAllowances": empty_connection,
                                    "pushAllowances": {
                                        "nodes": [first_actor],
                                        "pageInfo": {
                                            "endCursor": "C_push_1",
                                            "hasNextPage": True,
                                        },
                                    },
                                    "reviewDismissalAllowances": empty_connection,
                                }
                            ],
                            "pageInfo": {
                                "endCursor": None,
                                "hasNextPage": False,
                            },
                        }
                    }
                },
                ("GRAPHQL", "BranchProtectionRuleActors"): {
                    "node": {
                        "bypassForcePushAllowances": empty_connection,
                        "bypassPullRequestAllowances": empty_connection,
                        "pushAllowances": {
                            "nodes": [second_actor],
                            "pageInfo": {
                                "endCursor": "C_push_2",
                                "hasNextPage": False,
                            },
                        },
                        "reviewDismissalAllowances": empty_connection,
                    }
                },
            }
        )
        target: dict[str, Any] = {}

        Exporter(api)._export_branch_protection_rules(target, "acme", "api")

        self.assertEqual(
            target["branch_protection_rules"]["items"]["main"]["push_actors"],
            ["user:alice", "user:bob"],
        )
        self.assertEqual(target["branch_protection_rules"]["mode"], "exact")

    def test_incomplete_branch_actor_list_is_omitted_instead_of_truncated(
        self,
    ) -> None:
        empty_connection = {
            "nodes": [],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }
        api = FakeApi(
            responses={
                ("GRAPHQL", "RepositoryBranchProtectionRules"): {
                    "repository": {
                        "branchProtectionRules": {
                            "nodes": [
                                {
                                    "id": "B_main",
                                    "pattern": "main",
                                    "requiredStatusChecks": [],
                                    "bypassForcePushAllowances": empty_connection,
                                    "bypassPullRequestAllowances": empty_connection,
                                    "pushAllowances": {
                                        "nodes": [],
                                        "pageInfo": {
                                            "endCursor": None,
                                            "hasNextPage": True,
                                        },
                                    },
                                    "reviewDismissalAllowances": empty_connection,
                                }
                            ],
                            "pageInfo": {
                                "endCursor": None,
                                "hasNextPage": False,
                            },
                        }
                    }
                }
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_branch_protection_rules(target, "acme", "api")

        rule = target["branch_protection_rules"]["items"]["main"]
        self.assertNotIn("push_actors", rule)
        self.assertIn(
            (
                "repositories",
                "items",
                "api",
                "branch_protection_rules",
                "items",
                "main",
            ),
            exporter.comment_caveats,
        )

    def test_partial_branch_rule_omits_unreturned_authoritative_lists(self) -> None:
        empty_connection = {
            "nodes": [],
            "pageInfo": {"endCursor": None, "hasNextPage": False},
        }
        api = FakeApi(
            responses={
                ("GRAPHQL", "RepositoryBranchProtectionRules"): GraphqlData(
                    {
                        "repository": {
                            "branchProtectionRules": {
                                "nodes": [
                                    {
                                        "id": "B_main",
                                        "pattern": "main",
                                        "bypassForcePushAllowances": empty_connection,
                                        "bypassPullRequestAllowances": empty_connection,
                                        "reviewDismissalAllowances": empty_connection,
                                    }
                                ],
                                "pageInfo": {
                                    "endCursor": None,
                                    "hasNextPage": False,
                                },
                            }
                        }
                    },
                    ["Some branch protection fields could not be read"],
                )
            }
        )
        target: dict[str, Any] = {}
        exporter = Exporter(api)

        exporter._export_branch_protection_rules(target, "acme", "api")

        rule = target["branch_protection_rules"]["items"]["main"]
        self.assertNotIn("required_status_checks", rule)
        self.assertNotIn("push_actors", rule)
        self.assertEqual(target["branch_protection_rules"]["mode"], "merge")
        caveat = exporter.comment_caveats[
            (
                "repositories",
                "items",
                "api",
                "branch_protection_rules",
                "items",
                "main",
            )
        ]
        self.assertIn("required status checks", caveat)
        self.assertIn("push actors", caveat)


if __name__ == "__main__":
    unittest.main()
