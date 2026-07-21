from __future__ import annotations

import copy
import fnmatch
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias

import yaml

from .comments import add_key_comments, format_comment


class ConfigError(ValueError):
    pass


ConfigPathPart: TypeAlias = str | int
ConfigPath: TypeAlias = tuple[ConfigPathPart, ...]


_INTEGER_FIELDS = {
    "actor_id",
    "app_id",
    "authorized_credential_id",
    "budget_amount",
    "check_response_timeout_minutes",
    "days",
    "group_id",
    "integration_id",
    "max_cache_retention_days",
    "max_cache_size_gb",
    "max_entries_to_build",
    "max_entries_to_merge",
    "max_file_path_length",
    "max_file_size",
    "max_open_pull_requests",
    "maximum_runners",
    "member_count",
    "min_entries_to_merge",
    "min_entries_to_merge_wait_minutes",
    "minimum_approvals",
    "priority",
    "pinned_position",
    "repository_id",
    "required_approving_review_count",
    "reviewer_id",
    "security_configuration_id",
    "wait_timer",
}


_BOOLEAN_FIELDS = {
    "active",
    "answerable",
    "applies_to_installed_apps",
    "approved",
    "archived",
    "automated_security_fixes",
    "block_creations",
    "blocks_creations",
    "custom_branch_policies",
    "delete_branch_on_merge",
    "direct_membership",
    "dismiss_stale_reviews",
    "dismisses_stale_reviews",
    "dismiss_stale_reviews_on_push",
    "do_not_enforce_on_create",
    "enabled",
    "enforce_admins",
    "enable_static_ip",
    "failover_network_enabled",
    "github_owned_allowed",
    "https_enforced",
    "inherited",
    "allows_public_repositories",
    "fork",
    "image_gen",
    "lock_branch",
    "lock_allows_fetch_and_merge",
    "negate",
    "pinned",
    "protected_branches",
    "public",
    "read_only",
    "readers_can_create_discussions",
    "replaces_base",
    "required_conversation_resolution",
    "required_linear_history",
    "required_review_thread_resolution",
    "required_signatures",
    "required",
    "restricted_to_workflows",
    "review_draft_pull_requests",
    "review_on_push",
    "run_workflows_from_fork_pull_requests",
    "sha_pinning_required",
    "strict",
    "strict_required_status_checks_policy",
    "token_expired",
    "update_allows_fetch_and_merge",
    "template",
    "verified_allowed",
    "verified",
    "vulnerability_alerts",
    "web_commit_signoff_required",
    "will_alert",
    "uses_custom_image",
    "user_dismissible",
}


_STRING_FIELDS = {
    "_expires_at",
    "access_level",
    "account_id",
    "actor_type",
    "advanced_security",
    "algorithm",
    "alerts_threshold",
    "allowed_actions",
    "announcement",
    "approval_policy",
    "app",
    "app_slug",
    "api_host",
    "audience",
    "auth_type",
    "authorized_credential_expires_at",
    "authorized_credential_note",
    "authorized_credential_title",
    "aws_region",
    "billing_email",
    "blog",
    "branch",
    "base_role",
    "budget_entity_name",
    "budget_product_sku",
    "budget_scope",
    "budget_type",
    "build_type",
    "bypass_mode",
    "client_id",
    "cname",
    "code_scanning_default_setup",
    "code_scanning_delegated_alert_dismissal",
    "color",
    "company",
    "compute_service",
    "content_type",
    "context",
    "credential_accessed_at",
    "credential_authorized_at",
    "credential_type",
    "custom_pattern_version",
    "data_type",
    "default_branch",
    "default_repository_branch",
    "default_for_new_repos",
    "default_level",
    "default_repository_permission",
    "default_workflow_permissions",
    "dependabot_alerts",
    "dependabot_delegated_alert_dismissal",
    "dependabot_security_updates",
    "dependency_graph",
    "dependency_graph_autosubmit_action",
    "description",
    "digest_method",
    "domain",
    "domain_owner",
    "email",
    "emoji",
    "enabled_repositories",
    "end_delimiter",
    "expires_at",
    "fingerprint",
    "enforced_repositories",
    "enforcement",
    "expiry",
    "homepage",
    "idp_certificate",
    "grouping_strategy",
    "group_description",
    "group_name",
    "hash_algorithm",
    "identity_mapping_name",
    "insecure_ssl",
    "issuer",
    "issue_creation_policy",
    "jfrog_oidc_provider_name",
    "key_prefix",
    "key",
    "limit",
    "location",
    "login",
    "merge_commit_message",
    "merge_commit_title",
    "merge_method",
    "members_allowed_repository_creation_type",
    "mode",
    "name",
    "name_with_owner",
    "namespace",
    "network_configuration",
    "notification_setting",
    "operator",
    "owner",
    "parent",
    "path",
    "pattern_config_version",
    "pattern",
    "permission",
    "privacy",
    "pull_request_creation_policy",
    "push_protection_setting",
    "query_suite",
    "ref",
    "regex",
    "registry_type",
    "repository_selection",
    "reviewer_type",
    "role",
    "role_name",
    "runner_group",
    "runner_label",
    "runner_type",
    "secret_from_env",
    "secret_scanning_push_protection_custom_link",
    "secret_scanning",
    "secret_scanning_delegated_alert_dismissal",
    "secret_scanning_delegated_bypass",
    "secret_scanning_extended_metadata",
    "secret_scanning_generic_secrets",
    "secret_scanning_non_provider_patterns",
    "secret_scanning_push_protection",
    "secret_scanning_validity_checks",
    "security_alerts_threshold",
    "service_account",
    "service_slug",
    "sha",
    "signature_method",
    "size",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
    "source_type",
    "start_delimiter",
    "state",
    "status",
    "synced_at",
    "sso_url",
    "target",
    "tenant_id",
    "threat_model",
    "token_type",
    "token_expires_at",
    "token_last_eight",
    "token_name",
    "tool",
    "title",
    "type",
    "url",
    "url_template",
    "user",
    "username",
    "value",
    "value_from_env",
    "value_type",
    "values_editable_by",
    "verification_token",
    "visibility",
    "twitter_username",
    "workload_identity_provider",
    "_pattern_config_version",
}


