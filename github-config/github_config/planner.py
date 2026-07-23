from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Any

from .api import ApiClient, ApiError, quote
from .config import (
    ConfigError,
    collection_items,
    collection_mode,
    deep_merge,
    desired_repositories,
    validate_config_semantics,
    validate_config_types,
    validate_repository_selector,
    validate_repository_settings_semantics,
)
from .exporter import Snapshot
from .graphql import (
    ADD_DOMAIN_MUTATION,
    APPROVE_DOMAIN_MUTATION,
    CREATE_BRANCH_PROTECTION_RULE_MUTATION,
    CREATE_CUSTOM_PROPERTY_MUTATION,
    CREATE_IP_ALLOW_LIST_ENTRY_MUTATION,
    DELETE_BRANCH_PROTECTION_RULE_MUTATION,
    DELETE_DOMAIN_MUTATION,
    DELETE_IP_ALLOW_LIST_ENTRY_MUTATION,
    PIN_ENVIRONMENT_MUTATION,
    REORDER_ENVIRONMENT_MUTATION,
    UPDATE_BRANCH_PROTECTION_RULE_MUTATION,
    UPDATE_CUSTOM_PROPERTY_MUTATION,
    UPDATE_IP_ALLOW_LIST_ENABLED_MUTATION,
    UPDATE_IP_ALLOW_LIST_ENTRY_MUTATION,
    UPDATE_IP_ALLOW_LIST_FOR_APPS_MUTATION,
    UPDATE_NOTIFICATION_RESTRICTION_MUTATION,
    UPDATE_REPOSITORY_MUTATION,
    UPDATE_TEAM_REVIEW_ASSIGNMENT_MUTATION,
    VERIFY_DOMAIN_MUTATION,
)
from .operations import FieldChange, Operation, preflight_operations, sort_operations
from .specs import (
    APP_INSTALLATION_READ_ONLY_FIELDS,
    BUDGET_FIELDS,
    ORGANIZATION_COLLECTIONS,
    ORGANIZATION_READ_ONLY_SETTINGS_FIELDS,
    ORGANIZATION_REPOSITORY_SETS,
    ORGANIZATION_SETTINGS_FIELDS,
    ORGANIZATION_SINGLETONS,
    PRIVATE_REGISTRY_FIELDS,
    REPOSITORY_READ_ONLY_SETTINGS_FIELDS,
    REPOSITORY_SETTINGS_FIELDS,
    REPOSITORY_SINGLETONS,
    SECURITY_TOGGLES,
    SingletonSpec,
    organization_collection_create_body,
    organization_collection_request_body,
    without_organization_collection_response_metadata,
    without_organization_collection_response_only_nulls,
    without_organization_response_only_nulls,
    without_repository_response_metadata,
    without_repository_response_only_nulls,
    without_singleton_response_only_values,
)
from .util import get_path, pick

CREATE_REPOSITORY_FIELDS = (
    "allow_auto_merge",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_squash_merge",
    "delete_branch_on_merge",
    "description",
    "has_issues",
    "has_projects",
    "has_wiki",
    "homepage",
    "is_template",
    "merge_commit_message",
    "merge_commit_title",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
    "use_squash_pr_title_as_default",
    "visibility",
)

TEAM_CREATE_PERMISSIONS = ("pull", "push")
TEAM_UPDATE_PERMISSIONS = (*TEAM_CREATE_PERMISSIONS, "admin")
_UNSET = object()

BRANCH_PROTECTION_TEAM_PATHS = (
    ("restrictions", "teams"),
    (
        "required_pull_request_reviews",
        "dismissal_restrictions",
        "teams",
    ),
    (
        "required_pull_request_reviews",
        "bypass_pull_request_allowances",
        "teams",
    ),
)


REPOSITORY_KEYS = {
    "settings",
    "topics",
    "_facts",
    "collaborators",
    "collaborator_invitations",
    "rulesets",
    "hooks",
    "deploy_keys",
    "autolinks",
    "labels",
    "branch_protections",
    "branch_protection_rules",
    "environments",
    "actions",
    "agents",
    "codespaces",
    "dependabot",
    "custom_properties",
    "security",
    "pages",
    "workflow_states",
    "interaction_limit",
    "pull_request_creation_cap",
    "pull_request_creation_cap_bypass_users",
    "code_scanning",
    "code_quality",
    "secret_scanning",
    "discussion_categories",
    "social_preview",
}


_GRAPHQL_REPOSITORY_SETTINGS = {
    "has_discussions": "hasDiscussionsEnabled",
    "has_sponsorships": "hasSponsorshipsEnabled",
    "issue_creation_policy": "issueCreationPolicy",
}
_BRANCH_PROTECTION_RULE_FIELDS = {
    "pattern": "pattern",
    "allows_deletions": "allowsDeletions",
    "allows_force_pushes": "allowsForcePushes",
    "blocks_creations": "blocksCreations",
    "dismisses_stale_reviews": "dismissesStaleReviews",
    "is_admin_enforced": "isAdminEnforced",
    "lock_allows_fetch_and_merge": "lockAllowsFetchAndMerge",
    "lock_branch": "lockBranch",
    "require_last_push_approval": "requireLastPushApproval",
    "required_approving_review_count": "requiredApprovingReviewCount",
    "required_deployment_environments": "requiredDeploymentEnvironments",
    "requires_approving_reviews": "requiresApprovingReviews",
    "requires_code_owner_reviews": "requiresCodeOwnerReviews",
    "requires_commit_signatures": "requiresCommitSignatures",
    "requires_conversation_resolution": "requiresConversationResolution",
    "requires_deployments": "requiresDeployments",
    "requires_linear_history": "requiresLinearHistory",
    "requires_status_checks": "requiresStatusChecks",
    "requires_strict_status_checks": "requiresStrictStatusChecks",
    "restricts_pushes": "restrictsPushes",
    "restricts_review_dismissals": "restrictsReviewDismissals",
}
_BRANCH_PROTECTION_ACTOR_INPUTS = {
    "bypass_force_push_actors": "bypassForcePushActorIds",
    "bypass_pull_request_actors": "bypassPullRequestActorIds",
    "push_actors": "pushActorIds",
    "review_dismissal_actors": "reviewDismissalActorIds",
}


REPOSITORY_SECTION_KEYS = (
    (
        "actions",
        {
            "permissions",
            "allowed_actions",
            "workflow_permissions",
            "access",
            "artifact_and_log_retention",
            "fork_pull_request_approval",
            "private_fork_pull_request_workflows",
            "oidc_subject",
            "cache_retention",
            "cache_storage",
            "self_hosted_runners",
            "variables",
            "secrets",
        },
    ),
    ("agents", {"variables", "secrets", "cloud_configuration"}),
    ("codespaces", {"secrets"}),
    ("dependabot", {"secrets"}),
    ("code_scanning", {"default_setup"}),
    ("code_quality", {"setup"}),
    ("secret_scanning", {"custom_patterns"}),
)


