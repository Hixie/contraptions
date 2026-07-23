from __future__ import annotations

import copy
import unittest
from typing import Any

from github_config.config import ConfigError
from github_config.exporter import Snapshot
from github_config.operations import Operation
from github_config.planner import Planner

from .fakes import FakeApi


def snapshot() -> Snapshot:
    return Snapshot(
        config={
            "version": 1,
            "organization": {
                "settings": {
                    "default_repository_permission": "write",
                    "has_repository_projects": True,
                    "members_can_fork_private_repositories": True,
                },
                "actions": {
                    "secrets": {
                        "mode": "exact",
                        "items": {"TOKEN": {"visibility": "all"}},
                    }
                },
            },
            "repositories": {
                "mode": "merge",
                "items": {
                    "api": {
                        "settings": {
                            "visibility": "private",
                            "archived": False,
                            "delete_branch_on_merge": False,
                        },
                        "topics": ["service"],
                    },
                    "old": {
                        "settings": {"visibility": "private", "archived": True},
                        "topics": [],
                    },
                },
            },
        },
        ids={("repositories", "api"): 10, ("repositories", "old"): 11},
    )


def mapping_body(operation: Operation) -> dict[str, Any]:
    if not isinstance(operation.body, dict):
        raise TypeError(f"Expected a mapping request body for {operation.endpoint}")
    return operation.body


