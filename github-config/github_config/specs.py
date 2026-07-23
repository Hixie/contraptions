from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

APP_INSTALLATION_READ_ONLY_FIELDS = (
    "app_slug",
    "repository_selection",
    "permissions",
    "events",
    "single_file_name",
    "has_multiple_single_files",
    "single_file_paths",
    "suspended_at",
)


@dataclass(frozen=True)
class SingletonSpec:
    key: str
    path: str
    method: str
    fields: tuple[str, ...]
    optional: bool = True
    full_update: bool = True


@dataclass(frozen=True)
class RepositorySetSpec:
    key: str
    path: str


@dataclass(frozen=True)
class OrganizationCollectionSpec:
    key: str
    list_path: str
    item_key: str | None
    identity_field: str
    id_field: str
    fields: tuple[str, ...]
    create_path: str
    update_path: str
    update_method: str
    update_fields: tuple[str, ...]
    delete_path: str
    immutable_fields: tuple[str, ...] = ()


ORGANIZATION_SETTINGS_FIELDS = (
    "advanced_security_enabled_for_new_repositories",
    "billing_email",
    "blog",
    "company",
    "default_repository_permission",
    "dependabot_alerts_enabled_for_new_repositories",
    "dependabot_security_updates_enabled_for_new_repositories",
    "dependency_graph_enabled_for_new_repositories",
    "deploy_keys_enabled_for_repositories",
    "description",
    "email",
    "has_organization_projects",
    "has_repository_projects",
    "location",
    "members_allowed_repository_creation_type",
    "members_can_create_internal_repositories",
    "members_can_create_pages",
    "members_can_create_private_pages",
    "members_can_create_private_repositories",
    "members_can_create_public_pages",
    "members_can_create_public_repositories",
    "members_can_create_repositories",
    "members_can_fork_private_repositories",
    "name",
    "secret_scanning_enabled_for_new_repositories",
    "secret_scanning_push_protection_custom_link",
    "secret_scanning_push_protection_enabled_for_new_repositories",
    "secret_scanning_validity_checks_enabled",
    "twitter_username",
    "web_commit_signoff_required",
)


ORGANIZATION_READ_ONLY_SETTINGS_FIELDS = (
    "default_repository_branch",
    "display_commenter_full_name_setting_enabled",
    "members_can_change_repo_visibility",
    "members_can_create_teams",
    "members_can_delete_issues",
    "members_can_delete_repositories",
    "members_can_invite_outside_collaborators",
    "members_can_view_dependency_insights",
    "readers_can_create_discussions",
    "two_factor_requirement_enabled",
)


ORGANIZATION_RESPONSE_ONLY_NULLABLE_SETTINGS = (
    "billing_email",
    "default_repository_permission",
    "description",
    "members_can_create_repositories",
    "members_can_fork_private_repositories",
    "secret_scanning_push_protection_custom_link",
    "twitter_username",
)

REPOSITORY_RESPONSE_ONLY_NULLABLE_SETTINGS = ("description", "homepage")


REPOSITORY_SETTINGS_FIELDS = (
    "allow_auto_merge",
    "allow_forking",
    "allow_merge_commit",
    "allow_rebase_merge",
    "allow_squash_merge",
    "allow_update_branch",
    "archived",
    "default_branch",
    "delete_branch_on_merge",
    "description",
    "has_issues",
    "has_projects",
    "has_pull_requests",
    "has_wiki",
    "homepage",
    "is_template",
    "merge_commit_message",
    "merge_commit_title",
    "name",
    "pull_request_creation_policy",
    "security_and_analysis",
    "squash_merge_commit_message",
    "squash_merge_commit_title",
    "use_squash_pr_title_as_default",
    "visibility",
    "web_commit_signoff_required",
)


REPOSITORY_READ_ONLY_SETTINGS_FIELDS = (
    "anonymous_access_enabled",
    "hash_algorithm",
)