class Planner:
    def __init__(
        self,
        api: ApiClient,
        current: Snapshot,
        org: str,
        *,
        force: bool = False,
    ) -> None:
        self.api = api
        self.current = current
        self.org = org
        self.operations: list[Operation] = []
        self.organization_collection_matches: dict[tuple[str, str], str] = {}
        self.repository_api_names: dict[str, str] = {}
        self.current_organization: Mapping[str, Any] = {}
        self.team_current_to_logical: dict[str, str] = {}
        self.team_logical_to_current: dict[str, str] = {}
        self.team_slug_references: dict[str, tuple[tuple[str, ...], int]] = {}
        self.team_id_ready_phases: dict[str, int] = {}
        self.current_organization_allows_forking: bool | None = None
        self.organization_allows_forking: bool | None = None
        self.organization_allows_projects: bool | None = None
        self.force = force
        self.forced_changes: list[FieldChange] = []

    def plan(self, desired: Mapping[str, Any]) -> list[Operation]:
        validate_config_types(desired)
        validate_config_semantics(desired)
        self.operations = []
        self.organization_collection_matches = {}
        self.repository_api_names = {}
        self.team_current_to_logical = {}
        self.team_logical_to_current = {}
        self.team_slug_references = {}
        self.team_id_ready_phases = {}
        self.forced_changes = []
        organization = desired.get("organization", {})
        for index, policy in enumerate(desired.get("repository_policies", [])):
            if not isinstance(policy, dict) or not isinstance(policy.get("set"), dict):
                raise ConfigError(f"repository_policies[{index}].set must be a mapping")
            _validate_repository_shape(
                policy["set"], f"repository_policies[{index}].set"
            )
            selector = policy.get("match", {})
            if not isinstance(selector, dict):
                raise ConfigError(
                    f"repository_policies[{index}].match must be a mapping"
                )
            validate_repository_selector(selector, index)
        current_repositories = collection_items(
            self.current.config.get("repositories", {"items": {}}),
            "current.repositories",
        )
        repositories = desired_repositories(desired, current_repositories)
        matched_repositories: dict[str, str] = {}
        target_names: dict[str, str] = {}
        repository_plans: list[
            tuple[
                str,
                Mapping[str, Any],
                Mapping[str, Any] | None,
                str | None,
            ]
        ] = []
        for name, repository in repositories.items():
            if not isinstance(repository, Mapping):
                raise ConfigError(f"repositories.items.{name} must be a mapping")
            if {
                "branch_protections",
                "branch_protection_rules",
            }.issubset(repository):
                raise ConfigError(
                    f"repositories.items.{name} cannot contain both "
                    "branch_protections and branch_protection_rules; they "
                    "configure the same GitHub branch protection state"
                )
            settings = repository.get("settings", {})
            if not isinstance(settings, Mapping):
                raise ConfigError(
                    f"repositories.items.{name}.settings must be a mapping"
                )
            configured_name = settings.get("name")
            if configured_name is not None and (
                not isinstance(configured_name, str) or not configured_name.strip()
            ):
                raise ConfigError(
                    f"repositories.items.{name}.settings.name must be a non-empty string"
                )
            current_name = _match_current_key(
                name,
                configured_name,
                current_repositories,
                lambda value: (
                    get_path(value, "settings.name")
                    if isinstance(value, Mapping)
                    else None
                ),
                f"repositories.items.{name}",
            )
            if current_name is not None:
                previous = matched_repositories.get(current_name)
                if previous is not None:
                    raise ConfigError(
                        f"repositories.items.{previous} and repositories.items.{name} "
                        f"both identify current repository {current_name!r}"
                    )
                matched_repositories[current_name] = name
            target_name = configured_name or current_name or name
            previous_target = target_names.get(target_name.casefold())
            if previous_target is not None:
                raise ConfigError(
                    f"repositories.items.{previous_target} and repositories.items.{name} "
                    f"both configure repository name {target_name!r}"
                )
            target_names[target_name.casefold()] = name
            if current_name is None and target_name != name:
                raise ConfigError(
                    f"repositories.items.{name}.settings.name must match the collection "
                    "key when creating a repository"
                )
            if (
                current_name is not None
                and target_name != current_name
                and target_name in current_repositories
            ):
                raise ConfigError(
                    f"repositories.items.{name}.settings.name refers to existing "
                    f"repository {target_name!r}"
                )
            self._alias_repository_state(current_name, name, target_name)
            repository_plans.append(
                (
                    name,
                    repository,
                    current_repositories.get(current_name) if current_name else None,
                    current_name,
                )
            )
        self._register_static_read_only_fields(organization, repositories)
        current_organization = self.current.config.get("organization", {})
        self.current_organization = _normalize_organization_repository_references(
            current_organization if isinstance(current_organization, Mapping) else {},
            {
                current_name: self.repository_api_names[logical_name]
                for current_name, logical_name in matched_repositories.items()
                if current_name != self.repository_api_names[logical_name]
            },
        )
        current_organization_settings = self.current_organization.get("settings")
        desired_organization_settings = (
            organization.get("settings") if isinstance(organization, Mapping) else None
        )
        self.current_organization_allows_forking = _effective_organization_boolean(
            current_organization_settings,
            None,
            "members_can_fork_private_repositories",
        )
        self.organization_allows_forking = _effective_organization_boolean(
            current_organization_settings,
            desired_organization_settings,
            "members_can_fork_private_repositories",
        )
        self.organization_allows_projects = _effective_organization_boolean(
            current_organization_settings,
            desired_organization_settings,
            "has_repository_projects",
        )
        if organization:
            self._plan_organization(organization)
        for (
            name,
            repository_settings,
            current_repository,
            current_name,
        ) in repository_plans:
            self._plan_repository(
                name,
                repository_settings,
                current_repository,
                current_name=current_name,
            )
        self._plan_read_only_fields(
            {
                "organization": organization,
                "repositories": {"items": repositories},
            }
        )
        self._validate_archived_repository_operations(current_repositories)
        operations = sort_operations(self.operations)
        _block_operations_with_unavailable_state(operations, self.current.unavailable)
        _block_cross_organization_duplicate_identities(
            operations, desired.get("_observed"), self.org
        )
        _add_unavailable_collection_guards(
            operations,
            organization,
            repositories,
            self.current.unavailable_collections,
        )
        operations = _handle_read_only_item_operations(
            operations,
            self.current.read_only_items,
            self.team_current_to_logical,
            force=self.force,
            forced_changes=self.forced_changes,
        )
        operations = sort_operations(operations)
        preflight_operations(operations, self.current.ids)
        return operations

    def _alias_repository_state(
        self, current_name: str | None, logical_name: str, target_name: str
    ) -> None:
        self.repository_api_names[logical_name] = target_name
        if current_name is None:
            return
        for key, value in list(self.current.ids.items()):
            if len(key) >= 2 and key[:2] == ("repositories", current_name):
                self.current.ids.setdefault(
                    ("repositories", logical_name, *key[2:]), value
                )
                self.current.ids.setdefault(
                    ("repositories", target_name, *key[2:]), value
                )
        current_prefix = ("repositories", "items", current_name)
        for path, endpoint in list(self.current.unavailable_collections.items()):
            if path[:3] == current_prefix:
                self.current.unavailable_collections.setdefault(
                    ("repositories", "items", logical_name, *path[3:]), endpoint
                )
        current_base = f"/repos/{quote(self.org)}/{quote(current_name)}"
        target_base = f"/repos/{quote(self.org)}/{quote(target_name)}"
        for entry in list(self.current.unavailable):
            if entry == current_base or entry.startswith(f"{current_base}/"):
                self.current.unavailable.append(
                    f"{target_base}{entry[len(current_base) :]}"
                )
        for item_path, reason in list(self.current.read_only_items.items()):
            if (
                len(item_path) == 5
                and item_path[:2] == ("organization", "teams")
                and item_path[3] == "repositories"
                and item_path[4].casefold() == current_name.casefold()
            ):
                for alias in (logical_name, target_name):
                    self.current.read_only_items.setdefault(
                        (*item_path[:4], alias), reason
                    )
        for field_path, reason in list(self.current.read_only_fields.items()):
            if len(field_path) >= 3 and field_path[:3] == (
                "repositories",
                "items",
                current_name,
            ):
                for alias in (logical_name, target_name):
                    self.current.read_only_fields.setdefault(
                        ("repositories", "items", alias, *field_path[3:]), reason
                    )

    def _repository_api_name(self, logical_name: str) -> str:
        return self.repository_api_names.get(logical_name, logical_name)

    def _plan_read_only_fields(self, desired: Mapping[str, Any]) -> None:
        for path, reason in self.current.read_only_fields.items():
            if path[:2] in {
                ("organization", "app_installations"),
                ("organization", "credential_authorizations"),
                ("organization", "personal_access_tokens"),
            }:
                continue
            configured = _mapping_path_with_missing(desired, path)
            if configured is _UNSET:
                continue
            observed = _mapping_path_with_missing(self.current.config, path)
            if observed is not _UNSET and configured == observed:
                continue
            display_path = ".".join(str(part) for part in path if part != "items")
            change = FieldChange(
                display_path,
                "<current value unavailable>" if observed is _UNSET else observed,
                configured,
            )
            if self.force:
                self.forced_changes.append(change)
                continue
            self.operations.append(
                Operation(
                    "READ-ONLY",
                    "",
                    [change],
                    blocked_reason=reason,
                )
            )

    def _register_static_read_only_fields(
        self,
        organization: Mapping[str, Any],
        repositories: Mapping[str, Any],
    ) -> None:
        organization_reason = (
            "GitHub exposes this organization setting for inspection but does not "
            "provide a public API that changes it."
        )
        for field_name in ORGANIZATION_READ_ONLY_SETTINGS_FIELDS:
            self.current.read_only_fields.setdefault(
                ("organization", "settings", field_name),
                organization_reason,
            )
        for path, reason in (
            (
                ("organization", "saml_identity_provider"),
                (
                    "GitHub exposes the SAML identity provider for inspection but "
                    "does not provide a public organization settings API that "
                    "changes it."
                ),
            ),
            (
                ("organization", "pinned_items"),
                (
                    "GitHub exposes pinned organization profile items but does not "
                    "provide a public mutation that changes them."
                ),
            ),
            (
                ("organization", "copilot", "policies"),
                (
                    "GitHub exposes these Copilot policies through the billing API "
                    "but does not provide corresponding public update operations."
                ),
            ),
        ):
            self.current.read_only_fields.setdefault(path, reason)

        dynamic_read_only_fields = (
            (
                "domains",
                (
                    "required_for_policy_enforcement",
                    "verification_token",
                    "token_expires_at",
                ),
                (
                    "GitHub calculates this domain value and does not accept it as "
                    "mutation input."
                ),
            ),
            (
                "custom_properties",
                ("source_type",),
                (
                    "GitHub determines whether the custom property is defined by the "
                    "organization or inherited from its enterprise."
                ),
            ),
            (
                "custom_organization_roles",
                ("source",),
                (
                    "GitHub or the enterprise determines the source of this "
                    "organization role."
                ),
            ),
        )
        for organization_section, field_names, reason in dynamic_read_only_fields:
            section_value = organization.get(organization_section)
            if not isinstance(section_value, Mapping):
                continue
            items = section_value.get("items")
            if not isinstance(items, Mapping):
                continue
            for name, item in items.items():
                if not isinstance(name, str) or not isinstance(item, Mapping):
                    continue
                for field_name in field_names:
                    if field_name in item:
                        self.current.read_only_fields.setdefault(
                            (
                                "organization",
                                organization_section,
                                "items",
                                name,
                                field_name,
                            ),
                            reason,
                        )

        teams_value = organization.get("teams")
        team_items = (
            teams_value.get("items") if isinstance(teams_value, Mapping) else None
        )
        if isinstance(team_items, Mapping):
            for slug, team in team_items.items():
                if not isinstance(slug, str) or not isinstance(team, Mapping):
                    continue
                external_group = team.get("external_group")
                if (
                    isinstance(external_group, Mapping)
                    and "group_name" in external_group
                ):
                    self.current.read_only_fields.setdefault(
                        (
                            "organization",
                            "teams",
                            "items",
                            slug,
                            "external_group",
                            "group_name",
                        ),
                        "GitHub derives the external group name from its group ID.",
                    )
                sync_groups = team.get("team_sync_groups")
                if not isinstance(sync_groups, list):
                    continue
                for index, group in enumerate(sync_groups):
                    if not isinstance(group, Mapping):
                        continue
                    for field_name in ("status", "synced_at"):
                        if field_name in group:
                            self.current.read_only_fields.setdefault(
                                (
                                    "organization",
                                    "teams",
                                    "items",
                                    slug,
                                    "team_sync_groups",
                                    index,
                                    field_name,
                                ),
                                "GitHub reports this team synchronization status "
                                "and does not accept it in updates.",
                            )

        repository_reason = (
            "GitHub exposes this repository setting for inspection but does not "
            "provide a public API that changes it."
        )
        for repository in repositories:
            for field_name in REPOSITORY_READ_ONLY_SETTINGS_FIELDS:
                self.current.read_only_fields.setdefault(
                    (
                        "repositories",
                        "items",
                        repository,
                        "settings",
                        field_name,
                    ),
                    repository_reason,
                )
            for section, reason in (
                (
                    ("discussion_categories",),
                    (
                        "GitHub exposes discussion categories but does not provide "
                        "public operations that change them."
                    ),
                ),
                (
                    ("social_preview",),
                    (
                        "GitHub exposes social preview state but does not provide a "
                        "public operation that changes it."
                    ),
                ),
                (
                    ("agents", "cloud_configuration"),
                    (
                        "GitHub exposes Copilot cloud agent configuration but does "
                        "not provide a public operation that changes it."
                    ),
                ),
                (
                    ("_facts",),
                    (
                        "Repository facts are read-only observations used by "
                        "repository policy selectors."
                    ),
                ),
            ):
                self.current.read_only_fields.setdefault(
                    ("repositories", "items", repository, *section),
                    reason,
                )

    def _validate_archived_repository_operations(
        self, current_repositories: Mapping[str, Any]
    ) -> None:
        archived_repositories = {
            name.casefold(): name
            for name, repository in current_repositories.items()
            if _repository_is_archived(repository)
        }
        if not archived_repositories:
            return

        aliases = {
            name.casefold(): current_name
            for name, current_name in archived_repositories.items()
        }
        logical_names = {
            current_name.casefold(): current_name
            for current_name in archived_repositories.values()
        }
        for logical_name, api_name in self.repository_api_names.items():
            current_name = archived_repositories.get(logical_name.casefold())
            if current_name is None:
                current_name = archived_repositories.get(api_name.casefold())
            if current_name is None:
                continue
            aliases[logical_name.casefold()] = current_name
            aliases[api_name.casefold()] = current_name
            logical_names[current_name.casefold()] = logical_name

        unarchive_operations = [
            operation
            for operation in self.operations
            if _operation_unarchives_repository(operation)
        ]
        repositories_to_unarchive = {
            current_name
            for operation in unarchive_operations
            for repository_name in _operation_repository_names(operation)
            if (current_name := aliases.get(repository_name.casefold())) is not None
        }

        for operation in self.operations:
            if operation.blocked_reason is not None:
                continue
            affected_archived_repositories = {
                current_name
                for repository_name in _operation_repository_names(operation)
                if (current_name := aliases.get(repository_name.casefold())) is not None
                and current_name not in repositories_to_unarchive
            }
            if not affected_archived_repositories:
                continue
            if _operation_changes_code_security_assignment(operation):
                continue
            if _operation_enables_secret_scanning(operation):
                operation.body = {
                    "security_and_analysis": {"secret_scanning": {"status": "enabled"}}
                }
                continue
            current_name = min(affected_archived_repositories, key=str.casefold)
            logical_name = logical_names.get(current_name.casefold(), current_name)
            repository_path = f"repositories.items.{logical_name}"
            raise ConfigError(
                f"{repository_path} cannot change while the repository is "
                f"archived; set {repository_path}.settings.archived to false"
            )

    def _plan_organization(self, desired: Mapping[str, Any]) -> None:
        _check_keys(
            desired,
            {
                "settings",
                "members",
                "teams",
                "invitations",
                "outside_collaborators",
                "personal_access_tokens",
                "credential_authorizations",
                "organization_roles",
                "custom_repository_roles",
                "custom_organization_roles",
                "security_manager_teams",
                "announcement",
                "notification_restriction_enabled",
                "ip_allow_list",
                "domains",
                "saml_identity_provider",
                "pinned_items",
                "app_installations",
                "actions",
                "agents",
                "codespaces",
                "dependabot",
                "copilot",
                "rulesets",
                "hooks",
                "custom_properties",
                "custom_property_values",
                "immutable_releases",
                "immutable_release_repositories",
                "secret_scanning",
                "code_security",
                "hosted_compute",
                "issue_types",
                "issue_fields",
                "blocked_users",
                "interaction_limit",
                "budgets",
                "private_registries",
            },
            "organization",
        )
        for section, known in (
            (
                "actions",
                {
                    "permissions",
                    "allowed_actions",
                    "workflow_permissions",
                    "artifact_and_log_retention",
                    "fork_pull_request_approval",
                    "private_fork_pull_request_workflows",
                    "self_hosted_runner_permissions",
                    "oidc_subject",
                    "cache_retention",
                    "cache_storage",
                    "selected_repositories",
                    "self_hosted_runner_repositories",
                    "oidc_custom_properties",
                    "variables",
                    "secrets",
                    "runner_groups",
                    "self_hosted_runners",
                    "hosted_runners",
                },
            ),
            ("agents", {"variables", "secrets"}),
            ("codespaces", {"secrets"}),
            ("dependabot", {"secrets", "repository_access"}),
            (
                "copilot",
                {
                    "coding_agent_permissions",
                    "coding_agent_repositories",
                    "content_exclusion",
                    "seats",
                    "policies",
                },
            ),
            ("secret_scanning", {"pattern_configurations", "custom_patterns"}),
            ("code_security", {"configurations"}),
            ("hosted_compute", {"network_configurations"}),
        ):
            value = desired.get(section)
            if value is not None:
                if not isinstance(value, dict):
                    raise ConfigError(f"organization.{section} must be a mapping")
                _check_keys(value, known, f"organization.{section}")
        current = self.current_organization
        if "settings" in desired:
            desired_settings = desired["settings"]
            if not isinstance(desired_settings, dict):
                raise ConfigError("organization.settings must be a mapping")
            current_settings = current.get("settings", {})
            if not isinstance(current_settings, Mapping):
                current_settings = {}
            normalized_current_settings = without_organization_response_only_nulls(
                current_settings
            )
            normalized_desired_settings = without_organization_response_only_nulls(
                desired_settings
            )
            normalized_desired_settings = {
                key: value
                for key, value in normalized_desired_settings.items()
                if key not in ORGANIZATION_READ_ONLY_SETTINGS_FIELDS
            }
            prerequisite_keys = {
                "members_can_fork_private_repositories",
                "has_repository_projects",
            }
            early_settings = {
                key: value
                for key, value in normalized_desired_settings.items()
                if key in prerequisite_keys and value is True
            }
            remaining_settings = {
                key: value
                for key, value in normalized_desired_settings.items()
                if key not in early_settings
            }
            for settings, phase in ((early_settings, 0), (remaining_settings, 30)):
                if settings:
                    self._plan_mapping_endpoint(
                        "organization.settings",
                        normalized_current_settings,
                        settings,
                        "PATCH",
                        f"/orgs/{quote(self.org)}",
                        fields=ORGANIZATION_SETTINGS_FIELDS,
                        full_update=False,
                        phase=phase,
                    )
        self._plan_singletons(desired, current, ORGANIZATION_SINGLETONS, org=self.org)
        self._plan_repository_sets(desired, current)
        self._plan_oidc_property_inclusions(desired, current)
        self._plan_dependabot_access(desired, current)
        self._plan_organization_collections(desired, current)
        self._plan_code_security_assignments(desired, current)
        self._plan_runner_groups(desired, current)
        self._plan_self_hosted_runners(desired, current)
        self._plan_hosted_runners(desired, current)
        self._plan_blocked_users(desired, current)
        if "interaction_limit" in desired:
            self._plan_interaction_limit(
                "organization.interaction_limit",
                desired["interaction_limit"],
                current.get("interaction_limit", {"enabled": False}),
                f"/orgs/{quote(self.org)}/interaction-limits",
            )
        if "budgets" in desired:
            self._plan_budgets(desired["budgets"], current.get("budgets"))
        custom_patterns = get_path(desired, "secret_scanning.custom_patterns")
        if custom_patterns is not None:
            self._plan_custom_patterns(
                "organization.secret_scanning.custom_patterns",
                custom_patterns,
                get_path(current, "secret_scanning.custom_patterns"),
                f"/orgs/{quote(self.org)}/secret-scanning",
                ("organization",),
            )
        if "private_registries" in desired:
            self._plan_private_registries(
                desired["private_registries"],
                current.get("private_registries"),
            )
        outside_collaborators = _desired_collection_names(
            desired.get("outside_collaborators"),
            "organization.outside_collaborators",
        )
        if "members" in desired:
            self._plan_members(
                desired["members"],
                current.get("members"),
                converted_to_outside=outside_collaborators,
            )
        if "teams" in desired:
            self._plan_teams(desired["teams"], current.get("teams"))
        self._plan_copilot_seats(desired, current)
        if "invitations" in desired:
            self._plan_organization_invitations(
                desired["invitations"], current.get("invitations")
            )
        if "outside_collaborators" in desired:
            self._plan_outside_collaborators(
                desired["outside_collaborators"],
                current.get("outside_collaborators"),
                current.get("members"),
                desired.get("members"),
            )
        if "personal_access_tokens" in desired:
            self._plan_personal_access_tokens(
                desired["personal_access_tokens"],
                current.get("personal_access_tokens"),
            )
        if "credential_authorizations" in desired:
            self._plan_credential_authorizations(
                desired["credential_authorizations"],
                current.get("credential_authorizations"),
            )
        if "organization_roles" in desired:
            self._plan_organization_roles(
                desired["organization_roles"], current.get("organization_roles")
            )
        if "security_manager_teams" in desired:
            self._plan_security_managers(
                desired["security_manager_teams"],
                current.get("security_manager_teams", []),
            )
        for scope in ("actions", "agents"):
            desired_scope = desired.get(scope, {})
            current_scope = current.get(scope, {})
            if "variables" in desired_scope:
                self._plan_variables(
                    f"organization.{scope}.variables",
                    desired_scope["variables"],
                    current_scope.get("variables"),
                    f"/orgs/{quote(self.org)}/{scope}/variables",
                    organization_level=True,
                )
            if "secrets" in desired_scope:
                self._plan_secrets(
                    f"organization.{scope}.secrets",
                    desired_scope["secrets"],
                    current_scope.get("secrets"),
                    f"/orgs/{quote(self.org)}/{scope}/secrets",
                    organization_level=True,
                )
        for scope in ("codespaces", "dependabot"):
            desired_scope = desired.get(scope, {})
            current_scope = current.get(scope, {})
            if "secrets" in desired_scope:
                self._plan_secrets(
                    f"organization.{scope}.secrets",
                    desired_scope["secrets"],
                    current_scope.get("secrets"),
                    f"/orgs/{quote(self.org)}/{scope}/secrets",
                    organization_level=True,
                )
        if "rulesets" in desired:
            self._plan_rulesets(
                "organization.rulesets",
                desired["rulesets"],
                current.get("rulesets"),
                f"/orgs/{quote(self.org)}",
                ("organization",),
            )
        if "hooks" in desired:
            self._plan_hooks(
                "organization.hooks",
                desired["hooks"],
                current.get("hooks"),
                f"/orgs/{quote(self.org)}",
                ("organization",),
            )
        if "custom_properties" in desired:
            self._plan_custom_property_schema(
                desired["custom_properties"], current.get("custom_properties")
            )
        if "custom_property_values" in desired:
            self._plan_organization_custom_property_values(
                desired["custom_property_values"],
                current.get("custom_property_values"),
            )
        if "announcement" in desired:
            self._plan_announcement(
                desired["announcement"], current.get("announcement")
            )
        if "notification_restriction_enabled" in desired:
            self._plan_notification_restriction(
                desired["notification_restriction_enabled"],
                current.get("notification_restriction_enabled"),
            )
        if "ip_allow_list" in desired:
            self._plan_ip_allow_list(
                desired["ip_allow_list"], current.get("ip_allow_list")
            )
        if "domains" in desired:
            self._plan_domains(desired["domains"], current.get("domains"))
        if "custom_repository_roles" in desired:
            self._plan_custom_repository_roles(
                desired["custom_repository_roles"],
                current.get("custom_repository_roles"),
            )
        if "custom_organization_roles" in desired:
            self._plan_custom_organization_roles(
                desired["custom_organization_roles"],
                current.get("custom_organization_roles"),
            )
        if "app_installations" in desired:
            self._plan_app_installations(
                desired["app_installations"],
                current.get("app_installations"),
            )

    def _add_graphql_operation(
        self,
        document: str,
        operation_name: str,
        changes: list[FieldChange],
        input_value: Mapping[str, Any],
        *,
        phase: int = 50,
        body_id_references: list[tuple[tuple[str | int, ...], tuple[str, ...]]]
        | None = None,
        body_id_list_references: list[
            tuple[tuple[str | int, ...], list[tuple[str, ...]]]
        ]
        | None = None,
        capture_id: tuple[str, ...] | None = None,
        capture_response_path: tuple[str | int, ...] = ("id",),
        blocked_reason: str | None = None,
        warning_reason: str | None = None,
    ) -> None:
        self.operations.append(
            Operation(
                "POST",
                f"/graphql#{operation_name}",
                changes,
                body={"input": dict(input_value)},
                phase=phase,
                capture_id=capture_id,
                capture_response_path=capture_response_path,
                body_id_references=body_id_references or [],
                body_id_list_references=body_id_list_references or [],
                graphql_document=document,
                blocked_reason=blocked_reason,
                warning_reason=warning_reason,
            )
        )

    def _ignore_or_block_change(
        self,
        path: str,
        before: Any,
        after: Any,
        reason: str,
        *,
        action: str = "update",
    ) -> None:
        change = FieldChange(path, before, after, action)
        if self.force:
            self.forced_changes.append(change)
            return
        self.operations.append(
            Operation(
                "READ-ONLY",
                "",
                [change],
                blocked_reason=reason,
            )
        )

    def _plan_app_installations(
        self,
        desired_value: Any,
        current_value: Any,
    ) -> None:
        path = "organization.app_installations"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        matched_current: set[str] = set()
        known = {*APP_INSTALLATION_READ_ONLY_FIELDS, "selected_repositories"}
        for key, item in desired.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, known, f"{path}.items.{key}")
            current_key = _match_current_key(
                key,
                item.get("app_slug", key),
                current,
                lambda value: (
                    value.get("app_slug") if isinstance(value, Mapping) else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is None or current_item is None:
                self._ignore_or_block_change(
                    f"{path}.{key}",
                    None,
                    item,
                    "GitHub App installations must be created through the app "
                    "installation flow.",
                    action="add",
                )
                continue
            matched_current.add(current_key)
            installation_id = self.current.ids.get(("app_installations", current_key))
            if installation_id is not None:
                self.current.ids[("app_installations", key)] = installation_id
            for field_name in APP_INSTALLATION_READ_ONLY_FIELDS:
                if field_name in item and item[field_name] != current_item.get(
                    field_name
                ):
                    self._ignore_or_block_change(
                        f"{path}.{key}.{field_name}",
                        current_item.get(field_name),
                        item[field_name],
                        "GitHub returns this installation value for inspection. "
                        "Changing it requires the GitHub App installation flow.",
                    )
            if "selected_repositories" not in item:
                continue
            repositories = item["selected_repositories"]
            if not isinstance(repositories, list) or not all(
                isinstance(repository, str) for repository in repositories
            ):
                raise ConfigError(
                    f"{path}.items.{key}.selected_repositories must be a list "
                    "of repository names"
                )
            if len({repository.casefold() for repository in repositories}) != len(
                repositories
            ):
                raise ConfigError(
                    f"{path}.items.{key}.selected_repositories must not contain "
                    "duplicate repository names"
                )
            if current_item.get("repository_selection") != "selected":
                self._ignore_or_block_change(
                    f"{path}.{key}.selected_repositories",
                    current_item.get("selected_repositories", []),
                    repositories,
                    "GitHub can change selected repositories only after the app "
                    "installation is configured to use selected repositories.",
                )
                continue
            current_repositories = current_item.get("selected_repositories", [])
            if not isinstance(current_repositories, list):
                current_repositories = []
            desired_by_name = {
                repository.casefold(): repository for repository in repositories
            }
            current_by_name = {
                repository.casefold(): repository
                for repository in current_repositories
                if isinstance(repository, str)
            }
            for normalized_name in sorted(set(desired_by_name) - set(current_by_name)):
                repository = desired_by_name[normalized_name]
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/user/installations/"
                        f"{installation_id if installation_id is not None else 'unknown'}"
                        "/repositories/{repository_id}",
                        [
                            FieldChange(
                                f"{path}.{key}.selected_repositories.{repository}",
                                None,
                                repository,
                                "add",
                            )
                        ],
                        phase=30,
                        blocked_reason=None
                        if installation_id is not None
                        else "the current GitHub App installation ID is unavailable",
                        endpoint_id_references=[
                            ("{repository_id}", ("repositories", repository))
                        ],
                    )
                )
            for normalized_name in sorted(set(current_by_name) - set(desired_by_name)):
                repository = current_by_name[normalized_name]
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/user/installations/"
                        f"{installation_id if installation_id is not None else 'unknown'}"
                        "/repositories/{repository_id}",
                        [
                            FieldChange(
                                f"{path}.{key}.selected_repositories.{repository}",
                                repository,
                                None,
                                "remove",
                            )
                        ],
                        phase=80,
                        blocked_reason=(
                            "the current GitHub App installation ID is unavailable"
                            if installation_id is None
                            else (
                                None
                                if self.force
                                else (
                                    "GitHub may have hidden selected repositories "
                                    "from the token that produced this file; pass "
                                    "--force to authorize removing App access"
                                )
                            )
                        ),
                        endpoint_id_references=[
                            ("{repository_id}", ("repositories", repository))
                        ],
                        warning_reason=(
                            "GitHub can omit selected repositories that the source "
                            "token could not access. This request removes the app's "
                            "access to this repository."
                        ),
                    )
                )
        if mode == "exact":
            for key in set(current) - matched_current:
                self._ignore_or_block_change(
                    f"{path}.{key}",
                    current[key],
                    None,
                    "Removing a GitHub App installation requires the app "
                    "installation flow.",
                    action="remove",
                )

    def _plan_announcement(self, desired: Any, current: Any) -> None:
        path = "organization.announcement"
        if not isinstance(desired, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        _check_keys(
            desired,
            {"enabled", "announcement", "expires_at", "user_dismissible"},
            path,
        )
        enabled = desired.get("enabled")
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}.enabled must be true or false")
        current_mapping = (
            current if isinstance(current, Mapping) else {"enabled": False}
        )
        endpoint = f"/orgs/{quote(self.org)}/announcement"
        if not enabled:
            if current_mapping.get("enabled") is True:
                self.operations.append(
                    Operation(
                        "DELETE",
                        endpoint,
                        [FieldChange(path, current_mapping, desired, "remove")],
                        phase=80,
                    )
                )
            return
        message = desired.get("announcement")
        if not isinstance(message, str):
            raise ConfigError(f"{path}.announcement must be a string")
        body = pick(
            deep_merge(current_mapping, desired),
            ("announcement", "expires_at", "user_dismissible"),
        )
        changes = _leaf_changes(current_mapping, desired, path)
        changes = [change for change in changes if not change.path.endswith(".enabled")]
        if changes or current_mapping.get("enabled") is not True:
            self.operations.append(
                Operation(
                    "PATCH",
                    endpoint,
                    changes or [FieldChange(path, current_mapping, desired, "add")],
                    body=body,
                    phase=30,
                )
            )

    def _plan_notification_restriction(self, desired: Any, current: Any) -> None:
        path = "organization.notification_restriction_enabled"
        if not isinstance(desired, bool):
            raise ConfigError(f"{path} must be true or false")
        if desired == current:
            return
        self._add_graphql_operation(
            UPDATE_NOTIFICATION_RESTRICTION_MUTATION,
            "UpdateNotificationRestrictionConfiguration",
            [FieldChange(path, current, desired)],
            {
                "ownerId": None,
                "settingValue": "ENABLED" if desired else "DISABLED",
            },
            body_id_references=[(("input", "ownerId"), ("organization", "node_id"))],
        )

    def _plan_ip_allow_list(self, desired: Any, current: Any) -> None:
        path = "organization.ip_allow_list"
        if not isinstance(desired, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        _check_keys(desired, {"enabled", "applies_to_installed_apps", "entries"}, path)
        current_mapping = current if isinstance(current, Mapping) else {}
        for key, document, operation_name in (
            (
                "enabled",
                UPDATE_IP_ALLOW_LIST_ENABLED_MUTATION,
                "UpdateIpAllowListEnabledConfiguration",
            ),
            (
                "applies_to_installed_apps",
                UPDATE_IP_ALLOW_LIST_FOR_APPS_MUTATION,
                "UpdateIpAllowListForAppsConfiguration",
            ),
        ):
            if key not in desired:
                continue
            value = desired[key]
            if not isinstance(value, bool):
                raise ConfigError(f"{path}.{key} must be true or false")
            if current_mapping.get(key) == value:
                continue
            self._add_graphql_operation(
                document,
                operation_name,
                [FieldChange(f"{path}.{key}", current_mapping.get(key), value)],
                {
                    "ownerId": None,
                    "settingValue": "ENABLED" if value else "DISABLED",
                },
                body_id_references=[
                    (("input", "ownerId"), ("organization", "node_id"))
                ],
                phase=70 if key == "enabled" and value else 20,
            )
        if "entries" not in desired:
            return
        desired_entries = collection_items(desired["entries"], f"{path}.entries")
        mode = collection_mode(desired["entries"], f"{path}.entries")
        current_entries = collection_items(
            current_mapping.get("entries") or {"items": {}},
            f"current.{path}.entries",
        )
        fields = {"value", "name", "active"}
        matched_current: set[str] = set()
        for key, item in desired_entries.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.entries.items.{key} must be a mapping")
            _check_keys(item, fields, f"{path}.entries.items.{key}")
            current_key = _match_current_key(
                key,
                item.get("value", key),
                current_entries,
                lambda value: (
                    value.get("value") if isinstance(value, Mapping) else None
                ),
                f"{path}.entries.items.{key}",
            )
            current_item = (
                current_entries.get(current_key) if current_key is not None else None
            )
            if current_key is not None:
                if current_key in matched_current:
                    raise ConfigError(
                        f"{path}.entries.items.{key} and another configured entry "
                        f"both identify current IP allow-list entry {current_key!r}"
                    )
                matched_current.add(current_key)
            complete = deep_merge(
                current_item if isinstance(current_item, Mapping) else {}, item
            )
            if not isinstance(complete.get("value"), str):
                raise ConfigError(f"{path}.entries.items.{key}.value must be a string")
            if not isinstance(complete.get("active"), bool):
                raise ConfigError(
                    f"{path}.entries.items.{key}.active must be true or false"
                )
            changes = (
                [FieldChange(f"{path}.entries.{key}", None, item, "add")]
                if current_item is None
                else _leaf_changes(current_item, item, f"{path}.entries.{key}")
            )
            if not changes:
                continue
            input_value = {
                "allowListValue": complete["value"],
                "isActive": complete["active"],
            }
            if "name" in complete:
                input_value["name"] = complete["name"]
            if current_item is None:
                input_value["ownerId"] = None
                self._add_graphql_operation(
                    CREATE_IP_ALLOW_LIST_ENTRY_MUTATION,
                    "CreateIpAllowListEntryConfiguration",
                    changes,
                    input_value,
                    phase=20,
                    body_id_references=[
                        (("input", "ownerId"), ("organization", "node_id"))
                    ],
                    capture_id=("ip_allow_list_entries", key),
                    capture_response_path=(
                        "createIpAllowListEntry",
                        "ipAllowListEntry",
                        "id",
                    ),
                )
            else:
                input_value["ipAllowListEntryId"] = None
                self._add_graphql_operation(
                    UPDATE_IP_ALLOW_LIST_ENTRY_MUTATION,
                    "UpdateIpAllowListEntryConfiguration",
                    changes,
                    input_value,
                    phase=30,
                    body_id_references=[
                        (
                            ("input", "ipAllowListEntryId"),
                            ("ip_allow_list_entries", current_key or key),
                        )
                    ],
                )
        if mode == "exact":
            for key in set(current_entries) - matched_current:
                self._add_graphql_operation(
                    DELETE_IP_ALLOW_LIST_ENTRY_MUTATION,
                    "DeleteIpAllowListEntryConfiguration",
                    [
                        FieldChange(
                            f"{path}.entries.{key}",
                            current_entries[key],
                            None,
                            "remove",
                        )
                    ],
                    {"ipAllowListEntryId": None},
                    phase=80,
                    body_id_references=[
                        (
                            ("input", "ipAllowListEntryId"),
                            ("ip_allow_list_entries", key),
                        )
                    ],
                )

    def _plan_domains(self, desired_value: Any, current_value: Any) -> None:
        path = "organization.domains"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = {
            "approved",
            "verified",
            "required_for_policy_enforcement",
            "verification_token",
            "token_expires_at",
        }
        for domain, item in desired.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.items.{domain} must be a mapping")
            _check_keys(item, fields, f"{path}.items.{domain}")
            for key in ("approved", "verified"):
                if key in item and not isinstance(item[key], bool):
                    raise ConfigError(
                        f"{path}.items.{domain}.{key} must be true or false"
                    )
            current_item = current.get(domain)
            domain_id_key = ("domains", domain)
            if current_item is None:
                self._add_graphql_operation(
                    ADD_DOMAIN_MUTATION,
                    "AddOrganizationDomainConfiguration",
                    [FieldChange(f"{path}.{domain}", None, item, "add")],
                    {"domain": domain, "ownerId": None},
                    phase=10,
                    body_id_references=[
                        (("input", "ownerId"), ("organization", "node_id"))
                    ],
                    capture_id=domain_id_key,
                    capture_response_path=("addVerifiableDomain", "domain", "id"),
                )
                current_item = {"approved": False, "verified": False}
            for key, document, operation_name in (
                (
                    "approved",
                    APPROVE_DOMAIN_MUTATION,
                    "ApproveOrganizationDomainConfiguration",
                ),
                (
                    "verified",
                    VERIFY_DOMAIN_MUTATION,
                    "VerifyOrganizationDomainConfiguration",
                ),
            ):
                if key not in item or item[key] == current_item.get(key):
                    continue
                if item[key] is False:
                    self._ignore_or_block_change(
                        f"{path}.{domain}.{key}",
                        current_item.get(key),
                        False,
                        f"GitHub can set domain {key} to true but provides no "
                        "operation that returns it to false; remove and re-add the "
                        "domain if that is the intended change",
                    )
                    continue
                self._add_graphql_operation(
                    document,
                    operation_name,
                    [
                        FieldChange(
                            f"{path}.{domain}.{key}",
                            current_item.get(key),
                            True,
                        )
                    ],
                    {"id": None},
                    phase=20 if key == "verified" else 30,
                    body_id_references=[(("input", "id"), domain_id_key)],
                )
        if mode == "exact":
            for domain in set(current) - set(desired):
                self._add_graphql_operation(
                    DELETE_DOMAIN_MUTATION,
                    "DeleteOrganizationDomainConfiguration",
                    [FieldChange(f"{path}.{domain}", current[domain], None, "remove")],
                    {"id": None},
                    phase=80,
                    body_id_references=[(("input", "id"), ("domains", domain))],
                )

    def _plan_custom_repository_roles(
        self, desired_value: Any, current_value: Any
    ) -> None:
        self._plan_custom_role_definitions(
            "organization.custom_repository_roles",
            desired_value,
            current_value,
            f"/orgs/{quote(self.org)}/custom-repository-roles",
            ("custom_repository_roles",),
            {"name", "description", "base_role", "permissions"},
            {"name", "base_role", "permissions"},
        )

    def _plan_custom_organization_roles(
        self, desired_value: Any, current_value: Any
    ) -> None:
        self._plan_custom_role_definitions(
            "organization.custom_organization_roles",
            desired_value,
            current_value,
            f"/orgs/{quote(self.org)}/organization-roles",
            ("custom_organization_roles",),
            {"name", "description", "base_role", "permissions", "source"},
            {"name", "permissions"},
            source_field=True,
        )

    def _plan_custom_role_definitions(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        endpoint: str,
        id_prefix: tuple[str, ...],
        fields: set[str],
        create_required: set[str],
        *,
        source_field: bool = False,
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        writable_fields = fields - {"source"}
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, fields, f"{path}.items.{key}")
            current_key = _match_current_key(
                key,
                item.get("name", key),
                current,
                lambda value: value.get("name") if isinstance(value, Mapping) else None,
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key is not None else None
            if current_key is not None:
                if current_key in matched_current:
                    raise ConfigError(
                        f"{path}.items.{key} and another configured role both "
                        f"identify current role {current_key!r}"
                    )
                matched_current.add(current_key)
            if (
                source_field
                and isinstance(current_item, Mapping)
                and current_item.get("source") != "Organization"
            ):
                continue
            complete = deep_merge(
                current_item if isinstance(current_item, Mapping) else {}, item
            )
            if current_item is None:
                complete.setdefault("name", key)
                missing = create_required - set(complete)
                if missing:
                    raise ConfigError(
                        f"{path}.items.{key} is missing required keys: "
                        f"{', '.join(sorted(missing))}"
                    )
                self.operations.append(
                    Operation(
                        "POST",
                        endpoint,
                        [FieldChange(f"{path}.{key}", None, item, "add")],
                        body=pick(complete, writable_fields),
                        phase=10,
                        capture_id=(*id_prefix, key),
                    )
                )
                continue
            writable_item = {
                name: value for name, value in item.items() if name in writable_fields
            }
            current_writable = {
                name: value
                for name, value in current_item.items()
                if name in writable_fields
            }
            changes = _leaf_changes(current_writable, writable_item, f"{path}.{key}")
            if changes:
                self.operations.append(
                    Operation(
                        "PATCH",
                        f"{endpoint}/__ROLE_ID__",
                        changes,
                        body=pick(
                            deep_merge(current_writable, writable_item), writable_fields
                        ),
                        phase=30,
                        endpoint_id_references=[
                            ("__ROLE_ID__", (*id_prefix, current_key or key))
                        ],
                    )
                )
        if mode != "exact":
            return
        for key in set(current) - matched_current:
            item = current[key]
            if (
                source_field
                and isinstance(item, Mapping)
                and item.get("source") != "Organization"
            ):
                self._ignore_or_block_change(
                    f"{path}.{key}",
                    item,
                    None,
                    "GitHub or the enterprise supplies this role, so organization "
                    "custom-role operations cannot delete it",
                    action="remove",
                )
                continue
            self.operations.append(
                Operation(
                    "DELETE",
                    f"{endpoint}/__ROLE_ID__",
                    [FieldChange(f"{path}.{key}", item, None, "remove")],
                    phase=80,
                    endpoint_id_references=[("__ROLE_ID__", (*id_prefix, key))],
                )
            )

    def _plan_singletons(
        self,
        desired: Mapping[str, Any],
        current: Mapping[str, Any],
        specs: Iterable[SingletonSpec],
        *,
        org: str,
        repo: str | None = None,
        prefix: str = "organization",
    ) -> None:
        for spec in specs:
            desired_value = get_path(desired, spec.key)
            if desired_value is None:
                continue
            if not isinstance(desired_value, dict):
                raise ConfigError(f"{prefix}.{spec.key} must be a mapping")
            current_value = get_path(current, spec.key, {})
            path = spec.path.format(
                org=quote(org), repo=quote(repo) if repo is not None else ""
            )
            fields = None if spec.fields == ("*",) else spec.fields
            if spec.key == "secret_scanning.pattern_configurations":
                _check_keys(
                    desired_value,
                    {"provider_pattern_settings", "custom_pattern_settings"},
                    f"{prefix}.{spec.key}",
                )
                _validate_pattern_settings(desired_value, f"{prefix}.{spec.key}")
                current_mapping = (
                    current_value if isinstance(current_value, dict) else {}
                )
                changes = _leaf_changes(
                    current_mapping, desired_value, f"{prefix}.{spec.key}"
                )
                if changes:
                    body = pick(
                        deep_merge(current_mapping, desired_value),
                        ("provider_pattern_settings", "custom_pattern_settings"),
                    )
                    version = current_mapping.get("_pattern_config_version")
                    if version is not None:
                        body["pattern_config_version"] = version
                    self.operations.append(
                        Operation(
                            spec.method,
                            path,
                            changes,
                            body=body,
                            phase=30,
                        )
                    )
                continue
            current_mapping = current_value if isinstance(current_value, dict) else {}
            current_mapping = without_singleton_response_only_values(
                spec.key, current_mapping
            )
            desired_value = without_singleton_response_only_values(
                spec.key, desired_value
            )
            self._plan_mapping_endpoint(
                f"{prefix}.{spec.key}",
                current_mapping,
                desired_value,
                spec.method,
                path,
                fields=fields,
                full_update=spec.full_update,
            )

    def _plan_repository_sets(
        self, desired: Mapping[str, Any], current: Mapping[str, Any]
    ) -> None:
        for spec in ORGANIZATION_REPOSITORY_SETS:
            wanted = get_path(desired, spec.key)
            if wanted is None:
                continue
            if not isinstance(wanted, list) or not all(
                isinstance(name, str) for name in wanted
            ):
                raise ConfigError(
                    f"organization.{spec.key} must be a list of repository names"
                )
            actual = get_path(current, spec.key, [])
            if not isinstance(actual, list):
                actual = []
            if sorted(wanted) == sorted(actual):
                continue
            operation = Operation(
                "PUT",
                spec.path.format(org=quote(self.org)),
                [
                    FieldChange(
                        f"organization.{spec.key}", sorted(actual), sorted(wanted)
                    )
                ],
                body={},
                phase=30,
            )
            operation.body_id_list_references.append(
                (
                    ("selected_repository_ids",),
                    [("repositories", name) for name in wanted],
                )
            )
            self.operations.append(operation)

    def _plan_oidc_property_inclusions(
        self, desired: Mapping[str, Any], current: Mapping[str, Any]
    ) -> None:
        wanted = get_path(desired, "actions.oidc_custom_properties")
        if wanted is None:
            return
        if not isinstance(wanted, list) or not all(
            isinstance(name, str) for name in wanted
        ):
            raise ConfigError(
                "organization.actions.oidc_custom_properties must be a list of custom property names"
            )
        actual = get_path(current, "actions.oidc_custom_properties", [])
        if not isinstance(actual, list):
            actual = []
        base = f"/orgs/{quote(self.org)}/actions/oidc/customization/properties/repo"
        for name in set(wanted) - set(actual):
            self.operations.append(
                Operation(
                    "POST",
                    base,
                    [
                        FieldChange(
                            f"organization.actions.oidc_custom_properties.{name}",
                            None,
                            True,
                            "add",
                        )
                    ],
                    body={"custom_property_name": name},
                    phase=30,
                )
            )
        for name in set(actual) - set(wanted):
            self.operations.append(
                Operation(
                    "DELETE",
                    f"{base}/{quote(name)}",
                    [
                        FieldChange(
                            f"organization.actions.oidc_custom_properties.{name}",
                            True,
                            None,
                            "remove",
                        )
                    ],
                    phase=80,
                )
            )

    def _plan_dependabot_access(
        self, desired: Mapping[str, Any], current: Mapping[str, Any]
    ) -> None:
        wanted = get_path(desired, "dependabot.repository_access")
        if wanted is None:
            return
        if not isinstance(wanted, dict):
            raise ConfigError(
                "organization.dependabot.repository_access must be a mapping"
            )
        actual = get_path(current, "dependabot.repository_access", {})
        actual = actual if isinstance(actual, dict) else {}
        wanted_default_level = wanted.get("default_level")
        if wanted_default_level not in (None, "public", "internal"):
            raise ConfigError(
                "organization.dependabot.repository_access.default_level must be "
                "'public', 'internal', or null"
            )
        if wanted_default_level is not None and wanted_default_level != actual.get(
            "default_level"
        ):
            self.operations.append(
                Operation(
                    "PUT",
                    f"/orgs/{quote(self.org)}/dependabot/repository-access/default-level",
                    [
                        FieldChange(
                            "organization.dependabot.repository_access.default_level",
                            actual.get("default_level"),
                            wanted_default_level,
                        )
                    ],
                    body={"default_level": wanted_default_level},
                    phase=30,
                )
            )
        if "repositories" not in wanted:
            return
        repositories = wanted["repositories"]
        if not isinstance(repositories, list) or not all(
            isinstance(name, str) for name in repositories
        ):
            raise ConfigError(
                "organization.dependabot.repository_access.repositories must be a list"
            )
        actual_repositories = actual.get("repositories", [])
        additions = sorted(set(repositories) - set(actual_repositories))
        removals = sorted(set(actual_repositories) - set(repositories))
        if additions or removals:
            operation = Operation(
                "PATCH",
                f"/orgs/{quote(self.org)}/dependabot/repository-access",
                [
                    FieldChange(
                        "organization.dependabot.repository_access.repositories",
                        sorted(actual_repositories),
                        sorted(repositories),
                    )
                ],
                body={},
                phase=30,
            )
            operation.body_id_list_references.extend(
                [
                    (
                        ("repository_ids_to_add",),
                        [("repositories", name) for name in additions],
                    ),
                    (
                        ("repository_ids_to_remove",),
                        [("repositories", name) for name in removals],
                    ),
                ]
            )
            self.operations.append(operation)

    def _plan_organization_collections(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        for spec in ORGANIZATION_COLLECTIONS:
            desired_value = get_path(desired_root, spec.key)
            if desired_value is None:
                continue
            path = f"organization.{spec.key}"
            desired = collection_items(desired_value, path)
            mode = collection_mode(desired_value, path)
            current_value = get_path(current_root, spec.key, {"items": {}})
            current = collection_items(current_value, f"current.{path}")
            matched_current: set[str] = set()
            for key, item in desired.items():
                if not isinstance(item, dict):
                    raise ConfigError(f"{path}.items.{key} must be a mapping")
                extra_fields = (
                    {"repositories", "default_for_new_repos"}
                    if spec.key == "code_security.configurations"
                    else set()
                )
                _check_keys(
                    item,
                    set(spec.fields) | extra_fields,
                    f"{path}.items.{key}",
                )
                current_key = _match_current_key(
                    key,
                    item.get(spec.identity_field),
                    current,
                    partial(
                        _mapping_field_value,
                        field_name=spec.identity_field,
                    ),
                    f"{path}.items.{key}",
                )
                current_item = current.get(current_key) if current_key else None
                if current_key is not None:
                    matched_current.add(current_key)
                    self.organization_collection_matches[(spec.key, key)] = current_key
                    current_id = self.current.ids.get(
                        ("organization_collections", spec.key, current_key)
                    )
                    if current_id is not None:
                        self.current.ids[
                            ("organization_collections", spec.key, key)
                        ] = current_id
                    current_prefix = (
                        "organization",
                        *spec.key.split("."),
                        "items",
                        current_key,
                    )
                    for unavailable_path, endpoint in list(
                        self.current.unavailable_collections.items()
                    ):
                        if unavailable_path[: len(current_prefix)] == current_prefix:
                            self.current.unavailable_collections.setdefault(
                                (
                                    "organization",
                                    *spec.key.split("."),
                                    "items",
                                    key,
                                    *unavailable_path[len(current_prefix) :],
                                ),
                                endpoint,
                            )
                if current_item is None:
                    required_fields = {
                        "issue_types": {"is_enabled"},
                        "issue_fields": {"data_type"},
                        "hosted_compute.network_configurations": {
                            "network_settings_ids"
                        },
                    }.get(spec.key, set())
                    _require_keys(item, required_fields, f"{path}.items.{key}")
                    body = organization_collection_create_body(
                        spec.key, pick(item, spec.fields)
                    )
                    body.setdefault(spec.identity_field, key)
                    create_blocked_reason = (
                        _issue_field_options_error(body)
                        if spec.key == "issue_fields"
                        else _network_compute_service_error(item, None)
                        if spec.key == "hosted_compute.network_configurations"
                        else None
                    )
                    self.operations.append(
                        Operation(
                            "POST",
                            spec.create_path.format(org=quote(self.org)),
                            [FieldChange(f"{path}.{key}", None, item, "add")],
                            body=body,
                            phase=5
                            if spec.key == "hosted_compute.network_configurations"
                            else 10,
                            capture_id=("organization_collections", spec.key, key),
                            blocked_reason=create_blocked_reason,
                        )
                    )
                    continue
                writable_desired = without_organization_collection_response_metadata(
                    spec.key, pick(item, spec.fields)
                )
                writable_current = without_organization_collection_response_metadata(
                    spec.key, pick(current_item, spec.fields)
                )
                writable_desired = without_organization_collection_response_only_nulls(
                    spec.key, writable_desired
                )
                writable_current = without_organization_collection_response_only_nulls(
                    spec.key, writable_current
                )
                changes = _leaf_changes(
                    writable_current, writable_desired, f"{path}.{key}"
                )
                if not changes:
                    continue
                resource_id = self.current.ids.get(
                    (
                        "organization_collections",
                        spec.key,
                        current_key if current_key is not None else key,
                    )
                )
                immutable_changes = [
                    field
                    for field in spec.immutable_fields
                    if field in writable_desired
                    and writable_desired[field] != writable_current.get(field)
                ]
                complete_writable_item = deep_merge(writable_current, writable_desired)
                update_body = organization_collection_request_body(
                    spec.key,
                    pick(complete_writable_item, spec.update_fields),
                )
                issue_options_error = None
                if spec.key == "issue_fields":
                    options_changed = (
                        "options" in writable_desired
                        and writable_desired["options"]
                        != writable_current.get("options")
                    )
                    if options_changed:
                        issue_options_error = _issue_field_options_error(
                            complete_writable_item
                        )
                    else:
                        update_body.pop("options", None)
                network_compute_error = (
                    _network_compute_service_error(item, current_item)
                    if spec.key == "hosted_compute.network_configurations"
                    else None
                )
                if immutable_changes:
                    blocked_reason = f"{', '.join(immutable_changes)} cannot be changed without replacing the resource"
                elif issue_options_error is not None:
                    blocked_reason = issue_options_error
                elif network_compute_error is not None:
                    blocked_reason = network_compute_error
                elif resource_id is None:
                    blocked_reason = "the current resource ID is unavailable"
                else:
                    blocked_reason = None
                self.operations.append(
                    Operation(
                        spec.update_method,
                        spec.update_path.format(
                            org=quote(self.org),
                            id=quote(resource_id)
                            if resource_id is not None
                            else "unknown",
                        ),
                        changes,
                        body=update_body,
                        phase=30,
                        blocked_reason=blocked_reason,
                    )
                )
            if mode == "exact":
                for key in set(current) - matched_current:
                    resource_id = self.current.ids.get(
                        ("organization_collections", spec.key, key)
                    )
                    self.operations.append(
                        Operation(
                            "DELETE",
                            spec.delete_path.format(
                                org=quote(self.org),
                                id=quote(resource_id)
                                if resource_id is not None
                                else "unknown",
                            ),
                            [
                                FieldChange(
                                    f"{path}.{key}", current[key], None, "remove"
                                )
                            ],
                            phase=85,
                            blocked_reason=None
                            if resource_id is not None
                            else "the current resource ID is unavailable",
                        )
                    )

    def _plan_code_security_assignments(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        desired_value = get_path(desired_root, "code_security.configurations")
        if desired_value is None:
            return
        path = "organization.code_security.configurations"
        desired = collection_items(desired_value, path)
        current_value = get_path(
            current_root, "code_security.configurations", {"items": {}}
        )
        current = collection_items(current_value, f"current.{path}")
        assigned_to: dict[str, str] = {}
        for key, item in desired.items():
            if not isinstance(item, dict) or "repositories" not in item:
                continue
            repositories = item["repositories"]
            if not isinstance(repositories, list) or not all(
                isinstance(name, str) for name in repositories
            ):
                raise ConfigError(f"{path}.items.{key}.repositories must be a list")
            for repository in repositories:
                previous = assigned_to.get(repository)
                if previous is not None and previous != key:
                    raise ConfigError(
                        f"{path} assigns repository {repository!r} to both {previous!r} and {key!r}"
                    )
                assigned_to[repository] = key
        for key, item in desired.items():
            if not isinstance(item, dict):
                continue
            current_key = self.organization_collection_matches.get(
                ("code_security.configurations", key), key
            )
            current_item = current.get(current_key, {})
            id_key = ("organization_collections", "code_security.configurations", key)
            endpoint = (
                f"/orgs/{quote(self.org)}/code-security/configurations/"
                "__CONFIGURATION_ID__"
            )
            endpoint_reference: list[tuple[str, tuple[str, ...]]] = [
                ("__CONFIGURATION_ID__", id_key)
            ]
            if "repositories" in item:
                repositories = item["repositories"]
                if not isinstance(repositories, list) or not all(
                    isinstance(name, str) for name in repositories
                ):
                    raise ConfigError(f"{path}.items.{key}.repositories must be a list")
                actual_repositories = current_item.get("repositories", [])
                additions = sorted(set(repositories) - set(actual_repositories))
                removals = sorted(set(actual_repositories) - set(repositories))
                if additions:
                    operation = Operation(
                        "POST",
                        f"{endpoint}/attach",
                        [
                            FieldChange(
                                f"{path}.{key}.repositories",
                                sorted(actual_repositories),
                                sorted(repositories),
                            )
                        ],
                        body={"scope": "selected"},
                        phase=40,
                        endpoint_id_references=endpoint_reference,
                    )
                    operation.body_id_list_references.append(
                        (
                            ("selected_repository_ids",),
                            [("repositories", name) for name in additions],
                        )
                    )
                    self.operations.append(operation)
                if removals:
                    operation = Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/code-security/configurations/detach",
                        [
                            FieldChange(
                                f"{path}.{key}.repositories",
                                sorted(actual_repositories),
                                sorted(repositories),
                            )
                        ],
                        body={},
                        phase=39,
                    )
                    operation.body_id_list_references.append(
                        (
                            ("selected_repository_ids",),
                            [("repositories", name) for name in removals],
                        )
                    )
                    self.operations.append(operation)
            if "default_for_new_repos" in item and item[
                "default_for_new_repos"
            ] != current_item.get("default_for_new_repos", "none"):
                self.operations.append(
                    Operation(
                        "PUT",
                        f"{endpoint}/defaults",
                        [
                            FieldChange(
                                f"{path}.{key}.default_for_new_repos",
                                current_item.get("default_for_new_repos", "none"),
                                item["default_for_new_repos"],
                            )
                        ],
                        body={"default_for_new_repos": item["default_for_new_repos"]},
                        phase=40,
                        endpoint_id_references=endpoint_reference,
                    )
                )

    def _plan_runner_groups(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        desired_value = get_path(desired_root, "actions.runner_groups")
        if desired_value is None:
            return
        path = "organization.actions.runner_groups"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current_value = get_path(current_root, "actions.runner_groups", {"items": {}})
        current = collection_items(current_value, f"current.{path}")
        fields = (
            "name",
            "visibility",
            "allows_public_repositories",
            "restricted_to_workflows",
            "selected_workflows",
        )
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(
                item,
                {"settings", "repositories", "runners"},
                f"{path}.items.{key}",
            )
            settings = item.get("settings", {})
            if not isinstance(settings, dict):
                raise ConfigError(f"{path}.items.{key}.settings must be a mapping")
            _check_keys(
                settings,
                set(fields) | {"network_configuration"},
                f"{path}.items.{key}.settings",
            )
            read_only_reason = _read_only_identity_reason(
                self.current.read_only_identities,
                ("organization", "actions", "runner_groups"),
                settings.get("name", key),
            )
            protected_runner_reason = _read_only_name_in_values_reason(
                self.current.read_only_runner_group_runners,
                item.get("runners"),
            )
            unreadable_runner_reason = None
            configured_runners = item.get("runners")
            if (
                isinstance(configured_runners, list)
                and configured_runners
                and self.current.unreadable_inherited_runner_assignments
            ):
                endpoint = self.current.unreadable_inherited_runner_assignments[0]
                unreadable_runner_reason = (
                    "the inherited enterprise runner assignments could not be read "
                    f"at {endpoint}"
                )
            read_only_reason = (
                read_only_reason or protected_runner_reason or unreadable_runner_reason
            )
            if read_only_reason is not None:
                self.current.read_only_items.setdefault(
                    ("organization", "actions", "runner_groups", key),
                    read_only_reason,
                )
            runner_assignment_reason = (
                protected_runner_reason or unreadable_runner_reason
            )
            if runner_assignment_reason is not None:
                self.current.read_only_items.setdefault(
                    ("organization", "actions", "runner_groups", key, "runners"),
                    runner_assignment_reason,
                )
            current_key = _match_current_key(
                key,
                settings.get("name", key),
                current,
                lambda value: (
                    get_path(value, "settings.name")
                    if isinstance(value, Mapping)
                    else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
            current_settings = (
                current_item.get("settings", {})
                if isinstance(current_item, dict)
                else {}
            )
            group_id_key = ("runner_groups", key)
            group_id = self.current.ids.get(
                ("runner_groups", current_key if current_key is not None else key)
            )
            if group_id is not None:
                self.current.ids[group_id_key] = group_id
            if current_item is None:
                operation = Operation(
                    "POST",
                    f"/orgs/{quote(self.org)}/actions/runner-groups",
                    [FieldChange(f"{path}.{key}", None, item, "add")],
                    body={**pick(settings, fields), "name": settings.get("name", key)},
                    phase=6,
                    capture_id=group_id_key,
                )
                if settings.get("network_configuration") is not None:
                    self._add_network_configuration_reference(
                        operation, settings["network_configuration"]
                    )
                self.operations.append(operation)
            else:
                changes = _leaf_changes(
                    current_settings, settings, f"{path}.{key}.settings"
                )
                if changes:
                    endpoint = f"/orgs/{quote(self.org)}/actions/runner-groups/__RUNNER_GROUP_ID__"
                    operation = Operation(
                        "PATCH",
                        endpoint,
                        changes,
                        body=pick(deep_merge(current_settings, settings), fields),
                        phase=30,
                        endpoint_id_references=[("__RUNNER_GROUP_ID__", group_id_key)],
                    )
                    if "network_configuration" in settings:
                        self._add_network_configuration_reference(
                            operation, settings["network_configuration"]
                        )
                    self.operations.append(operation)
            current_mapping = current_item if isinstance(current_item, dict) else {}
            for list_name, body_name, id_prefix in (
                ("repositories", "selected_repository_ids", "repositories"),
                ("runners", "runners", "self_hosted_runners"),
            ):
                if list_name not in item:
                    continue
                wanted = item[list_name]
                if not isinstance(wanted, list) or not all(
                    isinstance(name, str) for name in wanted
                ):
                    raise ConfigError(
                        f"{path}.items.{key}.{list_name} must be a list of names"
                    )
                actual = current_mapping.get(list_name, [])
                if sorted(actual) == sorted(wanted):
                    continue
                endpoint = (
                    f"/orgs/{quote(self.org)}/actions/runner-groups/"
                    f"__RUNNER_GROUP_ID__/{list_name}"
                )
                operation = Operation(
                    "PUT",
                    endpoint,
                    [
                        FieldChange(
                            f"{path}.{key}.{list_name}", sorted(actual), sorted(wanted)
                        )
                    ],
                    body={},
                    phase=40,
                    endpoint_id_references=[("__RUNNER_GROUP_ID__", group_id_key)],
                )
                operation.body_id_list_references.append(
                    ((body_name,), [(id_prefix, name) for name in wanted])
                )
                self.operations.append(operation)
        if mode == "exact":
            for key in set(current) - matched_current:
                group_id = self.current.ids.get(("runner_groups", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/actions/runner-groups/"
                        f"{group_id if group_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if group_id is not None
                        else "the current runner group ID is unavailable",
                    )
                )

    def _add_network_configuration_reference(
        self, operation: Operation, name: Any
    ) -> None:
        if name is None:
            if isinstance(operation.body, dict):
                operation.body["network_configuration_id"] = None
            return
        if not isinstance(name, str):
            raise ConfigError(
                f"{operation.changes[0].path}.network_configuration must be a name or null"
            )
        operation.body_id_references.append(
            (
                ("network_configuration_id",),
                (
                    "organization_collections",
                    "hosted_compute.network_configurations",
                    name,
                ),
            )
        )

    def _plan_self_hosted_runners(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        desired_value = get_path(desired_root, "actions.self_hosted_runners")
        if desired_value is None:
            return
        path = "organization.actions.self_hosted_runners"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current_value = get_path(
            current_root, "actions.self_hosted_runners", {"items": {}}
        )
        current = collection_items(current_value, f"current.{path}")
        for name, item in desired.items():
            if not isinstance(item, dict) or not isinstance(
                item.get("labels", []), list
            ):
                raise ConfigError(f"{path}.items.{name} must contain a labels list")
            _check_keys(item, {"labels"}, f"{path}.items.{name}")
            if name not in current:
                self.operations.append(
                    Operation(
                        "PUT",
                        "",
                        [FieldChange(f"{path}.{name}", None, item, "add")],
                        blocked_reason="self-hosted runners must register themselves before their settings can be applied",
                    )
                )
                continue
            if sorted(current[name].get("labels", [])) != sorted(
                item.get("labels", [])
            ):
                runner_id = self.current.ids.get(("self_hosted_runners", name))
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/orgs/{quote(self.org)}/actions/runners/"
                        f"{runner_id if runner_id is not None else 'unknown'}/labels",
                        [
                            FieldChange(
                                f"{path}.{name}.labels",
                                current[name].get("labels", []),
                                item.get("labels", []),
                            )
                        ],
                        body={"labels": item.get("labels", [])},
                        phase=30,
                        blocked_reason=None
                        if runner_id is not None
                        else "the current runner ID is unavailable",
                    )
                )
        if mode == "exact":
            for name in set(current) - set(desired):
                runner_id = self.current.ids.get(("self_hosted_runners", name))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/actions/runners/"
                        f"{runner_id if runner_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if runner_id is not None
                        else "the current runner ID is unavailable",
                    )
                )

    def _plan_hosted_runners(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        desired_value = get_path(desired_root, "actions.hosted_runners")
        if desired_value is None:
            return
        path = "organization.actions.hosted_runners"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current_value = get_path(current_root, "actions.hosted_runners", {"items": {}})
        current = collection_items(current_value, f"current.{path}")
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(
                item,
                {
                    "name",
                    "image",
                    "size",
                    "runner_group",
                    "maximum_runners",
                    "enable_static_ip",
                    "image_gen",
                },
                f"{path}.items.{key}",
            )
            if "image" in item:
                image_value = item["image"]
                if not isinstance(image_value, dict):
                    raise ConfigError(f"{path}.items.{key}.image must be a mapping")
                _check_keys(
                    image_value,
                    {"id", "source", "version"},
                    f"{path}.items.{key}.image",
                )
            writable_item = _hosted_runner_writable_item(item)
            current_key = _match_current_key(
                key,
                writable_item.get("name", key),
                current,
                lambda value: value.get("name") if isinstance(value, Mapping) else None,
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                runner_id = self.current.ids.get(("hosted_runners", current_key))
                if runner_id is not None:
                    self.current.ids[("hosted_runners", key)] = runner_id
            if current_item is None:
                image = writable_item.get("image")
                if not isinstance(image, dict) or not isinstance(image.get("id"), str):
                    raise ConfigError(
                        f"{path}.items.{key}.image must contain an image ID"
                    )
                if not isinstance(writable_item.get("size"), str):
                    raise ConfigError(f"{path}.items.{key}.size must be a name")
                if not isinstance(writable_item.get("runner_group"), str):
                    raise ConfigError(
                        f"{path}.items.{key}.runner_group must name a runner group"
                    )
                body = pick(
                    writable_item,
                    (
                        "name",
                        "image",
                        "size",
                        "maximum_runners",
                        "enable_static_ip",
                        "image_gen",
                    ),
                )
                body.setdefault("name", key)
                operation = Operation(
                    "POST",
                    f"/orgs/{quote(self.org)}/actions/hosted-runners",
                    [FieldChange(f"{path}.{key}", None, item, "add")],
                    body=body,
                    phase=10,
                    capture_id=("hosted_runners", key),
                )
                self._add_runner_group_reference(
                    operation, writable_item["runner_group"]
                )
                self.operations.append(operation)
                continue
            writable_current_item = _hosted_runner_writable_item(
                current_item if isinstance(current_item, Mapping) else {}
            )
            changes = _leaf_changes(
                writable_current_item, writable_item, f"{path}.{key}"
            )
            if changes:
                runner_id = self.current.ids.get(("hosted_runners", current_key or key))
                body = pick(
                    writable_item,
                    (
                        "name",
                        "size",
                        "maximum_runners",
                        "enable_static_ip",
                        "image_gen",
                    ),
                )
                image = writable_item.get("image")
                if isinstance(image, dict):
                    if "source" in image:
                        body["image_source"] = image["source"]
                    if "id" in image:
                        body["image_id"] = image["id"]
                    if "version" in image:
                        body["image_version"] = image["version"]
                operation = Operation(
                    "PATCH",
                    f"/orgs/{quote(self.org)}/actions/hosted-runners/"
                    f"{runner_id if runner_id is not None else 'unknown'}",
                    changes,
                    body=body,
                    phase=30,
                    blocked_reason=None
                    if runner_id is not None
                    else "the current hosted runner ID is unavailable",
                )
                if "runner_group" in writable_item:
                    self._add_runner_group_reference(
                        operation, writable_item["runner_group"]
                    )
                self.operations.append(operation)
        if mode == "exact":
            for key in set(current) - matched_current:
                runner_id = self.current.ids.get(("hosted_runners", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/actions/hosted-runners/"
                        f"{runner_id if runner_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if runner_id is not None
                        else "the current hosted runner ID is unavailable",
                    )
                )

    def _add_runner_group_reference(self, operation: Operation, name: Any) -> None:
        if not isinstance(name, str):
            operation.blocked_reason = (
                "runner_group must name an existing or configured runner group"
            )
            return
        operation.body_id_references.append(
            (("runner_group_id",), ("runner_groups", name))
        )

    def _plan_blocked_users(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        if "blocked_users" not in desired_root:
            return
        desired = desired_root["blocked_users"]
        if not isinstance(desired, list) or not all(
            isinstance(user, str) for user in desired
        ):
            raise ConfigError(
                "organization.blocked_users must be a list of user logins"
            )
        current = current_root.get("blocked_users", [])
        for user in set(desired) - set(current):
            self.operations.append(
                Operation(
                    "PUT",
                    f"/orgs/{quote(self.org)}/blocks/{quote(user)}",
                    [
                        FieldChange(
                            f"organization.blocked_users.{user}", None, True, "add"
                        )
                    ],
                    phase=30,
                )
            )
        for user in set(current) - set(desired):
            self.operations.append(
                Operation(
                    "DELETE",
                    f"/orgs/{quote(self.org)}/blocks/{quote(user)}",
                    [
                        FieldChange(
                            f"organization.blocked_users.{user}", True, None, "remove"
                        )
                    ],
                    phase=80,
                )
            )

    def _plan_copilot_seats(
        self,
        desired_root: Mapping[str, Any],
        current_root: Mapping[str, Any],
    ) -> None:
        desired = get_path(desired_root, "copilot.seats")
        if desired is None:
            return
        if not isinstance(desired, dict):
            raise ConfigError("organization.copilot.seats must be a mapping")
        current = get_path(current_root, "copilot.seats", {})
        current = current if isinstance(current, dict) else {}
        for actor_type, endpoint_name, body_name in (
            ("users", "selected_users", "selected_usernames"),
            ("teams", "selected_teams", "selected_teams"),
        ):
            if actor_type not in desired:
                continue
            wanted = desired.get(actor_type, [])
            actual = current.get(actor_type, [])
            if not isinstance(wanted, list) or not all(
                isinstance(name, str) for name in wanted
            ):
                raise ConfigError(
                    f"organization.copilot.seats.{actor_type} must be a list"
                )
            if not isinstance(actual, list):
                actual = []
            if actor_type == "teams":
                wanted = self._logical_team_slugs(wanted)
                actual = self._logical_team_slugs(
                    name for name in actual if isinstance(name, str)
                )
            additions = sorted(set(wanted) - set(actual))
            removals = sorted(set(actual) - set(wanted))
            for method, values, action in (
                ("POST", additions, "add"),
                ("DELETE", removals, "remove"),
            ):
                if not values:
                    continue
                operation = Operation(
                    method,
                    f"/orgs/{quote(self.org)}/copilot/billing/{endpoint_name}",
                    [
                        FieldChange(
                            f"organization.copilot.seats.{actor_type}",
                            sorted(actual),
                            sorted(wanted),
                            action,
                        )
                    ],
                    body={body_name: values},
                    phase=30 if method == "POST" else 80,
                )
                if actor_type == "teams":
                    self._resolve_team_slug_body_list(operation, (body_name,))
                self.operations.append(operation)

    def _plan_interaction_limit(
        self,
        path: str,
        desired: Any,
        current: Any,
        endpoint: str,
    ) -> None:
        if not isinstance(desired, dict) or not isinstance(
            desired.get("enabled"), bool
        ):
            raise ConfigError(f"{path} must be a mapping with an enabled boolean")
        _check_keys(desired, {"enabled", "limit", "expiry"}, path)
        current = current if isinstance(current, dict) else {"enabled": False}
        enabled = desired["enabled"]
        if not enabled and current.get("enabled"):
            self.operations.append(
                Operation(
                    "DELETE",
                    endpoint,
                    [FieldChange(path, current, desired, "remove")],
                    phase=80,
                )
            )
            return
        if not enabled:
            return
        if "limit" not in desired:
            raise ConfigError(f"{path}.limit is required when enabled is true")
        changed = (
            not current.get("enabled")
            or current.get("limit") != desired.get("limit")
            or "expiry" in desired
        )
        if changed:
            self.operations.append(
                Operation(
                    "PUT",
                    endpoint,
                    [
                        FieldChange(
                            path,
                            current,
                            {
                                key: value
                                for key, value in desired.items()
                                if not key.startswith("_")
                            },
                            "add" if not current.get("enabled") else "update",
                        )
                    ],
                    body=pick(desired, ("limit", "expiry")),
                    phase=30,
                )
            )

    def _plan_budgets(self, desired_value: Any, current_value: Any) -> None:
        path = "organization.budgets"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        base = f"/organizations/{quote(self.org)}/settings/billing/budgets"
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, set(BUDGET_FIELDS), f"{path}.items.{key}")
            current_key = _match_current_key(
                key,
                _budget_identity(item),
                current,
                lambda value: (
                    _budget_identity(value) if isinstance(value, Mapping) else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                budget_id = self.current.ids.get(("budgets", current_key))
                if budget_id is not None:
                    self.current.ids[("budgets", key)] = budget_id
            if current_item is None:
                inherited_scope_error = _inherited_budget_scope_error(item)
                self.operations.append(
                    Operation(
                        "POST",
                        base,
                        [FieldChange(f"{path}.{key}", None, item, "add")],
                        body=pick(item, BUDGET_FIELDS),
                        phase=10,
                        capture_id=("budgets", key),
                        capture_response_path=("budget", "id"),
                        blocked_reason=inherited_scope_error,
                    )
                )
                continue
            changes = _leaf_changes(current_item, item, f"{path}.{key}")
            if changes:
                budget_id = self.current.ids.get(("budgets", current_key or key))
                inherited_scope_error = _inherited_budget_scope_error(
                    current_item
                ) or _inherited_budget_scope_error(item)
                blocked_reason = inherited_scope_error
                if blocked_reason is None and budget_id is None:
                    blocked_reason = "the current budget ID is unavailable"
                self.operations.append(
                    Operation(
                        "PATCH",
                        f"{base}/{quote(budget_id) if budget_id is not None else 'unknown'}",
                        changes,
                        body=pick(deep_merge(current_item, item), BUDGET_FIELDS),
                        phase=30,
                        blocked_reason=blocked_reason,
                    )
                )
        if mode == "exact":
            for key in set(current) - matched_current:
                budget_id = self.current.ids.get(("budgets", key))
                inherited_scope_error = _inherited_budget_scope_error(current[key])
                blocked_reason = inherited_scope_error
                if blocked_reason is None and budget_id is None:
                    blocked_reason = "the current budget ID is unavailable"
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{quote(budget_id) if budget_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=blocked_reason,
                    )
                )

    def _plan_custom_patterns(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        base: str,
        id_prefix: tuple[str, ...],
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = (
            "pattern",
            "start_delimiter",
            "end_delimiter",
            "must_match",
            "must_not_match",
        )
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(item, {"name", *fields}, f"{path}.items.{name}")
            current_item = current.get(name)
            if current_item is None:
                pattern_name = item.get("name", name)
                if not isinstance(pattern_name, str):
                    raise ConfigError(f"{path}.items.{name}.name must be a string")
                _require_keys(item, {"pattern"}, f"{path}.items.{name}")
                create_fields = {
                    key: value
                    for key, value in pick(item, fields).items()
                    if value is not None
                }
                self.operations.append(
                    Operation(
                        "POST",
                        f"{base}/custom-patterns",
                        [FieldChange(f"{path}.{name}", None, item, "add")],
                        body={"patterns": [{"name": pattern_name, **create_fields}]},
                        phase=10,
                    )
                )
                continue
            changes = _leaf_changes(current_item, item, f"{path}.{name}")
            if changes:
                pattern_id = self.current.ids.get(id_prefix + ("custom_patterns", name))
                version = self.current.ids.get(
                    id_prefix + ("custom_pattern_versions", name)
                )
                body = {
                    key: value
                    for key, value in pick(item, fields).items()
                    if value is not None
                }
                body["custom_pattern_version"] = version
                name_changed = "name" in item and item["name"] != current_item.get(
                    "name"
                )
                null_changed = any(
                    field in item
                    and item[field] is None
                    and current_item.get(field) is not None
                    for field in fields
                )
                self.operations.append(
                    Operation(
                        "PATCH",
                        f"{base}/custom-patterns/{pattern_id if pattern_id is not None else 'unknown'}",
                        changes,
                        body=body,
                        phase=30,
                        blocked_reason=(
                            "name cannot be changed; remove and recreate the custom pattern"
                            if name_changed
                            else (
                                "GitHub returns null for unset optional pattern fields but does not accept null when updating them"
                                if null_changed
                                else (
                                    None
                                    if pattern_id is not None and version is not None
                                    else "the current custom pattern ID or version is unavailable"
                                )
                            )
                        ),
                    )
                )
        if mode == "exact":
            changes = []
            for name in set(current) - set(desired):
                changes.append(
                    FieldChange(f"{path}.{name}", current[name], None, "remove")
                )
            if changes:
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/custom-patterns",
                        changes,
                        body=None,
                        phase=85,
                        blocked_reason=(
                            "GitHub requires custom pattern deletion to change associated "
                            "secret-scanning alerts, which are outside this configuration"
                        ),
                    )
                )

    def _plan_private_registries(self, desired_value: Any, current_value: Any) -> None:
        path = "organization.private_registries"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        base = f"/orgs/{quote(self.org)}/private-registries"
        oidc_auth_types = {
            "oidc_azure",
            "oidc_aws",
            "oidc_jfrog",
            "oidc_cloudsmith",
            "oidc_gcp",
        }
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(
                item,
                set(PRIVATE_REGISTRY_FIELDS)
                | {"value_from_env", "selected_repositories"},
                f"{path}.items.{name}",
            )
            environment_name = item.get("value_from_env")
            if environment_name is not None and (
                not isinstance(environment_name, str) or not environment_name
            ):
                raise ConfigError(
                    f"{path}.items.{name}.value_from_env must name an environment variable"
                )
            selected_repositories = item.get("selected_repositories")
            if selected_repositories is not None and (
                not isinstance(selected_repositories, list)
                or not all(
                    isinstance(repository, str) for repository in selected_repositories
                )
            ):
                raise ConfigError(
                    f"{path}.items.{name}.selected_repositories must be a list of repository names"
                )
            metadata = {
                key: value
                for key, value in item.items()
                if key not in ("value_from_env", "selected_repositories")
            }
            current_item = current.get(name)
            if current_item is None:
                _require_keys(
                    item,
                    {"registry_type", "url", "visibility"},
                    f"{path}.items.{name}",
                )
                registry_type = item.get("registry_type")
                expected_name = (
                    f"{registry_type.upper()}_SECRET"
                    if isinstance(registry_type, str)
                    else None
                )
                if name != expected_name:
                    raise ConfigError(
                        f"{path}.items.{name} must use GitHub's generated name "
                        f"{expected_name!r} for registry_type {registry_type!r}"
                    )
                operation = Operation(
                    "POST",
                    base,
                    [FieldChange(f"{path}.{name}", None, metadata, "add")],
                    body=pick(metadata, PRIVATE_REGISTRY_FIELDS),
                    phase=10,
                    secret_environment=environment_name,
                    secret_public_key_endpoint=f"{base}/public-key"
                    if environment_name
                    else None,
                    blocked_reason=(
                        "token and username_password registries need value_from_env"
                        if environment_name is None
                        and metadata.get("auth_type", "token") not in oidc_auth_types
                        else None
                    ),
                )
                self._add_selected_repository_ids(operation, item, True)
                self.operations.append(operation)
                continue
            changes = _leaf_changes(current_item, metadata, f"{path}.{name}")
            if environment_name:
                changes.append(
                    FieldChange(
                        f"{path}.{name}.value",
                        "<write-only>",
                        f"${environment_name}",
                        sensitive=True,
                    )
                )
            if "selected_repositories" in item and sorted(
                current_item.get("selected_repositories", [])
            ) != sorted(item["selected_repositories"]):
                changes.append(
                    FieldChange(
                        f"{path}.{name}.selected_repositories",
                        current_item.get("selected_repositories", []),
                        item["selected_repositories"],
                    )
                )
            if changes:
                auth_type_changed = "auth_type" in metadata and metadata[
                    "auth_type"
                ] != current_item.get("auth_type")
                operation = Operation(
                    "PATCH",
                    f"{base}/{quote(name)}",
                    changes,
                    body=pick(
                        deep_merge(current_item, metadata), PRIVATE_REGISTRY_FIELDS
                    ),
                    phase=30,
                    blocked_reason="auth_type cannot be changed; delete and recreate the registry"
                    if auth_type_changed
                    else None,
                    secret_environment=environment_name,
                    secret_public_key_endpoint=f"{base}/public-key"
                    if environment_name
                    else None,
                )
                self._add_selected_repository_ids(operation, item, True)
                self.operations.append(operation)
        if mode == "exact":
            for name in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                    )
                )

    def _plan_mapping_endpoint(
        self,
        display_path: str,
        current: Mapping[str, Any],
        desired: Any,
        method: str,
        endpoint: str,
        *,
        fields: Iterable[str] | None = None,
        full_update: bool = True,
        phase: int = 30,
    ) -> None:
        if not isinstance(desired, dict):
            raise ConfigError(f"{display_path} must be a mapping")
        if fields is not None:
            unknown = {key for key in desired if not key.startswith("_")} - set(fields)
            if unknown:
                raise ConfigError(
                    f"{display_path} has unknown settings: {', '.join(sorted(unknown))}"
                )
        changes = _leaf_changes(current, desired, display_path)
        if not changes:
            return
        merged = deep_merge(current, desired)
        allowed = set(fields) if fields is not None else None
        if full_update:
            body = {
                key: value
                for key, value in merged.items()
                if allowed is None or key in allowed
            }
        else:
            body = {
                key: merged[key]
                for key in desired
                if key in merged and (allowed is None or key in allowed)
            }
        self.operations.append(
            Operation(method, endpoint, changes, body=body, phase=phase)
        )

    def _plan_members(
        self,
        desired_value: Any,
        current_value: Any,
        *,
        converted_to_outside: set[str],
    ) -> None:
        desired = collection_items(desired_value, "organization.members")
        mode = collection_mode(desired_value, "organization.members")
        current = collection_items(
            current_value or {"items": {}}, "current.organization.members"
        )
        for login, value in desired.items():
            desired_item = value if isinstance(value, dict) else {"role": value}
            _check_keys(
                desired_item,
                {"role", "public"},
                f"organization.members.items.{login}",
            )
            current_item = current.get(login)
            current_item = current_item if isinstance(current_item, dict) else {}
            role = desired_item.get("role")
            if role not in (None, "admin", "member"):
                raise ConfigError(
                    f"organization.members.items.{login}.role must be 'admin' or 'member'"
                )
            if role is not None and current_item.get("role") != role:
                action = "add" if login not in current else "update"
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/orgs/{quote(self.org)}/memberships/{quote(login)}",
                        [
                            FieldChange(
                                f"organization.members.{login}.role",
                                current_item.get("role"),
                                role,
                                action,
                            )
                        ],
                        body={"role": role},
                        phase=10,
                    )
                )
            if (
                "public" in desired_item
                and current_item.get("public") != desired_item["public"]
            ):
                public = bool(desired_item["public"])
                self.operations.append(
                    Operation(
                        "PUT" if public else "DELETE",
                        f"/orgs/{quote(self.org)}/public_members/{quote(login)}",
                        [
                            FieldChange(
                                f"organization.members.{login}.public",
                                current_item.get("public", False),
                                public,
                            )
                        ],
                        phase=20,
                        blocked_reason=None
                        if self.current.ids.get(("authenticated_user",))
                        == login.casefold()
                        else "GitHub only lets the authenticated user change their public organization membership",
                    )
                )
        if mode == "exact":
            for login in set(current) - set(desired):
                if login in converted_to_outside:
                    continue
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/memberships/{quote(login)}",
                        [
                            FieldChange(
                                f"organization.members.{login}",
                                current[login],
                                None,
                                "remove",
                            )
                        ],
                        phase=95,
                    )
                )

    def _plan_teams(self, desired_value: Any, current_value: Any) -> None:
        desired = collection_items(desired_value, "organization.teams")
        mode = collection_mode(desired_value, "organization.teams")
        current = collection_items(
            current_value or {"items": {}}, "current.organization.teams"
        )
        current_matches = _team_current_matches(desired, current)
        self.team_logical_to_current.update(current_matches)
        self.team_current_to_logical.update(
            {
                current_slug: logical_slug
                for logical_slug, current_slug in current_matches.items()
            }
        )
        topology_desired = _team_topology_desired(desired, current_matches)
        topology_current = _team_topology_current(current, current_matches)
        _validate_team_final_topology(topology_desired, topology_current, mode)
        create_phases = _team_create_phases(topology_desired, topology_current)
        delete_phases = _team_delete_phases(topology_current)
        matched_current: set[str] = set()
        for slug, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"organization.teams.items.{slug} must be a mapping")
            _check_keys(
                item,
                {
                    "settings",
                    "members",
                    "repositories",
                    "review_assignment",
                    "external_group",
                    "team_sync_groups",
                },
                f"organization.teams.items.{slug}",
            )
            settings = item.get("settings", {})
            if not isinstance(settings, dict):
                raise ConfigError(
                    f"organization.teams.items.{slug}.settings must be a mapping"
                )
            _check_keys(
                settings,
                {
                    "name",
                    "description",
                    "privacy",
                    "notification_setting",
                    "permission",
                    "parent",
                },
                f"organization.teams.items.{slug}.settings",
            )
            read_only_reason = _read_only_identity_reason(
                self.current.read_only_identities,
                ("organization", "teams"),
                settings.get("name", slug),
            )
            if read_only_reason is not None:
                self.current.read_only_items.setdefault(
                    ("organization", "teams", slug), read_only_reason
                )
            if "privacy" in settings and settings["privacy"] not in (
                "secret",
                "closed",
            ):
                raise ConfigError(
                    f"organization.teams.items.{slug}.settings.privacy must be 'secret' or 'closed'"
                )
            current_slug = current_matches.get(slug)
            current_item = current.get(current_slug) if current_slug else None
            endpoint_slug = current_slug or slug
            created_slug_key: tuple[str, ...] | None = None
            updated_slug_key: tuple[str, ...] | None = None
            slug_ready_phase = 0
            if current_slug is not None:
                matched_current.add(current_slug)
                team_id = self.current.ids.get(("teams", current_slug))
                if team_id is not None:
                    self.current.ids[("teams", slug)] = team_id
                team_node_id = self.current.ids.get(("teams", current_slug, "node_id"))
                if team_node_id is not None:
                    self.current.ids[("teams", slug, "node_id")] = team_node_id
            if current_item is None:
                request_settings = _without_null_fields(settings, ("description",))
                body = pick(
                    request_settings,
                    (
                        "name",
                        "description",
                        "privacy",
                        "notification_setting",
                        "permission",
                    ),
                )
                body.setdefault("name", slug)
                requested_permission = body.get("permission")
                follow_up_permission = (
                    requested_permission if requested_permission == "admin" else None
                )
                if (
                    requested_permission is not None
                    and requested_permission not in TEAM_CREATE_PERMISSIONS
                ):
                    body.pop("permission")
                parent = settings.get("parent")
                topology_parent = _team_parent_alias(parent, current_matches)
                effective_privacy = settings.get(
                    "privacy", "closed" if topology_parent is not None else "secret"
                )
                privacy_error = _team_privacy_error(
                    slug,
                    effective_privacy,
                    topology_parent,
                    topology_desired,
                    topology_current,
                    mode,
                )
                parent_privacy_error = _team_parent_privacy_error(
                    topology_parent, topology_desired, topology_current, mode
                )
                created_slug_key = ("team_slugs", slug)
                operation = Operation(
                    "POST",
                    f"/orgs/{quote(self.org)}/teams",
                    [FieldChange(f"organization.teams.{slug}", None, item, "add")],
                    body=body,
                    phase=create_phases[slug],
                    capture_id=("teams", slug),
                    capture_response_values=[(created_slug_key, ("slug",))],
                    blocked_reason=(
                        f"GitHub cannot set the exported team permission {requested_permission!r}"
                        if requested_permission is not None
                        and requested_permission not in TEAM_UPDATE_PERMISSIONS
                        else privacy_error or parent_privacy_error
                    ),
                )
                operation.capture_response_values.append(
                    (("teams", slug, "node_id"), ("node_id",))
                )
                slug_ready_phase = operation.phase + 1
                self.team_slug_references[slug] = (
                    created_slug_key,
                    slug_ready_phase,
                )
                self.team_id_ready_phases[slug] = slug_ready_phase
                if parent is not None:
                    self._add_parent_reference(operation, parent)
                if parent is not None and (
                    (mode == "exact" and topology_parent not in topology_desired)
                    or (
                        ("teams", parent) not in self.current.ids
                        and topology_parent not in topology_desired
                    )
                ):
                    operation.blocked_reason = operation.blocked_reason or (
                        f"parent team {parent!r} does not exist or appear in the configuration"
                    )
                self.operations.append(operation)
                if follow_up_permission is not None:
                    self.operations.append(
                        Operation(
                            "PATCH",
                            f"/orgs/{quote(self.org)}/teams/__CREATED_TEAM_SLUG__",
                            [
                                FieldChange(
                                    f"organization.teams.{slug}.settings.permission",
                                    "pull",
                                    follow_up_permission,
                                )
                            ],
                            body={"permission": follow_up_permission},
                            phase=max(70, slug_ready_phase),
                            endpoint_id_references=[
                                ("__CREATED_TEAM_SLUG__", created_slug_key)
                            ],
                        )
                    )
                current_item = {}
            else:
                current_settings_value = current_item.get("settings", {})
                current_settings = _without_null_fields(
                    current_settings_value
                    if isinstance(current_settings_value, Mapping)
                    else {},
                    ("description",),
                )
                desired_settings_for_update = _without_null_fields(
                    settings, ("description",)
                )
                current_settings_for_changes = dict(current_settings)
                desired_settings_for_changes = dict(desired_settings_for_update)
                if "parent" in current_settings_for_changes:
                    current_settings_for_changes["parent"] = _team_parent_alias(
                        current_settings_for_changes["parent"], current_matches
                    )
                if "parent" in desired_settings_for_changes:
                    desired_settings_for_changes["parent"] = _team_parent_alias(
                        desired_settings_for_changes["parent"], current_matches
                    )
                changes = _leaf_changes(
                    current_settings_for_changes,
                    desired_settings_for_changes,
                    f"organization.teams.{slug}.settings",
                )
                if changes:
                    complete_settings = deep_merge(
                        current_settings, desired_settings_for_update
                    )
                    body = pick(
                        complete_settings,
                        (
                            "name",
                            "description",
                            "privacy",
                            "notification_setting",
                            "permission",
                        ),
                    )
                    parent_changes = [
                        change
                        for change in changes
                        if change.path.endswith(".settings.parent")
                    ]
                    setting_changes = [
                        change for change in changes if change not in parent_changes
                    ]
                    permission_changed = any(
                        change.path.endswith(".settings.permission")
                        for change in setting_changes
                    )
                    requested_permission = body.get("permission")
                    permission_error = None
                    if (
                        "permission" in body
                        and requested_permission not in TEAM_UPDATE_PERMISSIONS
                    ):
                        body.pop("permission")
                        if permission_changed:
                            permission_error = (
                                "GitHub cannot set the requested team permission "
                                f"{requested_permission!r}"
                            )
                    privacy_error = _team_privacy_error(
                        slug,
                        body.get("privacy"),
                        _team_parent_alias(
                            complete_settings.get("parent"), current_matches
                        ),
                        topology_desired,
                        topology_current,
                        mode,
                    )
                    parent_privacy_error = _team_parent_privacy_error(
                        _team_parent_alias(
                            complete_settings.get("parent"), current_matches
                        ),
                        topology_desired,
                        topology_current,
                        mode,
                    )
                    phase = _team_privacy_update_phase(
                        slug,
                        current_settings.get("privacy"),
                        complete_settings.get("privacy"),
                        topology_desired,
                        topology_current,
                        mode,
                    )
                    current_parent = current_settings.get("parent")
                    parent = complete_settings.get("parent")
                    topology_parent = _team_parent_alias(parent, current_matches)
                    if parent_changes and current_parent is not None:
                        self.operations.append(
                            Operation(
                                "PATCH",
                                f"/orgs/{quote(self.org)}/teams/{quote(endpoint_slug)}",
                                [
                                    FieldChange(
                                        parent_changes[0].path,
                                        current_parent,
                                        None,
                                    )
                                ],
                                body={"parent_team_id": None},
                                phase=3,
                            )
                        )
                    if setting_changes:
                        operation = Operation(
                            "PATCH",
                            f"/orgs/{quote(self.org)}/teams/{quote(endpoint_slug)}",
                            setting_changes,
                            body=body,
                            phase=phase,
                            blocked_reason=(
                                permission_error
                                or privacy_error
                                or parent_privacy_error
                            ),
                        )
                        if any(
                            change.path.endswith(".settings.name")
                            for change in setting_changes
                        ):
                            updated_slug_key = ("team_slugs", slug)
                            operation.capture_response_values.append(
                                (updated_slug_key, ("slug",))
                            )
                            slug_ready_phase = operation.phase + 1
                            self.team_slug_references[slug] = (
                                updated_slug_key,
                                slug_ready_phase,
                            )
                        self.operations.append(operation)
                    if parent_changes and parent is not None:
                        attach_slug = (
                            "__RESOLVED_TEAM_SLUG__"
                            if updated_slug_key is not None
                            else quote(endpoint_slug)
                        )
                        attach = Operation(
                            "PATCH",
                            f"/orgs/{quote(self.org)}/teams/{attach_slug}",
                            [
                                FieldChange(
                                    parent_changes[0].path,
                                    None
                                    if current_parent is not None
                                    else current_parent,
                                    parent,
                                )
                            ],
                            body={},
                            phase=max(
                                75,
                                slug_ready_phase,
                                create_phases.get(topology_parent, 0) + 1,
                            ),
                            blocked_reason=privacy_error or parent_privacy_error,
                        )
                        self._add_parent_reference(attach, parent)
                        if updated_slug_key is not None:
                            attach.endpoint_id_references.append(
                                ("__RESOLVED_TEAM_SLUG__", updated_slug_key)
                            )
                        if (
                            mode == "exact" and topology_parent not in topology_desired
                        ) or (
                            ("teams", parent) not in self.current.ids
                            and topology_parent not in topology_desired
                        ):
                            attach.blocked_reason = attach.blocked_reason or (
                                f"parent team {parent!r} does not exist or appear in the configuration"
                            )
                        self.operations.append(attach)
            if "members" in item:
                self._plan_team_members(
                    endpoint_slug,
                    item["members"],
                    current_item.get("members"),
                    display_slug=slug,
                    slug_reference=created_slug_key or updated_slug_key,
                    slug_ready_phase=slug_ready_phase,
                )
            if "repositories" in item:
                self._plan_team_repositories(
                    endpoint_slug,
                    item["repositories"],
                    current_item.get("repositories"),
                    display_slug=slug,
                    slug_reference=created_slug_key or updated_slug_key,
                    slug_ready_phase=slug_ready_phase,
                )
            if "review_assignment" in item:
                self._plan_team_review_assignment(
                    slug,
                    item["review_assignment"],
                    current_item.get("review_assignment"),
                    node_id_ready_phase=slug_ready_phase,
                    new_team=current_slug is None,
                )
            if "external_group" in item:
                self._plan_team_external_group(
                    endpoint_slug,
                    item["external_group"],
                    current_item.get("external_group"),
                    display_slug=slug,
                    slug_reference=created_slug_key or updated_slug_key,
                    slug_ready_phase=slug_ready_phase,
                )
            if "team_sync_groups" in item:
                self._plan_team_sync_groups(
                    endpoint_slug,
                    item["team_sync_groups"],
                    current_item.get("team_sync_groups"),
                    display_slug=slug,
                    slug_reference=created_slug_key or updated_slug_key,
                    slug_ready_phase=slug_ready_phase,
                )
        if mode == "exact":
            for slug in set(current) - matched_current:
                retained_child = _team_has_final_child(
                    slug, topology_desired, topology_current, mode
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/teams/{quote(slug)}",
                        [
                            FieldChange(
                                f"organization.teams.{slug}",
                                current[slug],
                                None,
                                "remove",
                            )
                        ],
                        phase=delete_phases.get(slug, 95),
                        blocked_reason=(
                            "GitHub would also delete a child team retained by the configuration"
                            if retained_child
                            else None
                        ),
                    )
                )

    def _plan_team_review_assignment(
        self,
        slug: str,
        desired: Any,
        current: Any,
        *,
        node_id_ready_phase: int,
        new_team: bool,
    ) -> None:
        path = f"organization.teams.{slug}.review_assignment"
        if not isinstance(desired, Mapping):
            raise ConfigError(f"{path} must be a mapping")
        fields = {"enabled", "algorithm", "member_count", "notify_team"}
        _check_keys(desired, fields, path)
        current_mapping = current if isinstance(current, Mapping) else {}
        complete = deep_merge(current_mapping, desired)
        if not isinstance(complete.get("enabled"), bool):
            raise ConfigError(f"{path}.enabled must be true or false")
        algorithm = complete.get("algorithm", "round_robin")
        if algorithm not in {"round_robin", "load_balance"}:
            raise ConfigError(
                f"{path}.algorithm must be 'round_robin' or 'load_balance'"
            )
        member_count = complete.get("member_count", 1)
        if type(member_count) is not int or member_count < 1:
            raise ConfigError(f"{path}.member_count must be a positive integer")
        notify_team = complete.get("notify_team", True)
        if not isinstance(notify_team, bool):
            raise ConfigError(f"{path}.notify_team must be true or false")
        changes = _leaf_changes(current_mapping, desired, path)
        if not changes:
            return
        self._add_graphql_operation(
            UPDATE_TEAM_REVIEW_ASSIGNMENT_MUTATION,
            "UpdateTeamReviewAssignmentConfiguration",
            changes,
            {
                "id": None,
                "enabled": complete["enabled"],
                "algorithm": algorithm.upper(),
                "teamMemberCount": member_count,
                "notifyTeam": notify_team,
            },
            phase=max(30, node_id_ready_phase),
            body_id_references=[(("input", "id"), ("teams", slug, "node_id"))],
            blocked_reason=(
                None
                if self.force or new_team
                else (
                    "GitHub does not return four review-assignment inputs. "
                    "Updating a returned field would reset those hidden inputs to "
                    "GitHub's defaults; pass --force to accept those resets"
                )
            ),
            warning_reason=(
                (
                    "GitHub does not return four review-assignment inputs. The new "
                    "team uses GitHub's documented defaults for excluded members, "
                    "child-team inclusion, existing-request counting, and "
                    "team-request removal."
                )
                if new_team
                else (
                    "GitHub does not return four review-assignment inputs. This "
                    "update resets excluded members, child-team inclusion, "
                    "existing-request counting, and team-request removal to "
                    "GitHub's documented defaults."
                )
            ),
        )

    def _plan_team_external_group(
        self,
        endpoint_slug: str,
        desired: Any,
        current: Any,
        *,
        display_slug: str,
        slug_reference: tuple[str, ...] | None,
        slug_ready_phase: int,
    ) -> None:
        path = f"organization.teams.{display_slug}.external_group"
        endpoint = (
            f"/orgs/{quote(self.org)}/teams/{quote(endpoint_slug)}/external-groups"
        )
        current_mapping = current if isinstance(current, Mapping) else None
        if desired is None:
            if current_mapping is None:
                return
            operation = Operation(
                "DELETE",
                endpoint,
                [FieldChange(path, current_mapping, None, "remove")],
                phase=max(70, slug_ready_phase),
            )
            self._add_team_slug_reference(operation, slug_reference)
            self.operations.append(operation)
            return
        if not isinstance(desired, Mapping):
            raise ConfigError(f"{path} must be a mapping or null")
        _check_keys(desired, {"group_id", "group_name"}, path)
        group_id = desired.get(
            "group_id",
            current_mapping.get("group_id") if current_mapping is not None else None,
        )
        if type(group_id) is not int:
            raise ConfigError(f"{path}.group_id must be an integer")
        current_group_id = (
            current_mapping.get("group_id") if current_mapping is not None else None
        )
        if current_group_id == group_id:
            return
        operation = Operation(
            "PATCH",
            endpoint,
            [
                FieldChange(
                    f"{path}.group_id",
                    current_group_id,
                    group_id,
                    "add" if current_mapping is None else "update",
                )
            ],
            body={"group_id": group_id},
            phase=max(30, slug_ready_phase),
        )
        self._add_team_slug_reference(operation, slug_reference)
        self.operations.append(operation)

    def _plan_team_sync_groups(
        self,
        endpoint_slug: str,
        desired: Any,
        current: Any,
        *,
        display_slug: str,
        slug_reference: tuple[str, ...] | None,
        slug_ready_phase: int,
    ) -> None:
        path = f"organization.teams.{display_slug}.team_sync_groups"
        if not isinstance(desired, list):
            raise ConfigError(f"{path} must be a list of mappings")
        writable_fields = {"group_id", "group_name", "group_description"}
        normalized_desired: list[dict[str, Any]] = []
        for index, group in enumerate(desired):
            if not isinstance(group, Mapping):
                raise ConfigError(f"{path}[{index}] must be a mapping")
            _check_keys(
                group,
                {*writable_fields, "status", "synced_at"},
                f"{path}[{index}]",
            )
            writable = pick(group, writable_fields)
            missing = writable_fields - set(writable)
            if missing:
                raise ConfigError(
                    f"{path}[{index}] is missing required keys: "
                    f"{', '.join(sorted(missing))}"
                )
            if not all(isinstance(writable[field], str) for field in writable_fields):
                raise ConfigError(
                    f"{path}[{index}] group_id, group_name, and group_description "
                    "must be strings"
                )
            normalized_desired.append(writable)
        normalized_current = (
            [
                pick(group, writable_fields)
                for group in current
                if isinstance(group, Mapping)
            ]
            if isinstance(current, list)
            else []
        )
        if normalized_current == normalized_desired:
            return
        endpoint = (
            f"/orgs/{quote(self.org)}/teams/{quote(endpoint_slug)}"
            "/team-sync/group-mappings"
        )
        operation = Operation(
            "PATCH",
            endpoint,
            [FieldChange(path, normalized_current, normalized_desired)],
            body={"groups": normalized_desired},
            phase=max(30, slug_ready_phase),
        )
        self._add_team_slug_reference(operation, slug_reference)
        self.operations.append(operation)

    @staticmethod
    def _add_team_slug_reference(
        operation: Operation, slug_reference: tuple[str, ...] | None
    ) -> None:
        if slug_reference is None:
            return
        encoded_slug = operation.endpoint.rsplit("/teams/", 1)[-1].split("/", 1)[0]
        operation.endpoint = operation.endpoint.replace(
            f"/teams/{encoded_slug}/", "/teams/__RESOLVED_TEAM_SLUG__/"
        )
        operation.endpoint_id_references.append(
            ("__RESOLVED_TEAM_SLUG__", slug_reference)
        )

    def _plan_organization_invitations(
        self, desired_value: Any, current_value: Any
    ) -> None:
        path = "organization.invitations"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for key, desired_item in desired.items():
            if not isinstance(desired_item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            desired_item = self._team_alias_mapping_paths(desired_item, (("teams",),))
            _check_keys(
                desired_item,
                {"login", "email", "role", "teams"},
                f"{path}.items.{key}",
            )
            current_item = current.get(key)
            if current_item is not None and not isinstance(current_item, dict):
                raise ConfigError(f"current.{path}.items.{key} must be a mapping")
            if isinstance(current_item, Mapping):
                current_item = self._team_alias_mapping_paths(
                    current_item, (("teams",),)
                )
            changes = (
                [FieldChange(f"{path}.{key}", None, desired_item, "add")]
                if current_item is None
                else _leaf_changes(current_item, desired_item, f"{path}.{key}")
            )
            if not changes:
                continue
            complete_item = deep_merge(current_item or {}, desired_item)
            body, user_reference, team_references = self._invitation_body(
                complete_item, f"{path}.items.{key}"
            )
            team_ready_phase = max(
                (
                    self.team_id_ready_phases.get(reference[-1], 0)
                    for reference in team_references
                ),
                default=0,
            )
            invitation_id = self.current.ids.get(("organization_invitations", key))
            blocked_reason = (
                None
                if current_item is None or invitation_id is not None
                else "the current organization invitation ID is unavailable"
            )
            replacement_phase = max(26, team_ready_phase)
            if current_item is not None:
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/invitations/{invitation_id if invitation_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current_item, None, "remove")],
                        phase=replacement_phase,
                        blocked_reason=blocked_reason,
                    )
                )
            operation = Operation(
                "POST",
                f"/orgs/{quote(self.org)}/invitations",
                [
                    FieldChange(
                        f"{path}.{key}",
                        current_item,
                        complete_item,
                        "add" if current_item is None else "update",
                    )
                ],
                body=body,
                phase=max(
                    27,
                    team_ready_phase,
                    replacement_phase + 1 if current_item is not None else 0,
                ),
                blocked_reason=blocked_reason,
                capture_id=("organization_invitations", key),
            )
            if user_reference is not None:
                operation.body_id_references.append((("invitee_id",), user_reference))
            operation.body_id_list_references.append((("team_ids",), team_references))
            self.operations.append(operation)
        if mode == "exact":
            for key in set(current) - set(desired):
                invitation_id = self.current.ids.get(("organization_invitations", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/invitations/{invitation_id if invitation_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if invitation_id is not None
                        else "the current organization invitation ID is unavailable",
                    )
                )

    def _invitation_body(
        self, item: Mapping[str, Any], path: str
    ) -> tuple[dict[str, Any], tuple[str, ...] | None, list[tuple[str, ...]]]:
        login = item.get("login")
        email = item.get("email")
        if login is not None and not isinstance(login, str):
            raise ConfigError(f"{path}.login must be a string")
        if email is not None and not isinstance(email, str):
            raise ConfigError(f"{path}.email must be a string")
        if not login and not email:
            raise ConfigError(f"{path} must contain login or email")
        role = item.get("role", "direct_member")
        if role not in ("admin", "direct_member", "billing_manager", "reinstate"):
            raise ConfigError(
                f"{path}.role must be admin, direct_member, billing_manager, or reinstate"
            )
        teams = item.get("teams", [])
        if not isinstance(teams, list) or not all(
            isinstance(team, str) for team in teams
        ):
            raise ConfigError(f"{path}.teams must be a list of team slugs")
        body: dict[str, Any] = {"role": role, "team_ids": []}
        user_reference: tuple[str, ...] | None = None
        if login:
            user_reference = self._ensure_user_id(login)
            body["invitee_id"] = None
        else:
            body["email"] = email
        return body, user_reference, [("teams", team) for team in teams]

    def _ensure_user_id(self, login: str) -> tuple[str, ...]:
        id_key = ("users", login.casefold())
        if id_key not in self.current.ids:
            user = self.api.request("GET", f"/users/{quote(login)}").data
            if isinstance(user, dict) and isinstance(user.get("id"), int):
                self.current.ids[id_key] = int(user["id"])
        return id_key

    def _plan_outside_collaborators(
        self,
        desired_value: Any,
        current_value: Any,
        current_members_value: Any,
        desired_members_value: Any,
    ) -> None:
        path = "organization.outside_collaborators"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        current_members = collection_items(
            current_members_value or {"items": {}}, "current.organization.members"
        )
        desired_members = (
            collection_items(desired_members_value, "organization.members")
            if desired_members_value is not None
            else {}
        )
        conflicts = set(desired) & set(desired_members)
        if conflicts:
            raise ConfigError(
                "The same login cannot be both an organization member and an outside "
                f"collaborator: {', '.join(sorted(conflicts))}"
            )
        for login, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{login} must be an empty mapping")
            _check_keys(item, set(), f"{path}.items.{login}")
            if login in current:
                continue
            self.operations.append(
                Operation(
                    "PUT",
                    f"/orgs/{quote(self.org)}/outside_collaborators/{quote(login)}",
                    [FieldChange(f"{path}.{login}", None, {}, "add")],
                    body={"async": False},
                    phase=90,
                    blocked_reason=None
                    if login in current_members
                    else "only a current organization member can be converted to an outside collaborator",
                )
            )
        if mode == "exact":
            for login in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/outside_collaborators/{quote(login)}",
                        [
                            FieldChange(
                                f"{path}.{login}", current[login], None, "remove"
                            )
                        ],
                        phase=95,
                    )
                )

    def _plan_personal_access_tokens(
        self, desired_value: Any, current_value: Any
    ) -> None:
        path = "organization.personal_access_tokens"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = {
            "owner",
            "token_name",
            "repository_selection",
            "repositories",
            "permissions",
            "token_expired",
            "token_expires_at",
        }
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, fields, f"{path}.items.{key}")
            current_item = current.get(key)
            if current_item is None:
                self._ignore_or_block_change(
                    f"{path}.{key}",
                    None,
                    item,
                    "GitHub only allows this API to revoke an existing personal "
                    "access token grant",
                    action="add",
                )
                continue
            changes = _leaf_changes(current_item, item, f"{path}.{key}")
            for change in changes:
                self._ignore_or_block_change(
                    change.path,
                    change.before,
                    change.after,
                    "GitHub only allows this API to revoke an existing personal "
                    "access token grant",
                    action=change.action,
                )
        if mode == "exact":
            for key in set(current) - set(desired):
                grant_id = self.current.ids.get(("personal_access_tokens", key))
                self.operations.append(
                    Operation(
                        "POST",
                        f"/orgs/{quote(self.org)}/personal-access-tokens/"
                        f"{grant_id if grant_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        body={"action": "revoke"},
                        phase=85,
                        blocked_reason=None
                        if grant_id is not None
                        else "the current personal access token grant ID is unavailable",
                        warning_reason=(
                            "Revocation cannot be undone through the API. The token "
                            "owner must create and authorize another token to restore "
                            "access."
                        ),
                    )
                )

    def _plan_credential_authorizations(
        self, desired_value: Any, current_value: Any
    ) -> None:
        path = "organization.credential_authorizations"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = {
            "login",
            "credential_type",
            "token_last_eight",
            "credential_authorized_at",
            "scopes",
            "fingerprint",
            "credential_accessed_at",
            "authorized_credential_id",
            "authorized_credential_title",
            "authorized_credential_note",
            "authorized_credential_expires_at",
        }
        for key, item in desired.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, fields, f"{path}.items.{key}")
            if key not in current:
                self._ignore_or_block_change(
                    f"{path}.{key}",
                    None,
                    item,
                    "GitHub only allows this API to revoke an existing credential "
                    "authorization",
                    action="add",
                )
                continue
            current_item = current[key]
            changes = _leaf_changes(current_item, item, f"{path}.{key}")
            for change in changes:
                self._ignore_or_block_change(
                    change.path,
                    change.before,
                    change.after,
                    "GitHub only allows this API to revoke an existing credential "
                    "authorization",
                    action=change.action,
                )
        if mode != "exact":
            return
        for key in set(current) - set(desired):
            credential_id = self.current.ids.get(("credential_authorizations", key))
            self.operations.append(
                Operation(
                    "DELETE",
                    f"/orgs/{quote(self.org)}/credential-authorizations/"
                    f"{credential_id if credential_id is not None else 'unknown'}",
                    [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                    phase=85,
                    blocked_reason=None
                    if credential_id is not None
                    else "the current credential authorization ID is unavailable",
                    warning_reason=(
                        "Revocation cannot be undone through the API. The credential "
                        "owner must authorize it again to restore organization access."
                    ),
                )
            )

    def _add_parent_reference(self, operation: Operation, parent: Any) -> None:
        if parent is None:
            if isinstance(operation.body, dict):
                operation.body["parent_team_id"] = None
            return
        if not isinstance(parent, str):
            raise ConfigError(
                f"{operation.changes[0].path}.settings.parent must be a team slug or null"
            )
        operation.body_id_references.append((("parent_team_id",), ("teams", parent)))

    def _logical_team_slug(self, slug: str) -> str:
        return self.team_current_to_logical.get(slug, slug)

    def _team_slug_resolution(
        self, slug: str
    ) -> tuple[str, str, tuple[str, ...] | None, int]:
        logical_slug = self._logical_team_slug(slug)
        reference = self.team_slug_references.get(logical_slug)
        if reference is not None:
            reference_key, ready_phase = reference
            return (
                logical_slug,
                "__RESOLVED_TEAM_SLUG__",
                reference_key,
                ready_phase,
            )
        return (
            logical_slug,
            self.team_logical_to_current.get(logical_slug, slug),
            None,
            0,
        )

    def _logical_team_slugs(self, values: Iterable[str]) -> list[str]:
        return [self._logical_team_slug(value) for value in values]

    def _resolve_team_slug_body_list(
        self, operation: Operation, path: tuple[str, ...]
    ) -> None:
        target = operation.body
        if not isinstance(target, dict):
            return
        for part in path[:-1]:
            value = target.get(part)
            if not isinstance(value, dict):
                return
            target = value
        values = target.get(path[-1])
        if not isinstance(values, list):
            return
        for index, value in enumerate(values):
            if not isinstance(value, str):
                continue
            _, api_slug, reference, ready_phase = self._team_slug_resolution(value)
            values[index] = api_slug
            operation.phase = max(operation.phase, ready_phase)
            if reference is not None:
                operation.body_id_references.append(((*path, index), reference))

    def _team_alias_mapping_paths(
        self,
        value: Mapping[str, Any],
        paths: Iterable[tuple[str, ...]],
    ) -> dict[str, Any]:
        normalized = copy.deepcopy(dict(value))
        for path in paths:
            target: Any = normalized
            for part in path[:-1]:
                if not isinstance(target, dict):
                    break
                target = target.get(part)
            else:
                if not isinstance(target, dict):
                    continue
                values = target.get(path[-1])
                if isinstance(values, list) and all(
                    isinstance(item, str) for item in values
                ):
                    target[path[-1]] = self._logical_team_slugs(values)
        return normalized

    def _team_alias_environment_settings(
        self, settings: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized = copy.deepcopy(dict(settings))
        reviewers = normalized.get("reviewers")
        if not isinstance(reviewers, list):
            return normalized
        for reviewer in reviewers:
            if (
                isinstance(reviewer, dict)
                and reviewer.get("type") == "team"
                and isinstance(reviewer.get("name"), str)
            ):
                reviewer["name"] = self._logical_team_slug(reviewer["name"])
        return normalized

    def _plan_team_members(
        self,
        slug: str,
        desired_value: Any,
        current_value: Any,
        *,
        display_slug: str | None = None,
        slug_reference: tuple[str, ...] | None = None,
        slug_ready_phase: int = 0,
    ) -> None:
        path = f"organization.teams.{display_slug or slug}.members"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        api_slug = (
            "__RESOLVED_TEAM_SLUG__" if slug_reference is not None else quote(slug)
        )
        for login, role in desired.items():
            if role not in ("member", "maintainer"):
                raise ConfigError(
                    f"{path}.items.{login} must be 'member' or 'maintainer'"
                )
            if current.get(login) != role:
                operation = Operation(
                    "PUT",
                    f"/orgs/{quote(self.org)}/teams/{api_slug}/memberships/{quote(login)}",
                    [
                        FieldChange(
                            f"{path}.{login}",
                            current.get(login),
                            role,
                            "add" if login not in current else "update",
                        )
                    ],
                    body={"role": role},
                    phase=max(20, slug_ready_phase),
                )
                if slug_reference is not None:
                    operation.endpoint_id_references.append(
                        ("__RESOLVED_TEAM_SLUG__", slug_reference)
                    )
                self.operations.append(operation)
        if mode == "exact":
            for login in set(current) - set(desired):
                operation = Operation(
                    "DELETE",
                    f"/orgs/{quote(self.org)}/teams/{api_slug}/memberships/{quote(login)}",
                    [FieldChange(f"{path}.{login}", current[login], None, "remove")],
                    phase=max(80, slug_ready_phase + 1),
                )
                if slug_reference is not None:
                    operation.endpoint_id_references.append(
                        ("__RESOLVED_TEAM_SLUG__", slug_reference)
                    )
                self.operations.append(operation)

    def _plan_team_repositories(
        self,
        slug: str,
        desired_value: Any,
        current_value: Any,
        *,
        display_slug: str | None = None,
        slug_reference: tuple[str, ...] | None = None,
        slug_ready_phase: int = 0,
    ) -> None:
        path = f"organization.teams.{display_slug or slug}.repositories"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        api_slug = (
            "__RESOLVED_TEAM_SLUG__" if slug_reference is not None else quote(slug)
        )
        valid = {"pull", "triage", "push", "maintain", "admin"}
        for repository, permission in desired.items():
            if not isinstance(permission, str):
                raise ConfigError(
                    f"{path}.items.{repository} must be a permission name"
                )
            if permission not in valid and not permission.strip():
                raise ConfigError(f"{path}.items.{repository} has an empty permission")
            if current.get(repository) != permission:
                operation = Operation(
                    "PUT",
                    f"/orgs/{quote(self.org)}/teams/{api_slug}/repos/{quote(self.org)}/{quote(repository)}",
                    [
                        FieldChange(
                            f"{path}.{repository}",
                            current.get(repository),
                            permission,
                            "add" if repository not in current else "update",
                        )
                    ],
                    body={"permission": permission},
                    phase=max(20, slug_ready_phase),
                    repository_names={repository},
                )
                if slug_reference is not None:
                    operation.endpoint_id_references.append(
                        ("__RESOLVED_TEAM_SLUG__", slug_reference)
                    )
                self.operations.append(operation)
        if mode == "exact":
            for repository in set(current) - set(desired):
                operation = Operation(
                    "DELETE",
                    f"/orgs/{quote(self.org)}/teams/{api_slug}/repos/{quote(self.org)}/{quote(repository)}",
                    [
                        FieldChange(
                            f"{path}.{repository}",
                            current[repository],
                            None,
                            "remove",
                        )
                    ],
                    phase=max(80, slug_ready_phase + 1),
                    repository_names={repository},
                )
                if slug_reference is not None:
                    operation.endpoint_id_references.append(
                        ("__RESOLVED_TEAM_SLUG__", slug_reference)
                    )
                self.operations.append(operation)

    def _plan_organization_roles(self, desired_value: Any, current_value: Any) -> None:
        path = "organization.organization_roles"
        desired = collection_items(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        mode = collection_mode(desired_value, path)
        for name, assignments in desired.items():
            if not isinstance(assignments, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(assignments, {"users", "teams"}, f"{path}.items.{name}")
            role_id = self.current.ids.get(("organization_roles", name))
            current_assignments = current.get(name, {})
            for actor_type, endpoint_part in (
                ("users", "users"),
                ("teams", "teams"),
            ):
                if actor_type not in assignments:
                    continue
                assignment_values = assignments.get(actor_type, [])
                if not isinstance(assignment_values, list) or not all(
                    isinstance(actor, str) for actor in assignment_values
                ):
                    raise ConfigError(
                        f"{path}.items.{name}.{actor_type} must be a list of names"
                    )
                current_values = current_assignments.get(actor_type, [])
                if not isinstance(current_values, list):
                    current_values = []
                if actor_type == "teams":
                    assignment_values = self._logical_team_slugs(assignment_values)
                    current_values = self._logical_team_slugs(
                        actor for actor in current_values if isinstance(actor, str)
                    )
                wanted = set(assignment_values)
                actual = set(current_values)
                for actor in wanted - actual:
                    endpoint, reference, ready_phase = self._role_assignment_target(
                        endpoint_part, actor, role_id
                    )
                    operation = Operation(
                        "PUT",
                        endpoint,
                        [
                            FieldChange(
                                f"{path}.{name}.{actor_type}.{actor}",
                                None,
                                True,
                                "add",
                            )
                        ],
                        phase=max(20, ready_phase),
                        blocked_reason=None
                        if role_id is not None
                        else f"organization role {name!r} does not exist in {self.org}",
                    )
                    if reference is not None:
                        operation.endpoint_id_references.append(
                            ("__RESOLVED_TEAM_SLUG__", reference)
                        )
                    self.operations.append(operation)
                if mode == "exact":
                    for actor in actual - wanted:
                        endpoint, reference, ready_phase = self._role_assignment_target(
                            endpoint_part, actor, role_id
                        )
                        operation = Operation(
                            "DELETE",
                            endpoint,
                            [
                                FieldChange(
                                    f"{path}.{name}.{actor_type}.{actor}",
                                    True,
                                    None,
                                    "remove",
                                )
                            ],
                            phase=max(80, ready_phase),
                            blocked_reason=None
                            if role_id is not None
                            else f"organization role {name!r} does not exist in {self.org}",
                        )
                        if reference is not None:
                            operation.endpoint_id_references.append(
                                ("__RESOLVED_TEAM_SLUG__", reference)
                            )
                        self.operations.append(operation)

        if mode == "exact":
            for name in set(current) - set(desired):
                role_id = self.current.ids.get(("organization_roles", name))
                current_assignments = current.get(name, {})
                if not isinstance(current_assignments, dict):
                    continue
                for actor_type, endpoint_part in (
                    ("users", "users"),
                    ("teams", "teams"),
                ):
                    actors = current_assignments.get(actor_type, [])
                    if not isinstance(actors, list):
                        continue
                    if actor_type == "teams":
                        actors = self._logical_team_slugs(
                            actor for actor in actors if isinstance(actor, str)
                        )
                    for actor in actors:
                        endpoint, reference, ready_phase = self._role_assignment_target(
                            endpoint_part, actor, role_id
                        )
                        operation = Operation(
                            "DELETE",
                            endpoint,
                            [
                                FieldChange(
                                    f"{path}.{name}.{actor_type}.{actor}",
                                    True,
                                    None,
                                    "remove",
                                )
                            ],
                            phase=max(80, ready_phase),
                            blocked_reason=None
                            if role_id is not None
                            else f"organization role {name!r} does not exist in {self.org}",
                        )
                        if reference is not None:
                            operation.endpoint_id_references.append(
                                ("__RESOLVED_TEAM_SLUG__", reference)
                            )
                        self.operations.append(operation)

    def _role_assignment_target(
        self, actor_type: str, actor: str, role_id: int | str | None
    ) -> tuple[str, tuple[str, ...] | None, int]:
        role = str(role_id) if role_id is not None else "unknown"
        reference = None
        ready_phase = 0
        if actor_type == "teams":
            _, actor, reference, ready_phase = self._team_slug_resolution(actor)
        endpoint = f"/orgs/{quote(self.org)}/organization-roles/{actor_type}/{quote(actor)}/{quote(role)}"
        return endpoint, reference, ready_phase

    def _plan_security_managers(self, desired: Any, current: Any) -> None:
        if not isinstance(desired, list) or not all(
            isinstance(team, str) for team in desired
        ):
            raise ConfigError(
                "organization.security_manager_teams must be a list of team slugs"
            )
        if not isinstance(current, list):
            current = []
        desired_teams = set(self._logical_team_slugs(desired))
        current_teams = set(
            self._logical_team_slugs(team for team in current if isinstance(team, str))
        )
        for method, teams, action, phase in (
            ("PUT", desired_teams - current_teams, "add", 20),
            ("DELETE", current_teams - desired_teams, "remove", 80),
        ):
            for team in teams:
                _, api_slug, reference, ready_phase = self._team_slug_resolution(team)
                operation = Operation(
                    method,
                    f"/orgs/{quote(self.org)}/security-managers/teams/{quote(api_slug)}",
                    [
                        FieldChange(
                            f"organization.security_manager_teams.{team}",
                            None if action == "add" else True,
                            True if action == "add" else None,
                            action,
                        )
                    ],
                    phase=max(phase, ready_phase),
                )
                if reference is not None:
                    operation.endpoint_id_references.append(
                        ("__RESOLVED_TEAM_SLUG__", reference)
                    )
                self.operations.append(operation)

    def _plan_variables(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        base: str,
        *,
        organization_level: bool,
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            variable_fields = {"value"}
            if organization_level:
                variable_fields.update({"visibility", "selected_repositories"})
            _check_keys(item, variable_fields, f"{path}.items.{name}")
            current_item = current.get(name)
            if current_item is None:
                required = {"value", "visibility"} if organization_level else {"value"}
                _require_keys(item, required, f"{path}.items.{name}")
                body = {"name": name, **pick(item, ("value", "visibility"))}
                operation = Operation(
                    "POST",
                    base,
                    [FieldChange(f"{path}.{name}", None, item, "add")],
                    body=body,
                    phase=10,
                )
                self._add_selected_repository_ids(operation, item, organization_level)
                self.operations.append(operation)
                continue
            if not isinstance(current_item, dict):
                current_item = {}
            changes = _leaf_changes(current_item, item, f"{path}.{name}")
            if changes:
                body = {
                    "name": name,
                    **pick(deep_merge(current_item, item), ("value", "visibility")),
                }
                operation = Operation(
                    "PATCH",
                    f"{base}/{quote(name)}",
                    changes,
                    body=body,
                    phase=30,
                )
                self._add_selected_repository_ids(operation, item, organization_level)
                self.operations.append(operation)
        if mode == "exact":
            for name in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                    )
                )

    def _plan_secrets(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        base: str,
        *,
        organization_level: bool,
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for name, item in desired.items():
            if item is None:
                item = {}
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            secret_fields = {"value_from_env"}
            if organization_level:
                secret_fields.update({"visibility", "selected_repositories"})
            _check_keys(item, secret_fields, f"{path}.items.{name}")
            current_item = current.get(name)
            current_item_mapping = (
                current_item if isinstance(current_item, dict) else {}
            )
            environment_name = item.get("value_from_env")
            if environment_name is not None and (
                not isinstance(environment_name, str) or not environment_name
            ):
                raise ConfigError(
                    f"{path}.items.{name}.value_from_env must be an environment variable name"
                )
            metadata = {
                key: value for key, value in item.items() if key != "value_from_env"
            }
            metadata_changes = _leaf_changes(
                current_item_mapping, metadata, f"{path}.{name}"
            )
            missing = name not in current
            if missing and organization_level:
                _require_keys(item, {"visibility"}, f"{path}.items.{name}")
            needs_value = missing or any(
                change.path.endswith(".visibility") for change in metadata_changes
            )
            if environment_name is not None or needs_value:
                body = (
                    pick(deep_merge(current_item_mapping, metadata), ("visibility",))
                    if organization_level
                    else {}
                )
                change = FieldChange(
                    f"{path}.{name}" if missing else f"{path}.{name}.value",
                    None if missing else "<write-only>",
                    f"${environment_name}" if environment_name else "<required>",
                    "add" if missing else "update",
                    sensitive=True,
                )
                operation = Operation(
                    "PUT",
                    f"{base}/{quote(name)}",
                    [change, *metadata_changes],
                    body=body,
                    phase=10 if missing else 30,
                    blocked_reason=(
                        None
                        if environment_name is not None
                        else "a missing or visibility-changing secret needs value_from_env"
                    ),
                    secret_environment=environment_name,
                    secret_public_key_endpoint=f"{base}/public-key",
                )
                self._add_selected_repository_ids(
                    operation, metadata, organization_level
                )
                self.operations.append(operation)
            elif metadata_changes:
                selected_changed = any(
                    change.path.endswith(".selected_repositories")
                    for change in metadata_changes
                )
                if selected_changed and organization_level:
                    operation = Operation(
                        "PUT",
                        f"{base}/{quote(name)}/repositories",
                        metadata_changes,
                        body={},
                        phase=30,
                    )
                    self._add_selected_repository_ids(operation, metadata, True)
                    self.operations.append(operation)
                else:
                    self.operations.append(
                        Operation(
                            "PUT",
                            f"{base}/{quote(name)}",
                            metadata_changes,
                            body=pick(metadata, ("visibility",)),
                            phase=30,
                            blocked_reason="changing this secret requires value_from_env",
                        )
                    )
        if mode == "exact":
            for name in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                    )
                )

    def _add_selected_repository_ids(
        self,
        operation: Operation,
        item: Mapping[str, Any],
        organization_level: bool,
    ) -> None:
        if not organization_level or "selected_repositories" not in item:
            return
        repositories = item["selected_repositories"]
        if not isinstance(repositories, list) or not all(
            isinstance(name, str) for name in repositories
        ):
            raise ConfigError(
                f"{operation.changes[0].path}.selected_repositories must be a list of repository names"
            )
        operation.body_id_list_references.append(
            (
                ("selected_repository_ids",),
                [("repositories", name) for name in repositories],
            )
        )

    def _plan_rulesets(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        base: str,
        id_prefix: tuple[str, ...],
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = (
            "name",
            "target",
            "enforcement",
            "bypass_actors",
            "conditions",
            "rules",
        )
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, set(fields), f"{path}.items.{key}")
            current_key = _match_current_key(
                key,
                item.get("name", key),
                current,
                lambda value: value.get("name") if isinstance(value, Mapping) else None,
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                ruleset_id = self.current.ids.get(id_prefix + ("rulesets", current_key))
                if ruleset_id is not None:
                    self.current.ids[id_prefix + ("rulesets", key)] = ruleset_id
            if current_item is None:
                _require_keys(item, {"enforcement"}, f"{path}.items.{key}")
                body = pick(_ruleset_writable_fields(item), fields)
                body.setdefault("name", key)
                self.operations.append(
                    Operation(
                        "POST",
                        f"{base}/rulesets",
                        [FieldChange(f"{path}.{key}", None, item, "add")],
                        body=body,
                        phase=10,
                        capture_id=id_prefix + ("rulesets", key),
                    )
                )
                continue
            writable_current = _ruleset_writable_fields(current_item)
            writable_desired = _ruleset_writable_fields(item)
            changes = _leaf_changes(writable_current, writable_desired, f"{path}.{key}")
            if changes:
                ruleset_id = self.current.ids.get(
                    id_prefix + ("rulesets", current_key or key)
                )
                self.operations.append(
                    Operation(
                        "PUT",
                        f"{base}/rulesets/{ruleset_id if ruleset_id is not None else 'unknown'}",
                        changes,
                        body=pick(
                            deep_merge(writable_current, writable_desired), fields
                        ),
                        phase=30,
                        blocked_reason=None
                        if ruleset_id is not None
                        else "the current ruleset ID is unavailable",
                    )
                )
        if mode == "exact":
            for key in set(current) - matched_current:
                ruleset_id = self.current.ids.get(id_prefix + ("rulesets", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/rulesets/{ruleset_id if ruleset_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if ruleset_id is not None
                        else "the current ruleset ID is unavailable",
                    )
                )

    def _plan_hooks(
        self,
        path: str,
        desired_value: Any,
        current_value: Any,
        base: str,
        id_prefix: tuple[str, ...],
    ) -> None:
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict) or not isinstance(
                item.get("config", {}), dict
            ):
                raise ConfigError(f"{path}.items.{key} must contain a config mapping")
            _check_keys(item, {"active", "events", "config"}, f"{path}.items.{key}")
            config_value = item.get("config", {})
            _check_keys(
                config_value,
                {"url", "content_type", "insecure_ssl", "secret_from_env"},
                f"{path}.items.{key}.config",
            )
            clean_item = copy.deepcopy(item)
            secret_environment = clean_item.get("config", {}).pop(
                "secret_from_env", None
            )
            if secret_environment is not None and (
                not isinstance(secret_environment, str) or not secret_environment
            ):
                raise ConfigError(
                    f"{path}.items.{key}.config.secret_from_env must name an environment variable"
                )
            desired_url = get_path(clean_item, "config.url")
            current_key = _match_current_key(
                key,
                desired_url,
                current,
                lambda value: (
                    get_path(value, "config.url")
                    if isinstance(value, Mapping)
                    else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                hook_id = self.current.ids.get(id_prefix + ("hooks", current_key))
                if hook_id is not None:
                    self.current.ids[id_prefix + ("hooks", key)] = hook_id
            if current_item is None:
                if not isinstance(config_value.get("url"), str):
                    raise ConfigError(
                        f"{path}.items.{key}.config.url is required when creating a webhook"
                    )
                body = {
                    "name": "web",
                    **pick(clean_item, ("active", "events", "config")),
                }
                operation = Operation(
                    "POST",
                    f"{base}/hooks",
                    [FieldChange(f"{path}.{key}", None, clean_item, "add")],
                    body=body,
                    phase=10,
                    capture_id=id_prefix + ("hooks", key),
                )
                if secret_environment:
                    operation.environment_fields.append(
                        (("config", "secret"), secret_environment)
                    )
                    operation.changes.append(
                        FieldChange(
                            f"{path}.{key}.config.secret",
                            None,
                            f"${secret_environment}",
                            "add",
                            sensitive=True,
                        )
                    )
                self.operations.append(operation)
                continue
            changes = _leaf_changes(current_item, clean_item, f"{path}.{key}")
            secret_change: FieldChange | None = None
            if secret_environment:
                secret_change = FieldChange(
                    f"{path}.{key}.config.secret",
                    "<write-only>",
                    f"${secret_environment}",
                    sensitive=True,
                )
            if changes or secret_change is not None:
                hook_id = self.current.ids.get(
                    id_prefix + ("hooks", current_key or key)
                )
                top_level_changes = [
                    change
                    for change in changes
                    if not change.path.startswith(f"{path}.{key}.config.")
                ]
                config_changes = [
                    change
                    for change in changes
                    if change.path.startswith(f"{path}.{key}.config.")
                ]
                if not top_level_changes:
                    desired_config = clean_item.get("config", {})
                    body = (
                        dict(desired_config)
                        if isinstance(desired_config, Mapping)
                        else {}
                    )
                    operation = Operation(
                        "PATCH",
                        f"{base}/hooks/"
                        f"{hook_id if hook_id is not None else 'unknown'}/config",
                        [
                            *config_changes,
                            *([secret_change] if secret_change is not None else []),
                        ],
                        body=body,
                        phase=30,
                        blocked_reason=None
                        if hook_id is not None
                        else "the current webhook ID is unavailable",
                    )
                    if secret_environment:
                        operation.environment_fields.append(
                            (("secret",), secret_environment)
                        )
                    self.operations.append(operation)
                    continue

                complete = deep_merge(current_item, clean_item)
                body = pick(complete, ("active", "events"))
                include_config = bool(config_changes) or secret_environment is not None
                if include_config:
                    body["config"] = complete.get("config", {})
                warning_parts: list[str] = []
                if secret_environment is None:
                    warning_parts.append(
                        "GitHub removes any existing write-only webhook secret "
                        "when active or event settings are updated; set "
                        "config.secret_from_env to preserve or replace it"
                    )
                if id_prefix == ("organization",) and include_config:
                    warning_parts.append(
                        "GitHub does not return organization webhook basic-auth "
                        "credentials, so replacing the config may remove them"
                    )
                operation = Operation(
                    "PATCH",
                    f"{base}/hooks/{hook_id if hook_id is not None else 'unknown'}",
                    [
                        *top_level_changes,
                        *config_changes,
                        *([secret_change] if secret_change is not None else []),
                    ],
                    body=body,
                    phase=30,
                    blocked_reason=None
                    if hook_id is not None
                    else "the current webhook ID is unavailable",
                    warning_reason=(
                        "; ".join(warning_parts) + "." if warning_parts else None
                    ),
                )
                if secret_environment:
                    operation.environment_fields.append(
                        (("config", "secret"), secret_environment)
                    )
                self.operations.append(operation)
        if mode == "exact":
            for key in set(current) - matched_current:
                hook_id = self.current.ids.get(id_prefix + ("hooks", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/hooks/{hook_id if hook_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if hook_id is not None
                        else "the current webhook ID is unavailable",
                    )
                )

    def _plan_custom_property_schema(
        self, desired_value: Any, current_value: Any
    ) -> None:
        path = "organization.custom_properties"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        fields = (
            "value_type",
            "required",
            "default_value",
            "description",
            "allowed_values",
            "values_editable_by",
            "require_explicit_values",
            "regex",
            "source_type",
        )
        rest_fields = tuple(
            field for field in fields if field not in {"regex", "source_type"}
        )
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(item, set(fields), f"{path}.items.{name}")
            current_item = current.get(name)
            if current_item is None:
                _require_keys(item, {"value_type"}, f"{path}.items.{name}")
            regex = item.get("regex")
            if regex is not None and not isinstance(regex, str):
                raise ConfigError(f"{path}.items.{name}.regex must be a string or null")
            if (
                regex is not None
                and item.get(
                    "value_type",
                    current_item.get("value_type")
                    if isinstance(current_item, Mapping)
                    else None,
                )
                != "string"
            ):
                raise ConfigError(
                    f"{path}.items.{name}.regex can be set only when value_type is "
                    "'string'"
                )
            if current_item is None and regex is not None:
                input_value = _custom_property_graphql_input(name, item)
                input_value["sourceId"] = None
                self._add_graphql_operation(
                    CREATE_CUSTOM_PROPERTY_MUTATION,
                    "CreateCustomPropertyConfiguration",
                    [FieldChange(f"{path}.{name}", None, item, "add")],
                    input_value,
                    phase=10,
                    body_id_references=[
                        (("input", "sourceId"), ("organization", "node_id"))
                    ],
                    capture_id=("custom_properties", name, "node_id"),
                    capture_response_path=(
                        "createRepositoryCustomProperty",
                        "repositoryCustomProperty",
                        "id",
                    ),
                )
                continue
            rest_desired = pick(item, rest_fields)
            rest_current = (
                pick(current_item, rest_fields)
                if isinstance(current_item, Mapping)
                else {}
            )
            changes = (
                [FieldChange(f"{path}.{name}", None, rest_desired, "add")]
                if current_item is None
                else _leaf_changes(rest_current, rest_desired, f"{path}.{name}")
            )
            if changes:
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/orgs/{quote(self.org)}/properties/schema/{quote(name)}",
                        changes,
                        body=pick(
                            deep_merge(
                                current_item
                                if isinstance(current_item, Mapping)
                                else {},
                                rest_desired,
                            ),
                            rest_fields,
                        ),
                        phase=10 if current_item is None else 30,
                    )
                )
            if (
                current_item is not None
                and "regex" in item
                and current_item.get("regex") != regex
            ):
                self._add_graphql_operation(
                    UPDATE_CUSTOM_PROPERTY_MUTATION,
                    "UpdateCustomPropertyConfiguration",
                    [
                        FieldChange(
                            f"{path}.{name}.regex",
                            current_item.get("regex"),
                            regex,
                        )
                    ],
                    {"repositoryCustomPropertyId": None, "regex": regex},
                    phase=30,
                    body_id_references=[
                        (
                            ("input", "repositoryCustomPropertyId"),
                            ("custom_properties", name, "node_id"),
                        )
                    ],
                )
        if mode == "exact":
            for name in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/orgs/{quote(self.org)}/properties/schema/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                    )
                )

    def _plan_organization_custom_property_values(
        self, desired_value: Any, current_value: Any
    ) -> None:
        path = "organization.custom_property_values"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        changes: list[FieldChange] = []
        properties: list[dict[str, Any]] = []
        for name, value in desired.items():
            _validate_custom_property_value(value, f"{path}.items.{name}")
            before = current.get(name, _UNSET)
            if before is _UNSET and value is None:
                continue
            if before is not _UNSET and before == value:
                continue
            action = (
                "add" if before is _UNSET else ("remove" if value is None else "update")
            )
            changes.append(
                FieldChange(
                    f"{path}.{name}",
                    None if before is _UNSET else before,
                    value,
                    action,
                )
            )
            properties.append({"property_name": name, "value": value})
        if mode == "exact":
            for name in sorted(set(current) - set(desired), key=str.casefold):
                changes.append(
                    FieldChange(
                        f"{path}.{name}",
                        current[name],
                        None,
                        "remove",
                    )
                )
                properties.append({"property_name": name, "value": None})
        if changes:
            self.operations.append(
                Operation(
                    "PATCH",
                    f"/organizations/{quote(self.org)}/org-properties/values",
                    changes,
                    body={"properties": properties},
                    phase=30,
                )
            )

    def _plan_graphql_repository_settings(
        self,
        repo: str,
        desired: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> None:
        configured = {
            key: desired[key] for key in _GRAPHQL_REPOSITORY_SETTINGS if key in desired
        }
        if not configured:
            return
        for key, value in configured.items():
            if key in {"has_discussions", "has_sponsorships"}:
                if not isinstance(value, bool):
                    raise ConfigError(
                        f"repositories.{repo}.settings.{key} must be true or false"
                    )
            elif value not in {"all", "collaborators_only"}:
                raise ConfigError(
                    f"repositories.{repo}.settings.issue_creation_policy must be "
                    "'all' or 'collaborators_only'"
                )
        changes = _leaf_changes(current, configured, f"repositories.{repo}.settings")
        if not changes:
            return
        input_value: dict[str, Any] = {"repositoryId": None}
        for key, value in configured.items():
            graphql_value = value.upper() if key == "issue_creation_policy" else value
            input_value[_GRAPHQL_REPOSITORY_SETTINGS[key]] = graphql_value
        self._add_graphql_operation(
            UPDATE_REPOSITORY_MUTATION,
            "UpdateRepositoryConfiguration",
            changes,
            input_value,
            phase=20,
            body_id_references=[
                (
                    ("input", "repositoryId"),
                    ("repositories", repo, "node_id"),
                )
            ],
        )

    def _plan_repository(
        self,
        name: str,
        desired: Mapping[str, Any],
        current_value: Mapping[str, Any] | None,
        *,
        current_name: str | None,
    ) -> None:
        operation_start = len(self.operations)
        path = f"repositories.{name}"
        _validate_repository_shape(desired, path)
        current_repository_settings = (
            current_value.get("settings")
            if isinstance(current_value, Mapping)
            else None
        )
        current_repository_facts = (
            current_value.get("_facts") if isinstance(current_value, Mapping) else None
        )
        if (
            isinstance(current_repository_settings, Mapping)
            and "archived" in current_repository_settings
        ):
            current_repository_is_archived = (
                current_repository_settings["archived"] is True
            )
        else:
            current_repository_is_archived = (
                isinstance(current_repository_facts, Mapping)
                and current_repository_facts.get("archived") is True
            )
        desired_settings = desired.get("settings")
        if isinstance(desired_settings, Mapping):
            current_settings = (
                current_value.get("settings")
                if isinstance(current_value, Mapping)
                else None
            )
            current_facts = (
                current_value.get("_facts")
                if isinstance(current_value, Mapping)
                else None
            )
            organization_allows_forking = self.organization_allows_forking
            if (
                desired_settings.get("allow_forking") is False
                and organization_allows_forking is False
                and self.current_organization_allows_forking is True
            ):
                organization_allows_forking = True
            if (
                isinstance(current_settings, Mapping)
                and "visibility" in current_settings
            ):
                validate_repository_settings_semantics(
                    desired_settings,
                    f"repositories.items.{name}.settings",
                    fallback_visibility=current_settings["visibility"],
                    require_known_visibility=True,
                    organization_allows_forking=organization_allows_forking,
                    organization_allows_projects=self.organization_allows_projects,
                )
            elif isinstance(current_facts, Mapping) and "visibility" in current_facts:
                validate_repository_settings_semantics(
                    desired_settings,
                    f"repositories.items.{name}.settings",
                    fallback_visibility=current_facts["visibility"],
                    require_known_visibility=True,
                    organization_allows_forking=organization_allows_forking,
                    organization_allows_projects=self.organization_allows_projects,
                )
            else:
                validate_repository_settings_semantics(
                    desired_settings,
                    f"repositories.items.{name}.settings",
                    require_known_visibility=True,
                    organization_allows_forking=organization_allows_forking,
                    organization_allows_projects=self.organization_allows_projects,
                )
        current: Mapping[str, Any]
        api_name = self._repository_api_name(name)
        if current_value is None:
            settings = desired.get("settings", {})
            if not isinstance(settings, dict):
                raise ConfigError(f"{path}.settings must be a mapping")
            request_settings = without_repository_response_only_nulls(settings)
            create_body = {
                "name": name,
                **pick(request_settings, CREATE_REPOSITORY_FIELDS),
            }
            self.operations.append(
                Operation(
                    "POST",
                    f"/orgs/{quote(self.org)}/repos",
                    [FieldChange(path, None, desired, "add")],
                    body=create_body,
                    phase=0,
                    capture_id=("repositories", name),
                    capture_response_values=[
                        (("repositories", name, "node_id"), ("node_id",))
                    ],
                )
            )
            current = {
                "settings": {
                    "name": name,
                    **pick(request_settings, CREATE_REPOSITORY_FIELDS),
                },
                "topics": [],
            }
        else:
            current = current_value
        base = f"/repos/{quote(self.org)}/{quote(api_name)}"
        if "settings" in desired:
            desired_settings = desired["settings"]
            if not isinstance(desired_settings, dict):
                raise ConfigError(f"{path}.settings must be a mapping")
            observed_settings = current.get("settings", {})
            current_settings = dict(
                observed_settings if isinstance(observed_settings, dict) else {}
            )
            current_settings = without_repository_response_metadata(current_settings)
            current_settings = without_repository_response_only_nulls(current_settings)
            if current_repository_is_archived:
                current_settings.setdefault("archived", True)
            desired_settings_for_update = without_repository_response_metadata(
                desired_settings
            )
            desired_settings_for_update = without_repository_response_only_nulls(
                desired_settings_for_update
            )
            self._plan_graphql_repository_settings(
                name,
                desired_settings_for_update,
                current_settings,
            )
            desired_settings_for_update = {
                key: value
                for key, value in desired_settings_for_update.items()
                if key not in _GRAPHQL_REPOSITORY_SETTINGS
                and key not in REPOSITORY_READ_ONLY_SETTINGS_FIELDS
            }
            if (
                desired_settings_for_update.get("allow_forking") is False
                and self.organization_allows_forking is False
                and self.current_organization_allows_forking is not True
            ):
                desired_settings_for_update.pop("allow_forking")
            final_archived = desired_settings_for_update.get(
                "archived", current_settings.get("archived")
            )
            settings_changes = _leaf_changes(
                current_settings,
                desired_settings_for_update,
                f"{path}.settings",
            )
            non_archive_changes = [
                change
                for change in settings_changes
                if change.path != f"{path}.settings.archived"
            ]
            unarchived_for_update = False
            if (
                current_settings.get("archived") is True
                and desired_settings_for_update.get("archived") is False
            ):
                current_base = (
                    f"/repos/{quote(self.org)}/{quote(current_name)}"
                    if current_name is not None
                    else base
                )
                self.operations.append(
                    Operation(
                        "PATCH",
                        current_base,
                        [FieldChange(f"{path}.settings.archived", True, False)],
                        body={"archived": False},
                        phase=1,
                    )
                )
                current_settings["archived"] = False
                desired_settings_for_update.pop("archived")
                unarchived_for_update = True
            if current_name is not None and api_name != current_name:
                old_base = f"/repos/{quote(self.org)}/{quote(current_name)}"
                self.operations.append(
                    Operation(
                        "PATCH",
                        old_base,
                        [FieldChange(f"{path}.settings.name", current_name, api_name)],
                        body={"name": api_name},
                        phase=2 if unarchived_for_update else 1,
                    )
                )
                current_settings["name"] = api_name
            archive_after_update = (
                final_archived is True
                and current_settings.get("archived") is not True
                and bool(non_archive_changes)
            )
            if archive_after_update:
                desired_settings_for_update.pop("archived", None)
            if (
                current_name is not None
                and "allow_forking" in desired_settings_for_update
                and current_settings.get("visibility") not in {"private", "internal"}
                and desired_settings_for_update.get("visibility")
                in {"private", "internal"}
            ):
                target_visibility = desired_settings_for_update.pop("visibility")
                self.operations.append(
                    Operation(
                        "PATCH",
                        base,
                        [
                            FieldChange(
                                f"{path}.settings.visibility",
                                current_settings.get("visibility"),
                                target_visibility,
                            )
                        ],
                        body={"visibility": target_visibility},
                        phase=10,
                    )
                )
                current_settings["visibility"] = target_visibility
            archive_phase = 20
            if desired_settings_for_update.get("archived") is True:
                archive_phase = 100
            elif (
                desired_settings_for_update.get("archived") is False
                and current_settings.get("archived") is True
            ):
                archive_phase = 1
            self._plan_mapping_endpoint(
                f"{path}.settings",
                current_settings,
                desired_settings_for_update,
                "PATCH",
                base,
                fields=REPOSITORY_SETTINGS_FIELDS,
                full_update=False,
                phase=archive_phase,
            )
            if archive_after_update:
                self.operations.append(
                    Operation(
                        "PATCH",
                        base,
                        [FieldChange(f"{path}.settings.archived", False, True)],
                        body={"archived": True},
                        phase=100,
                    )
                )
        if "topics" in desired:
            topics = desired["topics"]
            if not isinstance(topics, list) or not all(
                isinstance(topic, str) for topic in topics
            ):
                raise ConfigError(f"{path}.topics must be a list of strings")
            if sorted(current.get("topics", [])) != sorted(topics):
                self.operations.append(
                    Operation(
                        "PUT",
                        f"{base}/topics",
                        [
                            FieldChange(
                                f"{path}.topics", current.get("topics", []), topics
                            )
                        ],
                        body={"names": topics},
                        phase=20,
                    )
                )
        self._plan_singletons(
            desired,
            current,
            REPOSITORY_SINGLETONS,
            org=self.org,
            repo=api_name,
            prefix=path,
        )
        if "collaborators" in desired:
            self._plan_collaborators(
                name, desired["collaborators"], current.get("collaborators")
            )
        if "collaborator_invitations" in desired:
            self._plan_collaborator_invitations(
                name,
                desired["collaborator_invitations"],
                current.get("collaborator_invitations"),
                desired.get("collaborators"),
            )
        if "rulesets" in desired:
            self._plan_rulesets(
                f"{path}.rulesets",
                desired["rulesets"],
                current.get("rulesets"),
                base,
                ("repositories", name),
            )
        if "hooks" in desired:
            self._plan_hooks(
                f"{path}.hooks",
                desired["hooks"],
                current.get("hooks"),
                base,
                ("repositories", name),
            )
        if "deploy_keys" in desired:
            self._plan_deploy_keys(
                name, desired["deploy_keys"], current.get("deploy_keys")
            )
        if "autolinks" in desired:
            self._plan_autolinks(name, desired["autolinks"], current.get("autolinks"))
        if "labels" in desired:
            self._plan_labels(name, desired["labels"], current.get("labels"))
        if "branch_protections" in desired:
            if (
                "branch_protection_rules" in current
                and "branch_protections" not in current
            ):
                self.operations.append(
                    Operation(
                        "INCOMPLETE",
                        "",
                        [
                            FieldChange(
                                f"{path}.branch_protections",
                                current["branch_protection_rules"],
                                desired["branch_protections"],
                            )
                        ],
                        blocked_reason=(
                            "this file contains the incomplete REST branch "
                            "protection fallback, but the current token can read "
                            "the canonical rule collection; re-export the file "
                            "before applying branch protection changes"
                        ),
                    )
                )
            else:
                self._plan_branch_protections(
                    name,
                    desired["branch_protections"],
                    current.get("branch_protections"),
                )
        if "branch_protection_rules" in desired:
            self._plan_branch_protection_rules(
                name,
                desired["branch_protection_rules"],
                current.get("branch_protection_rules"),
            )
        if "environments" in desired:
            self._plan_environments(
                name, desired["environments"], current.get("environments")
            )
        for scope in ("actions", "agents"):
            desired_scope = desired.get(scope, {})
            current_scope = current.get(scope, {})
            if "variables" in desired_scope:
                self._plan_variables(
                    f"{path}.{scope}.variables",
                    desired_scope["variables"],
                    current_scope.get("variables"),
                    f"{base}/{scope}/variables",
                    organization_level=False,
                )
            if "secrets" in desired_scope:
                self._plan_secrets(
                    f"{path}.{scope}.secrets",
                    desired_scope["secrets"],
                    current_scope.get("secrets"),
                    f"{base}/{scope}/secrets",
                    organization_level=False,
                )
        desired_actions = desired.get("actions", {})
        current_actions = current.get("actions", {})
        if "self_hosted_runners" in desired_actions:
            self._plan_repo_self_hosted_runners(
                name,
                desired_actions["self_hosted_runners"],
                current_actions.get("self_hosted_runners"),
            )
        for scope in ("codespaces", "dependabot"):
            desired_scope = desired.get(scope, {})
            current_scope = current.get(scope, {})
            if "secrets" in desired_scope:
                self._plan_secrets(
                    f"{path}.{scope}.secrets",
                    desired_scope["secrets"],
                    current_scope.get("secrets"),
                    f"{base}/{scope}/secrets",
                    organization_level=False,
                )
        if "custom_properties" in desired:
            self._plan_repo_custom_properties(
                name, desired["custom_properties"], current.get("custom_properties")
            )
        if "security" in desired:
            self._plan_security_toggles(
                name, desired["security"], current.get("security", {})
            )
        if "pages" in desired:
            self._plan_pages(
                name, desired["pages"], current.get("pages", {"enabled": False})
            )
        if "workflow_states" in desired:
            self._plan_workflow_states(
                name, desired["workflow_states"], current.get("workflow_states")
            )
        if "interaction_limit" in desired:
            self._plan_interaction_limit(
                f"{path}.interaction_limit",
                desired["interaction_limit"],
                current.get("interaction_limit", {"enabled": False}),
                f"{base}/interaction-limits",
            )
        if "pull_request_creation_cap" in desired:
            self._plan_mapping_endpoint(
                f"{path}.pull_request_creation_cap",
                current.get("pull_request_creation_cap", {}),
                desired["pull_request_creation_cap"],
                "PATCH",
                f"{base}/interaction-limits/pulls/creation-cap",
                fields=("enabled", "max_open_pull_requests"),
                full_update=True,
            )
        if "pull_request_creation_cap_bypass_users" in desired:
            users = desired["pull_request_creation_cap_bypass_users"]
            if not isinstance(users, list) or not all(
                isinstance(user, str) for user in users
            ):
                raise ConfigError(
                    f"{path}.pull_request_creation_cap_bypass_users must be a list"
                )
            actual_users = current.get("pull_request_creation_cap_bypass_users", [])
            additions = sorted(set(users) - set(actual_users))
            removals = sorted(set(actual_users) - set(users))
            for method, changed_users, action in (
                ("PUT", additions, "add"),
                ("DELETE", removals, "remove"),
            ):
                if not changed_users:
                    continue
                self.operations.append(
                    Operation(
                        method,
                        f"{base}/interaction-limits/pulls/bypass-list",
                        [
                            FieldChange(
                                f"{path}.pull_request_creation_cap_bypass_users",
                                sorted(actual_users),
                                sorted(users),
                                action,
                            )
                        ],
                        body={"users": changed_users},
                        phase=30 if method == "PUT" else 80,
                    )
                )
        custom_patterns = get_path(desired, "secret_scanning.custom_patterns")
        if custom_patterns is not None:
            self._plan_custom_patterns(
                f"{path}.secret_scanning.custom_patterns",
                custom_patterns,
                get_path(current, "secret_scanning.custom_patterns"),
                f"{base}/secret-scanning",
                ("repositories", name),
            )
        for operation in self.operations[operation_start:]:
            operation.repository_names.add(name)

    def _plan_collaborators(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.collaborators"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for login, permission in desired.items():
            if not isinstance(permission, str):
                raise ConfigError(
                    f"{path}.items.{login} must be a permission or custom role name"
                )
            if current.get(login) != permission:
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/collaborators/{quote(login)}",
                        [
                            FieldChange(
                                f"{path}.{login}",
                                current.get(login),
                                permission,
                                "add" if login not in current else "update",
                            )
                        ],
                        body={"permission": permission},
                        phase=20,
                    )
                )
        if mode == "exact":
            for login in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/collaborators/{quote(login)}",
                        [
                            FieldChange(
                                f"{path}.{login}", current[login], None, "remove"
                            )
                        ],
                        phase=80,
                    )
                )

    def _plan_repo_self_hosted_runners(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.actions.self_hosted_runners"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(item, {"labels"}, f"{path}.items.{name}")
            labels = item.get("labels", [])
            if not isinstance(labels, list) or not all(
                isinstance(label, str) for label in labels
            ):
                raise ConfigError(f"{path}.items.{name}.labels must be a list")
            if name not in current:
                self.operations.append(
                    Operation(
                        "PUT",
                        "",
                        [FieldChange(f"{path}.{name}", None, item, "add")],
                        blocked_reason="self-hosted runners must register themselves before their settings can be applied",
                    )
                )
                continue
            current_item = current[name]
            current_labels = (
                current_item.get("labels", []) if isinstance(current_item, dict) else []
            )
            if sorted(current_labels) == sorted(labels):
                continue
            runner_id = self.current.ids.get(
                ("repositories", repo, "self_hosted_runners", name)
            )
            self.operations.append(
                Operation(
                    "PUT",
                    f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/actions/runners/"
                    f"{runner_id if runner_id is not None else 'unknown'}/labels",
                    [FieldChange(f"{path}.{name}.labels", current_labels, labels)],
                    body={"labels": labels},
                    phase=30,
                    blocked_reason=None
                    if runner_id is not None
                    else "the current runner ID is unavailable",
                )
            )
        if mode == "exact":
            for name in set(current) - set(desired):
                runner_id = self.current.ids.get(
                    ("repositories", repo, "self_hosted_runners", name)
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/actions/runners/"
                        f"{runner_id if runner_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=85,
                        blocked_reason=None
                        if runner_id is not None
                        else "the current runner ID is unavailable",
                    )
                )

    def _plan_collaborator_invitations(
        self,
        repo: str,
        desired_value: Any,
        current_value: Any,
        desired_collaborators_value: Any,
    ) -> None:
        path = f"repositories.{repo}.collaborator_invitations"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        desired_collaborators = (
            collection_items(
                desired_collaborators_value, f"repositories.{repo}.collaborators"
            )
            if desired_collaborators_value is not None
            else {}
        )
        conflicts = set(desired) & set(desired_collaborators)
        if conflicts:
            raise ConfigError(
                f"{path} also lists these users as collaborators: {', '.join(sorted(conflicts))}"
            )
        for login, permission in desired.items():
            if not isinstance(permission, str):
                raise ConfigError(f"{path}.items.{login} must be a permission name")
            if current.get(login) == permission:
                continue
            invitation_id = self.current.ids.get(
                ("repositories", repo, "invitations", login)
            )
            if login in current:
                self.operations.append(
                    Operation(
                        "PATCH",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/invitations/{invitation_id if invitation_id is not None else 'unknown'}",
                        [
                            FieldChange(
                                f"{path}.{login}",
                                current[login],
                                permission,
                            )
                        ],
                        body={"permissions": permission},
                        phase=30,
                        blocked_reason=None
                        if invitation_id is not None
                        else "the current repository invitation ID is unavailable",
                    )
                )
            else:
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/collaborators/{quote(login)}",
                        [FieldChange(f"{path}.{login}", None, permission, "add")],
                        body={"permission": permission},
                        phase=20,
                    )
                )
        if mode == "exact":
            for login in set(current) - set(desired):
                invitation_id = self.current.ids.get(
                    ("repositories", repo, "invitations", login)
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/invitations/{invitation_id if invitation_id is not None else 'unknown'}",
                        [
                            FieldChange(
                                f"{path}.{login}", current[login], None, "remove"
                            )
                        ],
                        phase=80,
                        blocked_reason=None
                        if invitation_id is not None
                        else "the current repository invitation ID is unavailable",
                    )
                )

    def _plan_deploy_keys(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.deploy_keys"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        matched_current: set[str] = set()
        for title, item in desired.items():
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                raise ConfigError(f"{path}.items.{title} must contain a public key")
            _check_keys(
                item,
                {"title", "key", "read_only"},
                f"{path}.items.{title}",
            )
            actual_title = item.get("title", title)
            if not isinstance(actual_title, str):
                raise ConfigError(f"{path}.items.{title}.title must be a string")
            current_key = _match_current_key(
                title,
                actual_title,
                current,
                lambda value: (
                    value.get("title") if isinstance(value, Mapping) else None
                ),
                f"{path}.items.{title}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                key_id = self.current.ids.get(
                    ("repositories", repo, "deploy_keys", current_key)
                )
                if key_id is not None:
                    self.current.ids[("repositories", repo, "deploy_keys", title)] = (
                        key_id
                    )
            if current_item is None:
                self.operations.append(
                    Operation(
                        "POST",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/keys",
                        [FieldChange(f"{path}.{title}", None, item, "add")],
                        body={
                            "title": actual_title,
                            **pick(item, ("key", "read_only")),
                        },
                        phase=20,
                        capture_id=("repositories", repo, "deploy_keys", title),
                    )
                )
            elif _leaf_changes(current_item, item, f"{path}.{title}"):
                key_id = self.current.ids.get(
                    ("repositories", repo, "deploy_keys", current_key or title)
                )
                blocked = (
                    None
                    if key_id is not None
                    else "the current deploy key ID is unavailable"
                )
                self.operations.extend(
                    [
                        Operation(
                            "DELETE",
                            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/keys/{key_id if key_id is not None else 'unknown'}",
                            [
                                FieldChange(
                                    f"{path}.{title}", current_item, None, "remove"
                                )
                            ],
                            phase=21,
                            blocked_reason=blocked,
                        ),
                        Operation(
                            "POST",
                            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/keys",
                            [FieldChange(f"{path}.{title}", current_item, item)],
                            body={
                                "title": actual_title,
                                **pick(item, ("key", "read_only")),
                            },
                            phase=22,
                            blocked_reason=blocked,
                            capture_id=("repositories", repo, "deploy_keys", title),
                        ),
                    ]
                )
        if mode == "exact":
            for title in set(current) - matched_current:
                key_id = self.current.ids.get(
                    ("repositories", repo, "deploy_keys", title)
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/keys/{key_id if key_id is not None else 'unknown'}",
                        [
                            FieldChange(
                                f"{path}.{title}", current[title], None, "remove"
                            )
                        ],
                        phase=80,
                        blocked_reason=None
                        if key_id is not None
                        else "the current deploy key ID is unavailable",
                    )
                )

    def _plan_autolinks(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.autolinks"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(
                item,
                {"key_prefix", "url_template", "is_alphanumeric"},
                f"{path}.items.{key}",
            )
            current_key = _match_current_key(
                key,
                item.get("key_prefix", key),
                current,
                lambda value: (
                    value.get("key_prefix") if isinstance(value, Mapping) else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                link_id = self.current.ids.get(
                    ("repositories", repo, "autolinks", current_key)
                )
                if link_id is not None:
                    self.current.ids[("repositories", repo, "autolinks", key)] = link_id
            if current_item is None:
                _require_keys(
                    item,
                    {"key_prefix", "url_template"},
                    f"{path}.items.{key}",
                )
            changes = (
                [FieldChange(f"{path}.{key}", None, item, "add")]
                if current_item is None
                else _leaf_changes(current_item, item, f"{path}.{key}")
            )
            if not changes:
                continue
            replacement = deep_merge(current_item or {}, item)
            _require_keys(
                replacement,
                {"key_prefix", "url_template"},
                f"{path}.items.{key}",
            )
            if current_item is not None:
                link_id = self.current.ids.get(
                    ("repositories", repo, "autolinks", current_key or key)
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/autolinks/{link_id if link_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current_item, None, "remove")],
                        phase=21,
                        blocked_reason=None
                        if link_id is not None
                        else "the current autolink ID is unavailable",
                    )
                )
            self.operations.append(
                Operation(
                    "POST",
                    f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/autolinks",
                    [
                        FieldChange(
                            f"{path}.{key}",
                            current_item,
                            replacement,
                            "add" if current_item is None else "update",
                        )
                    ],
                    body=pick(
                        replacement,
                        ("key_prefix", "url_template", "is_alphanumeric"),
                    ),
                    phase=22,
                    capture_id=("repositories", repo, "autolinks", key),
                )
            )
        if mode == "exact":
            for key in set(current) - matched_current:
                link_id = self.current.ids.get(("repositories", repo, "autolinks", key))
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/autolinks/{link_id if link_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=80,
                        blocked_reason=None
                        if link_id is not None
                        else "the current autolink ID is unavailable",
                    )
                )

    def _plan_labels(self, repo: str, desired_value: Any, current_value: Any) -> None:
        path = f"repositories.{repo}.labels"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        matched_current: set[str] = set()
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(item, {"name", "color", "description"}, f"{path}.items.{name}")
            request_item = _without_null_fields(item, ("description",))
            actual_name = item.get("name", name)
            if not isinstance(actual_name, str):
                raise ConfigError(f"{path}.items.{name}.name must be a string")
            current_key = _match_current_key(
                name,
                actual_name,
                current,
                lambda value: value.get("name") if isinstance(value, Mapping) else None,
                f"{path}.items.{name}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
            if current_item is None:
                self.operations.append(
                    Operation(
                        "POST",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/labels",
                        [FieldChange(f"{path}.{name}", None, item, "add")],
                        body={
                            "name": actual_name,
                            **pick(request_item, ("color", "description")),
                        },
                        phase=20,
                    )
                )
            else:
                current_request_item = _without_null_fields(
                    current_item if isinstance(current_item, Mapping) else {},
                    ("description",),
                )
                changes = _leaf_changes(
                    current_request_item, request_item, f"{path}.{name}"
                )
                if changes:
                    self.operations.append(
                        Operation(
                            "PATCH",
                            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/labels/{quote(current_key or name)}",
                            changes,
                            body={
                                "new_name": actual_name,
                                **pick(
                                    deep_merge(current_request_item, request_item),
                                    ("color", "description"),
                                ),
                            },
                            phase=30,
                        )
                    )
        if mode == "exact":
            for name in set(current) - matched_current:
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/labels/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=80,
                    )
                )

    def _plan_branch_protection_rules(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.branch_protection_rules"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        known = {
            *_BRANCH_PROTECTION_RULE_FIELDS,
            *_BRANCH_PROTECTION_ACTOR_INPUTS,
            "required_status_checks",
        }
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, Mapping):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, known, f"{path}.items.{key}")
            pattern = item.get("pattern", key.split("#github-id-", 1)[0])
            if not isinstance(pattern, str):
                raise ConfigError(f"{path}.items.{key}.pattern must be a string")
            current_key = _match_current_key(
                key,
                pattern,
                current,
                lambda value: (
                    value.get("pattern") if isinstance(value, Mapping) else None
                ),
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key is not None else None
            if current_key is not None:
                if current_key in matched_current:
                    raise ConfigError(
                        f"{path}.items.{key} and another configured rule both "
                        f"identify current branch protection rule {current_key!r}"
                    )
                matched_current.add(current_key)
            changes = (
                [FieldChange(f"{path}.{key}", None, item, "add")]
                if current_item is None
                else _leaf_changes(current_item, item, f"{path}.{key}")
            )
            if not changes:
                continue
            input_value: dict[str, Any] = {}
            for config_key, graphql_key in _BRANCH_PROTECTION_RULE_FIELDS.items():
                if config_key in item:
                    input_value[graphql_key] = item[config_key]
            input_value.setdefault("pattern", pattern)
            body_id_references: list[tuple[tuple[str | int, ...], tuple[str, ...]]] = []
            body_id_list_references: list[
                tuple[tuple[str | int, ...], list[tuple[str, ...]]]
            ] = []
            for config_key, graphql_key in _BRANCH_PROTECTION_ACTOR_INPUTS.items():
                if config_key not in item:
                    continue
                actors = item[config_key]
                if not isinstance(actors, list) or not all(
                    isinstance(actor, str) for actor in actors
                ):
                    raise ConfigError(
                        f"{path}.items.{key}.{config_key} must be a list of "
                        "user:, team:, or app: references"
                    )
                actor_ids = [
                    self._ensure_branch_protection_actor_id(
                        actor, f"{path}.items.{key}.{config_key}"
                    )
                    for actor in actors
                ]
                input_value[graphql_key] = []
                body_id_list_references.append((("input", graphql_key), actor_ids))
            if "required_status_checks" in item:
                checks = item["required_status_checks"]
                if not isinstance(checks, list):
                    raise ConfigError(
                        f"{path}.items.{key}.required_status_checks must be a "
                        "list of mappings"
                    )
                checks_changed = (
                    current_item is None
                    or not isinstance(current_item, Mapping)
                    or current_item.get("required_status_checks") != checks
                )
                input_checks: list[dict[str, Any]] = []
                for index, check in enumerate(checks):
                    if not isinstance(check, Mapping):
                        raise ConfigError(
                            f"{path}.items.{key}.required_status_checks[{index}] "
                            "must be a mapping"
                        )
                    _check_keys(
                        check,
                        {"context", "app"},
                        f"{path}.items.{key}.required_status_checks[{index}]",
                    )
                    context = check.get("context")
                    if not isinstance(context, str):
                        raise ConfigError(
                            f"{path}.items.{key}.required_status_checks[{index}]"
                            ".context must be a string"
                        )
                    app = check.get("app")
                    if app is not None and not isinstance(app, str):
                        raise ConfigError(
                            f"{path}.items.{key}.required_status_checks"
                            f"[{index}].app must be a string or null"
                        )
                    if not checks_changed:
                        continue
                    if app is None:
                        raise ConfigError(
                            f"{path}.items.{key}.required_status_checks[{index}]"
                            ".app is ambiguous in GitHub's API response; set it "
                            "to an App slug, 'any', or 'recent' before changing "
                            "this status-check list"
                        )
                    input_check: dict[str, Any] = {"context": context}
                    if app != "recent":
                        if app == "any":
                            input_check["appId"] = "any"
                        else:
                            input_check["appId"] = None
                            body_id_references.append(
                                (
                                    (
                                        "input",
                                        "requiredStatusChecks",
                                        index,
                                        "appId",
                                    ),
                                    self._ensure_app_node_id(app),
                                )
                            )
                    input_checks.append(input_check)
                if checks_changed:
                    input_value["requiredStatusChecks"] = input_checks
            if current_item is None:
                input_value["repositoryId"] = None
                self._add_graphql_operation(
                    CREATE_BRANCH_PROTECTION_RULE_MUTATION,
                    "CreateBranchProtectionRuleConfiguration",
                    changes,
                    input_value,
                    phase=20,
                    body_id_references=[
                        (
                            ("input", "repositoryId"),
                            ("repositories", repo, "node_id"),
                        ),
                        *body_id_references,
                    ],
                    body_id_list_references=body_id_list_references,
                    capture_id=(
                        "repositories",
                        repo,
                        "branch_protection_rules",
                        key,
                    ),
                    capture_response_path=(
                        "createBranchProtectionRule",
                        "branchProtectionRule",
                        "id",
                    ),
                )
            else:
                input_value["branchProtectionRuleId"] = None
                self._add_graphql_operation(
                    UPDATE_BRANCH_PROTECTION_RULE_MUTATION,
                    "UpdateBranchProtectionRuleConfiguration",
                    changes,
                    input_value,
                    phase=30,
                    body_id_references=[
                        (
                            ("input", "branchProtectionRuleId"),
                            (
                                "repositories",
                                repo,
                                "branch_protection_rules",
                                current_key or key,
                            ),
                        ),
                        *body_id_references,
                    ],
                    body_id_list_references=body_id_list_references,
                )
        if mode == "exact":
            for key in set(current) - matched_current:
                self._add_graphql_operation(
                    DELETE_BRANCH_PROTECTION_RULE_MUTATION,
                    "DeleteBranchProtectionRuleConfiguration",
                    [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                    {"branchProtectionRuleId": None},
                    phase=80,
                    body_id_references=[
                        (
                            ("input", "branchProtectionRuleId"),
                            (
                                "repositories",
                                repo,
                                "branch_protection_rules",
                                key,
                            ),
                        )
                    ],
                )

    def _ensure_branch_protection_actor_id(
        self, reference: str, path: str
    ) -> tuple[str, ...]:
        id_key = _branch_protection_actor_id_key(reference, path)
        if id_key in self.current.ids:
            return id_key
        actor_type, identity = reference.split(":", 1)
        existing_key: tuple[str, ...] | None = None
        if actor_type == "user":
            existing_key = ("users", identity.casefold(), "node_id")
        elif actor_type == "team":
            existing_key = next(
                (
                    key
                    for key in self.current.ids
                    if len(key) == 3
                    and key[0] == "teams"
                    and key[1].casefold() == identity.casefold()
                    and key[2] == "node_id"
                ),
                None,
            )
        else:
            existing_key = ("apps", identity, "node_id")
        if existing_key is not None and existing_key in self.current.ids:
            self.current.ids[id_key] = self.current.ids[existing_key]
            return id_key

        endpoint = (
            f"/users/{quote(identity)}"
            if actor_type == "user"
            else (
                f"/orgs/{quote(self.org)}/teams/{quote(identity)}"
                if actor_type == "team"
                else f"/apps/{quote(identity)}"
            )
        )
        try:
            actor = self.api.request("GET", endpoint).data
        except ApiError:
            return id_key
        if isinstance(actor, Mapping) and isinstance(actor.get("node_id"), str):
            self.current.ids[id_key] = actor["node_id"]
        return id_key

    def _ensure_app_node_id(self, slug: str) -> tuple[str, ...]:
        id_key = ("apps", slug, "node_id")
        if id_key in self.current.ids:
            return id_key
        try:
            app = self.api.request("GET", f"/apps/{quote(slug)}").data
        except ApiError:
            return id_key
        if isinstance(app, Mapping) and isinstance(app.get("node_id"), str):
            self.current.ids[id_key] = app["node_id"]
        return id_key

    def _plan_branch_protections(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.branch_protections"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for branch, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{branch} must be a mapping")
            _check_keys(
                item,
                {
                    "required_status_checks",
                    "enforce_admins",
                    "required_pull_request_reviews",
                    "restrictions",
                    "required_linear_history",
                    "allow_force_pushes",
                    "allow_deletions",
                    "block_creations",
                    "required_conversation_resolution",
                    "lock_branch",
                    "allow_fork_syncing",
                    "required_signatures",
                },
                f"{path}.items.{branch}",
            )
            current_item = current.get(branch)
            current_mapping = current_item if isinstance(current_item, dict) else {}
            current_mapping = self._team_alias_mapping_paths(
                current_mapping, BRANCH_PROTECTION_TEAM_PATHS
            )
            desired_mapping = self._team_alias_mapping_paths(
                item, BRANCH_PROTECTION_TEAM_PATHS
            )
            current_request_mapping = _branch_protection_writable_item(current_mapping)
            request_item = _branch_protection_desired_item(
                desired_mapping, current_mapping
            )
            if current_item is None:
                _require_keys(
                    item,
                    {
                        "required_status_checks",
                        "enforce_admins",
                        "required_pull_request_reviews",
                        "restrictions",
                    },
                    f"{path}.items.{branch}",
                )
            changes = (
                [FieldChange(f"{path}.{branch}", None, request_item, "add")]
                if current_item is None
                else _leaf_changes(
                    current_request_mapping, request_item, f"{path}.{branch}"
                )
            )
            signature_changes = [
                change for change in changes if ".required_signatures" in change.path
            ]
            protection_changes = [
                change for change in changes if change not in signature_changes
            ]
            endpoint = f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/branches/{quote(branch)}/protection"
            if protection_changes or (current_item is None and not signature_changes):
                merged = deep_merge(current_request_mapping, request_item)
                body = _branch_protection_writable_item(
                    {
                        key: value
                        for key, value in merged.items()
                        if key != "required_signatures"
                    }
                )
                operation = Operation(
                    "PUT",
                    endpoint,
                    protection_changes or changes,
                    body=body,
                    phase=30,
                )
                for team_path in BRANCH_PROTECTION_TEAM_PATHS:
                    self._resolve_team_slug_body_list(operation, team_path)
                self.operations.append(operation)
            if signature_changes:
                enabled = bool(request_item.get("required_signatures"))
                self.operations.append(
                    Operation(
                        "POST" if enabled else "DELETE",
                        f"{endpoint}/required_signatures",
                        signature_changes,
                        phase=31,
                    )
                )
        if mode == "exact":
            for branch in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/branches/{quote(branch)}/protection",
                        [
                            FieldChange(
                                f"{path}.{branch}", current[branch], None, "remove"
                            )
                        ],
                        phase=80,
                    )
                )

    def _plan_environments(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.environments"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for name, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{name} must be a mapping")
            _check_keys(
                item,
                {
                    "settings",
                    "branch_policies",
                    "deployment_protection_rules",
                    "variables",
                    "secrets",
                    "pinned",
                    "pinned_position",
                },
                f"{path}.items.{name}",
            )
            current_item = current.get(name)
            current_mapping = current_item if isinstance(current_item, dict) else {}
            base = f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/environments/{quote(name)}"
            if current_item is None or "settings" in item:
                settings = item.get("settings", {})
                if not isinstance(settings, dict):
                    raise ConfigError(f"{path}.items.{name}.settings must be a mapping")
                settings = self._team_alias_environment_settings(
                    _environment_settings(settings)
                )
                _check_keys(
                    settings,
                    {
                        "wait_timer",
                        "prevent_self_review",
                        "reviewers",
                        "deployment_branch_policy",
                    },
                    f"{path}.items.{name}.settings",
                )
                if "reviewers" in settings and settings["reviewers"] is None:
                    settings = {**settings, "reviewers": []}
                deployment_policy = settings.get("deployment_branch_policy")
                if deployment_policy is not None:
                    if not isinstance(deployment_policy, dict):
                        raise ConfigError(
                            f"{path}.items.{name}.settings.deployment_branch_policy must be a mapping"
                        )
                    _check_keys(
                        deployment_policy,
                        {"protected_branches", "custom_branch_policies"},
                        f"{path}.items.{name}.settings.deployment_branch_policy",
                    )
                current_settings_value = current_mapping.get("settings", {})
                current_settings = self._team_alias_environment_settings(
                    _environment_settings(
                        current_settings_value
                        if isinstance(current_settings_value, Mapping)
                        else {}
                    )
                )
                complete_settings = deep_merge(current_settings, settings)
                complete_deployment_policy = complete_settings.get(
                    "deployment_branch_policy"
                )
                if complete_deployment_policy is not None:
                    if not isinstance(complete_deployment_policy, dict):
                        raise ConfigError(
                            f"{path}.items.{name}.settings.deployment_branch_policy "
                            "must be a mapping or null"
                        )
                    protected_branches = complete_deployment_policy.get(
                        "protected_branches"
                    )
                    custom_branch_policies = complete_deployment_policy.get(
                        "custom_branch_policies"
                    )
                    if (
                        not isinstance(protected_branches, bool)
                        or not isinstance(custom_branch_policies, bool)
                        or protected_branches == custom_branch_policies
                    ):
                        raise ConfigError(
                            f"{path}.items.{name}.settings.deployment_branch_policy "
                            "must set exactly one of protected_branches and "
                            "custom_branch_policies to true"
                        )
                changes = (
                    [FieldChange(f"{path}.{name}.settings", None, settings, "add")]
                    if current_item is None
                    else _leaf_changes(
                        current_settings, settings, f"{path}.{name}.settings"
                    )
                )
                if changes:
                    body, references = self._environment_body(
                        complete_settings, path, name
                    )
                    team_ready_phase = max(
                        (
                            self.team_id_ready_phases.get(reference[-1], 0)
                            for _, reference in references
                            if reference and reference[0] == "teams"
                        ),
                        default=0,
                    )
                    operation = Operation(
                        "PUT",
                        base,
                        changes,
                        body=body,
                        body_id_references=references,
                        phase=max(
                            5 if current_item is None else 20,
                            team_ready_phase,
                        ),
                    )
                    if current_item is None:
                        operation.capture_response_values.append(
                            (
                                (
                                    "repositories",
                                    repo,
                                    "environments",
                                    name,
                                    "node_id",
                                ),
                                ("node_id",),
                            )
                        )
                    self.operations.append(operation)
            self._plan_environment_pin(
                repo,
                name,
                item,
                current_mapping,
                created=current_item is None,
            )
            if "branch_policies" in item:
                self._plan_environment_branch_policies(
                    repo,
                    name,
                    item["branch_policies"],
                    current_mapping.get("branch_policies"),
                )
            if "deployment_protection_rules" in item:
                self._plan_environment_protection_rules(
                    repo,
                    name,
                    item["deployment_protection_rules"],
                    current_mapping.get("deployment_protection_rules"),
                )
            if "variables" in item:
                self._plan_variables(
                    f"{path}.{name}.variables",
                    item["variables"],
                    current_mapping.get("variables"),
                    f"{base}/variables",
                    organization_level=False,
                )
            if "secrets" in item:
                self._plan_secrets(
                    f"{path}.{name}.secrets",
                    item["secrets"],
                    current_mapping.get("secrets"),
                    f"{base}/secrets",
                    organization_level=False,
                )
        if mode == "exact":
            for name in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/environments/{quote(name)}",
                        [FieldChange(f"{path}.{name}", current[name], None, "remove")],
                        phase=80,
                    )
                )

    def _plan_environment_pin(
        self,
        repo: str,
        name: str,
        desired: Mapping[str, Any],
        current: Mapping[str, Any],
        *,
        created: bool,
    ) -> None:
        path = f"repositories.{repo}.environments.{name}"
        if "pinned" not in desired and "pinned_position" not in desired:
            return
        pinned = desired.get("pinned", current.get("pinned"))
        if not isinstance(pinned, bool):
            raise ConfigError(f"{path}.pinned must be true or false")
        position = desired.get("pinned_position", current.get("pinned_position"))
        if position is not None and type(position) is not int:
            raise ConfigError(f"{path}.pinned_position must be an integer or null")
        if not pinned and position is not None:
            raise ConfigError(
                f"{path}.pinned_position must be null when pinned is false"
            )
        id_key = (
            "repositories",
            repo,
            "environments",
            name,
            "node_id",
        )
        if current.get("pinned") != pinned:
            self._add_graphql_operation(
                PIN_ENVIRONMENT_MUTATION,
                "PinEnvironmentConfiguration",
                [FieldChange(f"{path}.pinned", current.get("pinned"), pinned)],
                {"environmentId": None, "pinned": pinned},
                phase=25 if created or pinned else 70,
                body_id_references=[(("input", "environmentId"), id_key)],
            )
        if (
            pinned
            and "pinned_position" in desired
            and current.get("pinned_position") != position
        ):
            if position is None:
                raise ConfigError(
                    f"{path}.pinned_position must be an integer when pinned is true"
                )
            self._add_graphql_operation(
                REORDER_ENVIRONMENT_MUTATION,
                "ReorderEnvironmentConfiguration",
                [
                    FieldChange(
                        f"{path}.pinned_position",
                        current.get("pinned_position"),
                        position,
                    )
                ],
                {"environmentId": None, "position": position},
                phase=30,
                body_id_references=[(("input", "environmentId"), id_key)],
            )

    def _environment_body(
        self,
        settings: Mapping[str, Any],
        path: str,
        environment: str,
    ) -> tuple[dict[str, Any], list[tuple[tuple[str | int, ...], tuple[str, ...]]]]:
        body = pick(
            settings,
            ("wait_timer", "prevent_self_review", "deployment_branch_policy"),
        )
        reviewers = settings.get("reviewers", [])
        if reviewers is None:
            reviewers = []
        if not isinstance(reviewers, list):
            raise ConfigError(
                f"{path}.items.{environment}.settings.reviewers must be a list"
            )
        body["reviewers"] = []
        references: list[tuple[tuple[str | int, ...], tuple[str, ...]]] = []
        for index, reviewer in enumerate(reviewers):
            if not isinstance(reviewer, dict) or reviewer.get("type") not in (
                "user",
                "team",
            ):
                raise ConfigError(
                    f"{path}.items.{environment}.settings.reviewers entries need type 'user' or 'team' and a name"
                )
            name = reviewer.get("name")
            if not isinstance(name, str):
                raise ConfigError(
                    f"{path}.items.{environment}.settings.reviewers entries need a name"
                )
            actor_type = str(reviewer["type"])
            body["reviewers"].append({"type": actor_type.capitalize(), "id": None})
            id_key = (
                ("teams", name) if actor_type == "team" else ("users", name.casefold())
            )
            if actor_type == "user" and id_key not in self.current.ids:
                user = self.api.request("GET", f"/users/{quote(name)}").data
                if isinstance(user, dict) and "id" in user:
                    self.current.ids[id_key] = int(user["id"])
            references.append((("reviewers", index, "id"), id_key))
        return body, references

    def _plan_environment_protection_rules(
        self,
        repo: str,
        environment: str,
        desired_value: Any,
        current_value: Any,
    ) -> None:
        path = f"repositories.{repo}.environments.{environment}.deployment_protection_rules"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        base = (
            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/environments/{quote(environment)}"
            "/deployment_protection_rules"
        )
        for app_slug, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{app_slug} must be a mapping")
            _check_keys(item, {"enabled"}, f"{path}.items.{app_slug}")
            if item.get("enabled", True) is not True:
                if app_slug in current:
                    rule_id = self.current.ids.get(
                        (
                            "repositories",
                            repo,
                            "environments",
                            environment,
                            "protection_rules",
                            app_slug,
                        )
                    )
                    self.operations.append(
                        Operation(
                            "DELETE",
                            f"{base}/{rule_id if rule_id is not None else 'unknown'}",
                            [FieldChange(f"{path}.{app_slug}.enabled", True, False)],
                            phase=80,
                            blocked_reason=None
                            if rule_id is not None
                            else "the protection rule ID is unavailable",
                        )
                    )
                continue
            if app_slug in current:
                continue
            app_id = self.current.ids.get(("apps", app_slug))
            if app_id is None:
                try:
                    app = self.api.request("GET", f"/apps/{quote(app_slug)}").data
                except ApiError as error:
                    if error.status != 404:
                        raise
                else:
                    if isinstance(app, dict) and "id" in app:
                        app_id = int(app["id"])
                        self.current.ids[("apps", app_slug)] = app_id
            self.operations.append(
                Operation(
                    "POST",
                    base,
                    [FieldChange(f"{path}.{app_slug}", None, item, "add")],
                    body={"integration_id": app_id},
                    phase=30,
                    blocked_reason=None
                    if app_id is not None
                    else f"GitHub App {app_slug!r} was not found",
                )
            )
        if mode == "exact":
            for app_slug in set(current) - set(desired):
                rule_id = self.current.ids.get(
                    (
                        "repositories",
                        repo,
                        "environments",
                        environment,
                        "protection_rules",
                        app_slug,
                    )
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{rule_id if rule_id is not None else 'unknown'}",
                        [
                            FieldChange(
                                f"{path}.{app_slug}", current[app_slug], None, "remove"
                            )
                        ],
                        phase=80,
                        blocked_reason=None
                        if rule_id is not None
                        else "the protection rule ID is unavailable",
                    )
                )

    def _plan_environment_branch_policies(
        self,
        repo: str,
        environment: str,
        desired_value: Any,
        current_value: Any,
    ) -> None:
        path = f"repositories.{repo}.environments.{environment}.branch_policies"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        base = (
            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/environments/{quote(environment)}"
            "/deployment-branch-policies"
        )
        matched_current: set[str] = set()
        for key, item in desired.items():
            if not isinstance(item, dict):
                raise ConfigError(f"{path}.items.{key} must be a mapping")
            _check_keys(item, {"name", "type"}, f"{path}.items.{key}")
            current_key = _match_current_key(
                key,
                item.get("name"),
                current,
                lambda value: value.get("name") if isinstance(value, Mapping) else None,
                f"{path}.items.{key}",
            )
            current_item = current.get(current_key) if current_key else None
            if current_key is not None:
                matched_current.add(current_key)
                policy_id = self.current.ids.get(
                    (
                        "repositories",
                        repo,
                        "environments",
                        environment,
                        "branch_policies",
                        current_key,
                    )
                )
                if policy_id is not None:
                    self.current.ids[
                        (
                            "repositories",
                            repo,
                            "environments",
                            environment,
                            "branch_policies",
                            key,
                        )
                    ] = policy_id
            if current_item is None:
                _require_keys(item, {"type"}, f"{path}.items.{key}")
                body = pick(item, ("name", "type"))
                body.setdefault("name", key)
                self.operations.append(
                    Operation(
                        "POST",
                        base,
                        [FieldChange(f"{path}.{key}", None, item, "add")],
                        body=body,
                        phase=25,
                        capture_id=(
                            "repositories",
                            repo,
                            "environments",
                            environment,
                            "branch_policies",
                            key,
                        ),
                    )
                )
            else:
                changes = _leaf_changes(current_item, item, f"{path}.{key}")
                if changes:
                    policy_id = self.current.ids.get(
                        (
                            "repositories",
                            repo,
                            "environments",
                            environment,
                            "branch_policies",
                            key,
                        )
                    )
                    complete_item = deep_merge(current_item, item)
                    type_changed = "type" in item and item["type"] != current_item.get(
                        "type"
                    )
                    if type_changed:
                        blocked_reason = (
                            None
                            if policy_id is not None
                            else "the deployment branch policy ID is unavailable"
                        )
                        self.operations.extend(
                            [
                                Operation(
                                    "DELETE",
                                    f"{base}/{policy_id if policy_id is not None else 'unknown'}",
                                    [
                                        FieldChange(
                                            f"{path}.{key}",
                                            current_item,
                                            None,
                                            "remove",
                                        )
                                    ],
                                    phase=24,
                                    blocked_reason=blocked_reason,
                                ),
                                Operation(
                                    "POST",
                                    base,
                                    [
                                        FieldChange(
                                            f"{path}.{key}", None, complete_item, "add"
                                        )
                                    ],
                                    body=pick(complete_item, ("name", "type")),
                                    phase=25,
                                    blocked_reason=blocked_reason,
                                    capture_id=(
                                        "repositories",
                                        repo,
                                        "environments",
                                        environment,
                                        "branch_policies",
                                        key,
                                    ),
                                ),
                            ]
                        )
                    else:
                        self.operations.append(
                            Operation(
                                "PUT",
                                f"{base}/{policy_id if policy_id is not None else 'unknown'}",
                                changes,
                                body={"name": complete_item.get("name", key)},
                                phase=30,
                                blocked_reason=None
                                if policy_id is not None
                                else "the deployment branch policy ID is unavailable",
                            )
                        )
        if mode == "exact":
            for key in set(current) - matched_current:
                policy_id = self.current.ids.get(
                    (
                        "repositories",
                        repo,
                        "environments",
                        environment,
                        "branch_policies",
                        key,
                    )
                )
                self.operations.append(
                    Operation(
                        "DELETE",
                        f"{base}/{policy_id if policy_id is not None else 'unknown'}",
                        [FieldChange(f"{path}.{key}", current[key], None, "remove")],
                        phase=80,
                        blocked_reason=None
                        if policy_id is not None
                        else "the deployment branch policy ID is unavailable",
                    )
                )

    def _plan_repo_custom_properties(
        self, repo: str, desired: Any, current: Any
    ) -> None:
        path = f"repositories.{repo}.custom_properties"
        if not isinstance(desired, dict):
            raise ConfigError(f"{path} must be a mapping")
        current = current if isinstance(current, dict) else {}
        changes = _leaf_changes(current, desired, path)
        if changes:
            properties = [
                {"property_name": name, "value": value}
                for name, value in desired.items()
            ]
            self.operations.append(
                Operation(
                    "PATCH",
                    f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/properties/values",
                    changes,
                    body={"properties": properties},
                    phase=30,
                )
            )

    def _plan_security_toggles(self, repo: str, desired: Any, current: Any) -> None:
        path = f"repositories.{repo}.security"
        if not isinstance(desired, dict):
            raise ConfigError(f"{path} must be a mapping")
        current = current if isinstance(current, dict) else {}
        unknown = set(desired) - set(SECURITY_TOGGLES)
        if unknown:
            raise ConfigError(
                f"{path} has unknown settings: {', '.join(sorted(unknown))}"
            )
        for name, enabled in desired.items():
            if not isinstance(enabled, bool):
                raise ConfigError(f"{path}.{name} must be true or false")
            if current.get(name) != enabled:
                endpoint = SECURITY_TOGGLES[name].format(
                    org=quote(self.org), repo=quote(self._repository_api_name(repo))
                )
                self.operations.append(
                    Operation(
                        "PUT" if enabled else "DELETE",
                        endpoint,
                        [FieldChange(f"{path}.{name}", current.get(name), enabled)],
                        phase=30,
                    )
                )

    def _plan_pages(self, repo: str, desired: Any, current: Any) -> None:
        path = f"repositories.{repo}.pages"
        if not isinstance(desired, dict):
            raise ConfigError(f"{path} must be a mapping")
        desired = _without_empty_mapping_field(desired, "source")
        _check_keys(
            desired,
            {
                "enabled",
                "build_type",
                "source",
                "cname",
                "https_enforced",
                "public",
            },
            path,
        )
        if "source" in desired:
            source = desired["source"]
            if not isinstance(source, dict):
                raise ConfigError(f"{path}.source must be a mapping")
            _check_keys(source, {"branch", "path"}, f"{path}.source")
            if not isinstance(source.get("branch"), str) or source.get("path") not in (
                "/",
                "/docs",
            ):
                raise ConfigError(
                    f"{path}.source must contain a branch string and a path of '/' or '/docs'"
                )
        current = _without_empty_mapping_field(
            current if isinstance(current, dict) else {"enabled": False},
            "source",
        )
        enabled = desired.get("enabled")
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}.enabled must be true or false")
        endpoint = (
            f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/pages"
        )
        if enabled and not current.get("enabled"):
            body = pick(desired, ("build_type", "source"))
            self.operations.append(
                Operation(
                    "POST",
                    endpoint,
                    [FieldChange(path, current, desired, "add")],
                    body=body,
                    phase=30,
                )
            )
            remaining = {
                key: value
                for key, value in desired.items()
                if key not in ("enabled", "build_type", "source")
            }
            if remaining:
                self.operations.append(
                    Operation(
                        "PUT",
                        endpoint,
                        _leaf_changes({}, remaining, path),
                        body=remaining,
                        phase=31,
                    )
                )
        elif not enabled and current.get("enabled"):
            self.operations.append(
                Operation(
                    "DELETE",
                    endpoint,
                    [FieldChange(path, current, desired, "remove")],
                    phase=80,
                )
            )
        elif enabled:
            changes = _leaf_changes(current, desired, path)
            changes = [
                change for change in changes if not change.path.endswith(".enabled")
            ]
            if changes:
                body = {
                    key: value for key, value in desired.items() if key != "enabled"
                }
                self.operations.append(
                    Operation("PUT", endpoint, changes, body=body, phase=30)
                )

    def _plan_workflow_states(
        self, repo: str, desired_value: Any, current_value: Any
    ) -> None:
        path = f"repositories.{repo}.workflow_states"
        desired = collection_items(desired_value, path)
        mode = collection_mode(desired_value, path)
        current = collection_items(current_value or {"items": {}}, f"current.{path}")
        for workflow, state in desired.items():
            read_only_states = {"disabled_inactivity", "disabled_fork", "deleted"}
            if state not in {"active", "disabled_manually", *read_only_states}:
                raise ConfigError(
                    f"{path}.items.{workflow} has unknown workflow state {state!r}"
                )
            current_state = current.get(workflow)
            if current_state is None:
                self.operations.append(
                    Operation(
                        "PUT",
                        "",
                        [FieldChange(f"{path}.{workflow}", None, state, "add")],
                        blocked_reason="the workflow does not exist in the target repository",
                    )
                )
            elif current_state == "deleted" and state != "deleted":
                self.operations.append(
                    Operation(
                        "PUT",
                        "",
                        [FieldChange(f"{path}.{workflow}", current_state, state)],
                        blocked_reason=(
                            "restoring a deleted workflow requires restoring its "
                            "repository file, which is outside this configuration"
                        ),
                    )
                )
            elif state in read_only_states and current_state != state:
                self._ignore_or_block_change(
                    f"{path}.{workflow}",
                    current_state,
                    state,
                    f"GitHub reports workflow state {state!r} but does not provide "
                    "an API operation that sets it",
                )
            elif current_state != state:
                workflow_id = self.current.ids.get(
                    ("repositories", repo, "workflows", workflow)
                )
                action = "enable" if state == "active" else "disable"
                self.operations.append(
                    Operation(
                        "PUT",
                        f"/repos/{quote(self.org)}/{quote(self._repository_api_name(repo))}/actions/workflows/"
                        f"{workflow_id if workflow_id is not None else 'unknown'}/{action}",
                        [FieldChange(f"{path}.{workflow}", current_state, state)],
                        phase=30,
                        blocked_reason=None
                        if workflow_id is not None
                        else "the current workflow ID is unavailable",
                    )
                )
        if mode == "exact":
            for workflow in set(current) - set(desired):
                self.operations.append(
                    Operation(
                        "DELETE",
                        "",
                        [
                            FieldChange(
                                f"{path}.{workflow}",
                                current[workflow],
                                None,
                                "remove",
                            )
                        ],
                        blocked_reason="workflow files are repository content and cannot be removed by github-config",
                    )
                )