_STRING_LIST_FIELDS = {
    "alert_recipients",
    "allowed_merge_methods",
    "allowed_values",
    "apps",
    "blocked_users",
    "bypass_force_push_actors",
    "bypass_pull_request_actors",
    "coding_agent_repositories",
    "contexts",
    "events",
    "file_patterns",
    "failover_network_settings_ids",
    "include_claim_keys",
    "immutable_release_repositories",
    "languages",
    "must_match",
    "must_not_match",
    "network_settings_ids",
    "oidc_custom_properties",
    "patterns_allowed",
    "property_values",
    "push_actors",
    "pull_request_creation_cap_bypass_users",
    "required_deployments",
    "required_deployment_environments",
    "restricted_file_extensions",
    "restricted_file_paths",
    "review_dismissal_actors",
    "selected_repositories",
    "selected_workflows",
    "security_manager_teams",
    "self_hosted_runner_repositories",
    "single_file_paths",
    "scopes",
    "topics",
}


_INTEGER_LIST_FIELDS = {
    "repository_ids",
}


_MAPPING_OR_STRING_LIST_FIELDS = {
    "labels",
    "permissions",
    "repositories",
    "runners",
    "teams",
    "users",
}


_MAPPING_FIELDS = {
    "access",
    "actions",
    "agents",
    "announcement",
    "app_installations",
    "artifact_and_log_retention",
    "autolinks",
    "branch_policies",
    "branch_protections",
    "branch_protection_rules",
    "budget_alerting",
    "budgets",
    "bypass_pull_request_allowances",
    "cache_retention",
    "cache_storage",
    "code_quality",
    "code_scanning",
    "code_scanning_default_setup_options",
    "code_scanning_options",
    "code_security",
    "codespaces",
    "coding_agent_permissions",
    "collaborator_invitations",
    "collaborators",
    "conditions",
    "config",
    "configurations",
    "content_exclusion",
    "copilot",
    "credential_authorizations",
    "custom_organization_roles",
    "custom_patterns",
    "custom_properties",
    "custom_property_values",
    "custom_repository_roles",
    "default_setup",
    "dependabot",
    "dependency_graph_autosubmit_action_options",
    "deploy_keys",
    "deployment_branch_policy",
    "deployment_protection_rules",
    "discussion_categories",
    "domains",
    "dismissal_restriction",
    "dismissal_restrictions",
    "environments",
    "entries",
    "external_group",
    "fork_pull_request_approval",
    "hooks",
    "hosted_compute",
    "hosted_runners",
    "image",
    "interaction_limit",
    "ip_allow_list",
    "invitations",
    "issue_fields",
    "issue_types",
    "members",
    "network_configurations",
    "oidc_subject",
    "organization_roles",
    "outside_collaborators",
    "parameters",
    "pages",
    "pattern_configurations",
    "permissions",
    "personal_access_tokens",
    "pinned_items",
    "private_fork_pull_request_workflows",
    "private_registries",
    "pull_request_creation_cap",
    "required_pull_request_reviews",
    "repository_access",
    "review_assignment",
    "restrictions",
    "reviewer",
    "rulesets",
    "runner_groups",
    "seats",
    "secret_scanning_delegated_bypass_options",
    "secrets",
    "security",
    "security_and_analysis",
    "self_hosted_runner_permissions",
    "self_hosted_runners",
    "setup",
    "settings",
    "saml_identity_provider",
    "social_preview",
    "variables",
    "workflow_permissions",
    "workflow_states",
}