ORGANIZATION_SINGLETONS = (
    SingletonSpec(
        "actions.permissions",
        "/orgs/{org}/actions/permissions",
        "PUT",
        ("enabled_repositories", "allowed_actions", "sha_pinning_required"),
    ),
    SingletonSpec(
        "actions.allowed_actions",
        "/orgs/{org}/actions/permissions/selected-actions",
        "PUT",
        ("github_owned_allowed", "verified_allowed", "patterns_allowed"),
    ),
    SingletonSpec(
        "actions.workflow_permissions",
        "/orgs/{org}/actions/permissions/workflow",
        "PUT",
        ("default_workflow_permissions", "can_approve_pull_request_reviews"),
    ),
    SingletonSpec(
        "actions.artifact_and_log_retention",
        "/orgs/{org}/actions/permissions/artifact-and-log-retention",
        "PUT",
        ("days",),
    ),
    SingletonSpec(
        "actions.fork_pull_request_approval",
        "/orgs/{org}/actions/permissions/fork-pr-contributor-approval",
        "PUT",
        ("approval_policy",),
    ),
    SingletonSpec(
        "actions.private_fork_pull_request_workflows",
        "/orgs/{org}/actions/permissions/fork-pr-workflows-private-repos",
        "PUT",
        (
            "run_workflows_from_fork_pull_requests",
            "send_write_tokens_to_workflows",
            "send_secrets_and_variables",
            "require_approval_for_fork_pr_workflows",
        ),
    ),
    SingletonSpec(
        "actions.self_hosted_runner_permissions",
        "/orgs/{org}/actions/permissions/self-hosted-runners",
        "PUT",
        ("enabled_repositories",),
    ),
    SingletonSpec(
        "actions.oidc_subject",
        "/orgs/{org}/actions/oidc/customization/sub",
        "PUT",
        ("use_immutable_subject", "include_claim_keys"),
    ),
    SingletonSpec(
        "actions.cache_retention",
        "/organizations/{org}/actions/cache/retention-limit",
        "PUT",
        ("max_cache_retention_days",),
    ),
    SingletonSpec(
        "actions.cache_storage",
        "/organizations/{org}/actions/cache/storage-limit",
        "PUT",
        ("max_cache_size_gb",),
    ),
    SingletonSpec(
        "copilot.coding_agent_permissions",
        "/orgs/{org}/copilot/coding-agent/permissions",
        "PUT",
        ("enabled_repositories",),
    ),
    SingletonSpec(
        "copilot.content_exclusion",
        "/orgs/{org}/copilot/content_exclusion",
        "PUT",
        ("*",),
    ),
    SingletonSpec(
        "immutable_releases",
        "/orgs/{org}/settings/immutable-releases",
        "PUT",
        ("enforced_repositories",),
    ),
    SingletonSpec(
        "secret_scanning.pattern_configurations",
        "/orgs/{org}/secret-scanning/pattern-configurations",
        "PATCH",
        (
            "pattern_config_version",
            "provider_pattern_settings",
            "custom_pattern_settings",
        ),
        full_update=False,
    ),
)


ORGANIZATION_REPOSITORY_SETS = (
    RepositorySetSpec(
        "actions.selected_repositories",
        "/orgs/{org}/actions/permissions/repositories",
    ),
    RepositorySetSpec(
        "actions.self_hosted_runner_repositories",
        "/orgs/{org}/actions/permissions/self-hosted-runners/repositories",
    ),
    RepositorySetSpec(
        "copilot.coding_agent_repositories",
        "/orgs/{org}/copilot/coding-agent/permissions/repositories",
    ),
    RepositorySetSpec(
        "immutable_release_repositories",
        "/orgs/{org}/settings/immutable-releases/repositories",
    ),
)


CODE_SECURITY_CONFIGURATION_FIELDS = (
    "name",
    "description",
    "advanced_security",
    "dependency_graph",
    "dependency_graph_autosubmit_action",
    "dependency_graph_autosubmit_action_options",
    "dependabot_alerts",
    "dependabot_security_updates",
    "dependabot_delegated_alert_dismissal",
    "code_scanning_options",
    "code_scanning_default_setup",
    "code_scanning_default_setup_options",
    "code_scanning_delegated_alert_dismissal",
    "secret_scanning",
    "secret_scanning_push_protection",
    "secret_scanning_delegated_bypass",
    "secret_scanning_delegated_bypass_options",
    "secret_scanning_validity_checks",
    "secret_scanning_non_provider_patterns",
    "secret_scanning_generic_secrets",
    "secret_scanning_delegated_alert_dismissal",
    "secret_scanning_extended_metadata",
    "private_vulnerability_reporting",
    "enforcement",
)