def _leaf_changes(current: Any, desired: Any, path: str) -> list[FieldChange]:
    if isinstance(desired, dict):
        current_mapping = current if isinstance(current, Mapping) else {}
        changes: list[FieldChange] = []
        for key, desired_value in desired.items():
            if key.startswith("_") or key in ("value_from_env", "secret_from_env"):
                continue
            current_value = current_mapping.get(key)
            changes.extend(_leaf_changes(current_value, desired_value, f"{path}.{key}"))
        return changes
    if current != desired:
        return [FieldChange(path, current, desired)]
    return []


def _repository_is_archived(repository: Any) -> bool:
    if not isinstance(repository, Mapping):
        return False
    settings = repository.get("settings")
    if isinstance(settings, Mapping) and "archived" in settings:
        return settings["archived"] is True
    facts = repository.get("_facts")
    return isinstance(facts, Mapping) and facts.get("archived") is True


def _operation_repository_names(operation: Operation) -> set[str]:
    names = set(operation.repository_names)
    id_references = [key for _, key in operation.body_id_references]
    id_references.extend(
        key for _, keys in operation.body_id_list_references for key in keys
    )
    for reference in id_references:
        if len(reference) >= 2 and reference[0] == "repositories":
            names.add(reference[1])
    for change in operation.changes:
        field_name = change.path.rsplit(".", 1)[-1]
        if field_name not in {"repositories", "selected_repositories"} and not (
            field_name.endswith("_repositories")
        ):
            continue
        for value in (change.before, change.after):
            if isinstance(value, list):
                names.update(name for name in value if isinstance(name, str))
    return names


