from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .api import ApiClient, ApiError, quote, with_query
from .config import (
    exact_collection,
    validate_config_semantics,
    validate_config_types,
)
from .graphql import (
    BRANCH_PROTECTION_RULE_ACTORS_QUERY,
    ORGANIZATION_CONFIGURATION_QUERY,
    ORGANIZATION_CUSTOM_PROPERTIES_QUERY,
    ORGANIZATION_DOMAINS_QUERY,
    ORGANIZATION_IP_ALLOW_LIST_QUERY,
    REPOSITORY_BRANCH_PROTECTION_RULES_QUERY,
    REPOSITORY_CONFIGURATION_QUERY,
    TEAM_REVIEW_ASSIGNMENT_QUERY,
)
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
    without_organization_collection_response_metadata,
    without_repository_response_metadata,
)
from .util import get_path, pick, set_path, sorted_mapping, without_none

_MISSING: None = None
_OPTIONAL_UNAVAILABLE_STATUSES = {402, 403, 404, 409, 422}
_REPOSITORY_PERMISSION_RANK = {
    "pull": 0,
    "triage": 1,
    "push": 2,
    "maintain": 3,
    "admin": 4,
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
_BRANCH_PROTECTION_ACTOR_FIELDS = {
    "bypass_force_push_actors": "bypassForcePushAllowances",
    "bypass_pull_request_actors": "bypassPullRequestAllowances",
    "push_actors": "pushAllowances",
    "review_dismissal_actors": "reviewDismissalAllowances",
}
_BRANCH_PROTECTION_ACTOR_CURSORS = {
    "bypassForcePushAllowances": "bypassForcePushCursor",
    "bypassPullRequestAllowances": "bypassPullRequestCursor",
    "pushAllowances": "pushCursor",
    "reviewDismissalAllowances": "reviewDismissalCursor",
}


@dataclass
class Snapshot:
    config: dict[str, Any]
    ids: dict[tuple[str, ...], int | str] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    unavailable_collections: dict[tuple[str, ...], str] = field(default_factory=dict)
    read_only_items: dict[tuple[str, ...], str] = field(default_factory=dict)
    read_only_identities: dict[tuple[str, ...], str] = field(default_factory=dict)
    read_only_runner_group_runners: dict[str, str] = field(default_factory=dict)
    unreadable_inherited_runner_assignments: list[str] = field(default_factory=list)
    read_only_fields: dict[tuple[str | int, ...], str] = field(default_factory=dict)
    comment_caveats: dict[tuple[str | int, ...], str] = field(default_factory=dict)

    @property
    def comment_read_only_fields(self) -> dict[tuple[str | int, ...], str]:
        fields = dict(self.read_only_fields)
        for path, reason in self.read_only_items.items():
            fields.setdefault(_configuration_item_path(path), reason)
        return fields


class Exporter:
    def __init__(self, api: ApiClient) -> None:
        self.api = api
        self.ids: dict[tuple[str, ...], int | str] = {}
        self.unavailable: list[str] = []
        self.unavailable_collections: dict[tuple[str, ...], str] = {}
        self.read_only_items: dict[tuple[str, ...], str] = {}
        self.read_only_identities: dict[tuple[str, ...], str] = {}
        self.read_only_runner_group_runners: dict[str, str] = {}
        self.unreadable_inherited_runner_assignments: list[str] = []
        self.read_only_fields: dict[tuple[str | int, ...], str] = {}
        self.comment_caveats: dict[tuple[str | int, ...], str] = {}
        self.organization_allows_forking: bool | None = None
        self.organization_allows_projects: bool | None = None

    def export(self, org: str) -> Snapshot:
        self.ids = {}
        self.unavailable = []
        self.unavailable_collections = {}
        self.read_only_items = {}
        self.read_only_identities = {}
        self.read_only_runner_group_runners = {}
        self.unreadable_inherited_runner_assignments = []
        self.read_only_fields = {}
        self.comment_caveats = {}
        self.organization_allows_forking = None
        self.organization_allows_projects = None
        organization_response = self.api.request("GET", f"/orgs/{quote(org)}").data
        if not isinstance(organization_response, dict):
            raise TypeError(f"GitHub API did not return an organization for {org}")
        canonical_org = str(organization_response["login"])
        organization_forking_policy = organization_response.get(
            "members_can_fork_private_repositories"
        )
        self.organization_allows_forking = (
            organization_forking_policy
            if isinstance(organization_forking_policy, bool)
            else None
        )
        organization_projects_policy = organization_response.get(
            "has_repository_projects"
        )
        self.organization_allows_projects = (
            organization_projects_policy
            if isinstance(organization_projects_policy, bool)
            else None
        )
        self.ids[("organization",)] = int(organization_response["id"])
        repository_summaries = self._optional_list(
            with_query(
                f"/orgs/{quote(canonical_org)}/repos", type="all", sort="full_name"
            ),
            required=True,
        )
        if repository_summaries is None:
            raise RuntimeError(
                f"GitHub API did not return repositories for {canonical_org}"
            )
        for summary in repository_summaries:
            repository_id = int(summary["id"])
            repository_name = str(summary["name"])
            self.ids[("repositories", repository_name)] = repository_id
            self.ids[("repository_names", str(repository_id))] = repository_name
        organization: dict[str, Any] = {
            "settings": pick(
                organization_response,
                (
                    *ORGANIZATION_SETTINGS_FIELDS,
                    *ORGANIZATION_READ_ONLY_SETTINGS_FIELDS,
                ),
            ),
        }
        for field_name in ORGANIZATION_READ_ONLY_SETTINGS_FIELDS:
            if field_name in organization["settings"]:
                self._mark_field_read_only(
                    ("organization", "settings", field_name),
                    "GitHub exposes this organization setting for inspection but "
                    "does not provide a public API that changes it.",
                )
        if "secret_scanning_push_protection_custom_link" in organization["settings"]:
            self._mark_comment_caveat(
                (
                    "organization",
                    "settings",
                    "secret_scanning_push_protection_custom_link",
                ),
                "GitHub's public API does not expose the separate switch that "
                "enables this custom link.",
            )
        self._export_organization_graphql(organization, canonical_org)
        self._export_announcement(organization, canonical_org)
        self._export_custom_repository_roles(organization, canonical_org)
        self._export_custom_organization_roles(organization, canonical_org)
        self._export_app_installations(organization, canonical_org)
        self._export_copilot_policies(organization, canonical_org)
        self._export_singletons(
            organization, ORGANIZATION_SINGLETONS, org=canonical_org
        )
        self._export_repository_sets(organization, canonical_org)
        self._export_oidc_property_inclusions(organization, canonical_org)
        self._export_dependabot_access(organization, canonical_org)
        self._export_organization_collections(organization, canonical_org)
        if get_path(organization, "code_security.configurations") is not None:
            self._mark_comment_caveat(
                ("organization", "code_security", "configurations"),
                "GitHub accepts the write-only code_security and secret_protection "
                "aggregate inputs but never returns them. They are intentionally "
                "absent, and updates leave their current values unchanged.",
            )
        self._export_code_security_assignments(organization, canonical_org)
        self._export_runner_groups(organization, canonical_org)
        self._export_self_hosted_runners(organization, canonical_org)
        self._export_hosted_runners(organization, canonical_org)
        self._export_blocked_users(organization, canonical_org)
        self._export_copilot_seats(organization, canonical_org)
        self._export_interaction_limit(organization, f"/orgs/{quote(canonical_org)}")
        self._export_budgets(organization, canonical_org)
        self._export_custom_patterns(
            organization,
            f"/orgs/{quote(canonical_org)}/secret-scanning",
            ("organization",),
        )
        self._export_private_registries(organization, canonical_org)
        self._export_members(organization, canonical_org)
        self._export_teams(organization, canonical_org, repository_summaries)
        self._export_organization_invitations(organization, canonical_org)
        self._export_outside_collaborators(organization, canonical_org)
        self._export_personal_access_tokens(organization, canonical_org)
        self._export_credential_authorizations(organization, canonical_org)
        self._export_organization_roles(organization, canonical_org)
        self._export_security_managers(organization, canonical_org)
        self._export_variables_and_secrets(organization, canonical_org)
        self._export_rulesets(
            organization, f"/orgs/{quote(canonical_org)}", ("organization",)
        )
        self._export_hooks(
            organization, f"/orgs/{quote(canonical_org)}", ("organization",)
        )
        self._export_custom_property_schema(organization, canonical_org)
        self._export_organization_custom_property_values(organization, canonical_org)

        repositories: dict[str, Any] = {}
        for summary in sorted(
            repository_summaries, key=lambda item: item["name"].casefold()
        ):
            name = str(summary["name"])
            repositories[name] = self._export_repository(canonical_org, name)

        config: dict[str, Any] = {
            "version": 1,
            "organization": organization,
            "repositories": {"mode": "merge", "items": repositories},
            "_observed": {
                "organization": canonical_org,
                "api_version": self.api.api_version,
            },
        }
        if self.unavailable:
            config["_observed"]["unavailable"] = sorted(set(self.unavailable))
        validate_config_types(config)
        validate_config_semantics(config)
        return Snapshot(
            config=config,
            ids=dict(self.ids),
            unavailable=sorted(set(self.unavailable)),
            unavailable_collections=dict(self.unavailable_collections),
            read_only_items=dict(self.read_only_items),
            read_only_identities=dict(self.read_only_identities),
            read_only_runner_group_runners=dict(self.read_only_runner_group_runners),
            unreadable_inherited_runner_assignments=list(
                self.unreadable_inherited_runner_assignments
            ),
            read_only_fields=dict(self.read_only_fields),
            comment_caveats=dict(self.comment_caveats),
        )

    def _mark_collection_unavailable(
        self, config_path: tuple[str, ...], endpoint: str
    ) -> None:
        self.unavailable_collections.setdefault(config_path, endpoint)

    def _mark_item_read_only(self, path: tuple[str, ...], reason: str) -> None:
        self.read_only_items.setdefault(path, reason)

    def _mark_field_read_only(self, path: tuple[str | int, ...], reason: str) -> None:
        self.read_only_fields.setdefault(path, reason)

    def _mark_comment_caveat(self, path: tuple[str | int, ...], reason: str) -> None:
        existing = self.comment_caveats.get(path)
        if existing is None:
            self.comment_caveats[path] = reason
        elif reason not in existing:
            self.comment_caveats[path] = f"{existing} {reason}"

    def _mark_identity_read_only(
        self, collection_path: tuple[str, ...], identity: str, reason: str
    ) -> None:
        self.read_only_identities.setdefault((*collection_path, identity), reason)

    def _optional_graphql(
        self,
        document: str,
        variables: Mapping[str, Any],
        operation: str,
        caveat_paths: Iterable[tuple[str | int, ...]] = (),
    ) -> Mapping[str, Any] | None:
        try:
            data = self.api.graphql(document, variables)
        except ApiError as error:
            self.unavailable.append(f"/graphql#{operation} ({error.status})")
            return None
        errors = getattr(data, "errors", ())
        if errors:
            self.unavailable.append(
                f"/graphql#{operation} (partial: {'; '.join(errors)})"
            )
            for path in caveat_paths:
                self._mark_comment_caveat(
                    path,
                    "GitHub returned only part of this GraphQL query. Fields the "
                    "ambient token could not read are absent from this export.",
                )
        return data

    def _export_organization_graphql(self, target: dict[str, Any], org: str) -> None:
        self._export_ip_allow_list(target, org)
        self._export_domains(target, org)
        data = self._optional_graphql(
            ORGANIZATION_CONFIGURATION_QUERY,
            {"login": org},
            "OrganizationConfiguration",
            (
                ("organization", "notification_restriction_enabled"),
                ("organization", "saml_identity_provider"),
                ("organization", "pinned_items"),
            ),
        )
        organization = data.get("organization") if data is not None else None
        if not isinstance(organization, Mapping):
            for path in (
                ("organization", "notification_restriction_enabled"),
                ("organization", "saml_identity_provider"),
                ("organization", "pinned_items"),
            ):
                self._mark_collection_unavailable(
                    path, "/graphql#OrganizationConfiguration"
                )
            return

        node_id = organization.get("id")
        if isinstance(node_id, str):
            self.ids[("organization", "node_id")] = node_id

        restriction = organization.get("notificationDeliveryRestrictionEnabledSetting")
        if restriction in {"ENABLED", "DISABLED"}:
            target["notification_restriction_enabled"] = restriction == "ENABLED"
        else:
            self._mark_collection_unavailable(
                ("organization", "notification_restriction_enabled"),
                "/graphql#OrganizationConfiguration",
            )

        saml = organization.get("samlIdentityProvider")
        if isinstance(saml, Mapping):
            target["saml_identity_provider"] = without_none(
                {
                    "digest_method": saml.get("digestMethod"),
                    "idp_certificate": saml.get("idpCertificate"),
                    "issuer": saml.get("issuer"),
                    "signature_method": saml.get("signatureMethod"),
                    "sso_url": saml.get("ssoUrl"),
                }
            )
            self._mark_field_read_only(
                ("organization", "saml_identity_provider"),
                "GitHub's public organization APIs expose this SAML configuration "
                "but do not provide an operation that changes it.",
            )

        pinned = organization.get("pinnedItems")
        if isinstance(pinned, Mapping):
            items: dict[str, Any] = {}
            for node in _connection_nodes(pinned):
                typename = str(node.get("__typename", "Item"))
                identity = str(
                    node.get("nameWithOwner")
                    or node.get("name")
                    or node.get("id")
                    or len(items)
                )
                key = f"{typename}:{identity}"
                items[key] = without_none(
                    {
                        "type": typename,
                        "name": node.get("name"),
                        "name_with_owner": node.get("nameWithOwner"),
                        "description": node.get("description"),
                    }
                )
            target["pinned_items"] = exact_collection(sorted_mapping(items))
            self._mark_field_read_only(
                ("organization", "pinned_items"),
                "GitHub exposes pinned organization profile items but does not "
                "provide a public operation that changes them.",
            )
            if _connection_has_next_page(pinned):
                self._mark_comment_caveat(
                    ("organization", "pinned_items"),
                    "GitHub's GraphQL API does not support independently paginating "
                    "more than 100 pinned profile items in this query.",
                )

    def _export_ip_allow_list(self, target: dict[str, Any], org: str) -> None:
        nodes: list[Mapping[str, Any]] = []
        cursor: str | None = None
        settings: Mapping[str, Any] | None = None
        entries_complete = True
        while True:
            data = self._optional_graphql(
                ORGANIZATION_IP_ALLOW_LIST_QUERY,
                {"login": org, "cursor": cursor},
                "OrganizationIpAllowList",
                (("organization", "ip_allow_list"),),
            )
            if getattr(data, "errors", ()):
                entries_complete = False
            organization = data.get("organization") if data is not None else None
            if not isinstance(organization, Mapping):
                self._mark_collection_unavailable(
                    ("organization", "ip_allow_list"),
                    "/graphql#OrganizationIpAllowList",
                )
                return
            if isinstance(organization, Mapping) and isinstance(
                organization.get("id"), str
            ):
                self.ids[("organization", "node_id")] = str(organization["id"])
            settings = organization
            connection = organization.get("ipAllowListEntries")
            if not isinstance(connection, Mapping):
                break
            nodes.extend(_connection_nodes(connection))
            if not _connection_has_next_page(connection):
                break
            cursor = _connection_end_cursor(connection)
            if cursor is None:
                entries_complete = False
                self._mark_comment_caveat(
                    ("organization", "ip_allow_list", "entries"),
                    "GitHub reported additional IP allow-list entries without a "
                    "cursor. The returned entries use merge mode so a later apply "
                    "cannot remove entries missing from this export.",
                )
                break
        if settings is None:
            return
        if settings.get("ipAllowListEnabledSetting") not in {
            "ENABLED",
            "DISABLED",
        } or settings.get("ipAllowListForInstalledAppsEnabledSetting") not in {
            "ENABLED",
            "DISABLED",
        }:
            self._mark_collection_unavailable(
                ("organization", "ip_allow_list"),
                "/graphql#OrganizationIpAllowList",
            )
            return
        items: dict[str, Any] = {}
        for entry in nodes:
            entry_id = entry.get("id")
            value = str(entry.get("allowListValue", ""))
            preferred = value or str(entry.get("name") or entry_id)
            key = _unique_key(
                items,
                preferred,
                str(entry_id or preferred),
                duplicate=preferred in items,
            )
            if isinstance(entry_id, str):
                self.ids[("ip_allow_list_entries", key)] = entry_id
            items[key] = without_none(
                {
                    "value": entry.get("allowListValue"),
                    "name": entry.get("name"),
                    "active": entry.get("isActive"),
                }
            )
        target["ip_allow_list"] = {
            "enabled": settings.get("ipAllowListEnabledSetting") == "ENABLED",
            "applies_to_installed_apps": (
                settings.get("ipAllowListForInstalledAppsEnabledSetting") == "ENABLED"
            ),
            "entries": {
                "mode": "exact" if entries_complete else "merge",
                "items": sorted_mapping(items),
            },
        }
        self._mark_comment_caveat(
            ("organization", "ip_allow_list"),
            "GitHub does not expose the user-level IP allow-list enforcement "
            "setting for reading, so it is intentionally absent.",
        )

    def _export_domains(self, target: dict[str, Any], org: str) -> None:
        nodes: list[Mapping[str, Any]] = []
        cursor: str | None = None
        complete = True
        while True:
            data = self._optional_graphql(
                ORGANIZATION_DOMAINS_QUERY,
                {"login": org, "cursor": cursor},
                "OrganizationDomains",
                (("organization", "domains"),),
            )
            if getattr(data, "errors", ()):
                complete = False
            organization = data.get("organization") if data is not None else None
            connection = (
                organization.get("domains")
                if isinstance(organization, Mapping)
                else None
            )
            if not isinstance(connection, Mapping):
                self._mark_collection_unavailable(
                    ("organization", "domains"), "/graphql#OrganizationDomains"
                )
                return
            if isinstance(organization, Mapping) and isinstance(
                organization.get("id"), str
            ):
                self.ids[("organization", "node_id")] = str(organization["id"])
            nodes.extend(_connection_nodes(connection))
            if not _connection_has_next_page(connection):
                break
            cursor = _connection_end_cursor(connection)
            if cursor is None:
                complete = False
                self._mark_comment_caveat(
                    ("organization", "domains"),
                    "GitHub reported additional domains without a cursor. The "
                    "returned domains use merge mode so a later apply cannot remove "
                    "domains missing from this export.",
                )
                break
        items: dict[str, Any] = {}
        for domain in nodes:
            name = str(domain.get("domain") or domain.get("id"))
            domain_id = domain.get("id")
            if isinstance(domain_id, str):
                self.ids[("domains", name)] = domain_id
            items[name] = without_none(
                {
                    "approved": domain.get("isApproved"),
                    "verified": domain.get("isVerified"),
                    "required_for_policy_enforcement": domain.get(
                        "isRequiredForPolicyEnforcement"
                    ),
                    "verification_token": domain.get("verificationToken"),
                    "token_expires_at": domain.get("tokenExpirationTime"),
                }
            )
            for field_name in (
                "required_for_policy_enforcement",
                "verification_token",
                "token_expires_at",
            ):
                if field_name in items[name]:
                    self._mark_field_read_only(
                        ("organization", "domains", "items", name, field_name),
                        "GitHub calculates this domain value and does not accept it "
                        "as mutation input.",
                    )
        target["domains"] = {
            "mode": "exact" if complete else "merge",
            "items": sorted_mapping(items),
        }
        self._mark_comment_caveat(
            ("organization", "domains"),
            "Domain approval and verification are one-way API operations. DNS "
            "records must satisfy GitHub before verification can succeed.",
        )

    def _export_announcement(self, target: dict[str, Any], org: str) -> None:
        path = f"/orgs/{quote(org)}/announcement"
        announcement = self._optional_get(path)
        if isinstance(announcement, Mapping):
            target["announcement"] = {
                "enabled": True,
                **pick(
                    announcement,
                    ("announcement", "expires_at", "user_dismissible"),
                ),
            }
        elif announcement is _MISSING:
            self._mark_collection_unavailable(("organization", "announcement"), path)

    def _export_custom_repository_roles(self, target: dict[str, Any], org: str) -> None:
        path = f"/orgs/{quote(org)}/custom-repository-roles"
        roles = self._optional_list(path, item_key="custom_roles")
        if roles is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "custom_repository_roles"), path
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(role["name"]) for role in roles)
        for role in roles:
            role_id = int(role["id"])
            preferred = str(role["name"])
            key = _unique_key(
                items,
                preferred,
                role_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("custom_repository_roles", key)] = role_id
            items[key] = pick(role, ("name", "description", "base_role", "permissions"))
        target["custom_repository_roles"] = exact_collection(sorted_mapping(items))

    def _export_custom_organization_roles(
        self, target: dict[str, Any], org: str
    ) -> None:
        path = f"/orgs/{quote(org)}/organization-roles"
        roles = self._optional_list(path, item_key="roles")
        if roles is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "custom_organization_roles"), path
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(role["name"]) for role in roles)
        for role in roles:
            role_id = int(role["id"])
            preferred = str(role["name"])
            key = _unique_key(
                items,
                preferred,
                role_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("custom_organization_roles", key)] = role_id
            items[key] = pick(
                role,
                ("name", "description", "base_role", "permissions", "source"),
            )
            if role.get("source") != "Organization":
                self._mark_field_read_only(
                    (
                        "organization",
                        "custom_organization_roles",
                        "items",
                        key,
                    ),
                    "This role is supplied by GitHub or the enterprise. Organization "
                    "custom-role operations cannot change it.",
                )
        target["custom_organization_roles"] = exact_collection(sorted_mapping(items))

    def _export_app_installations(self, target: dict[str, Any], org: str) -> None:
        path = f"/orgs/{quote(org)}/installations"
        installations = self._optional_list(path, item_key="installations")
        if installations is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "app_installations"), path
            )
            return
        items: dict[str, Any] = {}
        for installation in installations:
            installation_id = int(installation["id"])
            app_slug = str(
                installation.get("app_slug")
                or installation.get("app_id")
                or installation_id
            )
            key = _unique_key(
                items,
                app_slug,
                installation_id,
                duplicate=app_slug in items,
            )
            app_node_id = installation.get("app_node_id")
            if isinstance(app_node_id, str):
                self.ids[("apps", app_slug, "node_id")] = app_node_id
            self.ids[("app_installations", key)] = installation_id
            item = without_none(pick(installation, APP_INSTALLATION_READ_ONLY_FIELDS))
            for field_name in item:
                self._mark_field_read_only(
                    (
                        "organization",
                        "app_installations",
                        "items",
                        key,
                        field_name,
                    ),
                    "GitHub returns this installation value for inspection. "
                    "Changing it requires the GitHub App installation flow.",
                )
            if installation.get("repository_selection") == "selected":
                repositories_path = (
                    f"/user/installations/{installation_id}/repositories"
                )
                repositories = self._optional_list(
                    repositories_path,
                    item_key="repositories",
                )
                if repositories is _MISSING:
                    self._mark_collection_unavailable(
                        (
                            "organization",
                            "app_installations",
                            "items",
                            key,
                            "selected_repositories",
                        ),
                        repositories_path,
                    )
                    self._mark_comment_caveat(
                        (
                            "organization",
                            "app_installations",
                            "items",
                            key,
                        ),
                        "The selected repository list could not be read and is "
                        "absent from this export.",
                    )
                else:
                    selected_names: list[str] = []
                    for repository in repositories:
                        if not isinstance(repository, Mapping):
                            continue
                        name = repository.get("name")
                        repository_id = repository.get("id")
                        if isinstance(name, str):
                            selected_names.append(name)
                        if isinstance(name, str) and type(repository_id) is int:
                            self.ids.setdefault(("repositories", name), repository_id)
                    item["selected_repositories"] = sorted(
                        selected_names,
                        key=str.casefold,
                    )
                self._mark_comment_caveat(
                    (
                        "organization",
                        "app_installations",
                        "items",
                        key,
                        "selected_repositories",
                    ),
                    "GitHub returns only repositories the ambient user can access "
                    "for this installation, so repositories outside that user's "
                    "access are absent. Updates require a classic personal access "
                    "token with the repo scope. Removing repository access is "
                    "blocked unless `--force` authorizes the removal.",
                )
            items[key] = item
        target["app_installations"] = exact_collection(sorted_mapping(items))

    def _export_copilot_policies(self, target: dict[str, Any], org: str) -> None:
        path = f"/orgs/{quote(org)}/copilot/billing"
        details = self._optional_get(path)
        if details is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "copilot", "policies"), path
            )
            return
        if not isinstance(details, Mapping):
            return
        policies = pick(
            details,
            (
                "public_code_suggestions",
                "ide_chat",
                "platform_chat",
                "cli",
                "seat_management_setting",
                "plan_type",
            ),
        )
        if policies:
            set_path(target, "copilot.policies", policies)
            self._mark_field_read_only(
                ("organization", "copilot", "policies"),
                "GitHub exposes these Copilot policies through the billing API but "
                "does not provide corresponding public update operations.",
            )

    def _export_singletons(
        self,
        target: dict[str, Any],
        specs: Iterable[SingletonSpec],
        *,
        org: str,
        repo: str | None = None,
    ) -> None:
        for spec in specs:
            path = spec.path.format(
                org=quote(org), repo=quote(repo) if repo is not None else ""
            )
            value = self._optional_get(path, required=not spec.optional)
            if value is _MISSING:
                continue
            if not isinstance(value, dict):
                self.unavailable.append(f"{path} (unexpected response)")
                continue
            fields = spec.fields
            if spec.key == "secret_scanning.pattern_configurations":
                normalized = _normalize_pattern_configurations(value)
            else:
                normalized = dict(value) if fields == ("*",) else pick(value, fields)
            if normalized:
                set_path(target, spec.key, normalized)

    def _export_repository_sets(self, target: dict[str, Any], org: str) -> None:
        for spec in ORGANIZATION_REPOSITORY_SETS:
            repositories = self._optional_list(
                spec.path.format(org=quote(org)),
                item_key="repositories",
            )
            if repositories is not _MISSING:
                set_path(
                    target,
                    spec.key,
                    sorted(str(repository["name"]) for repository in repositories),
                )

    def _export_oidc_property_inclusions(
        self, target: dict[str, Any], org: str
    ) -> None:
        values = self._optional_list(
            f"/orgs/{quote(org)}/actions/oidc/customization/properties/repo"
        )
        if values is not _MISSING:
            set_path(
                target,
                "actions.oidc_custom_properties",
                sorted(
                    str(value["custom_property_name"])
                    for value in values
                    if value.get("inclusion_source") == "organization"
                ),
            )

    def _export_dependabot_access(self, target: dict[str, Any], org: str) -> None:
        value = self._optional_get(f"/orgs/{quote(org)}/dependabot/repository-access")
        if value is _MISSING or not isinstance(value, dict):
            return
        repositories = value.get("accessible_repositories", [])
        set_path(
            target,
            "dependabot.repository_access",
            {
                "default_level": value.get("default_level"),
                "repositories": sorted(
                    str(repository["name"]) for repository in repositories
                ),
            },
        )

    def _export_organization_collections(
        self, target: dict[str, Any], org: str
    ) -> None:
        for spec in ORGANIZATION_COLLECTIONS:
            values = self._optional_list(
                spec.list_path.format(org=quote(org)),
                item_key=spec.item_key,
            )
            if values is _MISSING:
                self._mark_collection_unavailable(
                    ("organization", *spec.key.split(".")),
                    spec.list_path.format(org=quote(org)),
                )
                continue
            items: dict[str, Any] = {}
            identities = Counter(
                str(value[spec.identity_field])
                for value in values
                if isinstance(value, dict) and spec.identity_field in value
            )
            for value in values:
                if not isinstance(value, dict):
                    continue
                resource_id = value.get(spec.id_field)
                preferred = str(value[spec.identity_field])
                key = _unique_key(
                    items,
                    preferred,
                    str(resource_id),
                    duplicate=identities[preferred] > 1,
                )
                if resource_id is not None:
                    self.ids[("organization_collections", spec.key, key)] = resource_id
                    if spec.key == "hosted_compute.network_configurations":
                        self.ids[("network_configuration_names", str(resource_id))] = (
                            key
                        )
                items[key] = without_organization_collection_response_metadata(
                    spec.key, pick(value, spec.fields)
                )
            set_path(target, spec.key, exact_collection(sorted_mapping(items)))

    def _export_code_security_assignments(
        self, target: dict[str, Any], org: str
    ) -> None:
        collection = get_path(target, "code_security.configurations")
        if not isinstance(collection, dict):
            return
        items = collection.get("items", {})
        if not isinstance(items, dict):
            return
        defaults = self._optional_list(
            f"/orgs/{quote(org)}/code-security/configurations/defaults"
        )
        defaults_by_id: dict[int, str] = {}
        if defaults is not _MISSING:
            for value in defaults:
                configuration = value.get("configuration", {})
                if "id" in configuration:
                    defaults_by_id[int(configuration["id"])] = str(
                        value.get("default_for_new_repos", "none")
                    )
        for key, item in items.items():
            configuration_id = self.ids.get(
                ("organization_collections", "code_security.configurations", key)
            )
            if configuration_id is None:
                continue
            repositories = self._optional_list(
                f"/orgs/{quote(org)}/code-security/configurations/{configuration_id}/repositories"
            )
            if repositories is not _MISSING:
                item["repositories"] = sorted(
                    str(value["repository"]["name"])
                    for value in repositories
                    if value.get("status")
                    in ("attached", "attaching", "enforced", "updating")
                )
            else:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "code_security",
                        "configurations",
                        "items",
                        key,
                        "repositories",
                    ),
                    f"/orgs/{quote(org)}/code-security/configurations/{configuration_id}/repositories",
                )
            if defaults is not _MISSING:
                item["default_for_new_repos"] = defaults_by_id.get(
                    int(configuration_id), "none"
                )
            else:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "code_security",
                        "configurations",
                        "items",
                        key,
                        "default_for_new_repos",
                    ),
                    f"/orgs/{quote(org)}/code-security/configurations/defaults",
                )

    def _export_runner_groups(self, target: dict[str, Any], org: str) -> None:
        groups = self._optional_list(
            f"/orgs/{quote(org)}/actions/runner-groups",
            item_key="runner_groups",
        )
        if groups is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "actions", "runner_groups"),
                f"/orgs/{quote(org)}/actions/runner-groups",
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(group["name"]) for group in groups)
        for group in groups:
            group_id = int(group["id"])
            preferred = str(group["name"])
            key = _unique_key(
                items,
                preferred,
                group_id,
                duplicate=identities[preferred] > 1,
            )
            if group.get("inherited") is True:
                reason = (
                    "the current runner group is inherited from the enterprise and "
                    "cannot be changed through organization runner group endpoints"
                )
                self._mark_item_read_only(
                    ("organization", "actions", "runner_groups", key),
                    reason,
                )
                self._mark_identity_read_only(
                    ("organization", "actions", "runner_groups"),
                    preferred,
                    reason,
                )
                self.ids[("runner_groups", key)] = group_id
                self.ids[("runner_group_names", str(group_id))] = key
                inherited_item: dict[str, Any] = {
                    "settings": pick(
                        group,
                        (
                            "name",
                            "visibility",
                            "allows_public_repositories",
                            "restricted_to_workflows",
                            "selected_workflows",
                        ),
                    )
                }
                network_configuration_id = group.get("network_configuration_id")
                if network_configuration_id is None:
                    inherited_item["settings"]["network_configuration"] = None
                else:
                    network_configuration_name = self.ids.get(
                        (
                            "network_configuration_names",
                            str(network_configuration_id),
                        )
                    )
                    if network_configuration_name is not None:
                        inherited_item["settings"]["network_configuration"] = (
                            network_configuration_name
                        )
                repositories_endpoint = (
                    f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/repositories"
                )
                repositories = self._optional_list(
                    repositories_endpoint,
                    item_key="repositories",
                    record_unavailable=False,
                )
                if repositories is _MISSING:
                    self._mark_comment_caveat(
                        (
                            "organization",
                            "actions",
                            "runner_groups",
                            "items",
                            key,
                        ),
                        "The repositories assigned to this inherited runner group "
                        "could not be read and are absent.",
                    )
                else:
                    inherited_item["repositories"] = sorted(
                        str(repository["name"]) for repository in repositories
                    )
                runners_endpoint = (
                    f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/runners"
                )
                runners = self._optional_list(
                    runners_endpoint,
                    item_key="runners",
                    record_unavailable=False,
                )
                if runners is _MISSING:
                    self.unreadable_inherited_runner_assignments.append(
                        runners_endpoint
                    )
                    self._mark_comment_caveat(
                        (
                            "organization",
                            "actions",
                            "runner_groups",
                            "items",
                            key,
                        ),
                        "The runners assigned to this inherited runner group could "
                        "not be read and are absent.",
                    )
                else:
                    inherited_item["runners"] = sorted(
                        str(runner["name"]) for runner in runners
                    )
                    for runner in runners:
                        runner_name = str(runner["name"])
                        self.read_only_runner_group_runners.setdefault(
                            runner_name,
                            "the current runner belongs to an enterprise-owned runner "
                            "group and cannot be reassigned by organization configuration",
                        )
                        self.ids[("self_hosted_runners", runner_name)] = int(
                            runner["id"]
                        )
                items[key] = inherited_item
                continue
            self.ids[("runner_groups", key)] = group_id
            self.ids[("runner_group_names", str(group_id))] = key
            item: dict[str, Any] = {
                "settings": pick(
                    group,
                    (
                        "name",
                        "visibility",
                        "allows_public_repositories",
                        "restricted_to_workflows",
                        "selected_workflows",
                    ),
                )
            }
            network_configuration_id = group.get("network_configuration_id")
            if network_configuration_id is None:
                item["settings"]["network_configuration"] = None
            else:
                network_configuration_name = self.ids.get(
                    ("network_configuration_names", str(network_configuration_id))
                )
                if network_configuration_name is not None:
                    item["settings"]["network_configuration"] = (
                        network_configuration_name
                    )
            repositories = self._optional_list(
                f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/repositories",
                item_key="repositories",
            )
            if repositories is not _MISSING:
                item["repositories"] = sorted(
                    str(repository["name"]) for repository in repositories
                )
            else:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "actions",
                        "runner_groups",
                        "items",
                        key,
                        "repositories",
                    ),
                    f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/repositories",
                )
            runners = self._optional_list(
                f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/runners",
                item_key="runners",
            )
            if runners is not _MISSING:
                item["runners"] = sorted(str(runner["name"]) for runner in runners)
                for runner in runners:
                    self.ids[("self_hosted_runners", str(runner["name"]))] = int(
                        runner["id"]
                    )
            else:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "actions",
                        "runner_groups",
                        "items",
                        key,
                        "runners",
                    ),
                    f"/orgs/{quote(org)}/actions/runner-groups/{group_id}/runners",
                )
            items[key] = item
        set_path(
            target, "actions.runner_groups", exact_collection(sorted_mapping(items))
        )

    def _export_self_hosted_runners(self, target: dict[str, Any], org: str) -> None:
        runners = self._optional_list(
            f"/orgs/{quote(org)}/actions/runners",
            item_key="runners",
        )
        if runners is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "actions", "self_hosted_runners"),
                f"/orgs/{quote(org)}/actions/runners",
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(runner["name"]) for runner in runners)
        for runner in runners:
            runner_id = int(runner["id"])
            preferred = str(runner["name"])
            key = _unique_key(
                items,
                preferred,
                runner_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("self_hosted_runners", key)] = runner_id
            labels = [
                str(label["name"])
                for label in runner.get("labels", [])
                if label.get("type") == "custom"
            ]
            items[key] = {"labels": sorted(labels)}
        set_path(
            target,
            "actions.self_hosted_runners",
            exact_collection(sorted_mapping(items)),
        )

    def _export_hosted_runners(self, target: dict[str, Any], org: str) -> None:
        runners = self._optional_list(
            f"/orgs/{quote(org)}/actions/hosted-runners",
            item_key="runners",
        )
        if runners is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "actions", "hosted_runners"),
                f"/orgs/{quote(org)}/actions/hosted-runners",
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(runner["name"]) for runner in runners)
        for runner in runners:
            runner_id = int(runner["id"])
            preferred = str(runner["name"])
            key = _unique_key(
                items,
                preferred,
                runner_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("hosted_runners", key)] = runner_id
            image_details = runner.get("image_details")
            image = (
                pick(image_details, ("id", "source", "version"))
                if isinstance(image_details, Mapping)
                else None
            )
            if not image:
                image = None
            size = runner.get("machine_size_details") or {}
            group_id = runner.get("runner_group_id")
            item = without_none(
                {
                    "name": runner.get("name"),
                    "image": image,
                    "size": size.get("id"),
                    "runner_group": self.ids.get(("runner_group_names", str(group_id))),
                    "maximum_runners": runner.get("maximum_runners"),
                    "enable_static_ip": runner.get("public_ip_enabled"),
                    "image_gen": runner.get("image_gen"),
                }
            )
            items[key] = item
        set_path(
            target, "actions.hosted_runners", exact_collection(sorted_mapping(items))
        )

    def _export_blocked_users(self, target: dict[str, Any], org: str) -> None:
        users = self._optional_list(f"/orgs/{quote(org)}/blocks")
        if users is not _MISSING:
            target["blocked_users"] = sorted(str(user["login"]) for user in users)

    def _export_copilot_seats(self, target: dict[str, Any], org: str) -> None:
        seats = self._optional_list(
            f"/orgs/{quote(org)}/copilot/billing/seats", item_key="seats"
        )
        if seats is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "copilot", "seats"),
                f"/orgs/{quote(org)}/copilot/billing/seats",
            )
            return
        users: set[str] = set()
        teams: set[str] = set()
        for seat in seats:
            assignee = seat.get("assignee")
            assigning_team = seat.get("assigning_team")
            if isinstance(assigning_team, dict) and assigning_team.get("slug"):
                teams.add(str(assigning_team["slug"]))
            elif isinstance(assignee, dict) and assignee.get("login"):
                users.add(str(assignee["login"]))
        set_path(
            target, "copilot.seats", {"users": sorted(users), "teams": sorted(teams)}
        )

    def _export_interaction_limit(self, target: dict[str, Any], base: str) -> None:
        value = self._optional_get(f"{base}/interaction-limits")
        if value is _MISSING:
            return
        if isinstance(value, dict) and value.get("limit"):
            target["interaction_limit"] = {
                "enabled": True,
                "limit": value["limit"],
                "_expires_at": value.get("expires_at"),
            }
        else:
            target["interaction_limit"] = {"enabled": False}

    def _export_budgets(self, target: dict[str, Any], org: str) -> None:
        budgets = self._optional_list(
            f"/organizations/{quote(org)}/settings/billing/budgets",
            item_key="budgets",
        )
        if budgets is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "budgets"),
                f"/organizations/{quote(org)}/settings/billing/budgets",
            )
            return
        items: dict[str, Any] = {}
        identified = [
            (
                budget,
                ":".join(
                    str(value or "all")
                    for value in (
                        budget.get("budget_scope"),
                        budget.get("budget_entity_name") or budget.get("user"),
                        budget.get("budget_type"),
                        budget.get("budget_product_sku"),
                    )
                ),
            )
            for budget in budgets
        ]
        identities = Counter(preferred for _, preferred in identified)
        for budget, preferred in identified:
            budget_id = str(budget["id"])
            key = _unique_key(
                items,
                preferred,
                budget_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("budgets", key)] = budget_id
            items[key] = pick(budget, BUDGET_FIELDS)
        target["budgets"] = exact_collection(sorted_mapping(items))

    def _export_custom_patterns(
        self,
        target: dict[str, Any],
        base: str,
        id_prefix: tuple[str, ...],
    ) -> None:
        patterns = self._optional_list(f"{base}/custom-patterns")
        if patterns is _MISSING:
            config_path = (
                ("organization", "secret_scanning", "custom_patterns")
                if id_prefix == ("organization",)
                else (
                    "repositories",
                    "items",
                    id_prefix[1],
                    "secret_scanning",
                    "custom_patterns",
                )
            )
            self._mark_collection_unavailable(config_path, f"{base}/custom-patterns")
            return
        items: dict[str, Any] = {}
        identities = Counter(str(pattern["name"]) for pattern in patterns)
        fields = (
            "name",
            "pattern",
            "start_delimiter",
            "end_delimiter",
            "must_match",
            "must_not_match",
        )
        for pattern in patterns:
            pattern_id = int(pattern["id"])
            preferred = str(pattern["name"])
            key = _unique_key(
                items,
                preferred,
                pattern_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[id_prefix + ("custom_patterns", key)] = pattern_id
            version = pattern.get("custom_pattern_version")
            if version is not None:
                self.ids[id_prefix + ("custom_pattern_versions", key)] = str(version)
            items[key] = pick(pattern, fields)
        set_path(
            target,
            "secret_scanning.custom_patterns",
            exact_collection(sorted_mapping(items)),
        )

    def _export_private_registries(self, target: dict[str, Any], org: str) -> None:
        registries = self._optional_list(
            f"/orgs/{quote(org)}/private-registries",
            item_key="configurations",
        )
        if registries is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "private_registries"),
                f"/orgs/{quote(org)}/private-registries",
            )
            return
        items: dict[str, Any] = {}
        complete = True
        for summary in registries:
            name = str(summary["name"])
            detail_path = f"/orgs/{quote(org)}/private-registries/{quote(name)}"
            detail = self._optional_get(detail_path)
            if detail is _MISSING or not isinstance(detail, dict):
                complete = False
                self._mark_collection_unavailable(
                    ("organization", "private_registries"), detail_path
                )
                continue
            registry = {**summary, **detail}
            item = pick(registry, PRIVATE_REGISTRY_FIELDS)
            if item.get("visibility") == "selected":
                repository_names = []
                for repository_id in registry.get("selected_repository_ids", []):
                    repository_name = self.ids.get(
                        ("repository_names", str(repository_id))
                    )
                    if repository_name is None:
                        self.unavailable.append(
                            f"/orgs/{quote(org)}/private-registries/{quote(name)} "
                            f"(selected repository {repository_id} was not accessible)"
                        )
                        complete = False
                        self._mark_collection_unavailable(
                            ("organization", "private_registries"), detail_path
                        )
                    else:
                        repository_names.append(str(repository_name))
                item["selected_repositories"] = sorted(repository_names)
            items[name] = item
        if complete:
            target["private_registries"] = exact_collection(sorted_mapping(items))

    def _export_members(self, target: dict[str, Any], org: str) -> None:
        admins = self._optional_list(
            with_query(f"/orgs/{quote(org)}/members", role="admin")
        )
        members = self._optional_list(
            with_query(f"/orgs/{quote(org)}/members", role="member")
        )
        if admins is _MISSING or members is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "members"),
                f"/orgs/{quote(org)}/members",
            )
            return
        public_members = self._optional_list(f"/orgs/{quote(org)}/public_members")
        public_logins = (
            {str(member["login"]).casefold() for member in public_members}
            if public_members is not _MISSING
            else set()
        )
        authenticated = self._optional_get("/user")
        authenticated_login = (
            str(authenticated["login"]).casefold()
            if isinstance(authenticated, dict) and authenticated.get("login")
            else None
        )
        if authenticated_login is not None:
            self.ids[("authenticated_user",)] = authenticated_login
        items: dict[str, Any] = {}
        complete = True
        for role, users in (("admin", admins), ("member", members)):
            for user in users:
                login = str(user["login"])
                if "id" in user:
                    self.ids[("users", login.casefold())] = int(user["id"])
                if isinstance(user.get("node_id"), str):
                    self.ids[("users", login.casefold(), "node_id")] = str(
                        user["node_id"]
                    )
                membership_path = f"/orgs/{quote(org)}/memberships/{quote(login)}"
                membership = (
                    user
                    if "direct_membership" in user
                    else self._optional_get(membership_path)
                )
                if not isinstance(membership, dict):
                    complete = False
                    self._mark_collection_unavailable(
                        ("organization", "members"), membership_path
                    )
                    continue
                membership_role = membership.get("role")
                direct_role = (
                    membership_role if membership_role in ("admin", "member") else role
                )
                member_item: dict[str, Any] = {"role": direct_role}
                if public_members is not _MISSING:
                    member_item["public"] = login.casefold() in public_logins
                if membership.get("direct_membership") is False:
                    member_path = ("organization", "members", login)
                    reason = (
                        "the current organization membership is inherited from an "
                        "enterprise team and cannot be changed as a direct membership"
                    )
                    for path in (
                        member_path,
                        (*member_path, "role"),
                        (*member_path, "public"),
                    ):
                        self._mark_item_read_only(path, reason)
                items[login] = member_item
        if complete:
            target["members"] = exact_collection(sorted_mapping(items))

    def _export_teams(
        self,
        target: dict[str, Any],
        org: str,
        repository_summaries: Iterable[Mapping[str, Any]],
    ) -> None:
        teams = self._optional_list(f"/orgs/{quote(org)}/teams")
        if teams is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "teams"), f"/orgs/{quote(org)}/teams"
            )
            return
        items: dict[str, Any] = {}
        effective_repositories: dict[str, dict[str, str] | None] = {}
        parent_slugs: dict[str, str | None] = {}
        for team in sorted(teams, key=lambda item: item["slug"].casefold()):
            slug = str(team["slug"])
            self.ids[("teams", slug)] = int(team["id"])
            if isinstance(team.get("node_id"), str):
                self.ids[("teams", slug, "node_id")] = str(team["node_id"])
            settings = pick(
                team,
                (
                    "name",
                    "description",
                    "privacy",
                    "notification_setting",
                    "permission",
                ),
            )
            parent = team.get("parent")
            settings["parent"] = (
                parent.get("slug") if isinstance(parent, dict) else None
            )
            if team.get("type") == "enterprise":
                reason = (
                    "the current team is owned by the enterprise and cannot be "
                    "changed through organization team endpoints"
                )
                self._mark_item_read_only(("organization", "teams", slug), reason)
                self._mark_identity_read_only(
                    ("organization", "teams"), str(team["name"]), reason
                )
            parent_slugs[slug] = settings["parent"]
            all_members = self._optional_list(
                with_query(
                    f"/orgs/{quote(org)}/teams/{quote(slug)}/members", role="all"
                )
            )
            maintainers = self._optional_list(
                with_query(
                    f"/orgs/{quote(org)}/teams/{quote(slug)}/members", role="maintainer"
                )
            )
            member_items: dict[str, str] = {}
            if all_members is not _MISSING and maintainers is not _MISSING:
                maintainer_logins = {
                    str(user["login"]).casefold() for user in maintainers
                }
                for user in all_members:
                    login = str(user["login"])
                    if "id" in user:
                        self.ids[("users", login.casefold())] = int(user["id"])
                    if isinstance(user.get("node_id"), str):
                        self.ids[("users", login.casefold(), "node_id")] = str(
                            user["node_id"]
                        )
                    member_role = (
                        "maintainer"
                        if login.casefold() in maintainer_logins
                        else "member"
                    )
                    if user.get("inherited") is True:
                        self._mark_item_read_only(
                            ("organization", "teams", slug, "members", login),
                            "the current team membership is inherited from a child "
                            "team and cannot be changed as a direct membership",
                        )
                    member_items[login] = member_role
            else:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "teams",
                        "items",
                        slug,
                        "members",
                    ),
                    f"/orgs/{quote(org)}/teams/{quote(slug)}/members",
                )
            repositories = self._optional_list(
                f"/orgs/{quote(org)}/teams/{quote(slug)}/repos"
            )
            if repositories is not _MISSING:
                repository_items: dict[str, str] = {}
                for repository in repositories:
                    repository_items[str(repository["name"])] = _permission_name(
                        repository
                    )
                effective_repositories[slug] = repository_items
            else:
                effective_repositories[slug] = None
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "teams",
                        "items",
                        slug,
                        "repositories",
                    ),
                    f"/orgs/{quote(org)}/teams/{quote(slug)}/repos",
                )
            item: dict[str, Any] = {"settings": settings}
            if all_members is not _MISSING and maintainers is not _MISSING:
                item["members"] = exact_collection(sorted_mapping(member_items))
            self._export_team_review_assignment(item, org, slug)
            self._export_team_external_groups(item, org, slug)
            items[slug] = item
        direct_repositories = self._direct_team_repositories(
            org,
            repository_summaries,
            effective_repositories,
            parent_slugs,
        )
        for slug, direct_items in direct_repositories.items():
            effective_items = effective_repositories.get(slug)
            if isinstance(effective_items, dict):
                for repository in set(effective_items) - set(direct_items or {}):
                    reason = (
                        "the current repository access is not a direct team grant and "
                        "cannot be changed through direct team repository endpoints"
                        if direct_items is not None
                        else "GitHub did not expose whether the current repository "
                        "access is a direct or inherited team grant"
                    )
                    self._mark_item_read_only(
                        (
                            "organization",
                            "teams",
                            slug,
                            "repositories",
                            repository,
                        ),
                        reason,
                    )
                items[slug]["repositories"] = exact_collection(
                    sorted_mapping(effective_items)
                )
            elif direct_items is not None:
                items[slug]["repositories"] = exact_collection(
                    sorted_mapping(direct_items)
                )
        target["teams"] = exact_collection(items)

    def _export_team_review_assignment(
        self, target: dict[str, Any], org: str, slug: str
    ) -> None:
        data = self._optional_graphql(
            TEAM_REVIEW_ASSIGNMENT_QUERY,
            {"organization": org, "slug": slug},
            "TeamReviewAssignment",
            (
                (
                    "organization",
                    "teams",
                    "items",
                    slug,
                    "review_assignment",
                ),
            ),
        )
        organization = data.get("organization") if data is not None else None
        team = organization.get("team") if isinstance(organization, Mapping) else None
        if not isinstance(team, Mapping):
            self._mark_collection_unavailable(
                (
                    "organization",
                    "teams",
                    "items",
                    slug,
                    "review_assignment",
                ),
                "/graphql#TeamReviewAssignment",
            )
            return
        node_id = team.get("id")
        if isinstance(node_id, str):
            self.ids[("teams", slug, "node_id")] = node_id
        algorithm = team.get("reviewRequestDelegationAlgorithm")
        target["review_assignment"] = without_none(
            {
                "enabled": team.get("reviewRequestDelegationEnabled"),
                "algorithm": algorithm.lower() if isinstance(algorithm, str) else None,
                "member_count": team.get("reviewRequestDelegationMemberCount"),
                "notify_team": team.get("reviewRequestDelegationNotifyTeam"),
            }
        )
        self._mark_comment_caveat(
            (
                "organization",
                "teams",
                "items",
                slug,
                "review_assignment",
            ),
            "GitHub does not expose the excluded members, child-team inclusion, "
            "existing-request counting, or team-request removal values. Updating "
            "this section resets them to GitHub's documented defaults, so the "
            "update is blocked unless `--force` accepts those resets.",
        )

    def _export_team_external_groups(
        self, target: dict[str, Any], org: str, slug: str
    ) -> None:
        external_path = f"/orgs/{quote(org)}/teams/{quote(slug)}/external-groups"
        external = self._optional_get(external_path)
        if isinstance(external, Mapping):
            groups = external.get("groups")
            if isinstance(groups, list) and groups:
                group = groups[0]
                if isinstance(group, Mapping):
                    target["external_group"] = pick(group, ("group_id", "group_name"))
                    if "group_name" in target["external_group"]:
                        self._mark_field_read_only(
                            (
                                "organization",
                                "teams",
                                "items",
                                slug,
                                "external_group",
                                "group_name",
                            ),
                            "GitHub derives the external group name from its numeric "
                            "group ID.",
                        )
                if len(groups) > 1:
                    self._mark_comment_caveat(
                        (
                            "organization",
                            "teams",
                            "items",
                            slug,
                            "external_group",
                        ),
                        "GitHub returned more than one legacy external group, but "
                        "the update operation accepts only one group ID. This export "
                        "contains the first group.",
                    )
        elif external is _MISSING:
            self._mark_collection_unavailable(
                (
                    "organization",
                    "teams",
                    "items",
                    slug,
                    "external_group",
                ),
                external_path,
            )

        mapping_path = (
            f"/orgs/{quote(org)}/teams/{quote(slug)}/team-sync/group-mappings"
        )
        mapping = self._optional_get(mapping_path)
        if isinstance(mapping, Mapping) and isinstance(mapping.get("groups"), list):
            target["team_sync_groups"] = [
                pick(
                    group,
                    (
                        "group_id",
                        "group_name",
                        "group_description",
                        "status",
                        "synced_at",
                    ),
                )
                for group in mapping["groups"]
                if isinstance(group, Mapping)
            ]
            for index, group in enumerate(target["team_sync_groups"]):
                for field in ("status", "synced_at"):
                    if field in group:
                        self._mark_field_read_only(
                            (
                                "organization",
                                "teams",
                                "items",
                                slug,
                                "team_sync_groups",
                                index,
                                field,
                            ),
                            "GitHub reports this team synchronization status and "
                            "does not accept it in updates.",
                        )
        elif mapping is _MISSING:
            self._mark_collection_unavailable(
                (
                    "organization",
                    "teams",
                    "items",
                    slug,
                    "team_sync_groups",
                ),
                mapping_path,
            )

    def _direct_team_repositories(
        self,
        org: str,
        repository_summaries: Iterable[Mapping[str, Any]],
        effective: Mapping[str, dict[str, str] | None],
        parents: Mapping[str, str | None],
    ) -> dict[str, dict[str, str] | None]:
        direct: dict[str, dict[str, str] | None] = {
            slug: {} if repositories is not None else None
            for slug, repositories in effective.items()
        }
        repository_details = {
            str(summary["name"]): summary
            for summary in repository_summaries
            if "name" in summary
        }
        candidates = {
            repository
            for repositories in effective.values()
            if repositories is not None
            for repository in repositories
        }
        repository_teams: dict[str, dict[str, Any]] = {}
        for repository in sorted(
            candidates & repository_details.keys(), key=str.casefold
        ):
            repository_assignments = self._optional_list(
                f"/repos/{quote(org)}/{quote(repository)}/teams"
            )
            if repository_assignments is _MISSING:
                continue
            repository_teams[repository] = {
                str(assignment["slug"]): assignment
                for assignment in repository_assignments
                if isinstance(assignment, dict)
                and assignment.get("type") != "enterprise"
                and "slug" in assignment
            }

        for slug, repositories in effective.items():
            direct_items = direct[slug]
            if repositories is None or direct_items is None:
                continue
            parent_slug = parents.get(slug)
            parent_repositories = effective.get(parent_slug) if parent_slug else {}
            for repository, permission in repositories.items():
                repository_team_items = repository_teams.get(repository)
                if repository_team_items is not None:
                    assignment = repository_team_items.get(slug)
                    if assignment is None:
                        continue
                    if assignment.get("access_source") == "direct":
                        direct_items[repository] = permission
                        continue
                    if assignment.get("access_source") in (
                        "organization",
                        "enterprise",
                    ):
                        continue
                    repository_detail = repository_details.get(repository, {})
                    if repository_detail.get("visibility") == "public" or (
                        repository_detail.get("private") is False
                    ):
                        direct_items[repository] = permission
                        continue
                if parent_slug is not None and parent_repositories is None:
                    direct[slug] = None
                    self._mark_collection_unavailable(
                        (
                            "organization",
                            "teams",
                            "items",
                            slug,
                            "repositories",
                        ),
                        f"/orgs/{quote(org)}/teams/{quote(parent_slug)}/repos",
                    )
                    break
                inherited_permission = (
                    parent_repositories.get(repository)
                    if isinstance(parent_repositories, dict)
                    else None
                )
                if parent_slug is None or inherited_permission is None:
                    direct_items[repository] = permission
                    continue
                inherited_rank = _REPOSITORY_PERMISSION_RANK.get(inherited_permission)
                permission_rank = _REPOSITORY_PERMISSION_RANK.get(permission)
                if (
                    inherited_rank is not None
                    and permission_rank is not None
                    and permission_rank > inherited_rank
                ):
                    direct_items[repository] = permission
                    continue
                direct[slug] = None
                endpoint = f"/repos/{quote(org)}/{quote(repository)}/teams"
                self.unavailable.append(
                    f"{endpoint} (direct assignment for team {slug!r} is ambiguous)"
                )
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "teams",
                        "items",
                        slug,
                        "repositories",
                    ),
                    endpoint,
                )
                break
        return direct

    def _export_organization_invitations(
        self, target: dict[str, Any], org: str
    ) -> None:
        invitations = self._optional_list(f"/orgs/{quote(org)}/invitations")
        if invitations is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "invitations"),
                f"/orgs/{quote(org)}/invitations",
            )
            return
        items: dict[str, Any] = {}
        complete = True
        identities = Counter(
            str(invitation.get("login") or invitation.get("email"))
            for invitation in invitations
            if invitation.get("login") or invitation.get("email")
        )
        for invitation in invitations:
            invitation_id = int(invitation["id"])
            teams = self._optional_list(
                f"/orgs/{quote(org)}/invitations/{invitation_id}/teams"
            )
            if teams is _MISSING:
                complete = False
                self._mark_collection_unavailable(
                    ("organization", "invitations"),
                    f"/orgs/{quote(org)}/invitations/{invitation_id}/teams",
                )
                continue
            login = invitation.get("login")
            email = invitation.get("email")
            identity = login or email
            if not isinstance(identity, str):
                self.unavailable.append(
                    f"/orgs/{quote(org)}/invitations/{invitation_id} "
                    "(invitee has no login or email)"
                )
                complete = False
                self._mark_collection_unavailable(
                    ("organization", "invitations"),
                    f"/orgs/{quote(org)}/invitations/{invitation_id}",
                )
                continue
            key = _unique_key(
                items,
                identity,
                invitation_id,
                duplicate=identities[identity] > 1,
            )
            self.ids[("organization_invitations", key)] = invitation_id
            item = without_none(
                {
                    "login": login,
                    "email": email,
                    "role": invitation.get("role"),
                    "teams": sorted(str(team["slug"]) for team in teams),
                }
            )
            items[key] = item
        if complete:
            target["invitations"] = exact_collection(sorted_mapping(items))

    def _export_outside_collaborators(self, target: dict[str, Any], org: str) -> None:
        collaborators = self._optional_list(
            with_query(f"/orgs/{quote(org)}/outside_collaborators", filter="all")
        )
        if collaborators is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "outside_collaborators"),
                with_query(f"/orgs/{quote(org)}/outside_collaborators", filter="all"),
            )
            return
        items: dict[str, Any] = {}
        for collaborator in collaborators:
            login = str(collaborator["login"])
            items[login] = {}
            if "id" in collaborator:
                self.ids[("users", login.casefold())] = int(collaborator["id"])
        target["outside_collaborators"] = exact_collection(sorted_mapping(items))

    def _export_personal_access_tokens(self, target: dict[str, Any], org: str) -> None:
        base = f"/orgs/{quote(org)}/personal-access-tokens"
        grants = self._optional_list(base)
        if grants is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "personal_access_tokens"), base
            )
            return
        identified: list[tuple[dict[str, Any], str]] = []
        for grant in grants:
            owner = grant.get("owner")
            login = owner.get("login") if isinstance(owner, dict) else None
            token_name = grant.get("token_name")
            if not isinstance(login, str) or not isinstance(token_name, str):
                self.unavailable.append(
                    f"{base} (grant owner or token name is missing)"
                )
                self._mark_collection_unavailable(
                    ("organization", "personal_access_tokens"), base
                )
                return
            identified.append((grant, f"{login}:{token_name}"))
        identities = Counter(preferred for _, preferred in identified)
        items: dict[str, Any] = {}
        for grant, preferred in identified:
            grant_id = int(grant["id"])
            repositories = self._optional_list(f"{base}/{grant_id}/repositories")
            if repositories is _MISSING:
                self._mark_collection_unavailable(
                    ("organization", "personal_access_tokens"),
                    f"{base}/{grant_id}/repositories",
                )
                return
            key = _unique_key(
                items,
                preferred,
                grant_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("personal_access_tokens", key)] = grant_id
            owner = grant["owner"]
            item = without_none(
                {
                    "owner": owner["login"],
                    "token_name": grant["token_name"],
                    "repository_selection": grant.get("repository_selection"),
                    "repositories": sorted(
                        str(repository["name"]) for repository in repositories
                    ),
                    "permissions": grant.get("permissions"),
                    "token_expired": grant.get("token_expired"),
                    "token_expires_at": grant.get("token_expires_at"),
                }
            )
            items[key] = item
            for field_name in item:
                self._mark_field_read_only(
                    (
                        "organization",
                        "personal_access_tokens",
                        "items",
                        key,
                        field_name,
                    ),
                    "GitHub exposes this grant detail for inspection but only "
                    "provides an operation that revokes the entire grant.",
                )
        target["personal_access_tokens"] = exact_collection(sorted_mapping(items))

    def _export_credential_authorizations(
        self, target: dict[str, Any], org: str
    ) -> None:
        base = f"/orgs/{quote(org)}/credential-authorizations"
        authorizations = self._optional_list(base)
        if authorizations is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "credential_authorizations"), base
            )
            return
        fields = (
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
        )
        identified: list[tuple[Mapping[str, Any], str]] = []
        for authorization in authorizations:
            credential_id = authorization.get("credential_id")
            login = authorization.get("login")
            credential_type = authorization.get("credential_type")
            if (
                not isinstance(credential_id, int)
                or not isinstance(login, str)
                or not isinstance(credential_type, str)
            ):
                self.unavailable.append(f"{base} (credential identity is missing)")
                self._mark_collection_unavailable(
                    ("organization", "credential_authorizations"), base
                )
                return
            detail = (
                authorization.get("fingerprint")
                or authorization.get("token_last_eight")
                or authorization.get("authorized_credential_id")
                or credential_id
            )
            identified.append(
                (
                    authorization,
                    f"{login}:{credential_type}:{detail}",
                )
            )
        identities = Counter(preferred for _, preferred in identified)
        items: dict[str, Any] = {}
        read_only_reason = (
            "GitHub exposes this credential detail for inspection but only provides "
            "an operation that revokes the entire authorization."
        )
        for authorization, preferred in identified:
            credential_id = int(authorization["credential_id"])
            key = _unique_key(
                items,
                preferred,
                credential_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("credential_authorizations", key)] = credential_id
            item = without_none(pick(authorization, fields))
            items[key] = item
            for field_name in item:
                self._mark_field_read_only(
                    (
                        "organization",
                        "credential_authorizations",
                        "items",
                        key,
                        field_name,
                    ),
                    read_only_reason,
                )
        target["credential_authorizations"] = exact_collection(sorted_mapping(items))

    def _export_organization_roles(self, target: dict[str, Any], org: str) -> None:
        roles = self._optional_list(
            f"/orgs/{quote(org)}/organization-roles", item_key="roles"
        )
        if roles is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "organization_roles"),
                f"/orgs/{quote(org)}/organization-roles",
            )
            return
        items: dict[str, Any] = {}
        complete = True
        for role in roles:
            name = str(role["name"])
            role_id = int(role["id"])
            self.ids[("organization_roles", name)] = role_id
            users = self._optional_list(
                f"/orgs/{quote(org)}/organization-roles/{role_id}/users"
            )
            teams = self._optional_list(
                f"/orgs/{quote(org)}/organization-roles/{role_id}/teams"
            )
            if users is _MISSING or teams is _MISSING:
                complete = False
                self._mark_collection_unavailable(
                    ("organization", "organization_roles"),
                    f"/orgs/{quote(org)}/organization-roles/{role_id}",
                )
                continue
            items[name] = {
                "users": sorted(str(user["login"]) for user in users),
                "teams": sorted(str(team["slug"]) for team in teams),
            }
        if complete:
            target["organization_roles"] = exact_collection(sorted_mapping(items))

    def _export_security_managers(self, target: dict[str, Any], org: str) -> None:
        teams = self._optional_list(f"/orgs/{quote(org)}/security-managers")
        if teams is not _MISSING:
            target["security_manager_teams"] = sorted(
                str(team["slug"]) for team in teams
            )

    def _export_variables_and_secrets(self, target: dict[str, Any], org: str) -> None:
        for scope in ("actions", "agents"):
            self._export_org_variables(target, org, scope)
            self._export_org_secrets(target, org, scope)
        for scope in ("codespaces", "dependabot"):
            self._export_org_secrets(target, org, scope)

    def _export_org_variables(
        self, target: dict[str, Any], org: str, scope: str
    ) -> None:
        base = f"/orgs/{quote(org)}/{scope}/variables"
        variables = self._optional_list(base, item_key="variables")
        if variables is _MISSING:
            self._mark_collection_unavailable(
                ("organization", scope, "variables"), base
            )
            return
        items: dict[str, Any] = {}
        for variable in variables:
            name = str(variable["name"])
            item = pick(variable, ("value", "visibility"))
            if item.get("visibility") == "selected":
                repositories = self._optional_list(
                    f"{base}/{quote(name)}/repositories", item_key="repositories"
                )
                if repositories is not _MISSING:
                    item["selected_repositories"] = sorted(
                        str(repository["name"]) for repository in repositories
                    )
            items[name] = item
        set_path(target, f"{scope}.variables", exact_collection(sorted_mapping(items)))

    def _export_org_secrets(self, target: dict[str, Any], org: str, scope: str) -> None:
        base = f"/orgs/{quote(org)}/{scope}/secrets"
        secrets = self._optional_list(base, item_key="secrets")
        if secrets is _MISSING:
            self._mark_collection_unavailable(("organization", scope, "secrets"), base)
            return
        items: dict[str, Any] = {}
        for secret in secrets:
            name = str(secret["name"])
            item = pick(secret, ("visibility",))
            if item.get("visibility") == "selected":
                repositories = self._optional_list(
                    f"{base}/{quote(name)}/repositories", item_key="repositories"
                )
                if repositories is not _MISSING:
                    item["selected_repositories"] = sorted(
                        str(repository["name"]) for repository in repositories
                    )
            items[name] = item
        set_path(target, f"{scope}.secrets", exact_collection(sorted_mapping(items)))

    def _export_rulesets(
        self, target: dict[str, Any], base: str, id_prefix: tuple[str, ...]
    ) -> None:
        config_path = (
            ("organization", "rulesets")
            if id_prefix == ("organization",)
            else ("repositories", "items", id_prefix[1], "rulesets")
        )
        list_path = f"{base}/rulesets"
        if id_prefix[:1] == ("repositories",):
            list_path = with_query(list_path, includes_parents="false")
        rulesets = self._optional_list(list_path)
        if rulesets is _MISSING:
            self._mark_collection_unavailable(config_path, list_path)
            return
        items: dict[str, Any] = {}
        identities = Counter(str(summary["name"]) for summary in rulesets)
        for summary in rulesets:
            ruleset_id = int(summary["id"])
            detail = self._optional_get(f"{base}/rulesets/{ruleset_id}")
            if detail is _MISSING or not isinstance(detail, dict):
                self._mark_collection_unavailable(
                    config_path, f"{base}/rulesets/{ruleset_id}"
                )
                return
            preferred = str(detail["name"])
            key = _unique_key(
                items,
                preferred,
                ruleset_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[id_prefix + ("rulesets", key)] = ruleset_id
            items[key] = pick(
                detail,
                (
                    "name",
                    "target",
                    "enforcement",
                    "bypass_actors",
                    "conditions",
                    "rules",
                ),
            )
        target["rulesets"] = exact_collection(sorted_mapping(items))

    def _export_hooks(
        self, target: dict[str, Any], base: str, id_prefix: tuple[str, ...]
    ) -> None:
        config_path = (
            ("organization", "hooks")
            if id_prefix == ("organization",)
            else ("repositories", "items", id_prefix[1], "hooks")
        )
        hooks = self._optional_list(f"{base}/hooks")
        if hooks is _MISSING:
            self._mark_collection_unavailable(config_path, f"{base}/hooks")
            return
        resolved: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for summary in hooks:
            hook_id = int(summary["id"])
            detail = self._optional_get(f"{base}/hooks/{hook_id}")
            config = self._optional_get(f"{base}/hooks/{hook_id}/config")
            if (
                detail is _MISSING
                or config is _MISSING
                or not isinstance(detail, dict)
                or not isinstance(config, dict)
            ):
                self._mark_collection_unavailable(
                    config_path, f"{base}/hooks/{hook_id}"
                )
                return
            clean_config = pick(config, ("url", "content_type", "insecure_ssl"))
            resolved.append((hook_id, detail, clean_config))
        items: dict[str, Any] = {}
        identities = Counter(
            str(config.get("url", f"hook-{hook_id}")) for hook_id, _, config in resolved
        )
        for hook_id, detail, clean_config in resolved:
            preferred = str(clean_config.get("url", f"hook-{hook_id}"))
            key = _unique_key(
                items,
                preferred,
                hook_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[id_prefix + ("hooks", key)] = hook_id
            items[key] = {
                "active": bool(detail.get("active", True)),
                "events": sorted(detail.get("events", [])),
                "config": clean_config,
            }
        target["hooks"] = exact_collection(sorted_mapping(items))
        self._mark_comment_caveat(
            config_path,
            "GitHub never returns webhook secrets or organization webhook "
            "basic-auth credentials. Configuration-only updates preserve omitted "
            "credentials. Changing active or events removes an existing secret "
            "unless config.secret_from_env supplies it. A request that also "
            "replaces an organization webhook config may remove its basic-auth "
            "credentials. The diff warns before either potentially destructive "
            "request.",
        )

    def _export_custom_property_schema(self, target: dict[str, Any], org: str) -> None:
        properties = self._optional_list(f"/orgs/{quote(org)}/properties/schema")
        if properties is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "custom_properties"),
                f"/orgs/{quote(org)}/properties/schema",
            )
            return
        fields = (
            "value_type",
            "required",
            "default_value",
            "description",
            "allowed_values",
            "values_editable_by",
            "require_explicit_values",
            "source_type",
        )
        items = {str(prop["property_name"]): pick(prop, fields) for prop in properties}
        graphql_properties: dict[str, Mapping[str, Any]] = {}
        graphql_complete = True
        cursor: str | None = None
        while True:
            data = self._optional_graphql(
                ORGANIZATION_CUSTOM_PROPERTIES_QUERY,
                {"login": org, "cursor": cursor},
                "OrganizationCustomProperties",
                (("organization", "custom_properties"),),
            )
            if getattr(data, "errors", ()):
                graphql_complete = False
            organization = data.get("organization") if data is not None else None
            connection = (
                organization.get("repositoryCustomProperties")
                if isinstance(organization, Mapping)
                else None
            )
            if not isinstance(connection, Mapping):
                graphql_complete = False
                self._mark_comment_caveat(
                    ("organization", "custom_properties"),
                    "GitHub's REST API does not return regular-expression "
                    "constraints. GraphQL access was unavailable, so regex values "
                    "are absent from this export.",
                )
                break
            for prop in _connection_nodes(connection):
                name = prop.get("propertyName")
                if isinstance(name, str):
                    graphql_properties[name] = prop
            if not _connection_has_next_page(connection):
                break
            cursor = _connection_end_cursor(connection)
            if cursor is None:
                graphql_complete = False
                self._mark_comment_caveat(
                    ("organization", "custom_properties"),
                    "GitHub reported additional custom properties without a "
                    "cursor, so regex values are incomplete.",
                )
                break
        for name, item in items.items():
            graphql_property = graphql_properties.get(name)
            regex_available = (
                graphql_complete
                and isinstance(graphql_property, Mapping)
                and "regex" in graphql_property
            )
            if not regex_available:
                self._mark_collection_unavailable(
                    (
                        "organization",
                        "custom_properties",
                        "items",
                        name,
                        "regex",
                    ),
                    "/graphql#OrganizationCustomProperties",
                )
                self._mark_comment_caveat(
                    (
                        "organization",
                        "custom_properties",
                        "items",
                        name,
                    ),
                    "GitHub did not return a complete regular-expression "
                    "constraint for this property. The regex field is omitted and "
                    "remains unmanaged.",
                )
            if isinstance(graphql_property, Mapping):
                property_id = graphql_property.get("id")
                if isinstance(property_id, str):
                    self.ids[("custom_properties", name, "node_id")] = property_id
                if regex_available:
                    item["regex"] = graphql_property.get("regex")
                if "requireExplicitValues" in graphql_property:
                    item["require_explicit_values"] = graphql_property.get(
                        "requireExplicitValues"
                    )
            if item.get("source_type") == "enterprise":
                reason = (
                    "This property is inherited from the enterprise. Organization "
                    "custom-property operations cannot update or delete it."
                )
                self._mark_item_read_only(
                    ("organization", "custom_properties", name), reason
                )
            if "source_type" in item:
                self._mark_field_read_only(
                    (
                        "organization",
                        "custom_properties",
                        "items",
                        name,
                        "source_type",
                    ),
                    "GitHub determines whether the custom property is defined by "
                    "the organization or inherited from its enterprise.",
                )
        target["custom_properties"] = exact_collection(sorted_mapping(items))

    def _export_repository(self, org: str, repo: str) -> dict[str, Any]:
        base = f"/repos/{quote(org)}/{quote(repo)}"
        detail = self.api.request("GET", base).data
        if not isinstance(detail, dict):
            raise TypeError(
                f"GitHub API did not return repository settings for {org}/{repo}"
            )
        self.ids[("repositories", repo)] = int(detail["id"])
        settings = without_repository_response_metadata(
            pick(
                detail,
                (*REPOSITORY_SETTINGS_FIELDS, *REPOSITORY_READ_ONLY_SETTINGS_FIELDS),
            )
        )
        for field_name in REPOSITORY_READ_ONLY_SETTINGS_FIELDS:
            if field_name in settings:
                self._mark_field_read_only(
                    ("repositories", "items", repo, "settings", field_name),
                    "GitHub exposes this repository setting for inspection but "
                    "does not provide a public API that changes it.",
                )
        if (
            detail.get("visibility") not in {"private", "internal"}
            or self.organization_allows_forking is False
        ):
            settings.pop("allow_forking", None)
        if (
            settings.get("has_projects") is True
            and self.organization_allows_projects is False
        ):
            settings.pop("has_projects")
        target: dict[str, Any] = {
            "settings": settings,
            "topics": sorted(detail.get("topics", [])),
            "_facts": pick(detail, ("fork", "is_template", "archived", "visibility")),
        }
        self._export_singletons(target, REPOSITORY_SINGLETONS, org=org, repo=repo)
        self._export_repo_self_hosted_runners(target, org, repo)
        self._export_repo_collaborators(target, org, repo)
        self._export_repo_invitations(target, org, repo)
        self._export_rulesets(target, base, ("repositories", repo))
        self._export_hooks(target, base, ("repositories", repo))
        self._export_deploy_keys(target, org, repo)
        self._export_autolinks(target, org, repo)
        self._export_labels(target, org, repo)
        self._export_branch_protection_rules(target, org, repo)
        if "branch_protection_rules" not in target:
            self._export_branch_protections(target, org, repo)
            if "branch_protections" in target:
                self._mark_comment_caveat(
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protections",
                    ),
                    "The canonical GraphQL branch protection rule collection "
                    "could not be read. This REST fallback uses concrete branch "
                    "names and can flatten one wildcard rule onto several entries.",
                )
        self._export_environments(target, org, repo)
        self._export_repository_hash_algorithm(target, org, repo)
        self._export_repository_graphql(target, org, repo)
        self._export_cloud_agent_configuration(target, org, repo)
        self._export_repo_variables_and_secrets(target, org, repo)
        self._export_repo_custom_properties(target, org, repo)
        self._export_security_toggles(target, org, repo)
        self._export_pages(target, org, repo)
        self._export_workflow_states(target, org, repo)
        self._export_interaction_limit(target, base)
        self._export_pull_request_limits(target, org, repo)
        self._export_custom_patterns(
            target, f"{base}/secret-scanning", ("repositories", repo)
        )
        return target

    def _export_repository_hash_algorithm(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        path = f"/repos/{quote(org)}/{quote(repo)}/hash-algorithm"
        self._mark_field_read_only(
            ("repositories", "items", repo, "settings", "hash_algorithm"),
            "GitHub exposes the repository object hash algorithm but does not "
            "provide a public API that changes it.",
        )
        value = self._optional_get(path)
        if isinstance(value, Mapping) and isinstance(value.get("hash_algorithm"), str):
            target["settings"]["hash_algorithm"] = value["hash_algorithm"]

    def _export_repository_graphql(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        environments: dict[str, Mapping[str, Any]] = {}
        categories: dict[str, Mapping[str, Any]] = {}
        environment_cursor: str | None = None
        category_cursor: str | None = None
        environment_done = False
        category_done = False
        environments_available = False
        categories_available = False
        repository: Mapping[str, Any] | None = None
        while not (environment_done and category_done):
            data = self._optional_graphql(
                REPOSITORY_CONFIGURATION_QUERY,
                {
                    "owner": org,
                    "name": repo,
                    "environmentCursor": environment_cursor,
                    "categoryCursor": category_cursor,
                },
                "RepositoryConfiguration",
                (
                    ("repositories", "items", repo, "settings"),
                    ("repositories", "items", repo, "environments"),
                    (
                        "repositories",
                        "items",
                        repo,
                        "discussion_categories",
                    ),
                    ("repositories", "items", repo, "social_preview"),
                ),
            )
            page_repository = data.get("repository") if data is not None else None
            if not isinstance(page_repository, Mapping):
                for path in (
                    ("repositories", "items", repo, "settings"),
                    ("repositories", "items", repo, "environments"),
                    ("repositories", "items", repo, "discussion_categories"),
                    ("repositories", "items", repo, "social_preview"),
                ):
                    self._mark_collection_unavailable(
                        path, "/graphql#RepositoryConfiguration"
                    )
                break
            repository = page_repository
            node_id = repository.get("id")
            if isinstance(node_id, str):
                self.ids[("repositories", repo, "node_id")] = node_id

            environment_connection = repository.get("environments")
            if not environment_done and isinstance(environment_connection, Mapping):
                environments_available = True
                for environment in _connection_nodes(environment_connection):
                    name = str(environment.get("name"))
                    environments[name] = environment
                if _connection_has_next_page(environment_connection):
                    next_cursor = _connection_end_cursor(environment_connection)
                    if next_cursor is None or next_cursor == environment_cursor:
                        self._mark_comment_caveat(
                            ("repositories", "items", repo, "environments"),
                            "GitHub reported additional environments without a "
                            "usable cursor, so this export is incomplete.",
                        )
                        environment_done = True
                    else:
                        environment_cursor = next_cursor
                else:
                    environment_done = True
            elif not environment_done:
                environment_done = True

            category_connection = repository.get("discussionCategories")
            if not category_done and isinstance(category_connection, Mapping):
                categories_available = True
                for category in _connection_nodes(category_connection):
                    slug = str(category.get("slug") or category.get("id"))
                    categories[slug] = category
                if _connection_has_next_page(category_connection):
                    next_cursor = _connection_end_cursor(category_connection)
                    if next_cursor is None or next_cursor == category_cursor:
                        self._mark_comment_caveat(
                            (
                                "repositories",
                                "items",
                                repo,
                                "discussion_categories",
                            ),
                            "GitHub reported additional discussion categories "
                            "without a usable cursor, so this export is incomplete.",
                        )
                        category_done = True
                    else:
                        category_cursor = next_cursor
                else:
                    category_done = True
            elif not category_done:
                category_done = True

        if repository is None:
            return
        settings = target.setdefault("settings", {})
        if isinstance(settings, dict):
            for graphql_key, config_key in (
                ("hasDiscussionsEnabled", "has_discussions"),
                ("hasSponsorshipsEnabled", "has_sponsorships"),
                ("issueCreationPolicy", "issue_creation_policy"),
            ):
                value = repository.get(graphql_key)
                if isinstance(value, str):
                    settings[config_key] = value.lower()
                elif isinstance(value, bool):
                    settings[config_key] = value
                else:
                    self._mark_collection_unavailable(
                        (
                            "repositories",
                            "items",
                            repo,
                            "settings",
                            config_key,
                        ),
                        "/graphql#RepositoryConfiguration",
                    )

        if (
            "openGraphImageUrl" in repository
            and "usesCustomOpenGraphImage" in repository
        ):
            target["social_preview"] = without_none(
                {
                    "url": repository.get("openGraphImageUrl"),
                    "uses_custom_image": repository.get("usesCustomOpenGraphImage"),
                }
            )
            self._mark_field_read_only(
                ("repositories", "items", repo, "social_preview"),
                "GitHub exposes the repository social preview image but its public "
                "APIs do not provide an operation that changes it.",
            )
        else:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "social_preview"),
                "/graphql#RepositoryConfiguration",
            )

        if categories_available:
            category_items = {
                slug: without_none(
                    {
                        "name": category.get("name"),
                        "slug": category.get("slug"),
                        "description": category.get("description"),
                        "emoji": category.get("emoji"),
                        "answerable": category.get("isAnswerable"),
                    }
                )
                for slug, category in categories.items()
            }
            target["discussion_categories"] = exact_collection(
                sorted_mapping(category_items)
            )
            self._mark_field_read_only(
                ("repositories", "items", repo, "discussion_categories"),
                "GitHub exposes discussion categories but does not provide a public "
                "API that creates, updates, or deletes them.",
            )
        else:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "discussion_categories"),
                "/graphql#RepositoryConfiguration",
            )

        if environments_available:
            environment_collection = target.get("environments")
            if not isinstance(environment_collection, dict):
                environment_collection = {"mode": "merge", "items": {}}
                target["environments"] = environment_collection
                self._mark_comment_caveat(
                    ("repositories", "items", repo, "environments"),
                    "Only pin state was accessible for these environments. Their "
                    "other settings are absent, and merge mode prevents their "
                    "deletion.",
                )
            environment_items = environment_collection.setdefault("items", {})
            if isinstance(environment_items, dict):
                for name, environment in environments.items():
                    item = environment_items.setdefault(name, {})
                    if not isinstance(item, dict):
                        continue
                    item["pinned"] = bool(environment.get("isPinned"))
                    item["pinned_position"] = environment.get("pinnedPosition")
                    environment_id = environment.get("id")
                    if isinstance(environment_id, str):
                        self.ids[
                            (
                                "repositories",
                                repo,
                                "environments",
                                name,
                                "node_id",
                            )
                        ] = environment_id

    def _export_cloud_agent_configuration(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        path = f"/repos/{quote(org)}/{quote(repo)}/copilot/cloud-agent/configuration"
        value = self._optional_get(path)
        if value is _MISSING:
            self._mark_collection_unavailable(
                (
                    "repositories",
                    "items",
                    repo,
                    "agents",
                    "cloud_configuration",
                ),
                path,
            )
            return
        if isinstance(value, Mapping):
            set_path(target, "agents.cloud_configuration", dict(value))
            self._mark_field_read_only(
                (
                    "repositories",
                    "items",
                    repo,
                    "agents",
                    "cloud_configuration",
                ),
                "GitHub exposes this Copilot cloud agent configuration through a "
                "read-only public endpoint.",
            )

    def _export_branch_protection_rules(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        nodes: list[Mapping[str, Any]] = []
        cursor: str | None = None
        complete = True
        while True:
            data = self._optional_graphql(
                REPOSITORY_BRANCH_PROTECTION_RULES_QUERY,
                {"owner": org, "name": repo, "cursor": cursor},
                "RepositoryBranchProtectionRules",
                (
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protection_rules",
                    ),
                ),
            )
            if getattr(data, "errors", ()):
                complete = False
            repository = data.get("repository") if data is not None else None
            connection = (
                repository.get("branchProtectionRules")
                if isinstance(repository, Mapping)
                else None
            )
            if not isinstance(connection, Mapping):
                self._mark_collection_unavailable(
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protection_rules",
                    ),
                    "/graphql#RepositoryBranchProtectionRules",
                )
                return
            nodes.extend(_connection_nodes(connection))
            if not _connection_has_next_page(connection):
                break
            next_cursor = _connection_end_cursor(connection)
            if next_cursor is None or next_cursor == cursor:
                complete = False
                self._mark_comment_caveat(
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protection_rules",
                    ),
                    "GitHub reported additional branch protection rules without a "
                    "usable cursor. The returned rules use merge mode so a later "
                    "apply cannot remove rules missing from this export.",
                )
                break
            cursor = next_cursor

        items: dict[str, Any] = {}
        identities = Counter(str(node.get("pattern")) for node in nodes)
        for node in nodes:
            rule_id = node.get("id")
            pattern = str(node.get("pattern"))
            key = _unique_key(
                items,
                pattern,
                str(rule_id or pattern),
                duplicate=identities[pattern] > 1,
            )
            if isinstance(rule_id, str):
                self.ids[("repositories", repo, "branch_protection_rules", key)] = (
                    rule_id
                )
            item = {
                config_key: node.get(graphql_key)
                for config_key, graphql_key in _BRANCH_PROTECTION_RULE_FIELDS.items()
            }
            raw_checks = node.get("requiredStatusChecks")
            has_ambiguous_app = False
            if isinstance(raw_checks, list):
                checks: list[dict[str, Any]] = []
                checks_complete = True
                for check in raw_checks:
                    if not isinstance(check, Mapping):
                        checks_complete = False
                        break
                    context = check.get("context")
                    if not isinstance(context, str):
                        checks_complete = False
                        break
                    app = check.get("app")
                    if app is not None and not isinstance(app, Mapping):
                        checks_complete = False
                        break
                    if app is None:
                        has_ambiguous_app = True
                    app_slug = app.get("slug") if isinstance(app, Mapping) else None
                    app_id = app.get("id") if isinstance(app, Mapping) else None
                    if isinstance(app, Mapping) and not isinstance(app_slug, str):
                        checks_complete = False
                        break
                    if isinstance(app_slug, str) and isinstance(app_id, str):
                        self.ids[("apps", app_slug, "node_id")] = app_id
                    checks.append({"context": context, "app": app_slug})
                if checks_complete:
                    item["required_status_checks"] = checks
                    if has_ambiguous_app:
                        self._mark_comment_caveat(
                            (
                                "repositories",
                                "items",
                                repo,
                                "branch_protection_rules",
                                "items",
                                key,
                                "required_status_checks",
                            ),
                            "GitHub returns no App object both when a status check "
                            "uses its most recent App and when it accepts any App. "
                            "The export uses `null` because it cannot distinguish "
                            "those states. Set app to a specific App slug, `any`, or "
                            "`recent` before changing this status-check list.",
                        )
                else:
                    self._mark_comment_caveat(
                        (
                            "repositories",
                            "items",
                            repo,
                            "branch_protection_rules",
                            "items",
                            key,
                        ),
                        "GitHub returned a required status check that cannot be "
                        "represented safely. The status-check list is omitted and "
                        "remains unmanaged.",
                    )
            else:
                self._mark_comment_caveat(
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protection_rules",
                        "items",
                        key,
                    ),
                    "GitHub did not return the required status checks. That list "
                    "is omitted and remains unmanaged.",
                )
            actor_connections, incomplete_actor_fields = (
                self._complete_branch_protection_actor_connections(
                    node,
                    repo,
                    key,
                )
            )
            if incomplete_actor_fields:
                incomplete_names = ", ".join(
                    config_key.replace("_", " ")
                    for config_key, graphql_key in _BRANCH_PROTECTION_ACTOR_FIELDS.items()
                    if graphql_key in incomplete_actor_fields
                )
                self._mark_comment_caveat(
                    (
                        "repositories",
                        "items",
                        repo,
                        "branch_protection_rules",
                        "items",
                        key,
                    ),
                    f"GitHub did not return usable cursors for the {incomplete_names} "
                    "lists. Those actor lists are omitted and remain unmanaged.",
                )
            for config_key, graphql_key in _BRANCH_PROTECTION_ACTOR_FIELDS.items():
                if graphql_key in incomplete_actor_fields:
                    continue
                connection = actor_connections.get(graphql_key)
                actors: list[str] = []
                actors_complete = True
                if isinstance(connection, Mapping):
                    for allowance in _connection_nodes(connection):
                        actor = allowance.get("actor")
                        if not isinstance(actor, Mapping):
                            actors_complete = False
                            break
                        reference = _branch_actor_reference(actor)
                        if reference is None:
                            actors_complete = False
                            break
                        actors.append(reference)
                        actor_id = actor.get("id")
                        if isinstance(actor_id, str):
                            actor_type, identity = reference.split(":", 1)
                            self.ids[
                                (
                                    "branch_protection_actors",
                                    actor_type,
                                    identity.casefold(),
                                )
                            ] = actor_id
                if actors_complete:
                    item[config_key] = sorted(actors, key=str.casefold)
                else:
                    self._mark_comment_caveat(
                        (
                            "repositories",
                            "items",
                            repo,
                            "branch_protection_rules",
                            "items",
                            key,
                        ),
                        f"GitHub returned a {config_key.replace('_', ' ')} entry "
                        "without a usable actor identity. That actor list is "
                        "omitted and remains unmanaged.",
                    )
            normalized_item = without_none(item)
            if has_ambiguous_app and "required_status_checks" in item:
                normalized_item["required_status_checks"] = item[
                    "required_status_checks"
                ]
            items[key] = normalized_item
        target["branch_protection_rules"] = {
            "mode": "exact" if complete else "merge",
            "items": sorted_mapping(items),
        }

    def _complete_branch_protection_actor_connections(
        self,
        rule: Mapping[str, Any],
        repo: str,
        key: str,
    ) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
        connections: dict[str, Mapping[str, Any]] = {
            field_name: dict(connection)
            for field_name in _BRANCH_PROTECTION_ACTOR_CURSORS
            if isinstance((connection := rule.get(field_name)), Mapping)
        }
        pending = {
            field_name
            for field_name, connection in connections.items()
            if _connection_has_next_page(connection)
        }
        incomplete = set(_BRANCH_PROTECTION_ACTOR_CURSORS) - set(connections)
        if not pending:
            return connections, incomplete
        rule_id = rule.get("id")
        if not isinstance(rule_id, str):
            return connections, pending
        variables: dict[str, Any] = {"id": rule_id}
        for field_name, cursor_name in _BRANCH_PROTECTION_ACTOR_CURSORS.items():
            connection = connections.get(field_name)
            variables[cursor_name] = (
                _connection_end_cursor(connection)
                if isinstance(connection, Mapping)
                else None
            )
        for field_name in tuple(pending):
            cursor_name = _BRANCH_PROTECTION_ACTOR_CURSORS[field_name]
            if variables[cursor_name] is None:
                pending.remove(field_name)
                incomplete.add(field_name)

        caveat_path = (
            "repositories",
            "items",
            repo,
            "branch_protection_rules",
            "items",
            key,
        )
        while pending:
            data = self._optional_graphql(
                BRANCH_PROTECTION_RULE_ACTORS_QUERY,
                variables,
                "BranchProtectionRuleActors",
                (caveat_path,),
            )
            if data is None:
                incomplete.update(pending)
                break
            if getattr(data, "errors", ()):
                incomplete.update(pending)
                break
            node = data.get("node")
            if not isinstance(node, Mapping):
                incomplete.update(pending)
                break
            for field_name in tuple(pending):
                page = node.get(field_name)
                if not isinstance(page, Mapping):
                    pending.remove(field_name)
                    incomplete.add(field_name)
                    continue
                combined_nodes = [
                    *_connection_nodes(connections[field_name]),
                    *_connection_nodes(page),
                ]
                connections[field_name] = {
                    "nodes": combined_nodes,
                    "pageInfo": page.get("pageInfo"),
                }
                if not _connection_has_next_page(page):
                    pending.remove(field_name)
                    continue
                cursor_name = _BRANCH_PROTECTION_ACTOR_CURSORS[field_name]
                next_cursor = _connection_end_cursor(page)
                if next_cursor is None or next_cursor == variables[cursor_name]:
                    pending.remove(field_name)
                    incomplete.add(field_name)
                else:
                    variables[cursor_name] = next_cursor
        return connections, incomplete

    def _export_pull_request_limits(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        base = f"/repos/{quote(org)}/{quote(repo)}/interaction-limits/pulls"
        cap = self._optional_get(f"{base}/creation-cap")
        if cap is not _MISSING and isinstance(cap, dict):
            target["pull_request_creation_cap"] = pick(
                cap, ("enabled", "max_open_pull_requests")
            )
        users = self._optional_list(f"{base}/bypass-list")
        if users is not _MISSING:
            target["pull_request_creation_cap_bypass_users"] = sorted(
                str(user["login"]) for user in users
            )

    def _export_repo_self_hosted_runners(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        path = f"/repos/{quote(org)}/{quote(repo)}/actions/runners"
        runners = self._optional_list(path, item_key="runners")
        if runners is _MISSING:
            self._mark_collection_unavailable(
                (
                    "repositories",
                    "items",
                    repo,
                    "actions",
                    "self_hosted_runners",
                ),
                path,
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(runner["name"]) for runner in runners)
        for runner in runners:
            runner_id = int(runner["id"])
            preferred = str(runner["name"])
            key = _unique_key(
                items,
                preferred,
                runner_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("repositories", repo, "self_hosted_runners", key)] = runner_id
            items[key] = {
                "labels": sorted(
                    str(label["name"])
                    for label in runner.get("labels", [])
                    if label.get("type") == "custom"
                )
            }
        set_path(
            target,
            "actions.self_hosted_runners",
            exact_collection(sorted_mapping(items)),
        )

    def _export_repo_collaborators(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        path = with_query(
            f"/repos/{quote(org)}/{quote(repo)}/collaborators", affiliation="direct"
        )
        collaborators = self._optional_list(path)
        if collaborators is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "collaborators"), path
            )
            return
        items: dict[str, str] = {}
        for collaborator in collaborators:
            login = str(collaborator["login"])
            items[login] = str(
                collaborator.get("role_name") or _permission_name(collaborator)
            )
            if "id" in collaborator:
                self.ids[("users", login.casefold())] = int(collaborator["id"])
        target["collaborators"] = exact_collection(sorted_mapping(items))

    def _export_repo_invitations(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        invitations = self._optional_list(
            f"/repos/{quote(org)}/{quote(repo)}/invitations"
        )
        if invitations is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "collaborator_invitations"),
                f"/repos/{quote(org)}/{quote(repo)}/invitations",
            )
            return
        items: dict[str, str] = {}
        complete = True
        identities = Counter(
            str(invitee["login"])
            for invitation in invitations
            if isinstance((invitee := invitation.get("invitee")), dict)
            and invitee.get("login")
        )
        for invitation in invitations:
            invitation_id = int(invitation["id"])
            invitee = invitation.get("invitee")
            login = invitee.get("login") if isinstance(invitee, dict) else None
            permission = invitation.get("permissions")
            if not isinstance(login, str) or not isinstance(permission, str):
                self.unavailable.append(
                    f"/repos/{quote(org)}/{quote(repo)}/invitations/{invitation_id} "
                    "(invitee or permission is unavailable)"
                )
                complete = False
                self._mark_collection_unavailable(
                    (
                        "repositories",
                        "items",
                        repo,
                        "collaborator_invitations",
                    ),
                    f"/repos/{quote(org)}/{quote(repo)}/invitations/{invitation_id}",
                )
                continue
            key = _unique_key(
                items,
                login,
                invitation_id,
                duplicate=identities[login] > 1,
            )
            self.ids[("repositories", repo, "invitations", key)] = invitation_id
            items[key] = permission
            if "id" in invitee:
                self.ids[("users", login.casefold())] = int(invitee["id"])
        if complete:
            target["collaborator_invitations"] = exact_collection(sorted_mapping(items))

    def _export_deploy_keys(self, target: dict[str, Any], org: str, repo: str) -> None:
        keys = self._optional_list(f"/repos/{quote(org)}/{quote(repo)}/keys")
        if keys is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "deploy_keys"),
                f"/repos/{quote(org)}/{quote(repo)}/keys",
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(key["title"]) for key in keys)
        for key in keys:
            key_id = int(key["id"])
            preferred = str(key["title"])
            name = _unique_key(
                items,
                preferred,
                key_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("repositories", repo, "deploy_keys", name)] = key_id
            items[name] = pick(key, ("title", "key", "read_only"))
        target["deploy_keys"] = exact_collection(sorted_mapping(items))

    def _export_autolinks(self, target: dict[str, Any], org: str, repo: str) -> None:
        links = self._optional_list(f"/repos/{quote(org)}/{quote(repo)}/autolinks")
        if links is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "autolinks"),
                f"/repos/{quote(org)}/{quote(repo)}/autolinks",
            )
            return
        items: dict[str, Any] = {}
        identities = Counter(str(link["key_prefix"]) for link in links)
        for link in links:
            link_id = int(link["id"])
            preferred = str(link["key_prefix"])
            key = _unique_key(
                items,
                preferred,
                link_id,
                duplicate=identities[preferred] > 1,
            )
            self.ids[("repositories", repo, "autolinks", key)] = link_id
            items[key] = pick(link, ("key_prefix", "url_template", "is_alphanumeric"))
        target["autolinks"] = exact_collection(sorted_mapping(items))

    def _export_labels(self, target: dict[str, Any], org: str, repo: str) -> None:
        labels = self._optional_list(f"/repos/{quote(org)}/{quote(repo)}/labels")
        if labels is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "labels"),
                f"/repos/{quote(org)}/{quote(repo)}/labels",
            )
            return
        items = {
            str(label["name"]): pick(label, ("name", "color", "description"))
            for label in labels
        }
        target["labels"] = exact_collection(sorted_mapping(items))

    def _export_branch_protections(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        path = with_query(
            f"/repos/{quote(org)}/{quote(repo)}/branches", protected="true"
        )
        branches = self._optional_list(path)
        if branches is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "branch_protections"), path
            )
            return
        items: dict[str, Any] = {}
        complete = True
        for branch in branches:
            name = str(branch["name"])
            protection = self._optional_get(
                f"/repos/{quote(org)}/{quote(repo)}/branches/{quote(name)}/protection"
            )
            if protection is _MISSING or not isinstance(protection, dict):
                complete = False
                self._mark_collection_unavailable(
                    ("repositories", "items", repo, "branch_protections"),
                    f"/repos/{quote(org)}/{quote(repo)}/branches/{quote(name)}/protection",
                )
                continue
            normalized = _normalize_branch_protection(protection)
            signature_path = (
                f"/repos/{quote(org)}/{quote(repo)}/branches/{quote(name)}"
                "/protection/required_signatures"
            )
            try:
                signature = self.api.request("GET", signature_path).data
                normalized["required_signatures"] = (
                    bool(signature.get("enabled"))
                    if isinstance(signature, dict)
                    else True
                )
            except ApiError as error:
                if error.status == 404:
                    normalized["required_signatures"] = False
                elif error.status in _OPTIONAL_UNAVAILABLE_STATUSES:
                    self.unavailable.append(f"{signature_path} ({error.status})")
                    complete = False
                    self._mark_collection_unavailable(
                        ("repositories", "items", repo, "branch_protections"),
                        signature_path,
                    )
                else:
                    raise
            items[name] = normalized
        if complete:
            target["branch_protections"] = exact_collection(sorted_mapping(items))

    def _export_environments(self, target: dict[str, Any], org: str, repo: str) -> None:
        base = f"/repos/{quote(org)}/{quote(repo)}/environments"
        environments = self._optional_list(base, item_key="environments")
        if environments is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "environments"), base
            )
            return
        items: dict[str, Any] = {}
        complete = True
        for summary in environments:
            name = str(summary["name"])
            detail = self._optional_get(f"{base}/{quote(name)}")
            if detail is _MISSING or not isinstance(detail, dict):
                complete = False
                self._mark_collection_unavailable(
                    ("repositories", "items", repo, "environments"),
                    f"{base}/{quote(name)}",
                )
                continue
            item = _normalize_environment(detail)
            policy = item.get("settings", {}).get("deployment_branch_policy")
            if isinstance(policy, Mapping) and policy.get("custom_branch_policies"):
                policies = self._optional_list(
                    f"{base}/{quote(name)}/deployment-branch-policies",
                    item_key="branch_policies",
                )
                if policies is not _MISSING:
                    policy_items: dict[str, Any] = {}
                    identities = Counter(str(policy["name"]) for policy in policies)
                    for branch_policy in policies:
                        policy_id = int(branch_policy["id"])
                        policy_name = str(branch_policy["name"])
                        key = _unique_key(
                            policy_items,
                            policy_name,
                            policy_id,
                            duplicate=identities[policy_name] > 1,
                        )
                        self.ids[
                            (
                                "repositories",
                                repo,
                                "environments",
                                name,
                                "branch_policies",
                                key,
                            )
                        ] = policy_id
                        policy_items[key] = pick(branch_policy, ("name", "type"))
                    item["branch_policies"] = exact_collection(
                        sorted_mapping(policy_items)
                    )
                else:
                    self._mark_collection_unavailable(
                        (
                            "repositories",
                            "items",
                            repo,
                            "environments",
                            "items",
                            name,
                            "branch_policies",
                        ),
                        f"{base}/{quote(name)}/deployment-branch-policies",
                    )
            protection_rules = self._optional_list(
                f"{base}/{quote(name)}/deployment_protection_rules",
                item_key="custom_deployment_protection_rules",
            )
            if protection_rules is not _MISSING:
                protection_items: dict[str, Any] = {}
                identities = Counter(
                    str((rule.get("app") or {}).get("slug") or rule["id"])
                    for rule in protection_rules
                )
                for rule in protection_rules:
                    app = rule.get("app", {})
                    app_slug = str(app.get("slug") or rule["id"])
                    key = _unique_key(
                        protection_items,
                        app_slug,
                        int(rule["id"]),
                        duplicate=identities[app_slug] > 1,
                    )
                    self.ids[
                        (
                            "repositories",
                            repo,
                            "environments",
                            name,
                            "protection_rules",
                            key,
                        )
                    ] = int(rule["id"])
                    if app.get("id") is not None:
                        self.ids[("apps", app_slug)] = int(app["id"])
                    protection_items[key] = {"enabled": bool(rule.get("enabled", True))}
                item["deployment_protection_rules"] = exact_collection(
                    sorted_mapping(protection_items)
                )
            else:
                self._mark_collection_unavailable(
                    (
                        "repositories",
                        "items",
                        repo,
                        "environments",
                        "items",
                        name,
                        "deployment_protection_rules",
                    ),
                    f"{base}/{quote(name)}/deployment_protection_rules",
                )
            self._export_environment_variables_and_secrets(item, org, repo, name)
            items[name] = item
        if complete:
            target["environments"] = exact_collection(sorted_mapping(items))

    def _export_environment_variables_and_secrets(
        self,
        target: dict[str, Any],
        org: str,
        repo: str,
        environment: str,
    ) -> None:
        base = f"/repos/{quote(org)}/{quote(repo)}/environments/{quote(environment)}"
        variables = self._optional_list(f"{base}/variables", item_key="variables")
        if variables is not _MISSING:
            items = {str(value["name"]): pick(value, ("value",)) for value in variables}
            target["variables"] = exact_collection(sorted_mapping(items))
        else:
            self._mark_collection_unavailable(
                (
                    "repositories",
                    "items",
                    repo,
                    "environments",
                    "items",
                    environment,
                    "variables",
                ),
                f"{base}/variables",
            )
        secrets = self._optional_list(f"{base}/secrets", item_key="secrets")
        if secrets is not _MISSING:
            target["secrets"] = exact_collection(
                sorted_mapping({str(value["name"]): {} for value in secrets})
            )
        else:
            self._mark_collection_unavailable(
                (
                    "repositories",
                    "items",
                    repo,
                    "environments",
                    "items",
                    environment,
                    "secrets",
                ),
                f"{base}/secrets",
            )

    def _export_repo_variables_and_secrets(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        for scope in ("actions", "agents"):
            base = f"/repos/{quote(org)}/{quote(repo)}/{scope}"
            variables = self._optional_list(f"{base}/variables", item_key="variables")
            if variables is not _MISSING:
                items = {
                    str(value["name"]): pick(value, ("value",)) for value in variables
                }
                set_path(
                    target,
                    f"{scope}.variables",
                    exact_collection(sorted_mapping(items)),
                )
            else:
                self._mark_collection_unavailable(
                    ("repositories", "items", repo, scope, "variables"),
                    f"{base}/variables",
                )
            secrets = self._optional_list(f"{base}/secrets", item_key="secrets")
            if secrets is not _MISSING:
                set_path(
                    target,
                    f"{scope}.secrets",
                    exact_collection(
                        sorted_mapping({str(value["name"]): {} for value in secrets})
                    ),
                )
            else:
                self._mark_collection_unavailable(
                    ("repositories", "items", repo, scope, "secrets"),
                    f"{base}/secrets",
                )
        for scope in ("codespaces", "dependabot"):
            secrets = self._optional_list(
                f"/repos/{quote(org)}/{quote(repo)}/{scope}/secrets",
                item_key="secrets",
            )
            if secrets is not _MISSING:
                set_path(
                    target,
                    f"{scope}.secrets",
                    exact_collection(
                        sorted_mapping({str(value["name"]): {} for value in secrets})
                    ),
                )
            else:
                self._mark_collection_unavailable(
                    ("repositories", "items", repo, scope, "secrets"),
                    f"/repos/{quote(org)}/{quote(repo)}/{scope}/secrets",
                )

    def _export_repo_custom_properties(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        values = self._optional_list(
            f"/repos/{quote(org)}/{quote(repo)}/properties/values"
        )
        if values is not _MISSING:
            target["custom_properties"] = sorted_mapping(
                {str(value["property_name"]): value.get("value") for value in values}
            )

    def _export_organization_custom_property_values(
        self, target: dict[str, Any], org: str
    ) -> None:
        path = f"/organizations/{quote(org)}/org-properties/values"
        values = self._optional_list(path)
        if values is _MISSING:
            self._mark_collection_unavailable(
                ("organization", "custom_property_values"), path
            )
            return
        target["custom_property_values"] = exact_collection(
            sorted_mapping(
                {str(value["property_name"]): value.get("value") for value in values}
            )
        )

    def _export_security_toggles(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        toggles: dict[str, bool] = {}
        for name, template in SECURITY_TOGGLES.items():
            path = template.format(org=quote(org), repo=quote(repo))
            try:
                response = self.api.request("GET", path)
            except ApiError as error:
                if error.status == 404:
                    toggles[name] = False
                    continue
                if error.status in _OPTIONAL_UNAVAILABLE_STATUSES:
                    self.unavailable.append(f"{path} ({error.status})")
                    continue
                raise
            toggles[name] = response.status in (200, 204)
        if toggles:
            target["security"] = toggles

    def _export_pages(self, target: dict[str, Any], org: str, repo: str) -> None:
        path = f"/repos/{quote(org)}/{quote(repo)}/pages"
        try:
            value = self.api.request("GET", path).data
        except ApiError as error:
            if error.status == 404:
                target["pages"] = {"enabled": False}
                return
            if error.status in _OPTIONAL_UNAVAILABLE_STATUSES:
                self.unavailable.append(f"{path} ({error.status})")
                return
            raise
        if isinstance(value, dict):
            source_value = value.get("source")
            source = (
                pick(source_value, ("branch", "path"))
                if isinstance(source_value, Mapping)
                else None
            )
            if not source:
                source = None
            target["pages"] = without_none(
                {
                    "enabled": True,
                    "build_type": value.get("build_type"),
                    "source": source,
                    "cname": value.get("cname"),
                    "https_enforced": value.get("https_enforced"),
                    "public": value.get("public"),
                }
            )

    def _export_workflow_states(
        self, target: dict[str, Any], org: str, repo: str
    ) -> None:
        workflows = self._optional_list(
            f"/repos/{quote(org)}/{quote(repo)}/actions/workflows",
            item_key="workflows",
        )
        if workflows is _MISSING:
            self._mark_collection_unavailable(
                ("repositories", "items", repo, "workflow_states"),
                f"/repos/{quote(org)}/{quote(repo)}/actions/workflows",
            )
            return
        items: dict[str, str] = {}
        identities = Counter(str(workflow["path"]) for workflow in workflows)
        for workflow in workflows:
            path = str(workflow["path"])
            workflow_id = int(workflow["id"])
            key = _unique_key(
                items,
                path,
                workflow_id,
                duplicate=identities[path] > 1,
            )
            self.ids[("repositories", repo, "workflows", key)] = workflow_id
            items[key] = str(workflow["state"])
        target["workflow_states"] = exact_collection(sorted_mapping(items))

    def _optional_get(self, path: str, *, required: bool = False) -> Any:
        try:
            return self.api.request("GET", path).data
        except ApiError as error:
            if not required and error.status in _OPTIONAL_UNAVAILABLE_STATUSES:
                self.unavailable.append(f"{path} ({error.status})")
                return _MISSING
            raise

    def _optional_list(
        self,
        path: str,
        *,
        item_key: str | None = None,
        required: bool = False,
        record_unavailable: bool = True,
    ) -> list[Any] | None:
        try:
            return self.api.get_all(path, item_key=item_key)
        except ApiError as error:
            if not required and error.status in _OPTIONAL_UNAVAILABLE_STATUSES:
                if record_unavailable:
                    self.unavailable.append(f"{path} ({error.status})")
                return _MISSING
            raise


def _permission_name(value: Mapping[str, Any]) -> str:
    role_name = value.get("role_name")
    if isinstance(role_name, str):
        return role_name
    permissions = value.get("permissions", {})
    for name in ("admin", "maintain", "push", "triage", "pull"):
        if permissions.get(name):
            return name
    return "pull"


def _normalize_pattern_configurations(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    version = value.get("pattern_config_version")
    if version is not None:
        normalized["_pattern_config_version"] = version
    for source_name, target_name in (
        ("provider_pattern_overrides", "provider_pattern_settings"),
        ("custom_pattern_overrides", "custom_pattern_settings"),
    ):
        overrides = value.get(source_name)
        if not isinstance(overrides, list):
            continue
        settings = []
        for override in overrides:
            if not isinstance(override, dict):
                continue
            item = pick(override, ("token_type", "custom_pattern_version"))
            if "setting" in override:
                item["push_protection_setting"] = override["setting"]
            settings.append(item)
        normalized[target_name] = sorted(
            settings,
            key=lambda item: (
                str(item.get("token_type", "")),
                str(item.get("custom_pattern_version", "")),
            ),
        )
    return normalized


def _connection_nodes(connection: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    nodes = connection.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, Mapping)]


def _connection_has_next_page(connection: Mapping[str, Any]) -> bool:
    page_info = connection.get("pageInfo")
    return isinstance(page_info, Mapping) and page_info.get("hasNextPage") is True


def _connection_end_cursor(connection: Mapping[str, Any]) -> str | None:
    page_info = connection.get("pageInfo")
    cursor = page_info.get("endCursor") if isinstance(page_info, Mapping) else None
    return cursor if isinstance(cursor, str) else None


def _branch_actor_reference(actor: Any) -> str | None:
    if not isinstance(actor, Mapping):
        return None
    typename = actor.get("__typename")
    if typename == "User" and isinstance(actor.get("login"), str):
        return f"user:{actor['login']}"
    if typename == "Team" and isinstance(actor.get("slug"), str):
        return f"team:{actor['slug']}"
    if typename == "App" and isinstance(actor.get("slug"), str):
        return f"app:{actor['slug']}"
    return None


def _configuration_item_path(path: tuple[str, ...]) -> tuple[str, ...]:
    collection_names = {
        "custom_properties",
        "members",
        "repositories",
        "runner_groups",
        "teams",
    }
    result: list[str] = []
    for index, part in enumerate(path):
        result.append(part)
        if part in collection_names and index + 1 < len(path):
            result.append("items")
    return tuple(result)


def _unique_key(
    items: Mapping[str, Any],
    preferred: str,
    resource_id: int | str,
    *,
    duplicate: bool = False,
) -> str:
    if not duplicate and preferred not in items:
        return preferred
    stable = f"{preferred}#github-id-{resource_id}"
    if stable not in items:
        return stable
    index = 2
    while f"{stable}#{index}" in items:
        index += 1
    return f"{stable}#{index}"


def _normalize_branch_protection(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    status_checks = value.get("required_status_checks")
    if isinstance(status_checks, dict):
        checks = status_checks.get("checks")
        normalized["required_status_checks"] = without_none(
            {
                "strict": status_checks.get("strict", False),
                "checks": [pick(check, ("context", "app_id")) for check in checks]
                if isinstance(checks, list)
                else None,
                "contexts": status_checks.get("contexts")
                if not isinstance(checks, list)
                else None,
            }
        )
    else:
        normalized["required_status_checks"] = None
    normalized["enforce_admins"] = _enabled(value.get("enforce_admins"))
    pull_requests = value.get("required_pull_request_reviews")
    if isinstance(pull_requests, dict):
        review = pick(
            pull_requests,
            (
                "dismiss_stale_reviews",
                "require_code_owner_reviews",
                "required_approving_review_count",
                "require_last_push_approval",
            ),
        )
        review["dismissal_restrictions"] = _actor_lists(
            pull_requests.get("dismissal_restrictions")
        )
        review["bypass_pull_request_allowances"] = _actor_lists(
            pull_requests.get("bypass_pull_request_allowances")
        )
        normalized["required_pull_request_reviews"] = review
    else:
        normalized["required_pull_request_reviews"] = None
    normalized["restrictions"] = (
        _actor_lists(value.get("restrictions")) if value.get("restrictions") else None
    )
    for setting_name in (
        "required_linear_history",
        "allow_force_pushes",
        "allow_deletions",
        "block_creations",
        "required_conversation_resolution",
        "lock_branch",
        "allow_fork_syncing",
        "required_signatures",
    ):
        if setting_name in value:
            normalized[setting_name] = _enabled(value[setting_name])
    return normalized


def _actor_lists(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {"users": [], "teams": [], "apps": []}
    return {
        "users": sorted(str(item["login"]) for item in value.get("users", [])),
        "teams": sorted(str(item["slug"]) for item in value.get("teams", [])),
        "apps": sorted(str(item["slug"]) for item in value.get("apps", [])),
    }


def _enabled(value: Any) -> bool:
    return bool(value.get("enabled")) if isinstance(value, dict) else bool(value)


def _normalize_environment(value: Mapping[str, Any]) -> dict[str, Any]:
    wait_timer = 0
    prevent_self_review = False
    reviewers: list[dict[str, str]] = []
    for rule in value.get("protection_rules", []):
        if rule.get("type") == "wait_timer":
            wait_timer = int(rule.get("wait_timer", 0))
        if rule.get("type") == "required_reviewers":
            prevent_self_review = bool(rule.get("prevent_self_review", False))
            for reviewer in rule.get("reviewers", []):
                actor = reviewer.get("reviewer", {})
                actor_type = str(reviewer.get("type", "User")).lower()
                name = actor.get("slug") if actor_type == "team" else actor.get("login")
                if name:
                    reviewers.append({"type": actor_type, "name": str(name)})
    settings = {
        "wait_timer": wait_timer,
        "prevent_self_review": prevent_self_review,
        "reviewers": sorted(
            reviewers, key=lambda item: (item["type"], item["name"].casefold())
        ),
        "deployment_branch_policy": value.get("deployment_branch_policy"),
    }
    return {"settings": settings}