class PlannerTest(unittest.TestCase):
    def test_direct_planner_call_checks_root_container_types(self) -> None:
        invalid = (
            ({"version": 1, "organization": "bad"}, "organization must be a mapping"),
            (
                {"version": 1, "repository_policies": 7},
                "repository_policies must be a list of mappings",
            ),
            ({"version": 1, "repositories": "bad"}, "repositories must be a mapping"),
        )

        for desired, message in invalid:
            with (
                self.subTest(desired=desired),
                self.assertRaisesRegex(ConfigError, message),
            ):
                Planner(FakeApi(), snapshot(), "acme").plan(desired)

    def test_unmatched_policy_still_rejects_unknown_repository_section(self) -> None:
        current = Snapshot(
            config={
                "version": 1,
                "organization": {},
                "repositories": {"mode": "merge", "items": {}},
            }
        )
        desired: dict[str, Any] = {
            "repository_policies": [
                {
                    "match": {"name": "does-not-exist"},
                    "set": {"action": {"permissions": {"enabled": False}}},
                }
            ]
        }
        with self.assertRaisesRegex(ConfigError, "unknown keys: action"):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_policy_and_explicit_repository_cannot_combine_branch_protections(
        self,
    ) -> None:
        desired: dict[str, Any] = {
            "version": 1,
            "repository_policies": [
                {
                    "match": {"name": "api"},
                    "set": {"branch_protections": {"items": {}}},
                }
            ],
            "repositories": {
                "items": {
                    "api": {
                        "branch_protection_rules": {"items": {}},
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot contain both branch_protections and branch_protection_rules",
        ):
            Planner(FakeApi(), snapshot(), "acme").plan(desired)

    def test_named_resource_rejects_unknown_fields(self) -> None:
        desired: dict[str, Any] = {
            "version": 1,
            "repositories": {
                "items": {"api": {"labels": {"items": {"bug": {"colour": "ff0000"}}}}}
            },
        }
        with self.assertRaisesRegex(ConfigError, "unknown keys: colour"):
            Planner(FakeApi(), snapshot(), "acme").plan(desired)

    def test_plans_organization_and_policy_updates(self) -> None:
        desired = {
            "version": 1,
            "organization": {"settings": {"default_repository_permission": "read"}},
            "repository_policies": [
                {
                    "match": {"visibility": "private", "archived": False},
                    "set": {"settings": {"delete_branch_on_merge": True}},
                }
            ],
        }
        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)
        endpoints = [operation.endpoint for operation in operations]
        self.assertIn("/orgs/acme", endpoints)
        self.assertIn("/repos/acme/api", endpoints)
        self.assertNotIn("/repos/acme/old", endpoints)

    def test_allow_forking_is_rejected_for_a_current_public_repository(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["visibility"] = (
            "public"
        )
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"allow_forking": False}}}},
        }

        with self.assertRaises(ConfigError) as raised:
            Planner(FakeApi(), current, "acme").plan(desired)

        self.assertIn(
            "repositories.items.api.settings.allow_forking can be managed only "
            "for organization-owned private or internal repositories; the repository "
            "visibility is 'public'",
            str(raised.exception),
        )

    def test_allow_forking_policy_checks_each_matching_repository(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["visibility"] = (
            "public"
        )
        desired = {
            "version": 1,
            "repository_policies": [
                {
                    "match": {"name": "api"},
                    "set": {"settings": {"allow_forking": False}},
                }
            ],
        }

        with self.assertRaisesRegex(ConfigError, "repository visibility is 'public'"):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_later_policy_can_satisfy_allow_forking_requirements(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["visibility"] = (
            "public"
        )
        desired = {
            "version": 1,
            "repository_policies": [
                {
                    "match": {"name": "api", "visibility": "public"},
                    "set": {"settings": {"allow_forking": True}},
                },
                {
                    "match": {"name": "api", "visibility": "public"},
                    "set": {"settings": {"visibility": "private"}},
                },
            ],
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.body for operation in operations],
            [{"visibility": "private"}, {"allow_forking": True}],
        )

    def test_allow_forking_is_planned_for_non_public_repositories(self) -> None:
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"allow_forking": True}}}},
        }

        for visibility in ("private", "internal"):
            with self.subTest(visibility=visibility):
                current = snapshot()
                current.config["repositories"]["items"]["api"]["settings"][
                    "visibility"
                ] = visibility

                operations = Planner(FakeApi(), current, "acme").plan(desired)

                self.assertEqual(len(operations), 1)
                self.assertEqual(mapping_body(operations[0]), {"allow_forking": True})

    def test_repository_becomes_private_before_allow_forking_is_applied(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["visibility"] = (
            "public"
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": True,
                            "visibility": "private",
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.phase, operation.body) for operation in operations],
            [
                (10, {"visibility": "private"}),
                (20, {"allow_forking": True}),
            ],
        )

    def test_archived_repository_is_unarchived_before_visibility_and_forking(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"archived": True, "visibility": "public"}
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": True,
                            "archived": False,
                            "visibility": "private",
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.phase, operation.body) for operation in operations],
            [
                (1, {"archived": False}),
                (10, {"visibility": "private"}),
                (20, {"allow_forking": True}),
            ],
        )

    def test_archived_repository_follows_organization_forking_enablement(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"][
            "members_can_fork_private_repositories"
        ] = False
        current.config["repositories"]["items"]["api"]["settings"]["archived"] = True
        desired = {
            "version": 1,
            "organization": {
                "settings": {"members_can_fork_private_repositories": True}
            },
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": True,
                            "archived": False,
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.phase, operation.body) for operation in operations],
            [
                (0, {"members_can_fork_private_repositories": True}),
                (1, {"archived": False}),
                (20, {"allow_forking": True}),
            ],
        )

    def test_archived_repository_setting_change_requires_explicit_unarchive(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"archived": True, "visibility": "public"}
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": True,
                            "visibility": "private",
                        }
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot change while the repository is archived; set "
            "repositories.items.api.settings.archived to false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_allow_forking_false_is_satisfied_when_organization_policy_is_off(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"][
            "members_can_fork_private_repositories"
        ] = False
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"allow_forking": False}}}},
        }

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_repository_forking_is_disabled_before_organization_policy(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["allow_forking"] = (
            True
        )
        desired = {
            "version": 1,
            "organization": {
                "settings": {"members_can_fork_private_repositories": False}
            },
            "repositories": {"items": {"api": {"settings": {"allow_forking": False}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [
                (operation.endpoint, operation.phase, operation.body)
                for operation in operations
            ],
            [
                ("/repos/acme/api", 20, {"allow_forking": False}),
                (
                    "/orgs/acme",
                    30,
                    {"members_can_fork_private_repositories": False},
                ),
            ],
        )

    def test_organization_prerequisites_are_split_by_dependency_order(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"].update(
            {
                "has_repository_projects": False,
                "members_can_fork_private_repositories": True,
            }
        )
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"allow_forking": True, "has_projects": False}
        )
        desired = {
            "version": 1,
            "organization": {
                "settings": {
                    "has_repository_projects": True,
                    "members_can_fork_private_repositories": False,
                }
            },
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": False,
                            "has_projects": True,
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [
                (operation.endpoint, operation.phase, operation.body)
                for operation in operations
            ],
            [
                ("/orgs/acme", 0, {"has_repository_projects": True}),
                (
                    "/repos/acme/api",
                    20,
                    {"allow_forking": False, "has_projects": True},
                ),
                (
                    "/orgs/acme",
                    30,
                    {"members_can_fork_private_repositories": False},
                ),
            ],
        )

    def test_unchanged_archived_repository_settings_are_valid(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"allow_forking": True, "archived": True}
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": copy.deepcopy(
                            current.config["repositories"]["items"]["api"]["settings"]
                        )
                    }
                }
            },
        }

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_repository_is_archived_after_allow_forking_is_applied(self) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "allow_forking": True,
                            "archived": True,
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(
            [(operation.phase, operation.body) for operation in operations],
            [
                (20, {"allow_forking": True}),
                (100, {"archived": True}),
            ],
        )

    def test_allow_forking_is_rejected_when_organization_policy_is_off(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"][
            "members_can_fork_private_repositories"
        ] = False
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"allow_forking": True}}}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "members_can_fork_private_repositories to be true; it is false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_allow_forking_is_planned_when_organization_policy_is_unavailable(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"].pop(
            "members_can_fork_private_repositories"
        )
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"allow_forking": True}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.body for operation in operations], [{"allow_forking": True}]
        )

    def test_enabling_organization_forking_precedes_repository_setting(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"][
            "members_can_fork_private_repositories"
        ] = False
        desired = {
            "version": 1,
            "organization": {
                "settings": {"members_can_fork_private_repositories": True}
            },
            "repositories": {"items": {"api": {"settings": {"allow_forking": True}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.endpoint, operation.phase) for operation in operations],
            [("/orgs/acme", 0), ("/repos/acme/api", 20)],
        )

    def test_repository_projects_require_the_organization_setting(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"]["has_repository_projects"] = False
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"has_projects": True}}}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "organization.settings.has_repository_projects to be true; it is false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_repository_projects_are_planned_when_organization_setting_is_unavailable(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"].pop("has_repository_projects")
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"has_projects": True}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.body for operation in operations], [{"has_projects": True}]
        )

    def test_unavailable_organization_prerequisites_allow_an_unchanged_export(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"].pop(
            "members_can_fork_private_repositories"
        )
        current.config["organization"]["settings"].pop("has_repository_projects")
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"allow_forking": True, "has_projects": True}
        )

        desired = copy.deepcopy(current.config)

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_enabling_organization_projects_precedes_repository_setting(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"]["has_repository_projects"] = False
        desired = {
            "version": 1,
            "organization": {"settings": {"has_repository_projects": True}},
            "repositories": {"items": {"api": {"settings": {"has_projects": True}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.endpoint, operation.phase) for operation in operations],
            [("/orgs/acme", 0), ("/repos/acme/api", 20)],
        )

    def test_enabling_organization_projects_precedes_repository_creation(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"]["has_repository_projects"] = False
        desired = {
            "version": 1,
            "organization": {"settings": {"has_repository_projects": True}},
            "repositories": {"items": {"new": {"settings": {"has_projects": True}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [
                (operation.method, operation.endpoint, operation.phase, operation.body)
                for operation in operations
            ],
            [
                (
                    "PATCH",
                    "/orgs/acme",
                    0,
                    {"has_repository_projects": True},
                ),
                (
                    "POST",
                    "/orgs/acme/repos",
                    0,
                    {"name": "new", "has_projects": True},
                ),
            ],
        )

    def test_organization_update_omits_response_only_null_values(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"] = {
            "default_repository_permission": None,
            "members_can_create_repositories": None,
            "members_can_fork_private_repositories": None,
            "description": "Before",
        }
        desired = copy.deepcopy(current.config)
        desired["organization"]["settings"]["description"] = "After"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].body, {"description": "After"})

    def test_branch_protection_update_omits_response_only_null_app_id(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protections"] = {
            "mode": "exact",
            "items": {
                "main": {
                    "required_status_checks": {
                        "strict": True,
                        "checks": [{"context": "ci", "app_id": None}],
                    },
                    "enforce_admins": True,
                    "required_pull_request_reviews": None,
                    "restrictions": None,
                    "allow_deletions": False,
                }
            },
        }
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["branch_protections"]["items"]["main"][
            "allow_deletions"
        ] = True

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(
            mapping_body(operations[0])["required_status_checks"]["checks"],
            [{"context": "ci"}],
        )

    def test_response_only_null_app_id_preserves_the_current_app_constraint(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protections"] = {
            "items": {
                "main": {
                    "required_status_checks": {
                        "strict": True,
                        "checks": [{"context": "ci", "app_id": 12}],
                    },
                    "enforce_admins": True,
                    "required_pull_request_reviews": None,
                    "restrictions": None,
                    "allow_deletions": False,
                }
            },
        }
        desired = copy.deepcopy(current.config)
        protection = desired["repositories"]["items"]["api"]["branch_protections"][
            "items"
        ]["main"]
        protection["required_status_checks"]["checks"][0]["app_id"] = None

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

        protection["allow_deletions"] = True

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(
            mapping_body(operations[0])["required_status_checks"]["checks"],
            [{"context": "ci", "app_id": 12}],
        )
        self.assertTrue(
            all("app_id" not in change.path for change in operations[0].changes)
        )

    def test_dependabot_access_does_not_apply_response_only_null_default(self) -> None:
        current = snapshot()
        current.config["organization"]["dependabot"] = {
            "repository_access": {
                "default_level": "public",
                "repositories": [],
            }
        }
        desired = copy.deepcopy(current.config)
        desired["organization"]["dependabot"]["repository_access"]["default_level"] = (
            None
        )

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_exported_secret_metadata_is_stable_without_secret_value(self) -> None:
        current = snapshot()
        desired = {
            "version": 1,
            "organization": current.config["organization"],
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(operations, [])

    def test_missing_secret_without_environment_value_is_blocked(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "actions": {
                    "secrets": {
                        "items": {"NEW_TOKEN": {"visibility": "all"}},
                    }
                }
            },
        }
        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("value_from_env", operations[0].blocked_reason or "")

    def test_exact_collection_removes_extra_collaborator_but_never_repository(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["collaborators"] = {
            "mode": "exact",
            "items": {"alice": "push"},
        }
        desired = {
            "version": 1,
            "repositories": {
                "mode": "exact",
                "items": {
                    "api": {"collaborators": {"mode": "exact", "items": {}}},
                },
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [operation.endpoint for operation in operations],
            ["/repos/acme/api/collaborators/alice"],
        )

    def test_rich_exported_configuration_has_no_false_differences(self) -> None:
        current = snapshot()
        organization = current.config["organization"]
        organization.update(
            {
                "blocked_users": ["troll"],
                "interaction_limit": {
                    "enabled": True,
                    "limit": "existing_users",
                    "_expires_at": "later",
                },
                "copilot": {"seats": {"users": ["alice"], "teams": ["developers"]}},
                "budgets": {
                    "mode": "exact",
                    "items": {
                        "organization:all:ProductPricing:actions": {
                            "budget_amount": 100,
                            "budget_scope": "organization",
                            "budget_type": "ProductPricing",
                            "budget_product_sku": "actions",
                        }
                    },
                },
                "private_registries": {
                    "mode": "exact",
                    "items": {
                        "NPM": {
                            "registry_type": "npm_registry",
                            "url": "https://registry.example",
                            "visibility": "all",
                            "auth_type": "token",
                        }
                    },
                },
                "issue_types": {
                    "mode": "exact",
                    "items": {
                        "Bug": {"name": "Bug", "is_enabled": True, "color": "red"}
                    },
                },
                "secret_scanning": {
                    "custom_patterns": {
                        "mode": "exact",
                        "items": {"Token": {"pattern": "token_[a-z]+"}},
                    }
                },
            }
        )
        desired = copy.deepcopy(current.config)
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(operations, [])

    def test_legacy_effective_assignments_cannot_become_direct_assignments(
        self,
    ) -> None:
        team_settings = {
            "name": "Parent",
            "privacy": "closed",
            "parent": None,
        }
        child_settings = {
            "name": "Child",
            "privacy": "closed",
            "parent": "parent",
        }
        current = Snapshot(
            config={
                "version": 1,
                "organization": {
                    "members": {"mode": "exact", "items": {}},
                    "teams": {
                        "mode": "exact",
                        "items": {
                            "parent": {
                                "settings": team_settings,
                                "members": {"mode": "exact", "items": {}},
                                "repositories": {"mode": "exact", "items": {}},
                            },
                            "child": {
                                "settings": child_settings,
                                "members": {"mode": "exact", "items": {}},
                                "repositories": {"mode": "exact", "items": {}},
                            },
                        },
                    },
                },
                "repositories": {"mode": "merge", "items": {}},
            },
            ids={
                ("teams", "parent"): 1,
                ("teams", "child"): 2,
                ("teams", "enterprise-security"): 3,
            },
            read_only_items={
                (
                    "organization",
                    "members",
                    "alice",
                    "role",
                ): "indirect organization membership",
                (
                    "organization",
                    "teams",
                    "enterprise-security",
                ): "enterprise-owned team",
                (
                    "organization",
                    "teams",
                    "parent",
                    "members",
                    "alice",
                ): "inherited team membership",
                (
                    "organization",
                    "teams",
                    "child",
                    "repositories",
                    "shared",
                ): "inherited repository access",
            },
        )
        desired = copy.deepcopy(current.config)
        desired["organization"]["members"]["items"]["alice"] = {"role": "member"}
        desired["organization"]["teams"]["items"]["parent"]["members"]["items"][
            "alice"
        ] = "member"
        desired["organization"]["teams"]["items"]["child"]["repositories"]["items"][
            "shared"
        ] = "push"
        desired["organization"]["teams"]["items"]["enterprise-security"] = {
            "settings": {
                "name": "Enterprise Security",
                "privacy": "closed",
                "parent": None,
            }
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            {operation.changes[0].path for operation in operations},
            {
                "organization.members.alice.role",
                "organization.teams.enterprise-security",
                "organization.teams.parent.members.alice",
                "organization.teams.child.repositories.shared",
            },
        )
        self.assertTrue(all(operation.blocked_reason for operation in operations))

    def test_read_only_team_assignments_follow_logical_team_keys(self) -> None:
        current = Snapshot(
            config={
                "version": 1,
                "organization": {
                    "teams": {
                        "items": {
                            "live-slug": {
                                "settings": {"name": "Stable Name"},
                                "members": {"items": {}},
                                "repositories": {"items": {}},
                            }
                        }
                    }
                },
                "repositories": {"mode": "merge", "items": {}},
            },
            ids={("teams", "live-slug"): 1},
            read_only_items={
                (
                    "organization",
                    "teams",
                    "live-slug",
                    "members",
                    "alice",
                ): "inherited team membership",
                (
                    "organization",
                    "teams",
                    "live-slug",
                    "repositories",
                    "shared.lib",
                ): "inherited repository access",
            },
        )
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "logical-key": {
                            "settings": {"name": "Stable Name"},
                            "members": {"items": {"alice": "member"}},
                            "repositories": {"items": {"shared.lib": "push"}},
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 2)
        self.assertEqual(
            {operation.changes[0].path for operation in operations},
            {
                "organization.teams.logical-key.members.alice",
                "organization.teams.logical-key.repositories.shared.lib",
            },
        )
        self.assertTrue(all(operation.blocked_reason for operation in operations))

    def test_read_only_assignments_match_github_identities_without_case(self) -> None:
        for mode in ("merge", "exact"):
            with self.subTest(mode=mode):
                current = Snapshot(
                    config={
                        "version": 1,
                        "organization": {
                            "members": {"mode": mode, "items": {}},
                            "teams": {
                                "mode": mode,
                                "items": {
                                    "Platform": {
                                        "settings": {"name": "Platform"},
                                        "members": {"mode": mode, "items": {}},
                                        "repositories": {
                                            "mode": mode,
                                            "items": {},
                                        },
                                    }
                                },
                            },
                        },
                        "repositories": {"mode": "merge", "items": {}},
                    },
                    ids={("teams", "Platform"): 1},
                    read_only_items={
                        (
                            "organization",
                            "members",
                            "Alice",
                            "role",
                        ): "indirect organization membership",
                        (
                            "organization",
                            "teams",
                            "Platform",
                            "members",
                            "Alice",
                        ): "inherited team membership",
                        (
                            "organization",
                            "teams",
                            "Platform",
                            "repositories",
                            "Shared.Lib",
                        ): "inherited repository access",
                    },
                )
                desired = {
                    "version": 1,
                    "organization": {
                        "members": {
                            "mode": mode,
                            "items": {"alice": {"role": "member"}},
                        },
                        "teams": {
                            "mode": mode,
                            "items": {
                                "platform": {
                                    "settings": {"name": "Platform"},
                                    "members": {
                                        "mode": mode,
                                        "items": {"alice": "member"},
                                    },
                                    "repositories": {
                                        "mode": mode,
                                        "items": {"shared.lib": "push"},
                                    },
                                }
                            },
                        },
                    },
                }

                operations = Planner(FakeApi(), current, "acme").plan(desired)

                self.assertEqual(len(operations), 3)
                self.assertTrue(
                    all(operation.blocked_reason for operation in operations)
                )

    def test_repository_rename_aliases_read_only_team_access(self) -> None:
        current = Snapshot(
            config={
                "version": 1,
                "organization": {
                    "teams": {
                        "items": {
                            "child": {
                                "settings": {"name": "Child"},
                                "repositories": {"items": {}},
                            }
                        }
                    }
                },
                "repositories": {
                    "mode": "merge",
                    "items": {
                        "shared.lib": {
                            "settings": {
                                "name": "shared.lib",
                                "visibility": "private",
                                "archived": False,
                            }
                        }
                    },
                },
            },
            ids={
                ("repositories", "shared.lib"): 10,
                ("teams", "child"): 11,
            },
            read_only_items={
                (
                    "organization",
                    "teams",
                    "child",
                    "repositories",
                    "shared.lib",
                ): "inherited repository access"
            },
        )
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "child": {
                            "settings": {"name": "Child"},
                            "repositories": {"items": {"renamed.lib": "push"}},
                        }
                    }
                }
            },
            "repositories": {
                "items": {"shared.lib": {"settings": {"name": "renamed.lib"}}}
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        team_operation = next(
            operation
            for operation in operations
            if operation.changes[0].path
            == "organization.teams.child.repositories.renamed.lib"
        )
        self.assertEqual(
            team_operation.endpoint,
            "/orgs/acme/teams/child/repos/acme/renamed.lib",
        )
        self.assertIn("inherited", team_operation.blocked_reason or "")

    def test_enterprise_team_identity_guards_a_logical_key(self) -> None:
        reason = "enterprise-owned team"
        current = Snapshot(
            config={
                "version": 1,
                "organization": {"teams": {"mode": "exact", "items": {}}},
                "repositories": {"mode": "merge", "items": {}},
            },
            ids={("teams", "enterprise-security"): 3},
            read_only_items={("organization", "teams", "enterprise-security"): reason},
            read_only_identities={
                ("organization", "teams", "Enterprise Security"): reason
            },
        )
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "logical-security": {
                            "settings": {"name": "Enterprise Security"}
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].method, "POST")
        self.assertEqual(operations[0].blocked_reason, reason)

    def test_enterprise_runner_assignment_guards_a_renamed_legacy_group(
        self,
    ) -> None:
        reason = "runner belongs to an enterprise-owned runner group"
        current = Snapshot(
            config={
                "version": 1,
                "organization": {
                    "actions": {
                        "runner_groups": {"mode": "exact", "items": {}},
                        "self_hosted_runners": {
                            "mode": "exact",
                            "items": {"runner-1": {"labels": []}},
                        },
                    }
                },
                "repositories": {"mode": "merge", "items": {}},
            },
            ids={("self_hosted_runners", "runner-1"): 9},
            read_only_identities={
                (
                    "organization",
                    "actions",
                    "runner_groups",
                    "Enterprise Renamed",
                ): "enterprise-owned runner group"
            },
            read_only_runner_group_runners={"runner-1": reason},
        )
        desired = {
            "version": 1,
            "organization": {
                "actions": {
                    "runner_groups": {
                        "mode": "exact",
                        "items": {
                            "Enterprise": {
                                "settings": {"name": "Enterprise"},
                                "repositories": [],
                                "runners": ["runner-1"],
                            }
                        },
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.method for operation in operations], ["POST", "PUT"]
        )
        self.assertTrue(
            all(operation.blocked_reason == reason for operation in operations)
        )

    def test_unreadable_inherited_runner_assignments_only_block_runner_moves(
        self,
    ) -> None:
        endpoint = "/orgs/acme/actions/runner-groups/8/runners"
        current = Snapshot(
            config={
                "version": 1,
                "organization": {
                    "actions": {
                        "runner_groups": {
                            "mode": "exact",
                            "items": {
                                "Local": {
                                    "settings": {"name": "Local"},
                                    "repositories": [],
                                    "runners": [],
                                }
                            },
                        },
                        "self_hosted_runners": {
                            "mode": "exact",
                            "items": {"local-runner": {"labels": []}},
                        },
                    }
                },
                "repositories": {"mode": "merge", "items": {}},
            },
            ids={
                ("runner_groups", "Local"): 7,
                ("self_hosted_runners", "local-runner"): 9,
            },
            unreadable_inherited_runner_assignments=[endpoint],
        )

        self.assertEqual(
            Planner(FakeApi(), current, "acme").plan(copy.deepcopy(current.config)),
            [],
        )

        empty_group: dict[str, Any] = {
            "version": 1,
            "organization": {
                "actions": {
                    "runner_groups": {
                        "mode": "merge",
                        "items": {
                            "New": {
                                "settings": {"name": "New"},
                                "repositories": [],
                                "runners": [],
                            }
                        },
                    }
                }
            },
        }
        empty_operations = Planner(FakeApi(), current, "acme").plan(empty_group)
        self.assertEqual(len(empty_operations), 1)
        self.assertEqual(empty_operations[0].method, "POST")
        self.assertIsNone(empty_operations[0].blocked_reason)

        assigned_group = copy.deepcopy(empty_group)
        assigned_group["organization"]["actions"]["runner_groups"]["items"]["New"][
            "runners"
        ] = ["local-runner"]
        assigned_operations = Planner(FakeApi(), current, "acme").plan(assigned_group)
        self.assertEqual(
            [operation.method for operation in assigned_operations], ["POST", "PUT"]
        )
        self.assertTrue(
            all(
                endpoint in (operation.blocked_reason or "")
                for operation in assigned_operations
            )
        )

    def test_parent_teams_are_created_before_children(self) -> None:
        current = snapshot()
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "a-child": {
                            "settings": {"name": "A Child", "parent": "z-parent"}
                        },
                        "z-parent": {"settings": {"name": "Z Parent", "parent": None}},
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        creates = [
            operation
            for operation in operations
            if operation.endpoint == "/orgs/acme/teams"
        ]
        self.assertEqual(
            [operation.capture_id for operation in creates],
            [("teams", "z-parent"), ("teams", "a-child")],
        )

    def test_admin_team_permission_is_set_after_creation(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "eng": {
                            "settings": {
                                "name": "Engineering Team",
                                "permission": "admin",
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(
            [operation.method for operation in operations], ["POST", "PATCH"]
        )
        self.assertNotIn("permission", mapping_body(operations[0]))
        self.assertNotIn("parent_team_id", mapping_body(operations[0]))
        self.assertEqual(
            operations[0].capture_response_values,
            [
                (("team_slugs", "eng"), ("slug",)),
                (("teams", "eng", "node_id"), ("node_id",)),
            ],
        )
        self.assertEqual(operations[1].body, {"permission": "admin"})
        self.assertEqual(
            operations[1].endpoint,
            "/orgs/acme/teams/__CREATED_TEAM_SLUG__",
        )
        self.assertEqual(
            operations[1].endpoint_id_references,
            [("__CREATED_TEAM_SLUG__", ("team_slugs", "eng"))],
        )

    def test_new_team_subresources_use_the_created_slug(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "eng": {
                            "settings": {"name": "Engineering Team"},
                            "members": {"items": {"alice": "maintainer"}},
                            "repositories": {"items": {"api": "push"}},
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(
            [operation.method for operation in operations], ["POST", "PUT", "PUT"]
        )
        for operation in operations[1:]:
            self.assertIn("/__RESOLVED_TEAM_SLUG__/", operation.endpoint)
            self.assertEqual(
                operation.endpoint_id_references,
                [("__RESOLVED_TEAM_SLUG__", ("team_slugs", "eng"))],
            )
            self.assertIsNone(operation.blocked_reason)

    def test_team_rename_resolves_the_new_slug_before_subresource_removals(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "old-team": {
                    "settings": {"name": "Old Team"},
                    "members": {"mode": "exact", "items": {"alice": "member"}},
                    "repositories": {"mode": "exact", "items": {"api": "push"}},
                }
            },
        }
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "old-team": {
                            "settings": {"name": "New Team"},
                            "members": {"mode": "exact", "items": {}},
                            "repositories": {"mode": "exact", "items": {}},
                        }
                    },
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.method for operation in operations],
            ["PATCH", "DELETE", "DELETE"],
        )
        self.assertEqual(
            operations[0].capture_response_values,
            [(("team_slugs", "old-team"), ("slug",))],
        )
        for operation in operations[1:]:
            self.assertIn("/__RESOLVED_TEAM_SLUG__/", operation.endpoint)
            self.assertEqual(
                operation.endpoint_id_references,
                [("__RESOLVED_TEAM_SLUG__", ("team_slugs", "old-team"))],
            )

    def test_early_team_rename_resolves_the_slug_before_member_additions(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "parent": {
                    "settings": {"name": "Old Parent", "privacy": "secret"},
                    "members": {"items": {}},
                }
            }
        }
        current.ids[("teams", "parent")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "parent": {
                            "settings": {"name": "New Parent", "privacy": "closed"},
                            "members": {"items": {"alice": "member"}},
                        },
                        "child": {
                            "settings": {
                                "name": "Child",
                                "privacy": "closed",
                                "parent": "parent",
                            }
                        },
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.method for operation in operations], ["PATCH", "POST", "PUT"]
        )
        self.assertEqual(operations[0].phase, 4)
        self.assertEqual(operations[2].phase, 20)
        self.assertIn("/__RESOLVED_TEAM_SLUG__/", operations[2].endpoint)
        self.assertEqual(
            operations[2].endpoint_id_references,
            [("__RESOLVED_TEAM_SLUG__", ("team_slugs", "parent"))],
        )

    def test_team_reparenting_detaches_before_a_rename_and_reattaches_after(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "old-parent": {"settings": {"name": "Old Parent", "privacy": "closed"}},
                "new-parent": {"settings": {"name": "New Parent", "privacy": "closed"}},
                "old-team": {
                    "settings": {
                        "name": "Old Team",
                        "privacy": "closed",
                        "parent": "old-parent",
                    }
                },
            }
        }
        current.ids[("teams", "old-parent")] = 7
        current.ids[("teams", "new-parent")] = 8
        current.ids[("teams", "old-team")] = 9
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "old-team": {
                            "settings": {
                                "name": "Renamed Team",
                                "parent": "new-parent",
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual([operation.phase for operation in operations], [3, 70, 75])
        self.assertEqual(
            [operation.endpoint for operation in operations],
            [
                "/orgs/acme/teams/old-team",
                "/orgs/acme/teams/old-team",
                "/orgs/acme/teams/__RESOLVED_TEAM_SLUG__",
            ],
        )
        self.assertEqual(
            operations[1].capture_response_values,
            [(("team_slugs", "old-team"), ("slug",))],
        )
        self.assertEqual(
            operations[2].endpoint_id_references,
            [("__RESOLVED_TEAM_SLUG__", ("team_slugs", "old-team"))],
        )

    def test_team_aliases_converge_across_every_slug_reference(self) -> None:
        current = snapshot()
        current.config["organization"].update(
            {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "new-team": {
                            "settings": {"name": "Platform", "privacy": "closed"}
                        }
                    },
                },
                "copilot": {"seats": {"users": [], "teams": ["new-team"]}},
                "organization_roles": {
                    "mode": "exact",
                    "items": {"Release": {"users": [], "teams": ["new-team"]}},
                },
                "security_manager_teams": ["new-team"],
                "invitations": {
                    "mode": "exact",
                    "items": {
                        "alice": {
                            "login": "alice",
                            "role": "direct_member",
                            "teams": ["new-team"],
                        }
                    },
                },
            }
        )
        current.config["repositories"]["items"]["api"].update(
            {
                "branch_protections": {
                    "items": {
                        "main": {
                            "required_status_checks": None,
                            "enforce_admins": False,
                            "required_pull_request_reviews": None,
                            "restrictions": {
                                "users": [],
                                "teams": ["new-team"],
                                "apps": [],
                            },
                        }
                    }
                },
                "environments": {
                    "items": {
                        "production": {
                            "settings": {
                                "wait_timer": 0,
                                "prevent_self_review": False,
                                "reviewers": [{"type": "team", "name": "new-team"}],
                                "deployment_branch_policy": None,
                            }
                        }
                    }
                },
            }
        )
        current.ids[("teams", "new-team")] = 7
        current.ids[("organization_roles", "Release")] = 8
        current.ids[("organization_invitations", "alice")] = 9
        desired = copy.deepcopy(current.config)
        desired_team = desired["organization"]["teams"]["items"].pop("new-team")
        desired["organization"]["teams"]["items"]["old-team"] = desired_team
        desired["organization"]["copilot"]["seats"]["teams"] = ["old-team"]
        desired["organization"]["organization_roles"]["items"]["Release"]["teams"] = [
            "old-team"
        ]
        desired["organization"]["security_manager_teams"] = ["old-team"]
        desired["organization"]["invitations"]["items"]["alice"]["teams"] = ["old-team"]
        desired["repositories"]["items"]["api"]["branch_protections"]["items"]["main"][
            "restrictions"
        ]["teams"] = ["old-team"]
        desired["repositories"]["items"]["api"]["environments"]["items"]["production"][
            "settings"
        ]["reviewers"] = [{"type": "team", "name": "old-team"}]

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_team_rename_resolves_later_organization_slug_removals(self) -> None:
        current = snapshot()
        current.config["organization"].update(
            {
                "teams": {
                    "items": {
                        "old-team": {
                            "settings": {"name": "Old Team", "privacy": "closed"}
                        }
                    }
                },
                "copilot": {"seats": {"users": [], "teams": ["old-team"]}},
                "organization_roles": {
                    "mode": "exact",
                    "items": {"Release": {"users": [], "teams": ["old-team"]}},
                },
                "security_manager_teams": ["old-team"],
            }
        )
        current.ids[("teams", "old-team")] = 7
        current.ids[("organization_roles", "Release")] = 8
        desired = copy.deepcopy(current.config)
        desired["organization"]["teams"]["items"]["old-team"]["settings"]["name"] = (
            "New Team"
        )
        desired["organization"]["copilot"]["seats"]["teams"] = []
        desired["organization"]["organization_roles"]["items"]["Release"]["teams"] = []
        desired["organization"]["security_manager_teams"] = []

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.phase for operation in operations], [70, 80, 80, 80]
        )
        self.assertEqual(
            operations[0].capture_response_values,
            [(("team_slugs", "old-team"), ("slug",))],
        )
        dependent = operations[1:]
        endpoint_operations = [
            operation
            for operation in dependent
            if "organization-roles" in operation.endpoint
            or "security-managers" in operation.endpoint
        ]
        self.assertEqual(len(endpoint_operations), 2)
        for operation in endpoint_operations:
            self.assertIn("__RESOLVED_TEAM_SLUG__", operation.endpoint)
            self.assertEqual(
                operation.endpoint_id_references,
                [("__RESOLVED_TEAM_SLUG__", ("team_slugs", "old-team"))],
            )
        copilot = next(
            operation for operation in dependent if "/copilot/" in operation.endpoint
        )
        self.assertEqual(
            copilot.body_id_references,
            [(("selected_teams", 0), ("team_slugs", "old-team"))],
        )

    def test_team_rename_resolves_branch_protection_team_lists(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "old-team": {"settings": {"name": "Old Team", "privacy": "closed"}}
            }
        }
        current.config["repositories"]["items"]["api"]["branch_protections"] = {
            "items": {
                "main": {
                    "required_status_checks": None,
                    "enforce_admins": False,
                    "required_pull_request_reviews": None,
                    "restrictions": {
                        "users": [],
                        "teams": ["old-team"],
                        "apps": [],
                    },
                    "allow_deletions": False,
                }
            }
        }
        current.ids[("teams", "old-team")] = 7
        desired = copy.deepcopy(current.config)
        desired["organization"]["teams"]["items"]["old-team"]["settings"]["name"] = (
            "New Team"
        )
        desired["repositories"]["items"]["api"]["branch_protections"]["items"]["main"][
            "allow_deletions"
        ] = True

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual([operation.phase for operation in operations], [70, 71])
        protection = operations[1]
        self.assertEqual(
            mapping_body(protection)["restrictions"]["teams"],
            ["__RESOLVED_TEAM_SLUG__"],
        )
        self.assertEqual(
            protection.body_id_references,
            [
                (
                    ("restrictions", "teams", 0),
                    ("team_slugs", "old-team"),
                )
            ],
        )

    def test_unwritable_exported_team_permission_blocks_creation(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "platform": {
                            "settings": {
                                "name": "Platform",
                                "permission": "maintain",
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("permission", mapping_body(operations[0]))
        self.assertIn("cannot set", operations[0].blocked_reason or "")

    def test_unwritable_exported_team_permission_is_not_resent(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {
                    "settings": {
                        "name": "Platform",
                        "description": "Before",
                        "permission": "maintain",
                    }
                }
            }
        }
        desired = copy.deepcopy(current.config)
        desired["organization"]["teams"]["items"]["platform"]["settings"][
            "description"
        ] = "After"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("permission", mapping_body(operations[0]))
        self.assertIsNone(operations[0].blocked_reason)

    def test_unwritable_team_permission_update_is_blocked(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {"settings": {"name": "Platform", "permission": "push"}}
            }
        }
        desired = copy.deepcopy(current.config)
        desired["organization"]["teams"]["items"]["platform"]["settings"][
            "permission"
        ] = "maintain"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("permission", mapping_body(operations[0]))
        self.assertIn("cannot set", operations[0].blocked_reason or "")

    def test_nested_team_secret_privacy_is_blocked(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {"settings": {"name": "Platform", "privacy": "closed"}},
                "api": {
                    "settings": {
                        "name": "API",
                        "privacy": "closed",
                        "parent": "platform",
                    }
                },
            }
        }

        for slug in ("api", "platform"):
            with self.subTest(slug=slug):
                desired = {
                    "version": 1,
                    "organization": {
                        "teams": {"items": {slug: {"settings": {"privacy": "secret"}}}}
                    },
                }

                operations = Planner(FakeApi(), current, "acme").plan(desired)

                self.assertEqual(len(operations), 1)
                self.assertIn("closed privacy", operations[0].blocked_reason or "")

    def test_nested_team_privacy_uses_the_current_slug_after_a_rename(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "new-team": {"settings": {"name": "Platform", "privacy": "closed"}},
                "api": {
                    "settings": {
                        "name": "API",
                        "privacy": "closed",
                        "parent": "new-team",
                    }
                },
            }
        }
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "old-team": {
                            "settings": {"name": "Platform", "privacy": "secret"}
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].endpoint, "/orgs/acme/teams/new-team")
        self.assertIn("closed privacy", operations[0].blocked_reason or "")

    def test_current_parent_slug_orders_a_child_after_its_aliased_parent(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "new-parent": {"settings": {"name": "Platform", "privacy": "secret"}}
            },
        }
        current.ids[("teams", "new-parent")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "old-parent": {
                            "settings": {"name": "Platform", "privacy": "closed"}
                        },
                        "child": {
                            "settings": {
                                "name": "Child",
                                "privacy": "closed",
                                "parent": "new-parent",
                            }
                        },
                    },
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                ("PATCH", "/orgs/acme/teams/new-parent"),
                ("POST", "/orgs/acme/teams"),
            ],
        )
        self.assertEqual([operation.phase for operation in operations], [4, 5])
        self.assertTrue(
            all(operation.blocked_reason is None for operation in operations)
        )

    def test_existing_teams_cannot_be_updated_into_a_parent_cycle(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "api": {"settings": {"name": "API", "privacy": "closed"}},
                "platform": {"settings": {"name": "Platform", "privacy": "closed"}},
            }
        }
        current.ids[("teams", "api")] = 7
        current.ids[("teams", "platform")] = 8
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "api": {"settings": {"parent": "platform"}},
                        "platform": {"settings": {"parent": "api"}},
                    }
                }
            },
        }

        with self.assertRaisesRegex(ConfigError, "parent cycle"):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_hierarchy_inversion_detaches_the_old_child_before_attaching_it(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "a": {"settings": {"name": "A", "privacy": "closed"}},
                "z": {
                    "settings": {
                        "name": "Z",
                        "privacy": "closed",
                        "parent": "a",
                    }
                },
            }
        }
        current.ids[("teams", "a")] = 7
        current.ids[("teams", "z")] = 8
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "a": {"settings": {"parent": "z"}},
                        "z": {"settings": {"parent": None}},
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.endpoint for operation in operations],
            ["/orgs/acme/teams/z", "/orgs/acme/teams/a"],
        )
        self.assertEqual([operation.phase for operation in operations], [3, 75])
        self.assertEqual(mapping_body(operations[0]), {"parent_team_id": None})
        self.assertEqual(
            operations[1].body_id_references,
            [(("parent_team_id",), ("teams", "z"))],
        )

    def test_parent_is_closed_before_adding_a_child(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {"settings": {"name": "Platform", "privacy": "secret"}}
            }
        }
        current.ids[("teams", "platform")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "platform": {"settings": {"privacy": "closed"}},
                        "api": {
                            "settings": {
                                "name": "API",
                                "privacy": "closed",
                                "parent": "platform",
                            }
                        },
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                ("PATCH", "/orgs/acme/teams/platform"),
                ("POST", "/orgs/acme/teams"),
            ],
        )
        self.assertTrue(
            all(operation.blocked_reason is None for operation in operations)
        )

    def test_parent_becomes_secret_after_removing_its_last_child(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "platform": {"settings": {"name": "Platform", "privacy": "closed"}},
                "api": {
                    "settings": {
                        "name": "API",
                        "privacy": "closed",
                        "parent": "platform",
                    }
                },
            },
        }
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "platform": {"settings": {"privacy": "secret"}},
                    },
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                ("DELETE", "/orgs/acme/teams/api"),
                ("PATCH", "/orgs/acme/teams/platform"),
            ],
        )
        self.assertTrue(
            all(operation.blocked_reason is None for operation in operations)
        )

    def test_parent_deletion_is_blocked_when_it_would_delete_a_retained_child(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "parent": {"settings": {"name": "Parent", "privacy": "closed"}},
                "child": {
                    "settings": {
                        "name": "Child",
                        "privacy": "closed",
                        "parent": "parent",
                    }
                },
            },
        }
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {"child": {"settings": {}}},
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].endpoint, "/orgs/acme/teams/parent")
        self.assertIn("retained", operations[0].blocked_reason or "")

    def test_omitted_nested_teams_are_deleted_from_child_to_parent(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "a-parent": {"settings": {"name": "Parent", "privacy": "closed"}},
                "m-child": {
                    "settings": {
                        "name": "Child",
                        "privacy": "closed",
                        "parent": "a-parent",
                    }
                },
                "z-grandchild": {
                    "settings": {
                        "name": "Grandchild",
                        "privacy": "closed",
                        "parent": "m-child",
                    }
                },
            },
        }
        desired = {
            "version": 1,
            "organization": {"teams": {"mode": "exact", "items": {}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.endpoint for operation in operations],
            [
                "/orgs/acme/teams/z-grandchild",
                "/orgs/acme/teams/m-child",
                "/orgs/acme/teams/a-parent",
            ],
        )

    def test_renamed_parent_becomes_secret_after_detaching_its_child(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "new-parent": {"settings": {"name": "Platform", "privacy": "closed"}},
                "z-child": {
                    "settings": {
                        "name": "Child",
                        "privacy": "closed",
                        "parent": "new-parent",
                    }
                },
            }
        }
        current.ids[("teams", "new-parent")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "old-parent": {
                            "settings": {"name": "Platform", "privacy": "secret"}
                        },
                        "z-child": {"settings": {"parent": None}},
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.endpoint for operation in operations],
            ["/orgs/acme/teams/z-child", "/orgs/acme/teams/new-parent"],
        )
        self.assertTrue(
            all(operation.blocked_reason is None for operation in operations)
        )

    def test_new_child_is_blocked_when_its_parent_remains_secret(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {"settings": {"name": "Platform", "privacy": "secret"}}
            }
        }
        current.ids[("teams", "platform")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "api": {
                            "settings": {
                                "name": "API",
                                "privacy": "closed",
                                "parent": "platform",
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn("parent team", operations[0].blocked_reason or "")

    def test_private_registry_secret_uses_environment_reference(self) -> None:
        current = snapshot()
        desired = {
            "version": 1,
            "organization": {
                "private_registries": {
                    "items": {
                        "NPM_REGISTRY_SECRET": {
                            "registry_type": "npm_registry",
                            "url": "https://registry.example",
                            "visibility": "all",
                            "auth_type": "token",
                            "value_from_env": "NPM_TOKEN",
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].secret_environment, "NPM_TOKEN")
        self.assertNotIn("NPM_TOKEN", str(operations[0].body))

    def test_environment_is_created_before_its_variables(self) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {
                                "production": {
                                    "variables": {
                                        "items": {"REGION": {"value": "west"}}
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)
        self.assertEqual(
            [operation.endpoint for operation in operations],
            [
                "/repos/acme/api/environments/production",
                "/repos/acme/api/environments/production/variables",
            ],
        )

    def test_environment_accepts_null_for_no_required_reviewers(self) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {
                                "production": {
                                    "settings": {"reviewers": None},
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].body, {"reviewers": []})

        current = snapshot()
        current.config["repositories"]["items"]["api"]["environments"] = {
            "mode": "exact",
            "items": {"production": {"settings": {"reviewers": []}}},
        }
        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_partial_environment_update_preserves_required_reviewers(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["environments"] = {
            "mode": "exact",
            "items": {
                "production": {
                    "settings": {
                        "wait_timer": 0,
                        "reviewers": [{"type": "team", "name": "release"}],
                    }
                }
            },
        }
        current.ids[("teams", "release")] = 7
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {"production": {"settings": {"wait_timer": 5}}}
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(
            mapping_body(operations[0])["reviewers"],
            [{"type": "Team", "id": None}],
        )
        self.assertEqual(
            operations[0].body_id_references,
            [(("reviewers", 0, "id"), ("teams", "release"))],
        )
        self.assertTrue(
            all("reviewers" not in change.path for change in operations[0].changes)
        )

    def test_environment_deployment_policy_requires_exactly_one_mode(self) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {
                                "production": {
                                    "settings": {
                                        "deployment_branch_policy": {
                                            "protected_branches": True,
                                            "custom_branch_policies": True,
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }

        with self.assertRaisesRegex(ConfigError, "must set exactly one"):
            Planner(FakeApi(), snapshot(), "acme").plan(desired)

    def test_null_environment_deployment_policy_converges(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["environments"] = {
            "mode": "exact",
            "items": {
                "production": {
                    "settings": {
                        "wait_timer": 0,
                        "prevent_self_review": False,
                        "reviewers": [],
                        "deployment_branch_policy": None,
                    }
                }
            },
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {
                                "production": {
                                    "settings": {
                                        "deployment_branch_policy": None,
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_legacy_empty_environment_deployment_policy_converges(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["environments"] = {
            "mode": "exact",
            "items": {
                "production": {
                    "settings": {
                        "wait_timer": 0,
                        "prevent_self_review": False,
                        "reviewers": [],
                        "deployment_branch_policy": {},
                    }
                }
            },
        }
        desired = copy.deepcopy(current.config)

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_runner_group_is_created_before_a_hosted_runner_that_uses_it(
        self,
    ) -> None:
        desired = {
            "version": 1,
            "organization": {
                "actions": {
                    "runner_groups": {
                        "items": {"linux": {"settings": {"name": "Linux"}}}
                    },
                    "hosted_runners": {
                        "items": {
                            "large": {
                                "name": "large",
                                "image": {"id": "ubuntu-latest", "source": "github"},
                                "size": "large",
                                "runner_group": "linux",
                            }
                        }
                    },
                }
            },
        }
        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)
        self.assertEqual(
            [operation.endpoint for operation in operations],
            [
                "/orgs/acme/actions/runner-groups",
                "/orgs/acme/actions/hosted-runners",
            ],
        )
        self.assertTrue(
            all(operation.blocked_reason is None for operation in operations)
        )

    def test_runner_group_creation_omits_a_null_network_configuration(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "actions": {
                    "runner_groups": {
                        "items": {
                            "linux": {
                                "settings": {
                                    "name": "Linux",
                                    "network_configuration": None,
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("network_configuration_id", mapping_body(operations[0]))

    def test_partial_hosted_runner_update_keeps_its_runner_group(self) -> None:
        current = snapshot()
        current.config["organization"]["actions"]["hosted_runners"] = {
            "mode": "exact",
            "items": {
                "large": {
                    "name": "large",
                    "size": "large",
                    "runner_group": "linux",
                    "maximum_runners": 2,
                }
            },
        }
        current.ids[("hosted_runners", "large")] = 42
        desired = {
            "version": 1,
            "organization": {
                "actions": {
                    "hosted_runners": {"items": {"large": {"maximum_runners": 3}}}
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].body_id_references, [])
        self.assertIsNone(operations[0].blocked_reason)

    def test_hosted_runner_update_omits_an_empty_exported_image(self) -> None:
        current = snapshot()
        current.config["organization"]["actions"]["hosted_runners"] = {
            "mode": "exact",
            "items": {
                "image-builder": {
                    "name": "image-builder",
                    "image": {},
                    "maximum_runners": 2,
                    "image_gen": True,
                }
            },
        }
        current.ids[("hosted_runners", "image-builder")] = 42
        desired = copy.deepcopy(current.config)
        desired["organization"]["actions"]["hosted_runners"]["items"]["image-builder"][
            "maximum_runners"
        ] = 3

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        body = mapping_body(operations[0])
        self.assertEqual(body["maximum_runners"], 3)
        self.assertNotIn("image_source", body)
        self.assertNotIn("image_id", body)
        self.assertNotIn("image_version", body)

    def test_pages_update_omits_an_empty_exported_source(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["pages"] = {
            "enabled": True,
            "build_type": "workflow",
            "source": {},
            "https_enforced": False,
        }
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["pages"]["https_enforced"] = True

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(
            mapping_body(operations[0]),
            {"build_type": "workflow", "https_enforced": True},
        )

    def test_repository_is_archived_after_child_configuration_is_removed(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["collaborators"] = {
            "mode": "exact",
            "items": {"alice": "push"},
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {"archived": True},
                        "collaborators": {"mode": "exact", "items": {}},
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [operation.endpoint for operation in operations],
            ["/repos/acme/api/collaborators/alice", "/repos/acme/api"],
        )

    def test_exact_organization_roles_remove_assignments_for_omitted_roles(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["organization_roles"] = {
            "mode": "exact",
            "items": {"Security": {"users": ["alice"], "teams": ["platform"]}},
        }
        current.ids[("organization_roles", "Security")] = 7
        desired = {
            "version": 1,
            "organization": {"organization_roles": {"mode": "exact", "items": {}}},
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            {operation.endpoint for operation in operations},
            {
                "/orgs/acme/organization-roles/users/alice/7",
                "/orgs/acme/organization-roles/teams/platform/7",
            },
        )

    def test_unavailable_collection_state_blocks_a_duplicate_create(self) -> None:
        current = snapshot()
        current.unavailable.append("/repos/acme/api/hooks (403)")
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "hooks": {
                            "items": {
                                "deploy": {
                                    "config": {
                                        "url": "https://example.test/hook",
                                        "content_type": "json",
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertEqual(
            operations[0].blocked_reason,
            "the current GitHub state was unavailable at /repos/acme/api/hooks",
        )

    def test_webhook_config_only_update_preserves_write_only_credentials(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["hooks"] = {
            "mode": "exact",
            "items": {
                "deploy": {
                    "active": True,
                    "events": ["push"],
                    "config": {
                        "url": "https://example.test/hook",
                        "content_type": "form",
                        "insecure_ssl": "0",
                    },
                }
            },
        }
        current.ids[("repositories", "api", "hooks", "deploy")] = 7
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "hooks": {
                            "items": {
                                "deploy": {
                                    "config": {
                                        "url": "https://example.test/hook",
                                        "content_type": "json",
                                        "insecure_ssl": "0",
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].method, "PATCH")
        self.assertEqual(
            operations[0].endpoint,
            "/repos/acme/api/hooks/7/config",
        )
        self.assertEqual(
            mapping_body(operations[0]),
            {
                "url": "https://example.test/hook",
                "content_type": "json",
                "insecure_ssl": "0",
            },
        )
        self.assertIsNone(operations[0].warning_reason)

    def test_webhook_active_update_warns_that_an_existing_secret_is_removed(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["hooks"] = {
            "mode": "exact",
            "items": {
                "deploy": {
                    "active": True,
                    "events": ["push"],
                    "config": {"url": "https://example.test/hook"},
                }
            },
        }
        current.ids[("repositories", "api", "hooks", "deploy")] = 7
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "hooks": {
                            "items": {
                                "deploy": {
                                    "active": False,
                                    "config": {
                                        "url": "https://example.test/hook",
                                    },
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].endpoint, "/repos/acme/api/hooks/7")
        self.assertEqual(
            mapping_body(operations[0]),
            {"active": False, "events": ["push"]},
        )
        self.assertIn(
            "removes any existing write-only webhook secret",
            operations[0].warning_reason or "",
        )

    def test_organization_webhook_update_warns_about_basic_auth_loss(self) -> None:
        current = snapshot()
        current.config["organization"]["hooks"] = {
            "mode": "exact",
            "items": {
                "deploy": {
                    "active": True,
                    "events": ["push"],
                    "config": {
                        "url": "https://example.test/hook",
                        "content_type": "form",
                    },
                }
            },
        }
        current.ids[("organization", "hooks", "deploy")] = 7
        desired = {
            "version": 1,
            "organization": {
                "hooks": {
                    "items": {
                        "deploy": {
                            "active": False,
                            "config": {
                                "url": "https://example.test/hook",
                                "content_type": "json",
                            },
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn(
            "may remove them",
            operations[0].warning_reason or "",
        )

    def test_app_installation_selected_repositories_are_added_and_removed(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["app_installations"] = {
            "mode": "exact",
            "items": {
                "deploy": {
                    "app_slug": "deploy",
                    "repository_selection": "selected",
                    "selected_repositories": ["old"],
                }
            },
        }
        current.ids[("app_installations", "deploy")] = 9
        desired = {
            "version": 1,
            "organization": {
                "app_installations": {
                    "items": {
                        "deploy": {
                            "app_slug": "deploy",
                            "repository_selection": "selected",
                            "selected_repositories": ["api"],
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                (
                    "PUT",
                    "/user/installations/9/repositories/{repository_id}",
                ),
                (
                    "DELETE",
                    "/user/installations/9/repositories/{repository_id}",
                ),
            ],
        )
        self.assertEqual(
            operations[0].endpoint_id_references,
            [("{repository_id}", ("repositories", "api"))],
        )
        self.assertEqual(
            operations[1].endpoint_id_references,
            [("{repository_id}", ("repositories", "old"))],
        )
        self.assertIn(
            "can omit selected repositories",
            operations[1].warning_reason or "",
        )
        self.assertIsNone(operations[0].blocked_reason)
        self.assertIn("--force", operations[1].blocked_reason or "")

        forced = Planner(FakeApi(), current, "acme", force=True).plan(desired)
        self.assertEqual(
            [(operation.method, operation.blocked_reason) for operation in forced],
            [("PUT", None), ("DELETE", None)],
        )

    def test_app_installation_metadata_change_is_read_only_unless_forced(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["app_installations"] = {
            "mode": "exact",
            "items": {
                "deploy": {
                    "app_slug": "deploy",
                    "repository_selection": "selected",
                }
            },
        }
        current.ids[("app_installations", "deploy")] = 9
        desired = {
            "version": 1,
            "organization": {
                "app_installations": {
                    "items": {
                        "deploy": {
                            "app_slug": "deploy",
                            "repository_selection": "all",
                        }
                    }
                }
            },
        }

        blocked = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].method, "READ-ONLY")
        self.assertIn("installation flow", blocked[0].blocked_reason or "")

        forced_planner = Planner(FakeApi(), current, "acme", force=True)
        self.assertEqual(forced_planner.plan(desired), [])
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_unavailable_null_field_is_still_treated_as_configured(self) -> None:
        current = snapshot()
        current.config["organization"]["custom_properties"] = {
            "mode": "exact",
            "items": {
                "service": {
                    "value_type": "string",
                    "regex": "^api$",
                }
            },
        }
        current.ids[("custom_properties", "service", "node_id")] = "P_service"
        current.unavailable_collections[
            (
                "organization",
                "custom_properties",
                "items",
                "service",
                "regex",
            )
        ] = "/graphql#OrganizationCustomProperties"
        desired = {
            "version": 1,
            "organization": {
                "custom_properties": {
                    "items": {
                        "service": {
                            "regex": None,
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn("could not be read", operations[0].blocked_reason or "")

    def test_changed_credential_authorization_detail_is_read_only(self) -> None:
        current = snapshot()
        key = "alice:SSH key:SHA256:example"
        current.config["organization"]["credential_authorizations"] = {
            "mode": "exact",
            "items": {
                key: {
                    "login": "alice",
                    "credential_type": "SSH key",
                    "fingerprint": "SHA256:example",
                }
            },
        }
        desired = {
            "version": 1,
            "organization": {
                "credential_authorizations": {
                    "items": {
                        key: {
                            "login": "bob",
                        }
                    }
                }
            },
        }

        blocked = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].method, "READ-ONLY")

        forced_planner = Planner(FakeApi(), current, "acme", force=True)
        self.assertEqual(forced_planner.plan(desired), [])
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_computed_domain_field_blocks_a_create_unless_forced(self) -> None:
        current = snapshot()
        current.ids[("organization", "node_id")] = "O_acme"
        desired = {
            "version": 1,
            "organization": {
                "domains": {
                    "items": {
                        "example.com": {
                            "verification_token": "github-token",
                        }
                    }
                }
            },
        }

        blocked = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(blocked), 2)
        self.assertEqual(
            len(
                [operation for operation in blocked if operation.method == "READ-ONLY"]
            ),
            1,
        )

        forced_planner = Planner(FakeApi(), current, "acme", force=True)
        forced = forced_planner.plan(desired)
        self.assertEqual(len(forced), 1)
        self.assertEqual(
            forced[0].endpoint,
            "/graphql#AddOrganizationDomainConfiguration",
        )
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_pull_request_bypass_list_adds_and_removes_individual_users(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"][
            "pull_request_creation_cap_bypass_users"
        ] = ["old"]
        desired = {
            "version": 1,
            "repositories": {
                "items": {"api": {"pull_request_creation_cap_bypass_users": ["new"]}}
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [(operation.method, operation.body) for operation in operations],
            [("PUT", {"users": ["new"]}), ("DELETE", {"users": ["old"]})],
        )

    def test_exact_workflow_collection_does_not_delete_workflow_content(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["workflow_states"] = {
            "mode": "exact",
            "items": {".github/workflows/test.yaml": "active"},
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {"api": {"workflow_states": {"mode": "exact", "items": {}}}}
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("repository content", operations[0].blocked_reason or "")

    def test_code_security_migration_detaches_before_attaching(self) -> None:
        current = snapshot()
        current.config["organization"]["code_security"] = {
            "configurations": {
                "mode": "exact",
                "items": {
                    "A": {"name": "A", "repositories": []},
                    "Z": {"name": "Z", "repositories": ["api"]},
                },
            }
        }
        current.ids.update(
            {
                ("organization_collections", "code_security.configurations", "A"): 1,
                ("organization_collections", "code_security.configurations", "Z"): 2,
            }
        )
        desired = copy.deepcopy(current.config)
        configurations = desired["organization"]["code_security"]["configurations"]
        configurations["items"]["A"]["repositories"] = ["api"]
        configurations["items"]["Z"]["repositories"] = []
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                (
                    "DELETE",
                    "/orgs/acme/code-security/configurations/detach",
                ),
                (
                    "POST",
                    "/orgs/acme/code-security/configurations/__CONFIGURATION_ID__/attach",
                ),
            ],
        )

    def test_code_security_migration_supports_an_archived_repository(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["archived"] = True
        current.config["organization"]["code_security"] = {
            "configurations": {
                "mode": "exact",
                "items": {
                    "A": {"name": "A", "repositories": []},
                    "Z": {"name": "Z", "repositories": ["api"]},
                },
            }
        }
        current.ids.update(
            {
                ("organization_collections", "code_security.configurations", "A"): 1,
                ("organization_collections", "code_security.configurations", "Z"): 2,
            }
        )
        desired = copy.deepcopy(current.config)
        configurations = desired["organization"]["code_security"]["configurations"]
        configurations["items"]["A"]["repositories"] = ["api"]
        configurations["items"]["Z"]["repositories"] = []

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.method, operation.endpoint) for operation in operations],
            [
                ("DELETE", "/orgs/acme/code-security/configurations/detach"),
                (
                    "POST",
                    "/orgs/acme/code-security/configurations/__CONFIGURATION_ID__/attach",
                ),
            ],
        )

    def test_issue_field_creation_omits_existing_option_ids(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "issue_fields": {
                    "items": {
                        "Service": {
                            "name": "Service",
                            "data_type": "single_select",
                            "options": [
                                {
                                    "id": 7,
                                    "name": "API",
                                    "color": "blue",
                                    "priority": 1,
                                    "created_at": "2026-01-01T00:00:00Z",
                                    "updated_at": "2026-01-02T00:00:00Z",
                                }
                            ],
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        option = mapping_body(operations[0])["options"][0]
        self.assertNotIn("id", option)
        self.assertNotIn("created_at", option)
        self.assertNotIn("updated_at", option)
        self.assertEqual(option["name"], "API")

    def test_issue_field_update_omits_unchanged_response_only_option_nulls(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["issue_fields"] = {
            "mode": "exact",
            "items": {
                "options": {
                    "name": "options",
                    "description": "Before",
                    "data_type": "single_select",
                    "options": [
                        {
                            "id": 2,
                            "name": "API",
                            "color": None,
                            "priority": None,
                        }
                    ],
                }
            },
        }
        current.ids[("organization_collections", "issue_fields", "options")] = 1
        desired = copy.deepcopy(current.config)
        desired["organization"]["issue_fields"]["items"]["options"]["description"] = (
            "After"
        )

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("options", mapping_body(operations[0]))
        self.assertIsNone(operations[0].blocked_reason)

    def test_issue_field_option_update_requires_non_null_writable_values(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["issue_fields"] = {
            "mode": "exact",
            "items": {
                "Service": {
                    "name": "Service",
                    "data_type": "single_select",
                    "options": [
                        {
                            "id": 2,
                            "name": "API",
                            "color": None,
                            "priority": None,
                        }
                    ],
                }
            },
        }
        current.ids[("organization_collections", "issue_fields", "Service")] = 1
        desired = copy.deepcopy(current.config)
        desired["organization"]["issue_fields"]["items"]["Service"]["options"][0][
            "name"
        ] = "Backend"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn("requires non-null", operations[0].blocked_reason or "")

    def test_repository_update_omits_response_only_security_setting(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {
                "description": "Before",
                "security_and_analysis": {
                    "advanced_security": {"status": "enabled"},
                    "dependabot_security_updates": {"status": "enabled"},
                },
            }
        )
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["settings"]["description"] = "After"

        operations = Planner(FakeApi(), current, "acme").plan(desired)
        repository_update = next(
            operation
            for operation in operations
            if operation.endpoint == "/repos/acme/api"
        )

        security = mapping_body(repository_update)["security_and_analysis"]
        self.assertEqual(security, {"advanced_security": {"status": "enabled"}})

    def test_nullable_response_settings_are_not_resent(self) -> None:
        current = snapshot()
        current.config["organization"]["settings"].update(
            {
                "billing_email": None,
                "description": None,
                "secret_scanning_push_protection_custom_link": None,
                "twitter_username": None,
                "has_repository_projects": True,
            }
        )
        desired = copy.deepcopy(current.config)
        desired["organization"]["settings"]["has_repository_projects"] = False

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        body = mapping_body(operations[0])
        self.assertEqual(body["has_repository_projects"], False)
        for field in (
            "billing_email",
            "description",
            "secret_scanning_push_protection_custom_link",
            "twitter_username",
        ):
            self.assertNotIn(field, body)

        current = snapshot()
        repository_settings = current.config["repositories"]["items"]["api"]["settings"]
        repository_settings.update({"description": None, "homepage": None})
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["settings"][
            "delete_branch_on_merge"
        ] = True

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        body = mapping_body(operations[0])
        self.assertNotIn("description", body)
        self.assertNotIn("homepage", body)

        current = snapshot()
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {
                    "settings": {
                        "name": "Platform",
                        "description": None,
                        "notification_setting": "notifications_enabled",
                    }
                }
            }
        }
        desired = copy.deepcopy(current.config)
        desired["organization"]["teams"]["items"]["platform"]["settings"][
            "notification_setting"
        ] = "notifications_disabled"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("description", mapping_body(operations[0]))

        current = snapshot()
        current.config["repositories"]["items"]["api"]["labels"] = {
            "items": {
                "bug": {
                    "name": "bug",
                    "color": "ff0000",
                    "description": None,
                }
            }
        }
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["labels"]["items"]["bug"]["color"] = (
            "00ff00"
        )

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("description", mapping_body(operations[0]))

    def test_nullable_response_settings_are_omitted_when_creating_resources(
        self,
    ) -> None:
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "eng": {
                            "settings": {
                                "name": "Engineering Team",
                                "description": None,
                            }
                        }
                    }
                }
            },
            "repositories": {
                "items": {
                    "new": {
                        "settings": {"description": None, "homepage": None},
                    },
                    "api": {
                        "labels": {
                            "items": {
                                "bug": {
                                    "color": "ff0000",
                                    "description": None,
                                }
                            }
                        }
                    },
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        create_bodies = {
            operation.endpoint: mapping_body(operation)
            for operation in operations
            if operation.method == "POST"
        }
        self.assertNotIn("description", create_bodies["/orgs/acme/teams"])
        self.assertNotIn("description", create_bodies["/orgs/acme/repos"])
        self.assertNotIn("homepage", create_bodies["/orgs/acme/repos"])
        self.assertNotIn("description", create_bodies["/repos/acme/api/labels"])

    def test_code_security_update_omits_response_only_null_values(self) -> None:
        current = snapshot()
        current.config["organization"]["code_security"] = {
            "configurations": {
                "mode": "exact",
                "items": {
                    "Default": {
                        "name": "Default",
                        "description": None,
                        "advanced_security": "enabled",
                        "dependabot_delegated_alert_dismissal": None,
                        "code_scanning_default_setup_options": {
                            "runner_type": None,
                            "runner_label": None,
                        },
                        "enforcement": "unenforced",
                    }
                },
            }
        }
        current.ids[
            ("organization_collections", "code_security.configurations", "Default")
        ] = 3
        desired = copy.deepcopy(current.config)
        desired["organization"]["code_security"]["configurations"]["items"]["Default"][
            "enforcement"
        ] = "enforced"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        body = mapping_body(operations[0])
        self.assertNotIn("description", body)
        self.assertNotIn("dependabot_delegated_alert_dismissal", body)
        self.assertEqual(
            body["code_scanning_default_setup_options"], {"runner_label": None}
        )
        self.assertIsNone(operations[0].blocked_reason)

    def test_code_security_creation_omits_response_only_null_values(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "code_security": {
                    "configurations": {
                        "items": {
                            "Default": {
                                "name": "Default",
                                "description": None,
                                "dependabot_delegated_alert_dismissal": None,
                                "code_scanning_default_setup_options": {
                                    "runner_type": None,
                                    "runner_label": None,
                                },
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        body = mapping_body(operations[0])
        self.assertNotIn("description", body)
        self.assertNotIn("dependabot_delegated_alert_dismissal", body)
        self.assertEqual(
            body["code_scanning_default_setup_options"], {"runner_label": None}
        )

    def test_codespaces_network_service_is_not_resent_on_update(self) -> None:
        current = snapshot()
        current.config["organization"]["hosted_compute"] = {
            "network_configurations": {
                "mode": "exact",
                "items": {
                    "net1": {
                        "name": "Original",
                        "compute_service": "codespaces",
                        "network_settings_ids": ["network-1"],
                    }
                },
            }
        }
        current.ids[
            (
                "organization_collections",
                "hosted_compute.network_configurations",
                "net1",
            )
        ] = "net1"
        desired = copy.deepcopy(current.config)
        desired["organization"]["hosted_compute"]["network_configurations"]["items"][
            "net1"
        ]["name"] = "Renamed"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("compute_service", mapping_body(operations[0]))
        self.assertIsNone(operations[0].blocked_reason)

    def test_codespaces_network_service_creation_is_blocked(self) -> None:
        desired = {
            "version": 1,
            "organization": {
                "hosted_compute": {
                    "network_configurations": {
                        "items": {
                            "net1": {
                                "name": "Codespaces",
                                "compute_service": "codespaces",
                                "network_settings_ids": ["network-1"],
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("compute_service", mapping_body(operations[0]))
        self.assertIn("accepts only", operations[0].blocked_reason or "")

    def test_repository_setup_updates_omit_response_only_values(self) -> None:
        current = snapshot()
        repository = current.config["repositories"]["items"]["api"]
        repository["code_scanning"] = {
            "default_setup": {
                "state": "configured",
                "runner_type": "standard",
                "languages": ["javascript-typescript"],
            }
        }
        repository["code_quality"] = {
            "setup": {
                "state": "configured",
                "runner_type": "standard",
                "languages": ["python"],
            }
        }
        desired = copy.deepcopy(current.config)
        desired_repository = desired["repositories"]["items"]["api"]
        desired_repository["code_scanning"]["default_setup"] = {
            "state": "not-configured",
            "runner_type": None,
            "languages": ["javascript"],
        }
        desired_repository["code_quality"]["setup"] = {
            "state": "not-configured",
            "runner_type": None,
            "languages": ["rust"],
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        setup_operations = {
            operation.endpoint: operation.body for operation in operations
        }
        self.assertEqual(
            setup_operations,
            {
                "/repos/acme/api/code-scanning/default-setup": {
                    "state": "not-configured"
                },
                "/repos/acme/api/code-quality/setup": {"state": "not-configured"},
            },
        )

    def test_inherited_budgets_are_blocked(self) -> None:
        inherited = {
            "budget_amount": 100,
            "budget_scope": "multi_user_cost_center",
            "budget_entity_name": "Shared",
            "budget_type": "ProductPricing",
            "budget_product_sku": "actions",
        }
        create_desired = {
            "version": 1,
            "organization": {
                "budgets": {"items": {"Inherited": copy.deepcopy(inherited)}}
            },
        }

        create_operations = Planner(FakeApi(), snapshot(), "acme").plan(create_desired)

        self.assertEqual(len(create_operations), 1)
        self.assertIn("inherited", create_operations[0].blocked_reason or "")

        current = snapshot()
        current.config["organization"]["budgets"] = {
            "mode": "exact",
            "items": {"Inherited": copy.deepcopy(inherited)},
        }
        current.ids[("budgets", "Inherited")] = "budget-1"
        update_desired = copy.deepcopy(current.config)
        update_desired["organization"]["budgets"]["items"]["Inherited"][
            "budget_amount"
        ] = 200
        delete_desired = {
            "version": 1,
            "organization": {"budgets": {"mode": "exact", "items": {}}},
        }

        for desired in (update_desired, delete_desired):
            with self.subTest(desired=desired):
                operations = Planner(FakeApi(), current, "acme").plan(desired)
                self.assertEqual(len(operations), 1)
                self.assertIn("inherited", operations[0].blocked_reason or "")

    def test_ruleset_update_omits_response_only_null_conditions(self) -> None:
        current = snapshot()
        current.config["organization"]["rulesets"] = {
            "mode": "exact",
            "items": {
                "Repository policy": {
                    "name": "Repository policy",
                    "target": "repository",
                    "enforcement": "active",
                    "conditions": None,
                    "rules": [],
                }
            },
        }
        current.ids[("organization", "rulesets", "Repository policy")] = 8
        desired = copy.deepcopy(current.config)
        desired["organization"]["rulesets"]["items"]["Repository policy"][
            "enforcement"
        ] = "disabled"

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("conditions", mapping_body(operations[0]))
        self.assertIsNone(operations[0].blocked_reason)

    def test_nested_collection_response_metadata_is_not_applied(self) -> None:
        current = snapshot()
        current.config["organization"].update(
            {
                "issue_fields": {
                    "mode": "exact",
                    "items": {
                        "Service": {
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
                    },
                },
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {
                            "Default": {
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
                        },
                    }
                },
            }
        )
        current.ids.update(
            {
                ("organization_collections", "issue_fields", "Service"): 1,
                (
                    "organization_collections",
                    "code_security.configurations",
                    "Default",
                ): 3,
            }
        )
        desired = copy.deepcopy(current.config)
        desired["organization"]["issue_fields"]["items"]["Service"]["options"][0][
            "updated_at"
        ] = "2026-01-03T00:00:00Z"
        desired["organization"]["code_security"]["configurations"]["items"]["Default"][
            "secret_scanning_delegated_bypass_options"
        ]["reviewers"][0]["security_configuration_id"] = 4

        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])

    def test_partial_autolink_update_preserves_required_fields(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["autolinks"] = {
            "mode": "exact",
            "items": {
                "JIRA-": {
                    "key_prefix": "JIRA-",
                    "url_template": "https://jira.test/browse/<num>",
                    "is_alphanumeric": False,
                }
            },
        }
        current.ids[("repositories", "api", "autolinks", "JIRA-")] = 7
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "autolinks": {"items": {"JIRA-": {"is_alphanumeric": True}}}
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [operation.method for operation in operations], ["DELETE", "POST"]
        )
        self.assertEqual(
            operations[1].body,
            {
                "key_prefix": "JIRA-",
                "url_template": "https://jira.test/browse/<num>",
                "is_alphanumeric": True,
            },
        )

    def test_omitted_assignment_categories_remain_unmanaged(self) -> None:
        current = snapshot()
        current.config["organization"].update(
            {
                "copilot": {"seats": {"users": ["old"], "teams": ["platform"]}},
                "organization_roles": {
                    "mode": "exact",
                    "items": {
                        "Security": {
                            "users": ["old"],
                            "teams": ["platform"],
                        }
                    },
                },
            }
        )
        current.ids[("organization_roles", "Security")] = 7
        desired = {
            "version": 1,
            "organization": {
                "copilot": {"seats": {"users": ["new"]}},
                "organization_roles": {
                    "mode": "exact",
                    "items": {"Security": {"users": ["new"]}},
                },
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertTrue(
            all("platform" not in operation.endpoint for operation in operations)
        )
        self.assertTrue(
            all(
                ".teams" not in change.path
                for operation in operations
                for change in operation.changes
            )
        )

    def test_exact_empty_unavailable_collection_is_blocked(self) -> None:
        current = snapshot()
        current.unavailable_collections[("repositories", "items", "api", "hooks")] = (
            "/repos/acme/api/hooks"
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {"api": {"hooks": {"mode": "exact", "items": {}}}}
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("could not be read", operations[0].blocked_reason or "")

    def test_pull_request_cap_partial_update_includes_current_enabled_value(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["pull_request_creation_cap"] = {
            "enabled": True,
            "max_open_pull_requests": 10,
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {"pull_request_creation_cap": {"max_open_pull_requests": 20}}
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            operations[0].body,
            {"enabled": True, "max_open_pull_requests": 20},
        )

    def test_cross_organization_duplicate_identity_change_is_blocked(self) -> None:
        current = snapshot()
        desired = {
            "version": 1,
            "_observed": {"organization": "source"},
            "repositories": {
                "items": {
                    "api": {
                        "labels": {
                            "items": {"duplicate#github-id-12": {"color": "ff0000"}}
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("organization-local", operations[0].blocked_reason or "")

    def test_organization_invitation_resolves_user_and_team_ids(self) -> None:
        current = snapshot()
        current.ids[("teams", "platform")] = 4
        api = FakeApi(responses={("GET", "/users/alice"): {"id": 9}})
        desired = {
            "version": 1,
            "organization": {
                "invitations": {
                    "items": {
                        "alice": {
                            "login": "alice",
                            "role": "direct_member",
                            "teams": ["platform"],
                        }
                    }
                }
            },
        }
        operations = Planner(api, current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].endpoint, "/orgs/acme/invitations")
        self.assertEqual(
            operations[0].body_id_references,
            [(("invitee_id",), ("users", "alice"))],
        )
        self.assertEqual(
            operations[0].body_id_list_references,
            [(("team_ids",), [("teams", "platform")])],
        )

    def test_converting_exact_member_to_outside_collaborator_uses_one_request(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["members"] = {
            "mode": "exact",
            "items": {"alice": {"role": "member"}},
        }
        current.config["organization"]["outside_collaborators"] = {
            "mode": "exact",
            "items": {},
        }
        desired = {
            "version": 1,
            "organization": {
                "members": {"mode": "exact", "items": {}},
                "outside_collaborators": {
                    "mode": "exact",
                    "items": {"alice": {}},
                },
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [operation.endpoint for operation in operations],
            ["/orgs/acme/outside_collaborators/alice"],
        )

    def test_repository_invitation_permission_is_updated_by_invitation_id(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["collaborator_invitations"] = {
            "mode": "exact",
            "items": {"alice": "read"},
        }
        current.ids[("repositories", "api", "invitations", "alice")] = 44
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "collaborator_invitations": {
                            "mode": "exact",
                            "items": {"alice": "write"},
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(operations[0].method, "PATCH")
        self.assertEqual(operations[0].endpoint, "/repos/acme/api/invitations/44")
        self.assertEqual(operations[0].body, {"permissions": "write"})

    def test_exact_personal_access_token_collection_revokes_an_omitted_grant(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["personal_access_tokens"] = {
            "mode": "exact",
            "items": {"alice:automation": {"owner": "alice"}},
        }
        current.ids[("personal_access_tokens", "alice:automation")] = 31
        desired = {
            "version": 1,
            "organization": {"personal_access_tokens": {"mode": "exact", "items": {}}},
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            operations[0].endpoint,
            "/orgs/acme/personal-access-tokens/31",
        )
        self.assertEqual(operations[0].body, {"action": "revoke"})
        self.assertIsNotNone(operations[0].warning_reason)

    def test_force_ignores_unwritable_personal_access_token_edits(self) -> None:
        current = snapshot()
        current.config["organization"]["personal_access_tokens"] = {
            "mode": "exact",
            "items": {"alice:automation": {"owner": "alice"}},
        }
        desired = {
            "version": 1,
            "organization": {
                "personal_access_tokens": {
                    "items": {"alice:automation": {"owner": "bob"}}
                }
            },
        }

        blocked = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(blocked[0].method, "READ-ONLY")

        forced_planner = Planner(FakeApi(), current, "acme", force=True)
        self.assertEqual(forced_planner.plan(desired), [])
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_exact_credential_authorizations_revoke_omitted_entries(self) -> None:
        current = snapshot()
        key = "alice:SSH key:SHA256:example"
        current.config["organization"]["credential_authorizations"] = {
            "mode": "exact",
            "items": {key: {"login": "alice", "credential_type": "SSH key"}},
        }
        current.ids[("credential_authorizations", key)] = 42
        desired = {
            "version": 1,
            "organization": {
                "credential_authorizations": {"mode": "exact", "items": {}}
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0].method, "DELETE")
        self.assertEqual(
            operations[0].endpoint,
            "/orgs/acme/credential-authorizations/42",
        )
        self.assertIsNotNone(operations[0].warning_reason)

    def test_plans_organization_custom_property_value_updates_and_removals(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["custom_property_values"] = {
            "mode": "exact",
            "items": {
                "cost_center": "sales",
                "legacy": "remove-me",
            },
        }
        desired = {
            "version": 1,
            "organization": {
                "custom_property_values": {
                    "mode": "exact",
                    "items": {
                        "cost_center": "engineering",
                        "regions": ["us", "ca"],
                    },
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertEqual(
            operations[0].endpoint,
            "/organizations/acme/org-properties/values",
        )
        self.assertEqual(
            operations[0].body,
            {
                "properties": [
                    {"property_name": "cost_center", "value": "engineering"},
                    {"property_name": "regions", "value": ["us", "ca"]},
                    {"property_name": "legacy", "value": None},
                ]
            },
        )

    def test_rejects_invalid_organization_custom_property_value_type(self) -> None:
        desired = {
            "version": 1,
            "organization": {"custom_property_values": {"items": {"cost_center": 17}}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "organization.custom_property_values.items.cost_center must be a string",
        ):
            Planner(FakeApi(), snapshot(), "acme").plan(desired)

    def test_repository_self_hosted_runner_labels_are_updated(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["actions"] = {
            "self_hosted_runners": {
                "mode": "exact",
                "items": {"build": {"labels": ["old"]}},
            }
        }
        current.ids[("repositories", "api", "self_hosted_runners", "build")] = 6
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "actions": {
                            "self_hosted_runners": {
                                "items": {"build": {"labels": ["new"]}}
                            }
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            operations[0].endpoint,
            "/repos/acme/api/actions/runners/6/labels",
        )
        self.assertEqual(operations[0].body, {"labels": ["new"]})

    def test_pattern_configuration_update_uses_current_version(self) -> None:
        current = snapshot()
        current.config["organization"]["secret_scanning"] = {
            "pattern_configurations": {
                "_pattern_config_version": "current-version",
                "provider_pattern_settings": [
                    {
                        "token_type": "TOKEN",
                        "push_protection_setting": "disabled",
                    }
                ],
            }
        }
        desired = {
            "version": 1,
            "organization": {
                "secret_scanning": {
                    "pattern_configurations": {
                        "provider_pattern_settings": [
                            {
                                "token_type": "TOKEN",
                                "push_protection_setting": "enabled",
                            }
                        ]
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            operations[0].body,
            {
                "pattern_config_version": "current-version",
                "provider_pattern_settings": [
                    {
                        "token_type": "TOKEN",
                        "push_protection_setting": "enabled",
                    }
                ],
            },
        )

    def test_team_name_change_converges_after_github_changes_the_slug(self) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {"old-team": {"settings": {"name": "Old Team", "parent": None}}},
        }
        current.ids[("teams", "old-team")] = 7
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "old-team": {"settings": {"name": "New Team", "parent": None}}
                    },
                }
            },
        }
        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(first_plan[0].endpoint, "/orgs/acme/teams/old-team")

        current_after = snapshot()
        current_after.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {"new-team": {"settings": {"name": "New Team", "parent": None}}},
        }
        current_after.ids[("teams", "new-team")] = 7
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_ruleset_name_change_converges_with_the_original_logical_key(self) -> None:
        current = snapshot()
        current.config["organization"]["rulesets"] = {
            "mode": "exact",
            "items": {"Old": {"name": "Old", "enforcement": "active"}},
        }
        current.ids[("organization", "rulesets", "Old")] = 8
        desired = {
            "version": 1,
            "organization": {
                "rulesets": {
                    "mode": "exact",
                    "items": {"Old": {"name": "New", "enforcement": "active"}},
                }
            },
        }
        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(first_plan[0].endpoint, "/orgs/acme/rulesets/8")

        current_after = snapshot()
        current_after.config["organization"]["rulesets"] = {
            "mode": "exact",
            "items": {"New": {"name": "New", "enforcement": "active"}},
        }
        current_after.ids[("organization", "rulesets", "New")] = 8
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_custom_pattern_removal_cannot_change_security_alerts(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["secret_scanning"] = {
            "custom_patterns": {
                "mode": "exact",
                "items": {"Token": {"name": "Token", "pattern": "token_[a-z]+"}},
            }
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "secret_scanning": {
                            "custom_patterns": {"mode": "exact", "items": {}}
                        }
                    }
                }
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIsNone(operations[0].body)
        self.assertIn("alerts", operations[0].blocked_reason or "")

    def test_custom_pattern_requests_omit_read_only_null_values(self) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "secret_scanning": {
                            "custom_patterns": {
                                "items": {
                                    "Token": {
                                        "name": "Token",
                                        "pattern": "token_[0-9]+",
                                        "start_delimiter": None,
                                        "end_delimiter": None,
                                        "must_match": None,
                                        "must_not_match": None,
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }

        create_operations = Planner(FakeApi(), snapshot(), "acme").plan(desired)

        self.assertEqual(
            create_operations[0].body,
            {"patterns": [{"name": "Token", "pattern": "token_[0-9]+"}]},
        )

        current = snapshot()
        current.config["repositories"]["items"]["api"]["secret_scanning"] = {
            "custom_patterns": {
                "mode": "exact",
                "items": {
                    "Token": {
                        "name": "Token",
                        "pattern": "token_[a-z]+",
                        "start_delimiter": None,
                        "end_delimiter": None,
                        "must_match": None,
                        "must_not_match": None,
                    }
                },
            }
        }
        current.ids.update(
            {
                ("repositories", "api", "custom_patterns", "Token"): 8,
                ("repositories", "api", "custom_pattern_versions", "Token"): "v1",
            }
        )

        update_operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            update_operations[0].body,
            {"pattern": "token_[0-9]+", "custom_pattern_version": "v1"},
        )

    def test_custom_pattern_null_update_is_blocked(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["secret_scanning"] = {
            "custom_patterns": {
                "mode": "exact",
                "items": {
                    "Token": {
                        "name": "Token",
                        "pattern": "token_[0-9]+",
                        "must_match": ["prod"],
                    }
                },
            }
        }
        current.ids.update(
            {
                ("repositories", "api", "custom_patterns", "Token"): 8,
                ("repositories", "api", "custom_pattern_versions", "Token"): "v1",
            }
        )
        desired = copy.deepcopy(current.config)
        desired["repositories"]["items"]["api"]["secret_scanning"]["custom_patterns"][
            "items"
        ]["Token"]["must_match"] = None

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn("does not accept null", operations[0].blocked_reason or "")

    def test_unreadable_assignment_fields_produce_blocked_diffs(self) -> None:
        current = snapshot()
        current.config["organization"].update(
            {
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {"Default": {"name": "Default"}},
                    }
                },
                "copilot": {},
                "actions": {
                    "runner_groups": {
                        "mode": "exact",
                        "items": {"linux": {"settings": {"name": "linux"}}},
                    }
                },
            }
        )
        current.ids.update(
            {
                (
                    "organization_collections",
                    "code_security.configurations",
                    "Default",
                ): 4,
                ("runner_groups", "linux"): 5,
            }
        )
        current.unavailable_collections.update(
            {
                (
                    "organization",
                    "code_security",
                    "configurations",
                    "items",
                    "Default",
                    "repositories",
                ): "/orgs/acme/code-security/configurations/4/repositories",
                (
                    "organization",
                    "copilot",
                    "seats",
                ): "/orgs/acme/copilot/billing/seats",
                (
                    "organization",
                    "actions",
                    "runner_groups",
                    "items",
                    "linux",
                    "repositories",
                ): "/orgs/acme/actions/runner-groups/5/repositories",
            }
        )
        desired = {
            "version": 1,
            "organization": {
                "code_security": {
                    "configurations": {
                        "items": {"Default": {"name": "Default", "repositories": []}}
                    }
                },
                "copilot": {"seats": {"users": [], "teams": []}},
                "actions": {
                    "runner_groups": {
                        "items": {
                            "linux": {
                                "settings": {"name": "linux"},
                                "repositories": [],
                            }
                        }
                    }
                },
            },
        }
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 3)
        self.assertTrue(all(operation.blocked_reason for operation in operations))

    def test_workflow_observation_states_round_trip_without_unsupported_writes(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["workflow_states"] = {
            "mode": "exact",
            "items": {
                ".github/workflows/fork.yaml": "disabled_fork",
                ".github/workflows/removed.yaml": "deleted",
            },
        }
        desired: dict[str, Any] = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "workflow_states": copy.deepcopy(
                            current.config["repositories"]["items"]["api"][
                                "workflow_states"
                            ]
                        )
                    }
                }
            },
        }
        self.assertEqual(Planner(FakeApi(), current, "acme").plan(desired), [])
        desired["repositories"]["items"]["api"]["workflow_states"]["items"][
            ".github/workflows/fork.yaml"
        ] = "deleted"
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("does not provide", operations[0].blocked_reason or "")

        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "workflow_states": copy.deepcopy(
                            current.config["repositories"]["items"]["api"][
                                "workflow_states"
                            ]
                        )
                    }
                }
            },
        }
        desired["repositories"]["items"]["api"]["workflow_states"]["items"][
            ".github/workflows/removed.yaml"
        ] = "active"
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(operations), 1)
        self.assertIn("repository file", operations[0].blocked_reason or "")

    def test_repository_name_change_converges_and_routes_child_writes(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["name"] = "api"
        current.config["repositories"]["items"]["api"]["labels"] = {
            "mode": "exact",
            "items": {"bug": {"name": "bug", "color": "ff0000"}},
        }
        current.config["organization"].update(
            {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "platform": {
                            "settings": {"name": "Platform", "parent": None},
                            "repositories": {
                                "mode": "exact",
                                "items": {"api": "push"},
                            },
                        }
                    },
                },
                "dependabot": {"repository_access": {"repositories": ["api"]}},
            }
        )
        current.ids[("teams", "platform")] = 12
        desired: dict[str, Any] = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "platform": {
                            "settings": {"name": "Platform", "parent": None},
                            "repositories": {
                                "mode": "exact",
                                "items": {"service": "push"},
                            },
                        }
                    },
                },
                "dependabot": {"repository_access": {"repositories": ["service"]}},
            },
            "repositories": {
                "items": {
                    "api": {
                        "settings": {"name": "service"},
                        "labels": {
                            "items": {"bug": {"name": "bug", "color": "00ff00"}}
                        },
                    }
                }
            },
        }
        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(first_plan[0].endpoint, "/repos/acme/api")
        self.assertEqual(first_plan[0].body, {"name": "service"})
        self.assertEqual(first_plan[1].endpoint, "/repos/acme/service/labels/bug")

        current_after = snapshot()
        repository = current_after.config["repositories"]["items"].pop("api")
        repository["settings"]["name"] = "service"
        repository["labels"] = copy.deepcopy(
            desired["repositories"]["items"]["api"]["labels"]
        )
        current_after.config["repositories"]["items"]["service"] = repository
        current_after.ids = {("repositories", "service"): 10}
        current_after.config["organization"] = copy.deepcopy(desired["organization"])
        current_after.ids[("teams", "platform")] = 12
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_archived_repository_rename_requires_explicit_unarchive(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"name": "api", "archived": True}
        )
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": {"name": "service"}}}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot change while the repository is archived; set "
            "repositories.items.api.settings.archived to false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_archived_repository_topic_change_requires_explicit_unarchive(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["archived"] = True
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"topics": ["replacement"]}}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot change while the repository is archived; set "
            "repositories.items.api.settings.archived to false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_archived_repository_is_unarchived_before_topic_change(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].pop("archived")
        current.config["repositories"]["items"]["api"]["_facts"] = {
            "archived": True,
            "visibility": "private",
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {"archived": False},
                        "topics": ["replacement"],
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [(operation.phase, operation.body) for operation in operations],
            [(1, {"archived": False}), (20, {"names": ["replacement"]})],
        )

    def test_secret_scanning_can_be_enabled_on_an_archived_repository(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {
                "archived": True,
                "security_and_analysis": {"secret_scanning": {"status": "disabled"}},
            }
        )
        desired_settings = copy.deepcopy(
            current.config["repositories"]["items"]["api"]["settings"]
        )
        desired_settings["security_and_analysis"]["secret_scanning"]["status"] = (
            "enabled"
        )
        desired = {
            "version": 1,
            "repositories": {"items": {"api": {"settings": desired_settings}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [operation.body for operation in operations],
            [{"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}],
        )

    def test_archived_repository_team_access_requires_explicit_unarchive(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["archived"] = True
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {
                    "settings": {"name": "Platform"},
                    "repositories": {"items": {"api": "pull"}},
                }
            }
        }
        current.ids[("teams", "platform")] = 12
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {"platform": {"repositories": {"items": {"api": "push"}}}}
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot change while the repository is archived; set "
            "repositories.items.api.settings.archived to false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_archived_repository_is_unarchived_before_team_access_changes(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"]["archived"] = True
        current.config["organization"]["teams"] = {
            "items": {
                "platform": {
                    "settings": {"name": "Platform"},
                    "repositories": {"items": {"api": "pull"}},
                }
            }
        }
        current.ids[("teams", "platform")] = 12
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {"platform": {"repositories": {"items": {"api": "push"}}}}
                }
            },
            "repositories": {"items": {"api": {"settings": {"archived": False}}}},
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(
            [
                (operation.phase, operation.method, operation.endpoint, operation.body)
                for operation in operations
            ],
            [
                (1, "PATCH", "/repos/acme/api", {"archived": False}),
                (
                    20,
                    "PUT",
                    "/orgs/acme/teams/platform/repos/acme/api",
                    {"permission": "push"},
                ),
            ],
        )

    def test_archived_repository_selection_requires_explicit_unarchive(self) -> None:
        current = snapshot()
        current.config["organization"]["actions"]["selected_repositories"] = ["old"]
        desired = {
            "version": 1,
            "organization": {"actions": {"selected_repositories": []}},
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot change while the repository is archived; set "
            "repositories.items.old.settings.archived to false",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_archived_repository_is_unarchived_before_rename(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["settings"].update(
            {"name": "api", "archived": True}
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {"api": {"settings": {"name": "service", "archived": False}}}
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            [(operation.endpoint, operation.body) for operation in operations],
            [
                ("/repos/acme/api", {"archived": False}),
                ("/repos/acme/api", {"name": "service"}),
            ],
        )

    def test_code_security_name_change_keeps_current_assignments(self) -> None:
        current = snapshot()
        current.config["organization"]["code_security"] = {
            "configurations": {
                "mode": "exact",
                "items": {
                    "Old": {"name": "Old", "repositories": ["api"]},
                },
            }
        }
        current.ids[
            ("organization_collections", "code_security.configurations", "Old")
        ] = 7
        desired = {
            "version": 1,
            "organization": {
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {"Old": {"name": "New", "repositories": ["api"]}},
                    }
                }
            },
        }
        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(first_plan), 1)
        self.assertNotIn("/attach", first_plan[0].endpoint)

        current_after = snapshot()
        current_after.config["organization"]["code_security"] = {
            "configurations": {
                "mode": "exact",
                "items": {
                    "New": {"name": "New", "repositories": ["api"]},
                },
            }
        }
        current_after.ids[
            ("organization_collections", "code_security.configurations", "New")
        ] = 7
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_ip_allow_list_value_change_converges_with_the_logical_key(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["ip_allow_list"] = {
            "entries": {
                "mode": "exact",
                "items": {
                    "192.0.2.1": {
                        "value": "192.0.2.1",
                        "active": True,
                    }
                },
            }
        }
        current.ids[("ip_allow_list_entries", "192.0.2.1")] = "I_office"
        desired = {
            "version": 1,
            "organization": {
                "ip_allow_list": {
                    "entries": {
                        "mode": "exact",
                        "items": {
                            "192.0.2.1": {
                                "value": "192.0.2.2",
                                "active": True,
                            }
                        },
                    }
                }
            },
        }

        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(first_plan), 1)
        self.assertTrue(
            first_plan[0].endpoint.endswith("UpdateIpAllowListEntryConfiguration")
        )

        current_after = snapshot()
        current_after.config["organization"]["ip_allow_list"] = {
            "entries": {
                "mode": "exact",
                "items": {
                    "192.0.2.2": {
                        "value": "192.0.2.2",
                        "active": True,
                    }
                },
            }
        }
        current_after.ids[("ip_allow_list_entries", "192.0.2.2")] = "I_office"
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_ip_allow_list_value_change_rejects_identity_collisions(self) -> None:
        current = snapshot()
        current.config["organization"]["ip_allow_list"] = {
            "entries": {
                "mode": "exact",
                "items": {
                    "office": {"value": "192.0.2.1", "active": True},
                    "vpn": {"value": "192.0.2.2", "active": True},
                },
            }
        }
        desired = {
            "version": 1,
            "organization": {
                "ip_allow_list": {
                    "entries": {
                        "mode": "exact",
                        "items": {
                            "office": {
                                "value": "192.0.2.2",
                                "active": True,
                            }
                        },
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "configured identity to one already used",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_ip_allow_list_aliases_cannot_match_the_same_entry(self) -> None:
        current = snapshot()
        current.config["organization"]["ip_allow_list"] = {
            "entries": {
                "mode": "exact",
                "items": {
                    "office": {"value": "192.0.2.1", "active": True},
                },
            }
        }
        desired = {
            "version": 1,
            "organization": {
                "ip_allow_list": {
                    "entries": {
                        "items": {
                            "first": {
                                "value": "192.0.2.1",
                                "active": True,
                            },
                            "second": {
                                "value": "192.0.2.1",
                                "active": True,
                            },
                        }
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "both identify current IP allow-list entry",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

    def test_custom_role_name_change_converges_with_the_logical_key(self) -> None:
        current = snapshot()
        current.config["organization"]["custom_repository_roles"] = {
            "mode": "exact",
            "items": {
                "Release manager": {
                    "name": "Release manager",
                    "base_role": "write",
                    "permissions": ["delete_alerts"],
                }
            },
        }
        current.ids[("custom_repository_roles", "Release manager")] = 7
        desired = {
            "version": 1,
            "organization": {
                "custom_repository_roles": {
                    "mode": "exact",
                    "items": {
                        "Release manager": {
                            "name": "Deployment manager",
                        }
                    },
                }
            },
        }

        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(first_plan), 1)
        self.assertEqual(first_plan[0].method, "PATCH")

        current_after = snapshot()
        current_after.config["organization"]["custom_repository_roles"] = {
            "mode": "exact",
            "items": {
                "Deployment manager": {
                    "name": "Deployment manager",
                    "base_role": "write",
                    "permissions": ["delete_alerts"],
                }
            },
        }
        current_after.ids[("custom_repository_roles", "Deployment manager")] = 7
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_branch_rule_pattern_change_converges_with_the_logical_key(self) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protection_rules"] = {
            "mode": "exact",
            "items": {"release/*": {"pattern": "release/*"}},
        }
        current.ids[("repositories", "api", "branch_protection_rules", "release/*")] = (
            "B_release"
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protection_rules": {
                            "mode": "exact",
                            "items": {
                                "release/*": {
                                    "pattern": "stable/*",
                                }
                            },
                        }
                    }
                }
            },
        }

        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(first_plan), 1)
        self.assertTrue(
            first_plan[0].endpoint.endswith("UpdateBranchProtectionRuleConfiguration")
        )

        current_after = snapshot()
        current_after.config["repositories"]["items"]["api"][
            "branch_protection_rules"
        ] = {
            "mode": "exact",
            "items": {"stable/*": {"pattern": "stable/*"}},
        }
        current_after.ids[
            ("repositories", "api", "branch_protection_rules", "stable/*")
        ] = "B_release"
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_branch_rule_update_preserves_an_ambiguous_status_check_app(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protection_rules"] = {
            "mode": "exact",
            "items": {
                "main": {
                    "pattern": "main",
                    "allows_deletions": False,
                    "required_status_checks": [
                        {"context": "test", "app": None},
                    ],
                }
            },
        }
        current.ids[("repositories", "api", "branch_protection_rules", "main")] = (
            "B_main"
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protection_rules": {
                            "items": {
                                "main": {
                                    "pattern": "main",
                                    "allows_deletions": True,
                                    "required_status_checks": [
                                        {"context": "test", "app": None},
                                    ],
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertNotIn("requiredStatusChecks", mapping_body(operations[0])["input"])

    def test_changed_branch_status_checks_require_an_explicit_app_state(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protection_rules"] = {
            "items": {
                "main": {
                    "pattern": "main",
                    "required_status_checks": [
                        {"context": "test", "app": None},
                    ],
                }
            }
        }
        current.ids[("repositories", "api", "branch_protection_rules", "main")] = (
            "B_main"
        )
        desired: dict[str, Any] = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protection_rules": {
                            "items": {
                                "main": {
                                    "required_status_checks": [
                                        {"context": "lint", "app": None},
                                    ]
                                }
                            }
                        }
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "app is ambiguous.*'any'.*'recent'",
        ):
            Planner(FakeApi(), current, "acme").plan(desired)

        desired["repositories"]["items"]["api"]["branch_protection_rules"]["items"][
            "main"
        ]["required_status_checks"][0]["app"] = "recent"
        operations = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(
            mapping_body(operations[0])["input"]["requiredStatusChecks"],
            [{"context": "lint"}],
        )

    def test_rest_branch_protection_fallback_cannot_target_canonical_state(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["branch_protection_rules"] = {
            "mode": "exact",
            "items": {"release/*": {"pattern": "release/*"}},
        }
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protections": {
                            "mode": "exact",
                            "items": {"release/1": {}},
                        }
                    }
                }
            },
        }

        for force in (False, True):
            with self.subTest(force=force):
                operations = Planner(
                    FakeApi(), copy.deepcopy(current), "acme", force=force
                ).plan(desired)
                self.assertEqual(len(operations), 1)
                self.assertIn(
                    "re-export the file",
                    operations[0].blocked_reason or "",
                )

    def test_environment_branch_policy_rename_converges_without_writing_type(
        self,
    ) -> None:
        current = snapshot()
        current.config["repositories"]["items"]["api"]["environments"] = {
            "mode": "exact",
            "items": {
                "production": {
                    "branch_policies": {
                        "mode": "exact",
                        "items": {"old": {"name": "old", "type": "branch"}},
                    }
                }
            },
        }
        current.ids[
            (
                "repositories",
                "api",
                "environments",
                "production",
                "branch_policies",
                "old",
            )
        ] = 9
        desired: dict[str, Any] = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "environments": {
                            "items": {
                                "production": {
                                    "branch_policies": {
                                        "mode": "exact",
                                        "items": {
                                            "old": {
                                                "name": "new",
                                                "type": "branch",
                                            }
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            },
        }
        first_plan = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(first_plan), 1)
        self.assertEqual(first_plan[0].body, {"name": "new"})

        replacement = copy.deepcopy(desired)
        replacement_policy = replacement["repositories"]["items"]["api"][
            "environments"
        ]["items"]["production"]["branch_policies"]["items"]["old"]
        replacement_policy.update({"name": "old", "type": "tag"})
        replacement_plan = Planner(FakeApi(), current, "acme").plan(replacement)
        self.assertEqual(
            [operation.method for operation in replacement_plan], ["DELETE", "POST"]
        )
        self.assertEqual(replacement_plan[1].body, {"name": "old", "type": "tag"})

        current_after = copy.deepcopy(current)
        branch_policies = current_after.config["repositories"]["items"]["api"][
            "environments"
        ]["items"]["production"]["branch_policies"]["items"]
        branch_policies["new"] = branch_policies.pop("old")
        branch_policies["new"]["name"] = "new"
        current_after.ids[
            (
                "repositories",
                "api",
                "environments",
                "production",
                "branch_policies",
                "new",
            )
        ] = 9
        self.assertEqual(Planner(FakeApi(), current_after, "acme").plan(desired), [])

    def test_plans_graphql_repository_settings_rules_and_environment_pins(
        self,
    ) -> None:
        current = snapshot()
        repository = current.config["repositories"]["items"]["api"]
        repository["settings"].update(
            {
                "has_discussions": False,
                "has_sponsorships": False,
                "issue_creation_policy": "all",
            }
        )
        repository["branch_protection_rules"] = {"mode": "exact", "items": {}}
        repository["environments"] = {
            "mode": "exact",
            "items": {
                "production": {
                    "settings": {},
                    "pinned": False,
                    "pinned_position": None,
                }
            },
        }
        current.ids.update(
            {
                ("repositories", "api", "node_id"): "R_api",
                (
                    "repositories",
                    "api",
                    "environments",
                    "production",
                    "node_id",
                ): "E_production",
                ("apps", "actions", "node_id"): "A_actions",
            }
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "settings": {
                            "has_discussions": True,
                            "issue_creation_policy": "collaborators_only",
                        },
                        "branch_protection_rules": {
                            "mode": "exact",
                            "items": {
                                "release/*": {
                                    "pattern": "release/*",
                                    "required_status_checks": [
                                        {"context": "test", "app": "actions"},
                                        {"context": "lint", "app": "any"},
                                    ],
                                }
                            },
                        },
                        "environments": {
                            "items": {
                                "production": {
                                    "pinned": True,
                                    "pinned_position": 0,
                                }
                            }
                        },
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        operation_names = {
            operation.endpoint.split("#")[-1]: operation for operation in operations
        }
        self.assertEqual(
            mapping_body(operation_names["UpdateRepositoryConfiguration"])["input"],
            {
                "repositoryId": None,
                "hasDiscussionsEnabled": True,
                "issueCreationPolicy": "COLLABORATORS_ONLY",
            },
        )
        branch = operation_names["CreateBranchProtectionRuleConfiguration"]
        self.assertEqual(
            mapping_body(branch)["input"]["requiredStatusChecks"],
            [
                {"context": "test", "appId": None},
                {"context": "lint", "appId": "any"},
            ],
        )
        self.assertIn("PinEnvironmentConfiguration", operation_names)
        self.assertIn("ReorderEnvironmentConfiguration", operation_names)

    def test_resolves_graphql_branch_rule_actor_node_ids_from_rest(self) -> None:
        current = snapshot()
        repository = current.config["repositories"]["items"]["api"]
        repository["branch_protection_rules"] = {"mode": "exact", "items": {}}
        current.ids[("repositories", "api", "node_id")] = "R_api"
        api = FakeApi(
            responses={
                ("GET", "/users/alice"): {"node_id": "U_alice"},
                ("GET", "/orgs/acme/teams/platform"): {"node_id": "T_platform"},
                ("GET", "/apps/actions"): {"node_id": "A_actions"},
            }
        )
        desired = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protection_rules": {
                            "items": {
                                "main": {
                                    "push_actors": [
                                        "user:alice",
                                        "team:platform",
                                        "app:actions",
                                    ],
                                    "required_status_checks": [
                                        {"context": "test", "app": "actions"}
                                    ],
                                }
                            }
                        }
                    }
                }
            },
        }

        operations = Planner(api, current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIsNone(operations[0].blocked_reason)
        self.assertEqual(
            current.ids[("branch_protection_actors", "user", "alice")],
            "U_alice",
        )
        self.assertEqual(
            current.ids[("branch_protection_actors", "team", "platform")],
            "T_platform",
        )
        self.assertEqual(
            current.ids[("branch_protection_actors", "app", "actions")],
            "A_actions",
        )
        self.assertEqual(current.ids[("apps", "actions", "node_id")], "A_actions")

    def test_changed_read_only_field_blocks_apply_unless_force_ignores_it(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["settings"]["two_factor_requirement_enabled"] = (
            True
        )
        current.read_only_fields[
            ("organization", "settings", "two_factor_requirement_enabled")
        ] = "GitHub does not provide a public update operation."
        desired = {
            "version": 1,
            "organization": {"settings": {"two_factor_requirement_enabled": False}},
        }

        blocked = Planner(FakeApi(), current, "acme").plan(desired)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].method, "READ-ONLY")
        self.assertIsNotNone(blocked[0].blocked_reason)

        forced_planner = Planner(FakeApi(), current, "acme", force=True)
        self.assertEqual(forced_planner.plan(desired), [])
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_static_read_only_repository_setting_blocks_when_current_is_unavailable(
        self,
    ) -> None:
        desired = {
            "version": 1,
            "repositories": {
                "items": {"api": {"settings": {"hash_algorithm": "sha256"}}}
            },
        }

        blocked = Planner(FakeApi(), snapshot(), "acme").plan(desired)
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0].method, "READ-ONLY")
        self.assertIn("does not provide", blocked[0].blocked_reason or "")

        forced_planner = Planner(FakeApi(), snapshot(), "acme", force=True)
        self.assertEqual(forced_planner.plan(desired), [])
        self.assertEqual(len(forced_planner.forced_changes), 1)

    def test_domain_state_cannot_move_backwards_without_force(self) -> None:
        current = snapshot()
        current.config["organization"]["domains"] = {
            "mode": "exact",
            "items": {"example.com": {"approved": True, "verified": True}},
        }
        current.ids[("domains", "example.com")] = "D_example"
        desired = {
            "version": 1,
            "organization": {
                "domains": {
                    "mode": "merge",
                    "items": {"example.com": {"verified": False}},
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        self.assertEqual(len(operations), 1)
        self.assertIn("provides no operation", operations[0].blocked_reason or "")

    def test_team_review_assignment_requires_force_for_unreadable_inputs(
        self,
    ) -> None:
        current = snapshot()
        current.config["organization"]["teams"] = {
            "mode": "exact",
            "items": {
                "platform": {
                    "settings": {
                        "name": "Platform",
                        "privacy": "closed",
                        "permission": "pull",
                        "parent": None,
                    },
                    "review_assignment": {
                        "enabled": False,
                        "algorithm": "round_robin",
                        "member_count": 1,
                        "notify_team": True,
                    },
                }
            },
        }
        current.ids.update(
            {
                ("teams", "platform"): 7,
                ("teams", "platform", "node_id"): "T_platform",
            }
        )
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {"platform": {"review_assignment": {"enabled": True}}}
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        review = next(
            operation
            for operation in operations
            if operation.endpoint.endswith("UpdateTeamReviewAssignmentConfiguration")
        )
        self.assertIn("does not return four", review.warning_reason or "")
        self.assertIn("--force", review.blocked_reason or "")

        forced = Planner(FakeApi(), current, "acme", force=True).plan(desired)
        forced_review = next(
            operation
            for operation in forced
            if operation.endpoint.endswith("UpdateTeamReviewAssignmentConfiguration")
        )
        self.assertIsNone(forced_review.blocked_reason)
        self.assertIn("resets excluded members", forced_review.warning_reason or "")

    def test_new_team_review_assignment_uses_defaults_without_force(self) -> None:
        current = snapshot()
        desired = {
            "version": 1,
            "organization": {
                "teams": {
                    "items": {
                        "platform": {
                            "settings": {"name": "Platform"},
                            "review_assignment": {"enabled": True},
                        }
                    }
                }
            },
        }

        operations = Planner(FakeApi(), current, "acme").plan(desired)

        review = next(
            operation
            for operation in operations
            if operation.endpoint.endswith("UpdateTeamReviewAssignmentConfiguration")
        )
        self.assertIsNone(review.blocked_reason)
        self.assertIn("new team uses", review.warning_reason or "")


if __name__ == "__main__":
    unittest.main()