def _operation_unarchives_repository(operation: Operation) -> bool:
    return (
        operation.method == "PATCH"
        and operation.body == {"archived": False}
        and len(operation.changes) == 1
        and operation.changes[0].path.endswith(".settings.archived")
        and operation.changes[0].after is False
    )


def _operation_changes_code_security_assignment(operation: Operation) -> bool:
    endpoint_matches = (
        operation.method == "POST"
        and operation.endpoint.endswith(
            "/code-security/configurations/__CONFIGURATION_ID__/attach"
        )
    ) or (
        operation.method == "DELETE"
        and operation.endpoint.endswith("/code-security/configurations/detach")
    )
    return (
        endpoint_matches
        and bool(operation.changes)
        and all(
            change.path.startswith("organization.code_security.configurations.")
            and change.path.endswith(".repositories")
            for change in operation.changes
        )
    )


def _operation_enables_secret_scanning(operation: Operation) -> bool:
    return (
        operation.method == "PATCH"
        and bool(operation.changes)
        and all(
            change.path.endswith(
                ".settings.security_and_analysis.secret_scanning.status"
            )
            and change.after == "enabled"
            for change in operation.changes
        )
    )


def _desired_collection_names(value: Any, path: str) -> set[str]:
    if value is None:
        return set()
    return set(collection_items(value, path))