_MAPPING_LIST_FIELDS = {
    "allowed_actors",
    "bypass_actors",
    "checks",
    "code_scanning_tools",
    "custom_pattern_settings",
    "options",
    "provider_pattern_settings",
    "reviewers",
    "required_reviewers",
    "rules",
    "team_sync_groups",
    "workflows",
}


_NULLABLE_NON_STRING_FIELDS = {
    "actor_id",
    "allow_advanced",
    "allowed_values",
    "app_id",
    "authorized_credential_id",
    "code_scanning_default_setup_options",
    "code_scanning_options",
    "conditions",
    "deployment_branch_policy",
    "external_group",
    "integration_id",
    "members_can_create_repositories",
    "members_can_fork_private_repositories",
    "must_match",
    "must_not_match",
    "options",
    "priority",
    "pinned_position",
    "required_pull_request_reviews",
    "required_status_checks",
    "restrictions",
    "reviewers",
    "secret_scanning_delegated_bypass_options",
}


_NULLABLE_STRING_FIELDS = {
    "_expires_at",
    "_pattern_config_version",
    "billing_email",
    "blog",
    "cname",
    "color",
    "company",
    "custom_pattern_version",
    "default_level",
    "default_repository_permission",
    "dependabot_delegated_alert_dismissal",
    "description",
    "email",
    "end_delimiter",
    "expires_at",
    "credential_accessed_at",
    "authorized_credential_expires_at",
    "authorized_credential_note",
    "authorized_credential_title",
    "homepage",
    "location",
    "login",
    "merge_commit_message",
    "merge_commit_title",
    "network_configuration",
    "parent",
    "runner_group",
    "runner_label",
    "runner_type",
    "regex",
    "secret_scanning_push_protection_custom_link",
    "single_file_name",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
    "start_delimiter",
    "suspended_at",
    "synced_at",
    "token_expires_at",
    "twitter_username",
    "username",
    "values_editable_by",
}


_BOOLEAN_PREFIXES = (
    "allow_",
    "allows_",
    "blocks_",
    "can_",
    "enable_",
    "has_",
    "is_",
    "members_can_",
    "prevent_",
    "require_",
    "requires_",
    "restricts_",
    "send_",
    "use_",
)


REPOSITORY_SELECTOR_KEYS = {
    "name",
    "exclude",
    "visibility",
    "archived",
    "fork",
    "template",
    "topics_all",
    "custom_properties",
}


class _ConfigDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    node = yaml.SafeDumper.represent_str(dumper, value)
    if any(character in value for character in ("\x85", "\u2028", "\u2029")):
        node.style = '"'
    return node


_ConfigDumper.add_representer(str, _represent_string)


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        with Path(path).open(encoding="utf-8") as file:
            value = yaml.safe_load(file)
    except OSError as error:
        raise ConfigError(f"Could not read {path}: {error}") from None
    except yaml.YAMLError as error:
        raise ConfigError(f"Could not parse {path}: {error}") from None
    if not isinstance(value, dict):
        raise ConfigError("The configuration root must be a mapping")
    _expect_string_keys(value, ())
    if type(value.get("version")) is not int or value["version"] != 1:
        raise ConfigError("The configuration must contain 'version: 1'")
    unknown_root = set(value) - {
        "version",
        "organization",
        "repository_policies",
        "repositories",
        "_observed",
    }
    if unknown_root:
        raise ConfigError(f"Unknown top-level keys: {', '.join(sorted(unknown_root))}")
    _expect_mapping(value, "organization")
    _expect_mapping(value, "repositories")
    policies = value.get("repository_policies", [])
    if not isinstance(policies, list):
        raise ConfigError("repository_policies must be a list")
    for index, policy in enumerate(policies):
        if not isinstance(policy, dict):
            raise ConfigError(f"repository_policies[{index}] must be a mapping")
        _expect_string_keys(policy, ("repository_policies", index))
        if not isinstance(policy.get("match", {}), dict):
            raise ConfigError(f"repository_policies[{index}].match must be a mapping")
        if not isinstance(policy.get("set"), dict):
            raise ConfigError(f"repository_policies[{index}].set must be a mapping")
        _expect_string_keys(
            policy.get("match", {}), ("repository_policies", index, "match")
        )
        if "name" in policy and not isinstance(policy["name"], str):
            raise ConfigError(f"repository_policies[{index}].name must be a string")
        unknown_policy = set(policy) - {"name", "match", "set"}
        if unknown_policy:
            raise ConfigError(
                f"repository_policies[{index}] has unknown keys: {', '.join(sorted(unknown_policy))}"
            )
        validate_repository_selector(policy.get("match", {}), index)
    validate_config_types(value)
    validate_config_semantics(value)
    return value


def validate_config_types(config: Mapping[str, Any]) -> None:
    """Check the YAML types of known configuration values."""
    _validate_config_value(config, ())