ORGANIZATION_COLLECTIONS = (
    OrganizationCollectionSpec(
        "issue_types",
        "/orgs/{org}/issue-types",
        None,
        "name",
        "id",
        ("name", "is_enabled", "description", "color"),
        "/orgs/{org}/issue-types",
        "/orgs/{org}/issue-types/{id}",
        "PUT",
        ("name", "is_enabled", "description", "color"),
        "/orgs/{org}/issue-types/{id}",
    ),
    OrganizationCollectionSpec(
        "issue_fields",
        "/orgs/{org}/issue-fields",
        None,
        "name",
        "id",
        ("name", "description", "data_type", "visibility", "options"),
        "/orgs/{org}/issue-fields",
        "/orgs/{org}/issue-fields/{id}",
        "PATCH",
        ("name", "description", "visibility", "options"),
        "/orgs/{org}/issue-fields/{id}",
        immutable_fields=("data_type",),
    ),
    OrganizationCollectionSpec(
        "code_security.configurations",
        "/orgs/{org}/code-security/configurations",
        None,
        "name",
        "id",
        CODE_SECURITY_CONFIGURATION_FIELDS,
        "/orgs/{org}/code-security/configurations",
        "/orgs/{org}/code-security/configurations/{id}",
        "PATCH",
        CODE_SECURITY_CONFIGURATION_FIELDS,
        "/orgs/{org}/code-security/configurations/{id}",
    ),
    OrganizationCollectionSpec(
        "hosted_compute.network_configurations",
        "/orgs/{org}/settings/network-configurations",
        "network_configurations",
        "name",
        "id",
        (
            "name",
            "compute_service",
            "network_settings_ids",
            "failover_network_settings_ids",
            "failover_network_enabled",
        ),
        "/orgs/{org}/settings/network-configurations",
        "/orgs/{org}/settings/network-configurations/{id}",
        "PATCH",
        (
            "name",
            "compute_service",
            "network_settings_ids",
            "failover_network_settings_ids",
            "failover_network_enabled",
        ),
        "/orgs/{org}/settings/network-configurations/{id}",
    ),
)


REPOSITORY_SINGLETONS = (
    SingletonSpec(
        "actions.permissions",
        "/repos/{org}/{repo}/actions/permissions",
        "PUT",
        ("enabled", "allowed_actions", "sha_pinning_required"),
    ),
    SingletonSpec(
        "actions.allowed_actions",
        "/repos/{org}/{repo}/actions/permissions/selected-actions",
        "PUT",
        ("github_owned_allowed", "verified_allowed", "patterns_allowed"),
    ),
    SingletonSpec(
        "actions.workflow_permissions",
        "/repos/{org}/{repo}/actions/permissions/workflow",
        "PUT",
        ("default_workflow_permissions", "can_approve_pull_request_reviews"),
    ),
    SingletonSpec(
        "actions.access",
        "/repos/{org}/{repo}/actions/permissions/access",
        "PUT",
        ("access_level",),
    ),
    SingletonSpec(
        "actions.artifact_and_log_retention",
        "/repos/{org}/{repo}/actions/permissions/artifact-and-log-retention",
        "PUT",
        ("days",),
    ),
    SingletonSpec(
        "actions.fork_pull_request_approval",
        "/repos/{org}/{repo}/actions/permissions/fork-pr-contributor-approval",
        "PUT",
        ("approval_policy",),
    ),
    SingletonSpec(
        "actions.private_fork_pull_request_workflows",
        "/repos/{org}/{repo}/actions/permissions/fork-pr-workflows-private-repos",
        "PUT",
        (
            "run_workflows_from_fork_pull_requests",
            "send_write_tokens_to_workflows",
            "send_secrets_and_variables",
            "require_approval_for_fork_pr_workflows",
        ),
    ),
    SingletonSpec(
        "actions.oidc_subject",
        "/repos/{org}/{repo}/actions/oidc/customization/sub",
        "PUT",
        ("use_default", "use_immutable_subject", "include_claim_keys"),
    ),
    SingletonSpec(
        "actions.cache_retention",
        "/repos/{org}/{repo}/actions/cache/retention-limit",
        "PUT",
        ("max_cache_retention_days",),
    ),
    SingletonSpec(
        "actions.cache_storage",
        "/repos/{org}/{repo}/actions/cache/storage-limit",
        "PUT",
        ("max_cache_size_gb",),
    ),
    SingletonSpec(
        "code_scanning.default_setup",
        "/repos/{org}/{repo}/code-scanning/default-setup",
        "PATCH",
        (
            "state",
            "query_suite",
            "languages",
            "runner_type",
            "runner_label",
            "threat_model",
        ),
        full_update=False,
    ),
    SingletonSpec(
        "code_quality.setup",
        "/repos/{org}/{repo}/code-quality/setup",
        "PATCH",
        ("state", "runner_type", "runner_label", "languages", "ai_findings_option"),
        full_update=False,
    ),
)