def _match_current_key(
    desired_key: str,
    desired_identity: Any,
    current: Mapping[str, Any],
    current_identity: Callable[[Any], Any],
    path: str,
) -> str | None:
    if desired_key in current:
        current_value = current[desired_key]
        current_value_identity = current_identity(current_value)
        if desired_identity is not None and desired_identity != current_value_identity:
            collisions = [
                key
                for key, item in current.items()
                if key != desired_key and current_identity(item) == desired_identity
            ]
            if collisions:
                names = ", ".join(repr(key) for key in sorted(collisions))
                raise ConfigError(
                    f"{path} changes its configured identity to one already used "
                    f"by current resource(s) {names}"
                )
        return desired_key
    if desired_identity is None:
        return None
    matches = [
        key
        for key, item in current.items()
        if current_identity(item) == desired_identity
    ]
    if len(matches) > 1:
        raise ConfigError(
            f"{path} matches more than one current resource by its configured identity"
        )
    return matches[0] if matches else None


def _mapping_field_value(value: Any, *, field_name: str) -> Any:
    return value.get(field_name) if isinstance(value, Mapping) else None


def _budget_identity(item: Mapping[str, Any]) -> str:
    return ":".join(
        str(value or "all")
        for value in (
            item.get("budget_scope"),
            item.get("budget_entity_name") or item.get("user"),
            item.get("budget_type"),
            item.get("budget_product_sku"),
        )
    )