def validate_config_semantics(config: Mapping[str, Any]) -> None:
    """Check relationships between configuration values known without GitHub state."""
    organization = config.get("organization")
    organization_settings = (
        organization.get("settings") if isinstance(organization, Mapping) else None
    )
    organization_forking_policy = (
        organization_settings.get("members_can_fork_private_repositories")
        if isinstance(organization_settings, Mapping)
        and "members_can_fork_private_repositories" in organization_settings
        else None
    )
    organization_allows_forking = (
        organization_forking_policy
        if isinstance(organization_forking_policy, bool)
        else None
    )
    organization_projects_policy = (
        organization_settings.get("has_repository_projects")
        if isinstance(organization_settings, Mapping)
        and "has_repository_projects" in organization_settings
        else None
    )
    organization_allows_projects = (
        organization_projects_policy
        if isinstance(organization_projects_policy, bool)
        else None
    )
    repositories = config.get("repositories")
    if not isinstance(repositories, Mapping):
        return
    items = repositories.get("items")
    if not isinstance(items, Mapping):
        return
    for name, repository in items.items():
        if isinstance(repository, Mapping) and {
            "branch_protections",
            "branch_protection_rules",
        }.issubset(repository):
            raise ConfigError(
                f"repositories.items.{name} cannot contain both "
                "branch_protections and branch_protection_rules; they configure "
                "the same GitHub branch protection state"
            )
        settings = _repository_settings(repository)
        if settings is None:
            continue
        validate_repository_settings_semantics(
            settings,
            f"repositories.items.{name}.settings",
            organization_allows_forking=organization_allows_forking,
            organization_allows_projects=organization_allows_projects,
        )


_UNKNOWN = object()


def validate_repository_settings_semantics(
    settings: Mapping[str, Any],
    path: str,
    *,
    fallback_visibility: Any = _UNKNOWN,
    require_known_visibility: bool = False,
    organization_allows_forking: bool | None = None,
    organization_allows_projects: bool | None = None,
) -> None:
    """Check repository setting requirements using the supplied GitHub context."""
    if "allow_forking" in settings:
        allow_forking = settings["allow_forking"]
        visibility = settings.get("visibility", fallback_visibility)
        field_path = f"{path}.allow_forking"
        requirement = (
            f"{field_path} can be managed only for organization-owned private or "
            "internal repositories"
        )
        if visibility is _UNKNOWN:
            if require_known_visibility:
                raise ConfigError(
                    f"{requirement}; the repository visibility is unavailable"
                )
        elif visibility not in {"private", "internal"}:
            raise ConfigError(
                f"{requirement}; the repository visibility is {visibility!r}"
            )
        organization_policy_satisfies_false = (
            organization_allows_forking is False and allow_forking is False
        )
        if not organization_policy_satisfies_false:
            policy_path = "organization.settings.members_can_fork_private_repositories"
            policy_requirement = f"{field_path} also requires {policy_path} to be true"
            if organization_allows_forking is False:
                raise ConfigError(f"{policy_requirement}; it is false")

    if settings.get("has_projects") is not True:
        return
    field_path = f"{path}.has_projects"
    policy_path = "organization.settings.has_repository_projects"
    requirement = f"{field_path} requires {policy_path} to be true"
    if organization_allows_projects is False:
        raise ConfigError(f"{requirement}; it is false")


def _repository_settings(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    settings = value.get("settings")
    return settings if isinstance(settings, Mapping) else None


def _validate_config_value(value: Any, path: ConfigPath) -> None:
    if _is_read_only_tree(path):
        return
    if _is_app_installation_permissions(path):
        if not isinstance(value, Mapping):
            raise ConfigError(f"{_format_path(path)} must be a mapping")
        _expect_string_keys(value, path)
        for key, permission in value.items():
            _expect_scalar_type(permission, (*path, key), str)
        return
    if _is_content_exclusion_value(path):
        _validate_content_exclusion(value, path)
        return
    if _is_collection_item(path):
        _validate_collection_item(value, path)
        return
    if _is_personal_access_token_permissions_value(path):
        _validate_personal_access_token_permissions(value, path)
        return
    if value is None:
        required_type = _required_scalar_type(path)
        if required_type is not None:
            _expect_scalar_type(value, path, required_type)
        if _allows_null(path) or not _has_known_type_rule(path):
            return

    if _is_custom_property_value(path):
        if isinstance(value, list):
            _validate_string_list(value, path)
        else:
            _expect_scalar_type(value, path, str)
        return

    if _is_string_or_string_list(path):
        if isinstance(value, list):
            _validate_string_list(value, path)
        else:
            _expect_scalar_type(value, path, str)
        return

    if _is_mapping_or_string_list(path):
        if isinstance(value, Mapping):
            _validate_mapping(value, path)
        elif isinstance(value, list):
            _validate_string_list(value, path)
        else:
            raise ConfigError(
                f"{_format_path(path)} must be a mapping or a list of strings"
            )
        return

    if _expects_mapping(path):
        if not isinstance(value, Mapping):
            raise ConfigError(f"{_format_path(path)} must be a mapping")
        _validate_mapping(value, path)
        return

    if _expects_mapping_list(path):
        _validate_mapping_list(value, path)
        return

    expected = _expected_scalar_type(path)
    if expected is not None:
        _expect_scalar_type(value, path, expected)
        return

    if _expects_string_list(path):
        _validate_string_list(value, path)
        return

    if _expects_integer_list(path):
        _validate_integer_list(value, path)
        return

    if isinstance(value, Mapping):
        _validate_mapping(value, path)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_config_value(item, (*path, index))
        return

    if not isinstance(value, (str, bool, int)):
        raise ConfigError(
            f"{_format_path(path)} has unsupported YAML type {_yaml_type(value)}"
        )


def _validate_string_list(value: Any, path: ConfigPath) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"{_format_path(path)} must be a list of strings")
    for index, item in enumerate(value):
        _expect_scalar_type(item, (*path, index), str)