SECURITY_TOGGLES = {
    "automated_security_fixes": "/repos/{org}/{repo}/automated-security-fixes",
    "immutable_releases": "/repos/{org}/{repo}/immutable-releases",
    "private_vulnerability_reporting": "/repos/{org}/{repo}/private-vulnerability-reporting",
    "vulnerability_alerts": "/repos/{org}/{repo}/vulnerability-alerts",
}


BUDGET_FIELDS = (
    "budget_amount",
    "prevent_further_usage",
    "budget_alerting",
    "budget_scope",
    "budget_entity_name",
    "budget_type",
    "budget_product_sku",
    "user",
)


PRIVATE_REGISTRY_FIELDS = (
    "registry_type",
    "url",
    "username",
    "replaces_base",
    "visibility",
    "auth_type",
    "tenant_id",
    "client_id",
    "aws_region",
    "account_id",
    "role_name",
    "domain",
    "domain_owner",
    "jfrog_oidc_provider_name",
    "audience",
    "identity_mapping_name",
    "namespace",
    "service_slug",
    "api_host",
    "workload_identity_provider",
    "service_account",
)


def without_organization_collection_response_metadata(
    key: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = dict(item)
    if key == "issue_fields" and isinstance(normalized.get("options"), list):
        normalized["options"] = [
            {
                option_key: option_value
                for option_key, option_value in option.items()
                if option_key not in {"created_at", "updated_at"}
            }
            if isinstance(option, Mapping)
            else option
            for option in normalized["options"]
        ]
    if key == "code_security.configurations":
        bypass_options = normalized.get("secret_scanning_delegated_bypass_options")
        if isinstance(bypass_options, Mapping) and isinstance(
            bypass_options.get("reviewers"), list
        ):
            normalized_options = dict(bypass_options)
            normalized_options["reviewers"] = [
                {
                    reviewer_key: reviewer_value
                    for reviewer_key, reviewer_value in reviewer.items()
                    if reviewer_key != "security_configuration_id"
                }
                if isinstance(reviewer, Mapping)
                else reviewer
                for reviewer in bypass_options["reviewers"]
            ]
            normalized["secret_scanning_delegated_bypass_options"] = normalized_options
    return normalized


def without_organization_collection_response_only_nulls(
    key: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = dict(item)
    if key != "code_security.configurations":
        return normalized
    for field in ("description", "dependabot_delegated_alert_dismissal"):
        if normalized.get(field) is None:
            normalized.pop(field, None)
    options = normalized.get("code_scanning_default_setup_options")
    if isinstance(options, Mapping) and options.get("runner_type") is None:
        normalized_options = dict(options)
        normalized_options.pop("runner_type", None)
        normalized["code_scanning_default_setup_options"] = normalized_options
    return normalized


def organization_collection_request_body(
    key: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = without_organization_collection_response_metadata(key, item)
    normalized = without_organization_collection_response_only_nulls(key, normalized)
    if key == "hosted_compute.network_configurations" and normalized.get(
        "compute_service"
    ) not in ("none", "actions"):
        normalized.pop("compute_service", None)
    return normalized


def organization_collection_create_body(
    key: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = organization_collection_request_body(key, item)
    if key == "issue_fields" and isinstance(normalized.get("options"), list):
        normalized["options"] = [
            {
                option_key: option_value
                for option_key, option_value in option.items()
                if option_key != "id"
            }
            if isinstance(option, Mapping)
            else option
            for option in normalized["options"]
        ]
    return normalized


def without_organization_response_only_nulls(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if value is not None or key not in ORGANIZATION_RESPONSE_ONLY_NULLABLE_SETTINGS
    }


def without_repository_response_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    security_and_analysis = normalized.get("security_and_analysis")
    if isinstance(security_and_analysis, Mapping):
        normalized_security = dict(security_and_analysis)
        normalized_security.pop("dependabot_security_updates", None)
        normalized["security_and_analysis"] = normalized_security
    return normalized


def without_repository_response_only_nulls(
    item: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if value is not None or key not in REPOSITORY_RESPONSE_ONLY_NULLABLE_SETTINGS
    }


def without_singleton_response_only_values(
    key: str, item: Mapping[str, Any]
) -> dict[str, Any]:
    normalized = dict(item)
    unsupported_languages = {
        "code_scanning.default_setup": {"javascript", "typescript"},
        "code_quality.setup": {"rust"},
    }.get(key)
    if unsupported_languages is None:
        return normalized
    if normalized.get("runner_type") is None:
        normalized.pop("runner_type", None)
    languages = normalized.get("languages")
    if isinstance(languages, list) and any(
        isinstance(language, str) and language in unsupported_languages
        for language in languages
    ):
        normalized.pop("languages", None)
    return normalized