def _inherited_budget_scope_error(item: Any) -> str | None:
    if not isinstance(item, Mapping):
        return None
    scope = item.get("budget_scope")
    if scope not in {"enterprise", "cost_center", "multi_user_cost_center"}:
        return None
    return (
        f"GitHub reports {scope!r} budgets as inherited organization state but does "
        "not allow organizations to create, update, or delete them"
    )


def _without_null_fields(
    item: Mapping[str, Any], fields: Iterable[str]
) -> dict[str, Any]:
    response_only_nulls = set(fields)
    return {
        key: value
        for key, value in item.items()
        if value is not None or key not in response_only_nulls
    }


def _without_empty_mapping_field(item: Mapping[str, Any], field: str) -> dict[str, Any]:
    normalized = dict(item)
    value = normalized.get(field)
    if isinstance(value, Mapping) and not value:
        normalized.pop(field, None)
    return normalized


def _hosted_runner_writable_item(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    image = normalized.get("image")
    if not isinstance(image, Mapping):
        return normalized
    writable_image = {
        key: value
        for key, value in image.items()
        if value is not None or key == "version"
    }
    if writable_image:
        normalized["image"] = writable_image
    else:
        normalized.pop("image", None)
    return normalized


def _branch_protection_writable_item(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(item))
    status_checks = normalized.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return normalized
    checks = status_checks.get("checks")
    if not isinstance(checks, list):
        return normalized
    status_checks["checks"] = [
        {
            key: value
            for key, value in check.items()
            if key != "app_id" or value is not None
        }
        if isinstance(check, Mapping)
        else check
        for check in checks
    ]
    return normalized


def _branch_protection_desired_item(
    item: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(item))
    desired_status_checks = normalized.get("required_status_checks")
    current_status_checks = current.get("required_status_checks")
    if not isinstance(desired_status_checks, dict) or not isinstance(
        current_status_checks, Mapping
    ):
        return _branch_protection_writable_item(normalized)
    desired_checks = desired_status_checks.get("checks")
    current_checks = current_status_checks.get("checks")
    if not isinstance(desired_checks, list) or not isinstance(current_checks, list):
        return _branch_protection_writable_item(normalized)
    claimed_current_checks: set[int] = set()
    for desired_check in desired_checks:
        if not isinstance(desired_check, dict):
            continue
        matching_index = next(
            (
                index
                for index, current_check in enumerate(current_checks)
                if index not in claimed_current_checks
                and isinstance(current_check, Mapping)
                and current_check.get("context") == desired_check.get("context")
            ),
            None,
        )
        if matching_index is not None:
            claimed_current_checks.add(matching_index)
        if "app_id" not in desired_check or desired_check["app_id"] is not None:
            continue
        current_app_id = None
        if matching_index is not None:
            current_check = current_checks[matching_index]
            if isinstance(current_check, Mapping):
                current_app_id = current_check.get("app_id")
        if isinstance(current_app_id, int) and not isinstance(current_app_id, bool):
            desired_check["app_id"] = current_app_id
        else:
            desired_check.pop("app_id", None)
    return _branch_protection_writable_item(normalized)


def _environment_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(settings))
    policy = normalized.get("deployment_branch_policy")
    if isinstance(policy, Mapping) and not policy:
        normalized["deployment_branch_policy"] = None
    return normalized