def _validate_integer_list(value: Any, path: ConfigPath) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"{_format_path(path)} must be a list of integers")
    for index, item in enumerate(value):
        _expect_scalar_type(item, (*path, index), int)


def _validate_mapping(value: Mapping[Any, Any], path: ConfigPath) -> None:
    _expect_string_keys(value, path)
    for key, child in value.items():
        _validate_config_value(child, (*path, key))


def _validate_mapping_list(value: Any, path: ConfigPath) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"{_format_path(path)} must be a list of mappings")
    for index, item in enumerate(value):
        item_path = (*path, index)
        if not isinstance(item, Mapping):
            raise ConfigError(f"{_format_path(item_path)} must be a mapping")
        _validate_mapping(item, item_path)


def _expected_scalar_type(
    path: ConfigPath,
) -> type[str | bool | int] | None:
    if not path or isinstance(path[-1], int):
        return None
    key = path[-1]
    if path == ("version",):
        return int
    if key == "version":
        return str
    if key == "id":
        return str if "hosted_runners" in path and "image" in path else int
    if key == "group_id" and "team_sync_groups" in path:
        return str
    if len(path) >= 2 and path[-2] == "security_and_analysis":
        return None
    if key == "secret_scanning":
        return str if _is_code_security_configuration_value(path) else None
    if key == "private_vulnerability_reporting":
        if len(path) >= 2 and path[-2] == "security":
            return bool
        return str if _is_code_security_configuration_value(path) else None
    if key == "immutable_releases":
        return bool if len(path) >= 2 and path[-2] == "security" else None
    if key == "source":
        return None if len(path) >= 2 and path[-2] == "pages" else str
    if key in _INTEGER_FIELDS:
        return int
    if key in _STRING_FIELDS:
        return str
    if key in _BOOLEAN_FIELDS or key.startswith(_BOOLEAN_PREFIXES):
        return bool
    if key.endswith("_enabled") or "_enabled_for_" in key:
        return bool
    return None


def _expects_string_list(path: ConfigPath) -> bool:
    if not path or isinstance(path[-1], int):
        return False
    if _is_ruleset_condition_string_list(path):
        return True
    return path[-1] in _STRING_LIST_FIELDS


def _expects_integer_list(path: ConfigPath) -> bool:
    return bool(path and isinstance(path[-1], str) and path[-1] in _INTEGER_LIST_FIELDS)


def _is_custom_property_value(path: ConfigPath) -> bool:
    return (
        len(path) >= 2
        and path[-2] == "custom_properties"
        and path[:-1] != ("organization", "custom_properties")
    )


def _is_string_or_string_list(path: ConfigPath) -> bool:
    return bool(
        path and (path[-1] == "default_value" or _is_repository_selector_value(path))
    )


def _is_mapping_or_string_list(path: ConfigPath) -> bool:
    return bool(
        path
        and isinstance(path[-1], str)
        and path[-1] in _MAPPING_OR_STRING_LIST_FIELDS
    )


def _expects_mapping(path: ConfigPath) -> bool:
    if path in {("organization",), ("repositories",)}:
        return True
    if (
        len(path) == 3
        and path[0] == "repository_policies"
        and isinstance(path[1], int)
        and path[2] in {"match", "set"}
    ):
        return True
    if path and path[-1] == "items":
        return True
    if path and path[-1] == "announcement":
        return path == ("organization", "announcement")
    if path == ("organization", "immutable_releases"):
        return True
    if len(path) >= 2 and path[-2:] == ("actions", "allowed_actions"):
        return True
    if len(path) >= 2 and path[-2] == "security_and_analysis":
        return True
    if len(path) >= 2 and path[-2:] == ("pages", "source"):
        return True
    if (
        path
        and path[-1] == "secret_scanning"
        and not _is_code_security_configuration_value(path)
    ):
        return True
    if _is_ruleset_condition_selector(path):
        return True
    if path and path[-1] == "required_status_checks":
        if "branch_protection_rules" in path:
            return False
        return not _is_ruleset_parameter_value(path)
    return bool(path and isinstance(path[-1], str) and path[-1] in _MAPPING_FIELDS)