def _ruleset_writable_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if normalized.get("conditions") is None:
        normalized.pop("conditions", None)
    return normalized


def _validate_pattern_settings(value: Mapping[str, Any], path: str) -> None:
    for key, known, allowed_settings in (
        (
            "provider_pattern_settings",
            {"token_type", "push_protection_setting"},
            {"not-set", "disabled", "enabled"},
        ),
        (
            "custom_pattern_settings",
            {"token_type", "custom_pattern_version", "push_protection_setting"},
            {"disabled", "enabled"},
        ),
    ):
        if key not in value:
            continue
        settings = value[key]
        if not isinstance(settings, list):
            raise ConfigError(f"{path}.{key} must be a list")
        for index, setting in enumerate(settings):
            item_path = f"{path}.{key}[{index}]"
            if not isinstance(setting, dict):
                raise ConfigError(f"{item_path} must be a mapping")
            _check_keys(setting, known, item_path)
            if not isinstance(setting.get("token_type"), str):
                raise ConfigError(f"{item_path}.token_type must be a string")
            if setting.get("push_protection_setting") not in allowed_settings:
                raise ConfigError(
                    f"{item_path}.push_protection_setting has an invalid value"
                )


def _team_create_phases(
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, int]:
    phases: dict[str, int] = {}

    def phase(slug: str, stack: tuple[str, ...] = ()) -> int:
        if slug in phases:
            return phases[slug]
        if slug in stack:
            cycle = " -> ".join((*stack, slug))
            raise ConfigError(f"organization.teams contains a parent cycle: {cycle}")
        item = desired.get(slug, {})
        settings = item.get("settings", {}) if isinstance(item, dict) else {}
        parent = settings.get("parent") if isinstance(settings, dict) else None
        if parent is not None and not isinstance(parent, str):
            raise ConfigError(
                f"organization.teams.items.{slug}.settings.parent must be a team slug or null"
            )
        if slug in current or parent is None or parent in current:
            result = 5
        elif parent in desired:
            result = phase(parent, (*stack, slug)) + 1
        else:
            result = 5
        phases[slug] = result
        return result

    for slug in desired:
        phase(slug)
    return phases


def _team_delete_phases(current: Mapping[str, Any]) -> dict[str, int]:
    phases: dict[str, int] = {}

    def phase(slug: str, stack: tuple[str, ...] = ()) -> int:
        if slug in phases:
            return phases[slug]
        if slug in stack:
            cycle = " -> ".join((*stack, slug))
            raise ConfigError(
                f"current.organization.teams contains a parent cycle: {cycle}"
            )
        children = _team_children(slug, current)
        result = (
            max(phase(child, (*stack, slug)) for child in children) + 1
            if children
            else 95
        )
        phases[slug] = result
        return result

    for slug in current:
        phase(slug)
    return phases


def _team_current_matches(
    desired: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, str]:
    matches: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for slug, item in desired.items():
        settings = item.get("settings", {}) if isinstance(item, Mapping) else {}
        name = settings.get("name") if isinstance(settings, Mapping) else None
        current_slug = _match_current_key(
            slug,
            name,
            current,
            lambda value: (
                get_path(value, "settings.name") if isinstance(value, Mapping) else None
            ),
            f"organization.teams.items.{slug}",
        )
        if current_slug is None:
            continue
        previous = claimed.get(current_slug)
        if previous is not None:
            raise ConfigError(
                f"organization.teams.items.{slug} and "
                f"organization.teams.items.{previous} match the same current team"
            )
        matches[slug] = current_slug
        claimed[current_slug] = slug
    return matches


def _team_topology_current(
    current: Mapping[str, Any], matches: Mapping[str, str]
) -> dict[str, Any]:
    current_to_desired = {
        current_slug: desired_slug for desired_slug, current_slug in matches.items()
    }
    normalized: dict[str, Any] = {}
    for current_slug, item in current.items():
        desired_slug = current_to_desired.get(current_slug, current_slug)
        normalized_item = copy.deepcopy(item)
        if isinstance(normalized_item, dict):
            settings = normalized_item.get("settings")
            if isinstance(settings, dict) and isinstance(settings.get("parent"), str):
                settings["parent"] = current_to_desired.get(
                    settings["parent"], settings["parent"]
                )
        normalized[desired_slug] = normalized_item
    return normalized


def _team_topology_desired(
    desired: Mapping[str, Any], matches: Mapping[str, str]
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(desired))
    for item in normalized.values():
        if not isinstance(item, dict):
            continue
        settings = item.get("settings")
        if isinstance(settings, dict) and "parent" in settings:
            settings["parent"] = _team_parent_alias(settings["parent"], matches)
    return normalized


def _team_parent_alias(parent: Any, matches: Mapping[str, str]) -> Any:
    if not isinstance(parent, str):
        return parent
    current_to_desired = {
        current_slug: desired_slug for desired_slug, current_slug in matches.items()
    }
    return current_to_desired.get(parent, parent)


def _validate_team_final_topology(
    desired: Mapping[str, Any], current: Mapping[str, Any], mode: str
) -> None:
    candidates = set(desired)
    if mode == "merge":
        candidates.update(current)
    parents: dict[str, str] = {}
    for slug in candidates:
        settings = _team_final_settings(slug, desired, current, mode)
        if settings is not None and isinstance(settings.get("parent"), str):
            parents[slug] = settings["parent"]

    visited: set[str] = set()

    def visit(slug: str, stack: tuple[str, ...] = ()) -> None:
        if slug in visited:
            return
        if slug in stack:
            cycle_start = stack.index(slug)
            cycle = " -> ".join((*stack[cycle_start:], slug))
            raise ConfigError(f"organization.teams contains a parent cycle: {cycle}")
        parent = parents.get(slug)
        if parent in candidates:
            visit(parent, (*stack, slug))
        visited.add(slug)

    for slug in candidates:
        visit(slug)


def _team_privacy_error(
    slug: str,
    privacy: Any,
    parent: Any,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> str | None:
    if privacy != "secret":
        return None
    if parent is not None or _team_has_final_child(slug, desired, current, mode):
        return "GitHub requires a team with a parent or child to use closed privacy"
    return None


def _team_parent_privacy_error(
    parent: Any,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> str | None:
    if not isinstance(parent, str):
        return None
    if _team_final_privacy(parent, desired, current, mode) == "secret":
        return "GitHub requires a parent team to use closed privacy"
    return None


def _team_privacy_update_phase(
    slug: str,
    current_privacy: Any,
    desired_privacy: Any,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> int:
    if (
        current_privacy == "secret"
        and desired_privacy == "closed"
        and _team_has_final_child(slug, desired, current, mode)
    ):
        return 4
    if (
        current_privacy == "closed"
        and desired_privacy == "secret"
        and _team_has_child(slug, current)
    ):
        omitted_children = [
            child
            for child in _team_children(slug, current)
            if mode == "exact" and child not in desired
        ]
        if omitted_children:
            delete_phases = _team_delete_phases(current)
            return max(delete_phases[child] for child in omitted_children) + 1
        return 71
    return 70


def _team_final_privacy(
    slug: str,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> Any:
    settings = _team_final_settings(slug, desired, current, mode)
    if settings is None:
        return None
    if "privacy" in settings:
        return settings["privacy"]
    return "closed" if settings.get("parent") is not None else "secret"


def _team_has_final_child(
    slug: str,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> bool:
    candidates = set(desired)
    if mode == "merge":
        candidates.update(current)
    return any(
        (settings := _team_final_settings(child, desired, current, mode)) is not None
        and settings.get("parent") == slug
        for child in candidates
    )


def _team_final_settings(
    slug: str,
    desired: Mapping[str, Any],
    current: Mapping[str, Any],
    mode: str,
) -> dict[str, Any] | None:
    current_item = current.get(slug)
    current_settings = (
        current_item.get("settings", {}) if isinstance(current_item, Mapping) else {}
    )
    current_settings = (
        dict(current_settings) if isinstance(current_settings, Mapping) else {}
    )
    desired_item = desired.get(slug)
    if desired_item is None:
        return (
            current_settings if mode == "merge" and current_item is not None else None
        )
    desired_settings = (
        desired_item.get("settings", {}) if isinstance(desired_item, Mapping) else {}
    )
    desired_settings = (
        dict(desired_settings) if isinstance(desired_settings, Mapping) else {}
    )
    return deep_merge(current_settings, desired_settings)


def _team_has_child(slug: str, teams: Mapping[str, Any]) -> bool:
    return bool(_team_children(slug, teams))


def _team_children(slug: str, teams: Mapping[str, Any]) -> list[str]:
    return [
        child
        for child, item in teams.items()
        if isinstance(item, Mapping) and get_path(item, "settings.parent") == slug
    ]


def _check_keys(value: Mapping[str, Any], known: set[str], path: str) -> None:
    unknown = {key for key in value if not key.startswith("_")} - known
    if unknown:
        raise ConfigError(f"{path} has unknown keys: {', '.join(sorted(unknown))}")


def _branch_protection_actor_id_key(reference: str, path: str) -> tuple[str, ...]:
    actor_type, separator, identity = reference.partition(":")
    if not separator or not identity or actor_type not in {"user", "team", "app"}:
        raise ConfigError(
            f"{path} contains {reference!r}; actor references must use "
            "'user:LOGIN', 'team:SLUG', or 'app:SLUG'"
        )
    return ("branch_protection_actors", actor_type, identity.casefold())


def _custom_property_graphql_input(
    name: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    field_names = {
        "allowed_values": "allowedValues",
        "default_value": "defaultValue",
        "description": "description",
        "regex": "regex",
        "require_explicit_values": "requireExplicitValues",
        "required": "required",
        "values_editable_by": "valuesEditableBy",
    }
    result: dict[str, Any] = {
        "propertyName": name,
        "valueType": str(item["value_type"]).upper(),
    }
    for config_name, graphql_name in field_names.items():
        if config_name not in item:
            continue
        value = item[config_name]
        if config_name == "values_editable_by" and isinstance(value, str):
            value = value.upper()
        result[graphql_name] = value
    return result


def _validate_custom_property_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, str):
        return
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return
    raise ConfigError(f"{path} must be a string, a list of strings, or null")


def _require_keys(value: Mapping[str, Any], required: set[str], path: str) -> None:
    missing = required - set(value)
    if missing:
        raise ConfigError(
            f"{path} is missing required keys: {', '.join(sorted(missing))}"
        )


def _issue_field_options_error(item: Mapping[str, Any]) -> str | None:
    options = item.get("options")
    if not isinstance(options, list):
        return None
    for index, option in enumerate(options):
        if not isinstance(option, Mapping):
            return f"issue field option {index + 1} must be a mapping"
        missing = [
            key for key in ("name", "color", "priority") if option.get(key) is None
        ]
        if missing:
            return (
                f"issue field option {index + 1} requires non-null "
                f"{', '.join(missing)} values"
            )
    return None


def _network_compute_service_error(
    desired: Mapping[str, Any], current: Mapping[str, Any] | None
) -> str | None:
    if "compute_service" not in desired:
        return None
    requested = desired["compute_service"]
    if requested in ("none", "actions"):
        return None
    if current is not None and requested == current.get("compute_service"):
        return None
    return (
        f"GitHub reports compute_service {requested!r} but accepts only 'none' and "
        "'actions' when writing a network configuration"
    )


def _block_operations_with_unavailable_state(
    operations: Iterable[Operation], unavailable: Iterable[str]
) -> None:
    unavailable_paths = {
        entry.split(" (", 1)[0].split("?", 1)[0].rstrip("/") for entry in unavailable
    }
    for operation in operations:
        if operation.blocked_reason or not operation.endpoint:
            continue
        endpoint = operation.endpoint.split("?", 1)[0].rstrip("/")
        creates_item = any(change.action == "add" for change in operation.changes)
        blocking_path = next(
            (
                path
                for path in sorted(unavailable_paths)
                if endpoint == path
                or endpoint.startswith(f"{path}/")
                or (creates_item and path.startswith(f"{endpoint}/"))
            ),
            None,
        )
        if blocking_path is not None:
            operation.blocked_reason = (
                f"the current GitHub state was unavailable at {blocking_path}"
            )


def _block_cross_organization_duplicate_identities(
    operations: Iterable[Operation], observed: Any, target_org: str
) -> None:
    if not isinstance(observed, Mapping):
        return
    source_org = observed.get("organization")
    if (
        not isinstance(source_org, str)
        or source_org.casefold() == target_org.casefold()
    ):
        return
    for operation in operations:
        if operation.blocked_reason:
            continue
        if any("#github-id-" in change.path for change in operation.changes):
            operation.blocked_reason = (
                "duplicate resource names use organization-local GitHub IDs; choose the "
                "matching target resource key before applying across organizations"
            )


def _add_unavailable_collection_guards(
    operations: list[Operation],
    organization: Mapping[str, Any],
    repositories: Mapping[str, Mapping[str, Any]],
    unavailable_collections: Mapping[tuple[str, ...], str],
) -> None:
    resolved: dict[str, Any] = {
        "organization": organization,
        "repositories": {"items": repositories},
    }
    for config_path, endpoint in unavailable_collections.items():
        desired_value = _mapping_path_with_missing(resolved, config_path)
        if desired_value is _UNSET:
            continue
        display_path = ".".join(part for part in config_path if part != "items")
        collection_value = _is_collection_value(desired_value)
        needs_authoritative_read = not collection_value or (
            collection_mode(desired_value, display_path) == "exact"
        )
        matching_operations = [
            operation
            for operation in operations
            if any(
                change.path == display_path
                or change.path.startswith(f"{display_path}.")
                for change in operation.changes
            )
        ]
        reason = f"the current GitHub state could not be read at {endpoint}"
        for operation in matching_operations:
            if operation.blocked_reason is None:
                operation.blocked_reason = reason
        if matching_operations or not needs_authoritative_read:
            continue
        operations.append(
            Operation(
                "GET",
                endpoint,
                [
                    FieldChange(
                        display_path,
                        "<current state unavailable>",
                        "<configured state requested>",
                    )
                ],
                blocked_reason=reason,
            )
        )


def _handle_read_only_item_operations(
    operations: list[Operation],
    read_only_items: Mapping[tuple[str, ...], str],
    team_current_to_logical: Mapping[str, str],
    *,
    force: bool,
    forced_changes: list[FieldChange],
) -> list[Operation]:
    display_paths: dict[str, str] = {}
    for path, reason in read_only_items.items():
        display_paths.setdefault(".".join(path).casefold(), reason)
        if len(path) < 3 or path[:2] != ("organization", "teams"):
            continue
        logical_slug = team_current_to_logical.get(path[2])
        if logical_slug is not None:
            logical_path = (*path[:2], logical_slug, *path[3:])
            display_paths.setdefault(".".join(logical_path).casefold(), reason)

    retained: list[Operation] = []
    for operation in operations:
        if operation.blocked_reason is not None:
            retained.append(operation)
            continue
        blocked_reason: str | None = None
        for change in operation.changes:
            change_path = change.path.casefold()
            match = next(
                (
                    reason
                    for display_path, reason in display_paths.items()
                    if change_path == display_path
                    or change_path.startswith(f"{display_path}.")
                ),
                None,
            )
            if match is not None:
                blocked_reason = match
                break
        if blocked_reason is None:
            retained.append(operation)
        elif force:
            forced_changes.extend(operation.changes)
        else:
            operation.blocked_reason = blocked_reason
            retained.append(operation)
    return retained


def _read_only_identity_reason(
    read_only_identities: Mapping[tuple[str, ...], str],
    collection_path: tuple[str, ...],
    identity: Any,
) -> str | None:
    if not isinstance(identity, str):
        return None
    wanted = identity.casefold()
    for path, reason in read_only_identities.items():
        if path[:-1] == collection_path and path[-1].casefold() == wanted:
            return reason
    return None


def _read_only_name_in_values_reason(
    read_only_names: Mapping[str, str], values: Any
) -> str | None:
    if not isinstance(values, list):
        return None
    reasons_by_name = {
        name.casefold(): reason for name, reason in read_only_names.items()
    }
    for value in values:
        if isinstance(value, str) and value.casefold() in reasons_by_name:
            return reasons_by_name[value.casefold()]
    return None


def _is_collection_value(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    public_keys = {key for key in value if not str(key).startswith("_")}
    return public_keys <= {"mode", "items"}


def _mapping_path(root: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = root
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _mapping_path_with_missing(
    root: Mapping[str, Any], path: tuple[str | int, ...]
) -> Any:
    value: Any = root
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                return _UNSET
            value = value[part]
        elif not isinstance(value, Mapping) or part not in value:
            return _UNSET
        else:
            value = value[part]
    return value


def _normalize_organization_repository_references(
    organization: Mapping[str, Any], renames: Mapping[str, str]
) -> dict[str, Any]:
    def normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, Mapping):
            return copy.deepcopy(value)
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                result[key] = normalize(child)
                continue
            repository_list = key in {
                "repositories",
                "selected_repositories",
            } or key.endswith("_repositories")
            if repository_list and isinstance(child, list):
                normalized_names = [
                    renames.get(name, name)
                    if isinstance(name, str)
                    else normalize(name)
                    for name in child
                ]
                result[key] = list(dict.fromkeys(normalized_names))
                continue
            if key == "repositories" and isinstance(child, Mapping):
                normalized_collection = normalize(child)
                items = normalized_collection.get("items")
                if isinstance(items, Mapping):
                    normalized_items: dict[str, Any] = {}
                    for name, item in items.items():
                        normalized_name = renames.get(name, name)
                        if normalized_name in normalized_items:
                            raise ConfigError(
                                "a repository rename maps more than one current team "
                                f"repository entry to {normalized_name!r}"
                            )
                        normalized_items[normalized_name] = item
                    normalized_collection["items"] = normalized_items
                result[key] = normalized_collection
                continue
            result[key] = normalize(child)
        return result

    return normalize(organization)


def _effective_organization_boolean(
    current_settings: Any,
    desired_settings: Any,
    key: str,
) -> bool | None:
    current_value = (
        current_settings.get(key) if isinstance(current_settings, Mapping) else None
    )
    desired_value = (
        desired_settings.get(key) if isinstance(desired_settings, Mapping) else None
    )
    value = desired_value if isinstance(desired_value, bool) else current_value
    return value if isinstance(value, bool) else None


def _validate_repository_shape(value: Mapping[str, Any], path: str) -> None:
    _check_keys(value, REPOSITORY_KEYS, path)
    for section, known in REPOSITORY_SECTION_KEYS:
        nested = value.get(section)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            raise ConfigError(f"{path}.{section} must be a mapping")
        _check_keys(nested, known, f"{path}.{section}")