def _expects_mapping_list(path: ConfigPath) -> bool:
    if path == ("repository_policies",):
        return True
    if _is_ruleset_property_condition_list(path):
        return True
    if path and path[-1] == "required_status_checks":
        if "branch_protection_rules" in path:
            return True
        return _is_ruleset_parameter_value(path)
    return bool(path and isinstance(path[-1], str) and path[-1] in _MAPPING_LIST_FIELDS)


def _is_code_security_configuration_value(path: ConfigPath) -> bool:
    return len(path) >= 6 and path[:4] == (
        "organization",
        "code_security",
        "configurations",
        "items",
    )


def _ruleset_item_tail(path: ConfigPath) -> ConfigPath | None:
    for index in range(len(path) - 2):
        if path[index : index + 2] == ("rulesets", "items"):
            return path[index + 3 :]
    return None


def _is_ruleset_parameter_value(path: ConfigPath) -> bool:
    tail = _ruleset_item_tail(path)
    return bool(
        tail
        and len(tail) >= 4
        and tail[-2] == "parameters"
        and isinstance(tail[-3], int)
        and tail[-4] == "rules"
    )


def _is_ruleset_condition_selector(path: ConfigPath) -> bool:
    tail = _ruleset_item_tail(path)
    return bool(
        tail
        and len(tail) == 2
        and tail[0] == "conditions"
        and tail[1]
        in {"ref_name", "repository_id", "repository_name", "repository_property"}
    )


def _is_ruleset_condition_string_list(path: ConfigPath) -> bool:
    tail = _ruleset_item_tail(path)
    return bool(
        tail
        and len(tail) == 3
        and tail[0] == "conditions"
        and tail[1] in {"ref_name", "repository_name"}
        and tail[2] in {"exclude", "include"}
    )


def _is_ruleset_property_condition_list(path: ConfigPath) -> bool:
    tail = _ruleset_item_tail(path)
    return bool(
        tail
        and len(tail) == 3
        and tail[:2] == ("conditions", "repository_property")
        and tail[2] in {"exclude", "include"}
    )


def _is_personal_access_token_permissions_value(path: ConfigPath) -> bool:
    return (
        len(path) >= 5
        and path[:3] == ("organization", "personal_access_tokens", "items")
        and path[4] == "permissions"
    )


def _validate_personal_access_token_permissions(value: Any, path: ConfigPath) -> None:
    if isinstance(value, Mapping):
        _expect_string_keys(value, path)
        for key, child in value.items():
            _validate_personal_access_token_permissions(child, (*path, key))
        return
    if len(path) > 6:
        _expect_scalar_type(value, path, str)
        return
    raise ConfigError(f"{_format_path(path)} must be a mapping")


def _is_content_exclusion_value(path: ConfigPath) -> bool:
    return len(path) >= 3 and path[-3:-1] == ("copilot", "content_exclusion")


def _validate_content_exclusion(value: Any, path: ConfigPath) -> None:
    if not isinstance(value, list):
        raise ConfigError(
            f"{_format_path(path)} must be a list of path strings and condition mappings"
        )
    for index, item in enumerate(value):
        item_path = (*path, index)
        if isinstance(item, str):
            continue
        if not isinstance(item, Mapping):
            raise ConfigError(
                f"{_format_path(item_path)} must be a path string or condition mapping"
            )
        _expect_string_keys(item, item_path)
        for key, condition in item.items():
            condition_path = (*item_path, key)
            if key in {"ifAnyMatch", "ifNoneMatch"}:
                _validate_string_list(condition, condition_path)
            else:
                _validate_config_value(condition, condition_path)


def _allows_null(path: ConfigPath) -> bool:
    if not path or isinstance(path[-1], int):
        return False
    if (
        path[-1] == "app"
        and "branch_protection_rules" in path
        and "required_status_checks" in path
    ):
        return True
    if _is_custom_property_value(path) or path[-1] == "default_value":
        return True
    if path[-1] == "required_status_checks" and _is_ruleset_parameter_value(path):
        return False
    if path[-1] == "name" and "ip_allow_list" in path and "entries" in path:
        return True
    expected = _expected_scalar_type(path)
    if expected is str:
        if _is_repository_selector_value(path):
            return False
        if path[-1] in _NULLABLE_STRING_FIELDS:
            return True
        if path == ("organization", "settings", "name"):
            return True
        if path[-1] == "version" and "hosted_runners" in path and "image" in path:
            return True
    return path[-1] in _NULLABLE_NON_STRING_FIELDS


def _has_known_type_rule(path: ConfigPath) -> bool:
    return any(
        (
            _is_custom_property_value(path),
            _is_string_or_string_list(path),
            _is_mapping_or_string_list(path),
            _expects_mapping(path),
            _expects_mapping_list(path),
            _expected_scalar_type(path) is not None,
            _expects_string_list(path),
            _expects_integer_list(path),
        )
    )


def _is_repository_selector_value(path: ConfigPath) -> bool:
    return (
        len(path) == 4
        and path[0] == "repository_policies"
        and isinstance(path[1], int)
        and path[2] == "match"
        and path[3] in {"name", "exclude", "topics_all"}
    )


def _is_collection_item(path: ConfigPath) -> bool:
    return len(path) >= 3 and path[-2] == "items"


def _validate_collection_item(value: Any, path: ConfigPath) -> None:
    collection = path[-3]
    if collection == "custom_property_values":
        if value is None or isinstance(value, str):
            return
        if isinstance(value, list):
            _validate_string_list(value, path)
            return
        raise ConfigError(
            f"{_format_path(path)} must be a string, a list of strings, or null"
        )
    expects_string = collection in {
        "collaborators",
        "collaborator_invitations",
        "workflow_states",
    } or (collection == "repositories" and path[:2] != ("repositories", "items"))
    if collection == "members" and path[:2] != ("organization", "members"):
        expects_string = True
    if expects_string:
        _expect_scalar_type(value, path, str)
        return
    if collection == "members" and isinstance(value, str):
        return
    if not isinstance(value, Mapping):
        expectation = (
            "a string or a mapping" if collection == "members" else "a mapping"
        )
        raise ConfigError(f"{_format_path(path)} must be {expectation}")
    _expect_string_keys(value, path)
    for key, child in value.items():
        _validate_config_value(child, (*path, key))


def _required_scalar_type(path: ConfigPath) -> type[str] | None:
    if (
        len(path) >= 4
        and path[-4] == "labels"
        and path[-3] == "items"
        and path[-1] in {"name", "color"}
    ):
        return str
    return None


def _is_read_only_tree(path: ConfigPath) -> bool:
    if path and path[0] == "_observed":
        return True
    if any(
        part in path
        for part in (
            "cloud_configuration",
            "discussion_categories",
            "pinned_items",
            "saml_identity_provider",
            "social_preview",
        )
    ):
        return True
    if not path or path[-1] != "_facts":
        return False
    parent = path[:-1]
    return len(parent) == 3 and (
        parent[:2] == ("repositories", "items")
        or (
            parent[0] == "repository_policies"
            and isinstance(parent[1], int)
            and parent[2] == "set"
        )
    )


def _is_app_installation_permissions(path: ConfigPath) -> bool:
    return (
        len(path) >= 5
        and path[:2] == ("organization", "app_installations")
        and path[-1] == "permissions"
    )


def _expect_scalar_type(
    value: Any,
    path: ConfigPath,
    expected: type[str | bool | int],
) -> None:
    if type(value) is expected:
        return
    expected_name = {
        str: "a string",
        bool: "true or false",
        int: "an integer",
    }[expected]
    message = (
        f"{_format_path(path)} must be {expected_name}, but the file contains "
        f"{_yaml_type(value)}"
    )
    if expected is str and isinstance(value, (bool, int, float)):
        message += "; quote the value if it is meant to be text"
    raise ConfigError(message)


def _expect_string_keys(value: Mapping[Any, Any], path: ConfigPath) -> None:
    for key in value:
        if not isinstance(key, str):
            location = _format_path(path) if path else "The configuration root"
            raise ConfigError(f"{location} must use string keys")


def _format_path(path: ConfigPath) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += ("." if result else "") + part
    return result or "The configuration root"


def _yaml_type(value: Any) -> str:
    if isinstance(value, Mapping):
        return "a mapping"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if value is None:
        return "null"
    return type(value).__name__


def dump_config(
    config: Mapping[str, Any],
    *,
    comments: bool = False,
    read_only_fields: Mapping[ConfigPath, str] | None = None,
    caveats: Mapping[ConfigPath, str] | None = None,
) -> str:
    heading = (
        format_comment("github-config version 1")
        + format_comment(
            "Omitting a setting does not reset or delete it; github-config leaves "
            "its current GitHub value unchanged."
        )
        + format_comment(
            "Omitting an entire collection also leaves it unchanged. Inside an "
            "included collection, mode: merge keeps omitted items; mode: exact "
            "removes them when github-config manages removal for that collection."
        )
        + format_comment(
            "Values named *_from_env refer to environment variable names; secret "
            "values are never exported."
        )
        + "\n"
    )
    document = yaml.dump(
        dict(config),
        Dumper=_ConfigDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    if comments:
        document = add_key_comments(
            document,
            config,
            read_only_fields=read_only_fields,
            caveats=caveats,
        )
    return heading + document


def desired_repositories(
    config: Mapping[str, Any],
    current_repositories: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    desired: dict[str, dict[str, Any]] = {}
    for index, policy in enumerate(config.get("repository_policies", [])):
        selector = policy.get("match", {})
        for name, current in current_repositories.items():
            if repository_matches(name, current, selector, policy_index=index):
                desired[name] = deep_merge(desired.get(name, {}), policy["set"])
    repositories = config.get("repositories", {})
    items = collection_items(repositories, "repositories") if repositories else {}
    for name, settings in items.items():
        if not isinstance(settings, dict):
            raise ConfigError(f"repositories.items.{name} must be a mapping")
        configured_settings = settings.get("settings", {})
        configured_name = (
            configured_settings.get("name")
            if isinstance(configured_settings, dict)
            else None
        )
        if (
            isinstance(configured_name, str)
            and configured_name != name
            and configured_name in items
        ):
            raise ConfigError(
                f"repositories.items.{name} and repositories.items.{configured_name} "
                f"both configure repository name {configured_name!r}"
            )
        inherited = desired.get(name, {})
        if (
            isinstance(configured_name, str)
            and configured_name != name
            and configured_name in desired
        ):
            inherited = deep_merge(desired.pop(configured_name), inherited)
        desired[name] = deep_merge(inherited, settings)
    return desired


def repository_matches(
    name: str,
    current: Mapping[str, Any],
    selector: Mapping[str, Any],
    *,
    policy_index: int,
) -> bool:
    validate_repository_selector(selector, policy_index)
    name_patterns = _as_list(
        selector.get("name", "*"), f"repository_policies[{policy_index}].match.name"
    )
    if not any(fnmatch.fnmatchcase(name, pattern) for pattern in name_patterns):
        return False
    excludes = _as_list(
        selector.get("exclude", []),
        f"repository_policies[{policy_index}].match.exclude",
    )
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes):
        return False
    settings = current.get("settings", {})
    facts = current.get("_facts", {})
    scalar_fields = {
        "visibility": "visibility",
        "archived": "archived",
        "fork": "fork",
        "template": "is_template",
    }
    for selector_name, setting_name in scalar_fields.items():
        actual = settings.get(setting_name, facts.get(setting_name))
        if selector_name in selector and actual != selector[selector_name]:
            return False
    if "topics_all" in selector:
        required_topics = set(selector["topics_all"])
        if not required_topics.issubset(set(current.get("topics", []))):
            return False
    if "custom_properties" in selector:
        expected = selector["custom_properties"]
        actual = current.get("custom_properties", {})
        if any(actual.get(key) != value for key, value in expected.items()):
            return False
    return True


def validate_repository_selector(
    selector: Mapping[str, Any], policy_index: int
) -> None:
    unknown = set(selector) - REPOSITORY_SELECTOR_KEYS
    if unknown:
        unknown_names = ", ".join(sorted(unknown))
        raise ConfigError(
            f"repository_policies[{policy_index}].match has unknown selectors: {unknown_names}"
        )
    for key in ("name", "exclude", "topics_all"):
        values = _as_list(
            selector.get(key, "*" if key == "name" else []),
            f"repository_policies[{policy_index}].match.{key}",
        )
        if not all(isinstance(value, str) for value in values):
            raise ConfigError(
                f"repository_policies[{policy_index}].match.{key} must contain strings"
            )
    if "visibility" in selector and not isinstance(selector["visibility"], str):
        raise ConfigError(
            f"repository_policies[{policy_index}].match.visibility must be a string"
        )
    for key in ("archived", "fork", "template"):
        if key in selector and not isinstance(selector[key], bool):
            raise ConfigError(
                f"repository_policies[{policy_index}].match.{key} must be true or false"
            )
    if "custom_properties" in selector:
        expected = selector["custom_properties"]
        if not isinstance(expected, dict):
            raise ConfigError(
                f"repository_policies[{policy_index}].match.custom_properties must be a mapping"
            )


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def collection_items(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    unknown = {key for key in value if not key.startswith("_")} - {"mode", "items"}
    if unknown:
        raise ConfigError(
            f"{path} has unknown collection keys: {', '.join(sorted(unknown))}"
        )
    items = value.get("items", {})
    if not isinstance(items, dict):
        raise ConfigError(f"{path}.items must be a mapping")
    mode = value.get("mode", "merge")
    if mode not in ("merge", "exact"):
        raise ConfigError(f"{path}.mode must be 'merge' or 'exact'")
    return items


def collection_mode(value: Any, path: str) -> str:
    collection_items(value, path)
    return value.get("mode", "merge")


def exact_collection(items: Mapping[str, Any]) -> dict[str, Any]:
    return {"mode": "exact", "items": dict(items)}


def _expect_mapping(root: Mapping[str, Any], key: str) -> None:
    if key in root and not isinstance(root[key], dict):
        raise ConfigError(f"{key} must be a mapping")


def _as_list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (str, bool, int)):
        return [value]
    raise ConfigError(f"{path} must be a scalar or list")
