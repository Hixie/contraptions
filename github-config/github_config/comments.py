from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from textwrap import wrap
from typing import Any
from unicodedata import category

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

PathPart = str | int
ConfigPath = tuple[PathPart, ...]


@dataclass(frozen=True)
class KeyComment:
    notes: tuple[str, ...]
    description: str
    values: str
    docs: str


_REST = "https://docs.github.com/en/rest"
_GRAPHQL = "https://docs.github.com/en/graphql/reference"
_GENERAL_DOCS = f"{_REST}/about-the-rest-api/about-the-rest-api"
_VERSION_DOCS = f"{_REST}/about-the-rest-api/api-versions"
_COMMENT_WIDTH = 80


_COLLECTION_NAMES = {
    "autolinks": "autolink reference",
    "branch_policies": "deployment branch policy",
    "branch_protections": "branch protection rule",
    "branch_protection_rules": "classic branch protection rule",
    "budgets": "budget",
    "collaborator_invitations": "repository invitation",
    "collaborators": "repository collaborator",
    "configurations": "code security configuration",
    "credential_authorizations": "SAML SSO credential authorization",
    "custom_patterns": "secret scanning custom pattern",
    "custom_properties": "custom property",
    "custom_property_values": "organization custom property value",
    "custom_organization_roles": "custom organization role",
    "custom_repository_roles": "custom repository role",
    "discussion_categories": "discussion category",
    "domains": "verified or approved domain",
    "deploy_keys": "deploy key",
    "deployment_protection_rules": "deployment protection rule",
    "environments": "deployment environment",
    "hooks": "webhook",
    "hosted_runners": "GitHub-hosted runner",
    "invitations": "organization invitation",
    "app_installations": "GitHub App installation",
    "entries": "IP allow-list entry",
    "issue_fields": "issue field",
    "issue_types": "issue type",
    "labels": "label",
    "members": "organization or team member",
    "network_configurations": "hosted compute network configuration",
    "organization_roles": "organization role",
    "outside_collaborators": "outside collaborator",
    "personal_access_tokens": "fine-grained personal access token grant",
    "private_registries": "private registry configuration",
    "repositories": "repository",
    "rulesets": "ruleset",
    "runner_groups": "self-hosted runner group",
    "secrets": "secret",
    "self_hosted_runners": "self-hosted runner",
    "teams": "team",
    "pinned_items": "pinned profile item",
    "variables": "variable",
    "workflow_states": "workflow",
}


_SECTION_NAMES = {
    "actions": "GitHub Actions settings",
    "agents": "Copilot coding agent settings",
    "branch_policies": "deployment branch policies",
    "branch_protections": "branch protection rules",
    "branch_protection_rules": "classic branch protection rules",
    "code_quality": "code quality settings",
    "code_scanning": "code scanning settings",
    "code_security": "code security settings",
    "codespaces": "Codespaces settings",
    "copilot": "GitHub Copilot settings",
    "dependabot": "Dependabot settings",
    "environments": "deployment environments",
    "hosted_compute": "hosted compute settings",
    "pages": "GitHub Pages settings",
    "review_assignment": "team review assignment settings",
    "social_preview": "repository social preview settings",
    "secret_scanning": "secret scanning settings",
    "security": "repository security feature settings",
}


_SETTING_DESCRIPTIONS = {
    "access_level": "Which outside repositories may call this repository's actions and reusable workflows.",
    "active": "Whether GitHub delivers this webhook's subscribed events.",
    "ai_findings_option": "When AI findings run for this repository's code quality analysis.",
    "algorithm": "How GitHub selects team members for automatic review assignment.",
    "announcement": "The organization announcement message.",
    "answerable": "Whether discussions in this category can have a marked answer.",
    "applies_to_installed_apps": "Whether the organization IP allow list applies to installed GitHub Apps.",
    "approved": "Whether the organization has approved this domain.",
    "advanced_security_enabled_for_new_repositories": "Whether GitHub Advanced Security is enabled when the organization creates repositories.",
    "allow_auto_merge": "Whether pull requests can merge automatically after requirements pass.",
    "allow_deletions": "Whether users with push access may delete the protected branch.",
    "allow_force_pushes": "Whether force pushes are allowed on the protected branch.",
    "allow_fork_syncing": "Whether a protected fork branch can sync with its upstream branch.",
    "allow_forking": "Whether private or internal repository forks are allowed.",
    "allow_merge_commit": "Whether pull requests can use merge commits.",
    "allow_rebase_merge": "Whether pull requests can use rebase merging.",
    "allow_squash_merge": "Whether pull requests can use squash merging.",
    "allow_update_branch": "Whether a pull request branch can be updated when it is behind its base branch.",
    "allowed_actions": "Which categories of actions and reusable workflows may run.",
    "allowed_values": "The allowed choices for a single-select or multi-select custom property.",
    "allows_public_repositories": "Whether public repositories may use this runner group.",
    "archived": "Whether the repository is archived and read-only.",
    "billing_email": "The organization's private billing email address.",
    "block_creations": "Whether users with push access may create matching protected branches.",
    "build_type": "Whether GitHub Pages uses the legacy source branch or a GitHub Actions workflow.",
    "credential_accessed_at": "When this credential last accessed an organization resource.",
    "credential_authorized_at": "When the credential was authorized for this organization.",
    "credential_type": "The kind of credential authorized through SAML SSO.",
    "custom_property_values": "Enterprise custom property values assigned to this organization.",
    "can_approve_pull_request_reviews": "Whether GitHub Actions may create or approve pull request reviews.",
    "cname": "The custom domain for the GitHub Pages site.",
    "default_branch": "The repository's default branch.",
    "default_repository_permission": "The base repository permission granted to organization members.",
    "default_workflow_permissions": "The default permissions granted to the GitHub Actions GITHUB_TOKEN.",
    "delete_branch_on_merge": "Whether GitHub deletes a pull request's head branch after merge.",
    "deployment_branch_policy": "Which branches and tags may deploy to this environment.",
    "description": "The human-readable description for this resource.",
    "dismiss_stale_reviews": "Whether new commits dismiss existing approving reviews.",
    "email": "The organization's publicly visible email address.",
    "enabled_repositories": "Which repositories may use this organization-level feature.",
    "expires_at": "When the organization announcement expires, if it has an expiration.",
    "fingerprint": "The SSH key fingerprint that identifies this credential.",
    "enforce_admins": "Whether branch protection requirements apply to repository administrators.",
    "events": "The GitHub events delivered to this webhook.",
    "has_issues": "Whether GitHub Issues is enabled for the repository.",
    "has_discussions": "Whether GitHub Discussions is enabled for the repository.",
    "has_sponsorships": "Whether the repository displays a GitHub Sponsors button.",
    "has_organization_projects": "Whether organization-level classic projects are enabled.",
    "has_projects": "Whether classic projects are enabled for the repository.",
    "has_pull_requests": "Whether pull requests are enabled for the repository.",
    "has_repository_projects": "Whether repository-level classic projects are enabled by default.",
    "has_wiki": "Whether the repository wiki is enabled.",
    "hash_algorithm": "The hash algorithm used to store this repository's Git objects.",
    "homepage": "The repository homepage URL.",
    "https_enforced": "Whether the GitHub Pages site redirects HTTP requests to HTTPS.",
    "include_claim_keys": "The claims included in the customized Actions OIDC subject.",
    "insecure_ssl": "Whether GitHub verifies the webhook endpoint's TLS certificate.",
    "is_template": "Whether the repository can be used as a template.",
    "limit": "Which group of users is limited by the interaction restriction.",
    "lock_branch": "Whether the protected branch is read-only.",
    "member_count": "How many team members GitHub assigns to each review.",
    "max_cache_retention_days": "The maximum number of days GitHub Actions caches may be retained.",
    "max_cache_size_gb": "The maximum GitHub Actions cache storage in gigabytes.",
    "members_can_create_repositories": "Whether organization members may create repositories.",
    "members_can_fork_private_repositories": "Whether organization members may fork private repositories.",
    "name": "The GitHub name for this resource.",
    "notification_setting": "Whether team members receive notifications when the team is mentioned.",
    "notification_restriction_enabled": "Whether GitHub restricts email notifications to verified or approved domains.",
    "parent": "This team's parent team.",
    "patterns_allowed": "Action and reusable-workflow reference patterns that may run when selected actions are enforced.",
    "prevent_self_review": "Whether users who initiated a deployment may approve it.",
    "pinned": "Whether the deployment environment is pinned.",
    "pinned_position": "The position of this pinned deployment environment.",
    "privacy": "The team's visibility and membership behavior.",
    "public": "Whether this organization membership appears publicly on the user's profile.",
    "read_only": "Whether the deploy key may only read repository data.",
    "require_code_owner_reviews": "Whether changes to owned files require approval from a code owner.",
    "require_last_push_approval": "Whether the most recent push must be approved by someone other than its author.",
    "required_approving_review_count": "The number of approving reviews required before merge.",
    "required_conversation_resolution": "Whether all review conversations must be resolved before merge.",
    "required_linear_history": "Whether the protected branch requires a linear commit history.",
    "required_signatures": "Whether commits pushed to the protected branch must have verified signatures.",
    "source_type": "Whether the organization or its enterprise defines this custom property.",
    "regex": "The regular expression that string custom property values must match.",
    "restricted_to_workflows": "Whether this runner group is limited to selected workflows.",
    "selected_repositories": "The repositories selected for this organization-level resource.",
    "selected_workflows": "The workflows allowed to use this runner group.",
    "secret_from_env": "The environment variable whose value is written as this webhook's secret.",
    "sha_pinning_required": "Whether actions must be pinned to a full-length commit SHA.",
    "topics": "All repository topics.",
    "token_last_eight": "The last eight characters that identify this token.",
    "use_default": "Whether the repository uses GitHub's default Actions OIDC subject format.",
    "use_immutable_subject": "Whether Actions OIDC subjects use immutable repository identifiers.",
    "use_squash_pr_title_as_default": "Whether a squash merge uses the pull request title by default.",
    "value_from_env": "The environment variable whose value is written to this write-only GitHub setting.",
    "visibility": "Who may access or use this resource.",
    "verified": "Whether GitHub has verified ownership of this domain.",
    "user_dismissible": "Whether organization members may dismiss the announcement.",
    "wait_timer": "The number of minutes a deployment waits before proceeding.",
    "web_commit_signoff_required": "Whether commits made in GitHub's web interface require signoff.",
}


_ENUM_VALUES = {
    "access_level": ("none", "user", "organization"),
    "allowed_actions": ("all", "local_only", "selected"),
    "approval_policy": (
        "first_time_contributors",
        "first_time_contributors_new_to_github",
        "all_external_contributors",
    ),
    "ai_findings_option": ("disabled", "on_push"),
    "algorithm": ("round_robin", "load_balance"),
    "auth_type": (
        "token",
        "username_password",
        "oidc_azure",
        "oidc_aws",
        "oidc_jfrog",
        "oidc_cloudsmith",
        "oidc_gcp",
    ),
    "build_type": ("legacy", "workflow"),
    "content_type": ("json", "form"),
    "credential_type": (
        "personal access token",
        "SSH key",
        "OAuth app token",
        "GitHub app token",
    ),
    "default_for_new_repos": ("all", "none", "private_and_internal", "public"),
    "default_level": ("public", "internal"),
    "default_repository_permission": ("read", "write", "admin", "none"),
    "default_workflow_permissions": ("read", "write"),
    "enforced_repositories": ("all", "none", "selected"),
    "hash_algorithm": ("sha1", "sha256"),
    "members_allowed_repository_creation_type": ("all", "private", "none"),
    "limit": ("existing_users", "contributors_only", "collaborators_only"),
    "insecure_ssl": ("0", "1"),
    "issue_creation_policy": ("all", "collaborators_only"),
    "merge_commit_message": ("PR_BODY", "PR_TITLE", "BLANK"),
    "merge_commit_title": ("PR_TITLE", "MERGE_MESSAGE"),
    "notification_setting": ("notifications_enabled", "notifications_disabled"),
    "permission": ("pull", "triage", "push", "maintain", "admin"),
    "privacy": ("secret", "closed"),
    "pull_request_creation_policy": ("all", "collaborators_only"),
    "push_protection_setting": ("enabled", "disabled"),
    "query_suite": ("default", "extended"),
    "registry_type": (
        "maven_repository",
        "nuget_feed",
        "goproxy_server",
        "npm_registry",
        "rubygems_server",
        "cargo_registry",
        "composer_repository",
        "docker_registry",
        "git_source",
        "helm_registry",
        "hex_organization",
        "hex_repository",
        "pub_repository",
        "python_index",
        "terraform_registry",
    ),
    "repository_selection": ("none", "all", "subset"),
    "runner_type": ("standard", "labeled"),
    "squash_merge_commit_message": ("PR_BODY", "COMMIT_MESSAGES", "BLANK"),
    "squash_merge_commit_title": ("PR_TITLE", "COMMIT_OR_PR_TITLE"),
}


_ORGANIZATION_RULESET_RULE_TYPES = (
    "creation",
    "update",
    "deletion",
    "required_linear_history",
    "required_deployments",
    "required_signatures",
    "pull_request",
    "required_status_checks",
    "non_fast_forward",
    "commit_message_pattern",
    "commit_author_email_pattern",
    "committer_email_pattern",
    "branch_name_pattern",
    "tag_name_pattern",
    "file_path_restriction",
    "max_file_path_length",
    "file_extension_restriction",
    "max_file_size",
    "workflows",
    "code_scanning",
    "copilot_code_review",
)


_REPOSITORY_RULESET_RULE_TYPES = (
    *_ORGANIZATION_RULESET_RULE_TYPES[:4],
    "merge_queue",
    *_ORGANIZATION_RULESET_RULE_TYPES[4:],
    "license_compliance_scanning",
)


_ISSUE_COLOR_VALUES = (
    "gray",
    "blue",
    "green",
    "yellow",
    "orange",
    "red",
    "pink",
    "purple",
)


def format_comment(
    text: str,
    *,
    indentation: str = "",
    label: str | None = None,
    break_long_words: bool = True,
) -> str:
    """Format one YAML comment paragraph for an 80-column document."""
    content = f"{label}: {text}" if label is not None else text
    width = max(1, _COMMENT_WIDTH - len(indentation) - len("# "))
    lines = wrap(
        content,
        width=width,
        break_long_words=break_long_words,
        break_on_hyphens=False,
    )
    return "".join(f"{indentation}# {line}\n" for line in lines)


def add_key_comments(
    document: str,
    config: Mapping[str, Any],
    *,
    read_only_fields: Mapping[ConfigPath, str] | None = None,
    caveats: Mapping[ConfigPath, str] | None = None,
) -> str:
    """Add one explanatory comment block before every YAML mapping key."""
    root = yaml.compose(document, Loader=yaml.SafeLoader)
    if root is None:
        return document
    comments_by_key_offset: dict[int, KeyComment] = {}
    _collect_comments(
        root,
        config,
        (),
        comments_by_key_offset,
        read_only_fields or {},
        caveats or {},
    )

    comments_by_line_offset: dict[int, KeyComment] = {}
    for key_offset, comment in comments_by_key_offset.items():
        line_offset = document.rfind("\n", 0, key_offset) + 1
        comments_by_line_offset[line_offset] = comment

    output: list[str] = []
    previous_offset = 0
    for line_offset, comment in sorted(comments_by_line_offset.items()):
        output.append(document[previous_offset:line_offset])
        line_end = document.find("\n", line_offset)
        if line_end == -1:
            line_end = len(document)
        line = document[line_offset:line_end]
        indentation = line[: len(line) - len(line.lstrip())]
        output.append(
            "".join(
                format_comment(note, indentation=indentation) for note in comment.notes
            )
            + format_comment(comment.description, indentation=indentation)
            + format_comment(comment.values, indentation=indentation, label="Values")
            + format_comment(
                comment.docs,
                indentation=indentation,
                break_long_words=False,
            )
        )
        previous_offset = line_offset
    output.append(document[previous_offset:])
    return "".join(output)


def _collect_comments(
    node: Node,
    value: Any,
    path: ConfigPath,
    output: dict[int, KeyComment],
    read_only_fields: Mapping[ConfigPath, str],
    caveats: Mapping[ConfigPath, str],
) -> None:
    if isinstance(node, MappingNode) and isinstance(value, Mapping):
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                continue
            key = key_node.value
            if key not in value:
                continue
            child = value[key]
            child_path = (*path, key)
            output[key_node.start_mark.index] = _comment_for(
                child_path,
                child,
                read_only_fields,
                caveats,
            )
            _collect_comments(
                value_node,
                child,
                child_path,
                output,
                read_only_fields,
                caveats,
            )
    elif isinstance(node, SequenceNode) and isinstance(value, Sequence):
        for index, (item_node, item) in enumerate(zip(node.value, value)):
            _collect_comments(
                item_node,
                item,
                (*path, index),
                output,
                read_only_fields,
                caveats,
            )


def _comment_for(
    path: ConfigPath,
    value: Any,
    read_only_fields: Mapping[ConfigPath, str] | None = None,
    caveats: Mapping[ConfigPath, str] | None = None,
) -> KeyComment:
    notes: list[str] = []
    read_only_reason = _metadata_for_path(path, read_only_fields or {})
    if read_only_reason is not None:
        notes.append(f"Read-only. {_as_sentence(read_only_reason)}")
    caveat = _metadata_for_path(path, caveats or {})
    if caveat is not None:
        notes.append(f"Export caveat. {_as_sentence(caveat)}")
    return KeyComment(
        notes=tuple(notes),
        description=_description(path, value),
        values=_values(path, value),
        docs=_docs(path),
    )


def _metadata_for_path(
    path: ConfigPath, metadata: Mapping[ConfigPath, str]
) -> str | None:
    matches = [
        (candidate, reason)
        for candidate, reason in metadata.items()
        if path == candidate or path[: len(candidate)] == candidate
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def _as_sentence(value: str) -> str:
    sentence = value[:1].upper() + value[1:]
    return sentence if sentence.endswith((".", "?", "!")) else f"{sentence}."


def _description(path: ConfigPath, value: Any) -> str:
    key = str(path[-1])
    semantic = _semantic_parts(path)
    if len(path) == 1:
        root_descriptions = {
            "version": "The github-config file format version.",
            "organization": "Organization-wide settings and resources.",
            "repository_policies": "Reusable settings selected across repositories.",
            "repositories": "Explicitly managed repositories by repository name.",
            "_observed": "Read-only details about the GitHub state used for this export.",
        }
        if key in root_descriptions:
            return root_descriptions[key]
    if _is_app_installation_path(path):
        if len(path) == 2:
            return "Installed GitHub Apps and their repository access."
        if _is_collection_mode(path):
            return (
                "How omitted GitHub App installations are compared with the "
                "organization."
            )
        if _is_collection_items(path):
            return "The installed GitHub Apps, keyed by app slug."
        if _is_collection_item(path):
            return f"The GitHub App installation named {_code(key)}."
        if key == "selected_repositories":
            return "The repositories selected for this GitHub App installation."
        return f"The installation's observed {_humanize(key)}."
    if _is_personal_access_token_path(path):
        if len(path) == 2:
            return (
                "Observed fine-grained personal access token grants and their "
                "revocation policy."
            )
        if _is_collection_mode(path):
            return "Whether omitted personal access token grants remain active or are revoked."
        if _is_collection_items(path):
            return (
                "The observed personal access token grants to retain, keyed by their "
                "exported identities."
            )
        if _is_collection_item(path):
            return (
                f"The observed personal access token grant named {_code(key)}, retained "
                "when present and revoked when omitted in exact mode."
            )
        return (
            f"The grant's observed {_humanize(key)}, a read-only value not changed "
            "by apply."
        )
    if _is_credential_authorization_path(path):
        if len(path) == 2:
            return (
                "Observed SAML SSO credential authorizations and their revocation "
                "policy."
            )
        if _is_collection_mode(path):
            return (
                "Whether omitted credential authorizations remain active or are "
                "revoked."
            )
        if _is_collection_items(path):
            return (
                "The observed credential authorizations to retain, keyed by their "
                "exported identities."
            )
        if _is_collection_item(path):
            return (
                f"The observed credential authorization named {_code(key)}, retained "
                "when present and revoked when omitted in exact mode."
            )
        return (
            f"The authorization's observed {_humanize(key)}, a read-only value not "
            "changed by apply."
        )
    if _is_repository_facts_path(path):
        if key == "_facts":
            return "Read-only repository facts used by repository policy selectors."
        return (
            f"The repository's observed {_humanize(key)} value for policy selectors, "
            "a read-only value not applied."
        )
    if path == ("repositories", "mode"):
        return (
            "The merge behavior for explicitly listed repositories, with repositories "
            "omitted from items always left unchanged."
        )
    if _is_collection_mode(path):
        if str(path[-2]) == "custom_patterns":
            return (
                "Comparison of omitted custom patterns, with exact mode reporting "
                "omissions and apply blocking their deletion."
            )
        if str(path[-2]) == "workflow_states":
            return (
                "Comparison of omitted workflows, with exact mode reporting omissions "
                "and github-config never deleting workflow files."
            )
        if str(path[-2]) == "organization_roles":
            return (
                "Reconciliation of user and team assignments for existing organization "
                "roles, with GitHub roles themselves never deleted."
            )
        return "Whether this collection keeps or removes GitHub entries omitted from items."
    if _is_collection_items(path):
        collection = _collection_name(path[-2] if len(path) > 1 else "resource")
        return f"The managed {collection} entries, keyed by their GitHub identities."
    if _is_collection_item(path):
        collection = _collection_name(path[-3] if len(path) > 2 else "resource")
        if str(path[-3]) == "secrets":
            return (
                f"The secret named {_code(key)}, retained or updated without exporting "
                "its write-only value."
            )
        if str(path[-3]) == "workflow_states":
            return (
                f"The active or manually disabled state of the workflow named "
                f"{_code(key)}, with other exported states read-only."
            )
        return f"The {collection} named {_code(key)} and the values attached to it."
    if _is_copilot_content_exclusion_key(path):
        return (
            f"The Copilot content-exclusion rules for the repository selector "
            f"named {_code(key)}."
        )
    if len(semantic) >= 2 and semantic[-2] == "custom_properties":
        return f"The value of the custom property named {_code(key)}."
    if _is_organization_member_public(path):
        return (
            "The public visibility of this membership when it belongs to the "
            "authenticated user, with other members' values read-only."
        )
    if key == "data_type" and "issue_fields" in semantic:
        return (
            "The issue field's data type at creation, with replacement required to "
            "change an existing field's data type."
        )
    if key == "auth_type" and "private_registries" in semantic:
        return (
            "The registry authentication type at creation, with replacement required "
            "to change it later."
        )
    if key == "name" and "custom_patterns" in semantic:
        return (
            "The custom pattern name at creation, which GitHub does not allow to "
            "change later."
        )
    if _is_response_metadata(path):
        return (
            f"The {_humanize(key)} returned by GitHub, response metadata not applied "
            "by github-config."
        )
    if key == "settings":
        return f"Editable settings for this {_resource(path)}."
    if key == "match":
        return "The existing repositories to which this repository policy applies."
    if key == "set":
        return "The repository settings applied when this policy matches."
    if key == "_expires_at":
        return (
            "The expiration time reported by GitHub for this interaction restriction."
        )
    if key == "_pattern_config_version":
        return "The GitHub pattern configuration version used for concurrency checks."
    if key in _SECTION_NAMES and isinstance(value, Mapping):
        section_name = _SECTION_NAMES[key]
        return (
            f"{section_name[:1].upper()}{section_name[1:]} for this {_resource(path)}."
        )
    if key in _SETTING_DESCRIPTIONS:
        return _SETTING_DESCRIPTIONS[key]
    if key == "unavailable":
        return (
            "GitHub API sections that could not be read and therefore remain unmanaged."
        )
    if key == "api_version":
        return "The GitHub REST API version used for this export."
    if key == "organization" and path[-2:] == ("_observed", "organization"):
        return "The canonical GitHub organization login used for this export."
    label = _humanize(key)
    resource = _resource(path)
    if isinstance(value, Mapping):
        return f"The {label} settings for this {resource}."
    if isinstance(value, list):
        return f"The {label} list for this {resource}."
    if isinstance(value, bool):
        return f"The {label} state for this {resource}."
    return f"The {label} for this {resource}."


def _values(path: ConfigPath, value: Any) -> str:
    key = str(path[-1])
    semantic = _semantic_parts(path)
    if _is_repository_facts_path(path):
        return f"{_value_shape(value)} This read-only observation is not applied."
    if _is_app_installation_path(path):
        if len(path) == 2:
            return "A mapping containing `mode` and `items`."
        if _is_collection_mode(path):
            return (
                "`merge` leaves omitted installations unchanged. `exact` reports "
                "omitted installations, but apply blocks their removal."
            )
        if _is_collection_items(path):
            return "A mapping keyed by each installed GitHub App's slug."
        if _is_collection_item(path):
            return (
                "A mapping of read-only installation details and an optional "
                "writable `selected_repositories` list."
            )
        if key == "repository_selection":
            return (
                "`all` or `selected`. This read-only value must already be "
                "`selected` before selected_repositories can be changed."
            )
        if key == "selected_repositories":
            return (
                "A YAML list of repository names. Updates require a classic "
                "personal access token with the repo scope."
            )
        return _value_shape(value)
    if _is_personal_access_token_path(path):
        if len(path) == 2:
            return "A mapping containing `mode` and `items`."
        if _is_collection_mode(path):
            return (
                "`merge` leaves omitted grants active; `exact` revokes omitted grants."
            )
        if _is_collection_items(path):
            return (
                "A mapping keyed by each exported personal access token grant identity."
            )
        if _is_collection_item(path):
            return (
                "A mapping of read-only grant details. Keep the item to retain the "
                "grant, or omit it in exact mode to revoke the grant."
            )
        if key == "repository_selection":
            return (
                "`none`, `all`, or `subset`. The `repositories` list is relevant only "
                "for `subset`. This read-only observation is not changed by apply."
            )
        if key == "repositories":
            return (
                "A YAML list of repository names. It is relevant only when "
                "`repository_selection` is `subset`. This read-only observation is "
                "not changed by apply."
            )
        if key == "token_expires_at":
            return (
                "An ISO 8601 timestamp, or `null`. This read-only observation is not "
                "changed by apply."
            )
        return (
            f"{_value_shape(value)} This read-only observation is not changed by apply."
        )
    if _is_credential_authorization_path(path):
        if len(path) == 2:
            return "A mapping containing `mode` and `items`."
        if _is_collection_mode(path):
            return (
                "`merge` leaves omitted authorizations active. `exact` revokes "
                "omitted authorizations."
            )
        if _is_collection_items(path):
            return (
                "A mapping keyed by each exported SAML SSO credential authorization "
                "identity."
            )
        if _is_collection_item(path):
            return (
                "A mapping of read-only authorization details. Keep the item to "
                "retain access, or omit it in exact mode to revoke access. GitHub "
                "cannot recreate a revoked authorization; the owner must authorize "
                "the credential again."
            )
        return (
            f"{_value_shape(value)} This read-only observation is not changed by apply."
        )
    if path == ("repositories", "mode"):
        return (
            "`merge` and `exact` are accepted. Both leave unlisted repositories "
            "unchanged because github-config never deletes repositories."
        )
    if _is_collection_mode(path):
        if str(path[-2]) == "custom_patterns":
            return (
                "`merge` leaves omitted patterns unchanged. `exact` reports omitted "
                "patterns, but apply blocks deletion because it would change alerts."
            )
        if str(path[-2]) == "workflow_states":
            return (
                "`merge` leaves omitted workflows unchanged. `exact` reports omitted "
                "workflows, but apply blocks removal because workflows are repository content."
            )
        if str(path[-2]) == "organization_roles":
            return (
                "`merge` never revokes omitted assignments. In `exact`, every present "
                "`users` or `teams` list is authoritative, and omitting a role revokes "
                "all of its assignments. GitHub organization roles remain intact."
            )
        return "`merge` keeps unlisted entries; `exact` removes unlisted entries when GitHub supports deletion."
    if _is_collection_item(path):
        collection = str(path[-3])
        if collection == "secrets":
            if semantic[0] == "organization":
                return (
                    "An exported mapping records the secret's metadata without "
                    "replacing its write-only value. Creating a secret requires "
                    "`visibility` and `value_from_env`. Changing `visibility` also "
                    "requires `value_from_env`; `selected_repositories` is valid only "
                    "with `visibility: selected`."
                )
            return (
                "`{}` records an existing secret without replacing its write-only "
                "value. A mapping with `value_from_env: ENV_NAME` creates or replaces "
                "the secret."
            )
        if collection == "collaborators":
            return "`pull`, `triage`, `push`, `maintain`, `admin`, or a custom repository role name."
        if collection == "collaborator_invitations":
            return _enum_values(("read", "write", "triage", "maintain", "admin"))
        if collection == "members" and "teams" in semantic:
            return _enum_values(("member", "maintainer"))
        if collection == "repositories" and "teams" in semantic:
            return "`pull`, `triage`, `push`, `maintain`, `admin`, or a custom repository role name."
        if collection == "workflow_states":
            return (
                "Writable values are `active` and `disabled_manually`. GitHub may "
                "export the read-only states `disabled_inactivity`, `disabled_fork`, "
                "and `deleted`."
            )
        if collection == "private_registries":
            return (
                "A mapping whose key is `REGISTRY_TYPE.upper() + '_SECRET'`. "
                "Creation requires `registry_type`, `url`, and `visibility`, plus "
                "the credentials required by `auth_type`."
            )
        return _value_shape(value)
    if _is_copilot_content_exclusion_key(path):
        return (
            "A YAML list containing excluded path strings and optional `ifAnyMatch` "
            "or `ifNoneMatch` condition mappings; use `[]` for none."
        )
    if len(semantic) >= 2 and semantic[-2] == "custom_properties":
        return (
            "A string, list of strings, or `null`, as allowed by the property's schema."
        )
    if _is_organization_member_public(path):
        return (
            "`true` or `false`. Only the authenticated user's own membership is "
            "writable; other members' exported values are read-only."
        )
    if key == "data_type" and "issue_fields" in semantic:
        return (
            f"{_enum_values(('text', 'date', 'single_select', 'multi_select', 'number'))[:-1]}. "
            "This value is create-only; changing it requires replacing the issue field."
        )
    if key == "auth_type" and "private_registries" in semantic:
        return (
            f"{_enum_values(_ENUM_VALUES['auth_type'])[:-1]}. This value is "
            "create-only; changing it requires replacing the registry."
        )
    if key == "name" and "custom_patterns" in semantic:
        return (
            "A string accepted by the linked GitHub endpoint. The name is create-only; "
            "replacing the pattern is required to change it."
        )
    if _is_response_metadata(path):
        return f"{_value_shape(value)} This response metadata is not applied."
    if len(path) == 1 and key == "version":
        return "The integer `1`."
    if key == "runner_type" and "code_security" in semantic:
        return (
            "Writable values are `standard`, `labeled`, and `not_set`. GitHub may "
            "export the additional read value `null`, which is not applied."
        )
    if key == "api_version":
        return "A supported GitHub REST API version in `YYYY-MM-DD` form. This observation is not applied."
    if key == "unavailable":
        return "A YAML list of API paths with status details. This observation is not applied."
    if key == "organization" and path[-2:] == ("_observed", "organization"):
        return (
            "The canonical GitHub organization login. This observation is not applied."
        )
    documented_values = _documented_values(path, value)
    if documented_values is not None:
        return documented_values
    if key == "required_approving_review_count" and "branch_protections" in semantic:
        return "An integer from `0` through `6`."
    if key == "required_approving_review_count" and "rulesets" in semantic:
        return "An integer from `0` through `10`."
    if key == "wait_timer" and "environments" in semantic:
        return "An integer from `0` through `43200`, measured in minutes."
    if key == "status" and "security_and_analysis" in semantic:
        return _enum_values(("enabled", "disabled"))
    if key == "description" and "code_security" in semantic:
        return (
            "A writable string of up to 255 characters. GitHub may export the "
            "additional read value `null`, which is not applied."
        )
    if key in {"code_scanning_default_setup_options", "code_scanning_options"} and (
        "code_security" in semantic
    ):
        return "A mapping containing the documented nested keys, or `null`."
    if key == "runner_label" and "code_security" in semantic:
        return "A runner label string, or `null`."
    if key == "allow_advanced" and "code_security" in semantic:
        return "`true`, `false`, or `null`."
    if key == "expiry" and "interaction_limit" in semantic:
        return _enum_values(
            ("one_day", "three_days", "one_week", "one_month", "six_months")
        )
    if key == "budget_scope" and "budgets" in semantic:
        return _enum_values(
            (
                "enterprise",
                "organization",
                "repository",
                "cost_center",
                "multi_user_customer",
                "user",
            )
        )
    if key == "budget_type" and "budgets" in semantic:
        return _enum_values(("BundlePricing", "ProductPricing", "SkuPricing"))
    if key == "compute_service" and "network_configurations" in semantic:
        return (
            "Writable values are `none` and `actions`. GitHub may export the "
            "additional read value `codespaces`, which is not applied."
        )
    if key == "network_settings_ids" and "network_configurations" in semantic:
        return "A YAML list containing exactly one network settings ID."
    if key == "failover_network_settings_ids" and "network_configurations" in semantic:
        return "A YAML list containing zero or one network settings ID."
    if key == "source" and "hosted_runners" in semantic and "image" in semantic:
        return _enum_values(("github", "partner", "custom"))
    if key == "version" and "hosted_runners" in semantic and "image" in semantic:
        return "A custom image version string, or `null`."
    if key == "name" and "hosted_runners" in semantic:
        return (
            "A string from 1 through 64 characters using letters, numbers, `.`, "
            "`-`, or `_`."
        )
    if key == "allowed_values" and "custom_properties" in semantic:
        return "A YAML list of strings, or `null` when every value is allowed."
    if key == "default_value" and "custom_properties" in semantic:
        return "A string, a YAML list of strings, or `null`."
    if key == "description" and "custom_properties" in semantic:
        return "A string, or `null`."
    if key == "options" and "issue_fields" in semantic:
        return "A YAML list of option mappings, or `null` for a non-select field."
    if key == "push_protection_setting" and "provider_pattern_settings" in semantic:
        return _enum_values(("not-set", "disabled", "enabled"))
    if key == "push_protection_setting" and "custom_pattern_settings" in semantic:
        return _enum_values(("disabled", "enabled"))
    if key == "permission" and "teams" in semantic and "settings" in semantic:
        return (
            "`pull` or `push` when creating a team; `admin` can be set after creation. "
            "Other strings that GitHub exports cannot be applied to this setting."
        )
    if key == "privacy" and "teams" in semantic and "settings" in semantic:
        return "`secret` or `closed`. A team with a parent or child must use `closed`."
    if key == "advanced_security" and "code_security" in semantic:
        return _enum_values(
            ("enabled", "disabled", "code_security", "secret_protection")
        )
    if key == "max_open_pull_requests" and "pull_request_creation_cap" in semantic:
        return "An integer from `1` through `1000`."
    if key == "pull_request_creation_cap_bypass_users":
        return "A YAML list of up to 100 GitHub user logins; use `[]` for none."
    if key in {
        "bypass_force_push_actors",
        "bypass_pull_request_actors",
        "push_actors",
        "review_dismissal_actors",
    }:
        return (
            "A YAML list of `user:LOGIN`, `team:SLUG`, and `app:SLUG` references; "
            "use `[]` for none."
        )
    if key == "pinned_position":
        return "A non-negative integer when `pinned` is true, or `null` otherwise."
    if key == "color" and "issue_fields" in semantic and "options" in semantic:
        return (
            f"Writable values are {_enum_values(_ISSUE_COLOR_VALUES)[:-1]}. GitHub "
            "may export the additional read value `null`."
        )
    if key == "color" and "issue_types" in semantic:
        return f"{_enum_values(_ISSUE_COLOR_VALUES)[:-1]}, or `null`."
    if key in _ENUM_VALUES:
        return _enum_values(_ENUM_VALUES[key])
    if key == "enabled_repositories":
        return _enum_values(("all", "none", "selected"))
    if key == "visibility":
        if any(
            part in semantic for part in ("secrets", "variables", "private_registries")
        ):
            return _enum_values(("all", "private", "selected"))
        if "runner_groups" in semantic:
            return _enum_values(("all", "selected", "private"))
        if "issue_fields" in semantic:
            return _enum_values(("organization_members_only", "all"))
        return _enum_values(("public", "private", "internal"))
    if key == "role":
        if "invitations" in semantic:
            return (
                "Writable values are `direct_member`, `admin`, `billing_manager`, and "
                "`reinstate`. GitHub may export the read-only `hiring_manager` role."
            )
        return _enum_values(("member", "admin"))
    if key == "type":
        if "reviewers" in semantic:
            return _enum_values(("user", "team"))
        if "branch_policies" in semantic:
            return _enum_values(("branch", "tag"))
    if key == "state":
        if "workflow_states" in semantic:
            return _enum_values(
                (
                    "active",
                    "disabled_manually",
                    "disabled_inactivity",
                    "disabled_fork",
                    "deleted",
                )
            )
        if "code_scanning" in semantic or "code_quality" in semantic:
            return _enum_values(("configured", "not-configured"))
        if "rulesets" in semantic:
            return _enum_values(("active", "disabled", "evaluate"))
    if key == "enforcement":
        if "rulesets" in semantic:
            return _enum_values(("active", "disabled", "evaluate"))
        return _enum_values(("enforced", "unenforced"))
    if key == "mode" and "code_security" in semantic:
        return _enum_values(("ALWAYS", "EXEMPT"))
    if key == "reviewer_type" and "code_security" in semantic:
        return _enum_values(("TEAM", "ROLE"))
    if key == "dependabot_delegated_alert_dismissal" and "code_security" in semantic:
        return (
            "Writable values are `enabled`, `disabled`, and `not_set`. GitHub may "
            "export the additional read value `null`, which is not applied."
        )
    if "code_security" in semantic and key in {
        "dependency_graph",
        "dependency_graph_autosubmit_action",
        "dependabot_alerts",
        "dependabot_security_updates",
        "dependabot_delegated_alert_dismissal",
        "code_scanning_default_setup",
        "code_scanning_delegated_alert_dismissal",
        "secret_scanning",
        "secret_scanning_push_protection",
        "secret_scanning_delegated_bypass",
        "secret_scanning_validity_checks",
        "secret_scanning_non_provider_patterns",
        "secret_scanning_generic_secrets",
        "secret_scanning_delegated_alert_dismissal",
        "secret_scanning_extended_metadata",
        "private_vulnerability_reporting",
    }:
        return _enum_values(("enabled", "disabled", "not_set"))
    if key == "value_type":
        return _enum_values(
            ("string", "single_select", "multi_select", "true_false", "url")
        )
    if key == "values_editable_by":
        return "`org_actors`, `org_and_repo_actors`, or `null`."
    if key == "target" and "rulesets" in semantic:
        return _enum_values(
            ("branch", "tag", "push", "repository")
            if semantic[0] == "organization"
            else ("branch", "tag", "push")
        )
    if key == "actor_type" and "rulesets" in semantic:
        return _enum_values(
            (
                "Integration",
                "OrganizationAdmin",
                "RepositoryRole",
                "Team",
                "DeployKey",
                "User",
            )
        )
    if key == "bypass_mode" and "rulesets" in semantic:
        return _enum_values(("always", "pull_request", "exempt"))
    if key == "source" and "repository_property" in semantic:
        return _enum_values(("custom", "system"))
    if key == "type" and "rulesets" in semantic:
        if "allowed_actors" in semantic:
            return _enum_values(
                ("User", "Team", "IntegrationInstallation", "RepositoryRole")
            )
        if "reviewer" in semantic and "required_reviewers" in semantic:
            return "`Team`."
        if "rules" in semantic:
            rule_types = (
                _ORGANIZATION_RULESET_RULE_TYPES
                if semantic[0] == "organization"
                else _REPOSITORY_RULESET_RULE_TYPES
            )
            return _enum_values(rule_types)
    if key == "operator" and "rulesets" in semantic:
        return _enum_values(("starts_with", "ends_with", "contains", "regex"))
    if key == "allowed_merge_methods" and "rulesets" in semantic:
        return "A YAML list containing one or more of `merge`, `squash`, and `rebase`."
    if key == "merge_method" and "rulesets" in semantic:
        return _enum_values(("MERGE", "SQUASH", "REBASE"))
    if key == "alerts_threshold" and "rulesets" in semantic:
        return _enum_values(("none", "errors", "errors_and_warnings", "all"))
    if key == "security_alerts_threshold" and "rulesets" in semantic:
        return _enum_values(
            ("none", "critical", "high_or_higher", "medium_or_higher", "all")
        )
    if key == "threat_model":
        return _enum_values(("remote", "remote_and_local"))
    if key == "source" and isinstance(value, Mapping):
        return "A mapping with `branch` and `path`; `path` is `/` or `/docs`."
    if key == "parent":
        return "A parent team slug, or `null` for no parent."
    if key in {"runner_group", "network_configuration"}:
        return "A configured resource name, or `null` when none is assigned."
    if key == "_expires_at":
        return "An ISO 8601 timestamp from GitHub, or `null`. This observation is not applied."
    if key == "value_from_env" or key == "secret_from_env":
        return "The name of an environment variable. The environment variable's value is never exported."
    return _value_shape(value)


def _documented_values(path: ConfigPath, value: Any) -> str | None:
    key = str(path[-1])
    semantic = _semantic_parts(path)
    is_organization = semantic[0] == "organization"
    is_repository = semantic[0] == "repositories"

    if semantic[:2] == ("organization", "settings"):
        if key == "default_repository_permission":
            return (
                "Writable values are `read`, `write`, `admin`, and `none`. GitHub may "
                "export the additional read value `null`, which is not applied."
            )
        if key == "members_can_create_repositories":
            return (
                "Writable values are `true` and `false`. GitHub may export the "
                "additional read value `null`, which is not applied. "
                "`members_allowed_repository_creation_type` takes precedence."
            )
        if key == "members_can_fork_private_repositories":
            return (
                "Writable values are `true` and `false`. GitHub may export the "
                "additional read value `null`, which is not applied."
            )
        if key in {"billing_email", "description", "twitter_username"}:
            return (
                "A writable string. GitHub may export the additional read value "
                "`null`, which is not applied."
            )
        if key == "secret_scanning_push_protection_custom_link":
            return (
                "A writable URL string. GitHub may export the additional read value "
                "`null`, which is not applied. GitHub uses the URL only when its "
                "separate custom-link switch is enabled."
            )

    if key == "default_level" and "dependabot" in semantic:
        return (
            "Writable values are `public` and `internal`. GitHub may export the "
            "additional read value `null`, which is not applied."
        )

    if is_repository and "settings" in semantic:
        if key in {"description", "homepage"}:
            return (
                "A writable string. GitHub may export the additional read value "
                "`null`, which is not applied."
            )
        if key == "allow_forking":
            return (
                "`true` or `false`. This setting can be managed only for an "
                "organization-owned private or internal repository whose "
                "organization policy allows repository forking."
            )
        if key == "has_projects":
            return (
                "`true` or `false`. `true` is valid only when repository projects "
                "are enabled for the organization."
            )
        if key == "status" and _contains_semantic(
            semantic, ("security_and_analysis", "advanced_security")
        ):
            return (
                "`enabled` or `disabled`. Advanced Security is unavailable to "
                "repositories using the standalone Code Security or Secret Protection product."
            )
        if (
            key == "secret_scanning_delegated_bypass_options"
            and "security_and_analysis" in semantic
        ):
            return (
                "A reviewer-options mapping, or `null`. GitHub uses it only when "
                "`secret_scanning_delegated_bypass.status` is `enabled`."
            )

    if key == "description":
        if {"teams", "labels"}.intersection(semantic):
            return (
                "A writable string. GitHub may export the additional read value "
                "`null`, which is not applied."
            )
        nullable_resources = {
            "issue_types",
            "issue_fields",
            "custom_patterns",
        }
        if nullable_resources.intersection(semantic):
            return "A string, or `null`."
    if key in {"start_delimiter", "end_delimiter"} and "custom_patterns" in semantic:
        return (
            "A writable regular-expression string. GitHub may export the additional "
            "read value `null`."
        )
    if key in {"must_match", "must_not_match"} and "custom_patterns" in semantic:
        return (
            "A writable YAML list of regular-expression strings. GitHub may export "
            "the additional read value `null`."
        )
    if key in {"_pattern_config_version", "custom_pattern_version"} and (
        "pattern_configurations" in semantic
    ):
        return "A GitHub version string, or `null`."

    if _contains_semantic(semantic, ("actions", "allowed_actions")):
        if key == "patterns_allowed":
            return (
                "A YAML list of action or reusable-workflow reference patterns. It "
                "takes effect only when the sibling permissions use `allowed_actions: "
                "selected`, and applies only to public repositories."
            )
        if key in {"github_owned_allowed", "verified_allowed"}:
            return (
                "`true` or `false`. It takes effect only when the sibling permissions "
                "use `allowed_actions: selected`."
            )

    organization_repository_sets = {
        ("organization", "actions", "selected_repositories"): (
            "`organization.actions.permissions.enabled_repositories`",
            "selected",
        ),
        ("organization", "actions", "self_hosted_runner_repositories"): (
            "`organization.actions.self_hosted_runner_permissions.enabled_repositories`",
            "selected",
        ),
        ("organization", "copilot", "coding_agent_repositories"): (
            "`organization.copilot.coding_agent_permissions.enabled_repositories`",
            "selected",
        ),
        ("organization", "immutable_release_repositories"): (
            "`organization.immutable_releases.enforced_repositories`",
            "selected",
        ),
    }
    repository_set_condition = organization_repository_sets.get(semantic)
    if repository_set_condition is not None:
        setting, required_value = repository_set_condition
        return (
            f"A YAML list of repository names; use `[]` for none. It applies only "
            f"when {setting} is `{required_value}`."
        )

    if key == "include_claim_keys" and _contains_semantic(
        semantic, ("actions", "oidc_subject")
    ):
        condition = (
            " GitHub ignores the list while `use_default` is `true`."
            if is_repository
            else ""
        )
        return (
            "A YAML list of unique claim names containing only letters, numbers, and "
            f"underscores; use `[]` for none.{condition}"
        )

    if "runner_groups" in semantic:
        if key == "selected_workflows":
            return (
                "A YAML list of workflow references that each include a branch, tag, "
                "or full commit SHA. GitHub ignores it unless "
                "`restricted_to_workflows` is `true`."
            )
        if key == "repositories":
            return (
                "A YAML list of repository names; use `[]` for none. GitHub uses it "
                "when the runner group's `visibility` is `selected`."
            )

    if "hosted_runners" in semantic and "image" in semantic:
        if key == "id":
            return "An image ID available from the collection selected by `source`."
        if key == "version":
            return (
                "A custom image version string, or `null`. It is used only when "
                "`source` is `custom`."
            )

    if key == "days" and "artifact_and_log_retention" in semantic:
        if is_organization:
            return (
                "An integer from `1` through `400`. Public-repository artifacts are "
                "still limited to 90 days."
            )
        return (
            "An integer from `1` through `90` for a public repository, or `1` through "
            "`400` for a private or internal repository. It cannot exceed the owner limit."
        )
    if key in {"max_cache_retention_days", "max_cache_size_gb"}:
        unit = "days" if key == "max_cache_retention_days" else "gigabytes"
        if is_organization:
            return (
                f"An integer measured in {unit}. An enterprise policy may constrain "
                "the accepted value."
            )
        organization_key = (
            "organization.actions.cache_retention.max_cache_retention_days"
            if key == "max_cache_retention_days"
            else "organization.actions.cache_storage.max_cache_size_gb"
        )
        return f"An integer measured in {unit}. It cannot exceed `{organization_key}`."

    if (
        key == "selected_repositories"
        and is_organization
        and any(
            collection in semantic
            for collection in ("variables", "secrets", "private_registries")
        )
    ):
        return (
            "A YAML list of repository names; use `[]` for none. It is valid only "
            "when `visibility` is `selected`."
        )

    if "budgets" in semantic:
        if key == "budget_amount":
            return "A whole-dollar integer of at least `0`."
        if key == "prevent_further_usage":
            return (
                "`true` or `false`. It must be `true` for `user` and "
                "`multi_user_customer` scopes."
            )
        if key == "budget_scope":
            return (
                "Writable values are `organization`, `repository`, "
                "`multi_user_customer`, and `user`. GitHub may also export inherited "
                "`enterprise`, `cost_center`, and `multi_user_cost_center` scopes. "
                "Inherited scopes are not applied. User scopes require `ai_credits` "
                "or `premium_requests`."
            )
        if key == "budget_entity_name":
            return (
                "An organization or repository name. It may be an empty string for a "
                "`user` scope."
            )
        if key == "budget_type":
            return (
                "`BundlePricing`, `ProductPricing`, or `SkuPricing`. The type decides "
                "whether `budget_product_sku` names the AI credits bundle, a product, or one SKU."
            )
        if key == "budget_product_sku":
            return (
                "`ai_credits` for `BundlePricing`, a product such as `actions` for "
                "`ProductPricing`, or a specific SKU such as `actions_linux` for `SkuPricing`."
            )
        if key == "user":
            return "A GitHub login. It is required when `budget_scope` is `user`."
        if key == "budget_alerting":
            return "A mapping containing `will_alert` and `alert_recipients`."
        if key == "alert_recipients":
            return "A YAML list of GitHub user logins; use `[]` for none."

    if "private_registries" in semantic:
        if key == "registry_type":
            return (
                f"{_enum_values(_ENUM_VALUES['registry_type'])[:-1]}. The collection "
                "key must equal the uppercased value followed by `_SECRET`."
            )
        if key == "username":
            return "A registry username string, or `null`."
        if key == "visibility":
            return (
                "`all`, `private`, or `selected`. `selected_repositories` is valid "
                "only with `selected`."
            )
        if key == "value_from_env":
            return (
                "The name of an environment variable. It is required for `token` and "
                "`username_password`, and must be omitted for OIDC authentication."
            )
        registry_requirements = {
            "tenant_id": "Required for `oidc_azure`.",
            "client_id": "Required for `oidc_azure`.",
            "aws_region": "Required for `oidc_aws`.",
            "account_id": "Required for `oidc_aws`.",
            "role_name": "Required for `oidc_aws`.",
            "domain": "Required for `oidc_aws`.",
            "domain_owner": "Required for `oidc_aws`.",
            "jfrog_oidc_provider_name": "Required for `oidc_jfrog`.",
            "namespace": "Required for `oidc_cloudsmith`.",
            "service_slug": "Required for `oidc_cloudsmith`.",
            "workload_identity_provider": "Required for `oidc_gcp`.",
        }
        requirement = registry_requirements.get(key)
        if requirement is not None:
            return f"A string. {requirement}"
        if key == "audience":
            return (
                "A string. It is optional for `oidc_jfrog` and required for "
                "`oidc_cloudsmith`."
            )
        if key == "identity_mapping_name":
            return "An optional identity-mapping string for `oidc_jfrog`."
        if key == "api_host":
            return (
                "An optional API host string for `oidc_cloudsmith`; the default is "
                "`api.cloudsmith.io`."
            )
        if key == "service_account":
            return "An optional service-account string for `oidc_gcp`."

    if "rulesets" in semantic:
        if key == "conditions":
            if is_repository:
                return (
                    "A mapping containing `ref_name` include and exclude patterns. "
                    "Repository rulesets do not accept repository selectors. GitHub "
                    "may export `null`, which is not applied."
                )
            return (
                "A mapping with one repository selector. Branch and tag targets also "
                "require `ref_name`; push targets do not. GitHub may export `null`, "
                "which is not applied."
            )
        if key == "actor_id" and "bypass_actors" in semantic:
            return (
                "An actor ID for `Integration`, `RepositoryRole`, `Team`, or `User`; "
                "ignored for `OrganizationAdmin`; and `null` for `DeployKey`."
            )
        if key == "bypass_mode":
            return (
                "`always`, `pull_request`, or `exempt`. `pull_request` is valid only "
                "for branch rulesets and is invalid for `DeployKey`."
            )
        if key == "strict_required_status_checks_policy":
            return (
                "`true` or `false`. It has an effect only when at least one required "
                "status check is configured."
            )

    if (
        "branch_protection_rules" in semantic
        and key == "app"
        and "required_status_checks" in semantic
    ):
        return (
            "A GitHub App slug, `any` to accept the check from any App, or "
            "`recent` to select the App that most recently supplied the check. "
            "GitHub may export the ambiguous read value `null`; resolve it before "
            "changing the status-check list."
        )

    if "branch_protections" in semantic:
        if key == "app_id" and "required_status_checks" in semantic:
            return (
                "An integer app ID or `-1` to allow any app. GitHub may export "
                "`null`, which is not applied. Omit the key to select the app that "
                "most recently supplied the check."
            )
        if key == "required_linear_history":
            return (
                "`true` or `false`. It can be `true` only when squash merging or "
                "rebase merging is enabled for the repository."
            )

    if "environments" in semantic:
        if key == "deployment_branch_policy":
            return (
                "`null` to allow all branches, or a mapping with exactly one of "
                "`protected_branches` and `custom_branch_policies` set to `true`."
            )
        if key in {"protected_branches", "custom_branch_policies"}:
            return (
                "`true` or `false`. Exactly one must be `true` when a deployment "
                "branch policy mapping is present."
            )
        if key == "branch_policies":
            return (
                "A collection of branch or tag patterns. It is used only when "
                "`custom_branch_policies` is `true`."
            )
        if key == "reviewers":
            return (
                "A YAML list of up to six user or team mappings; use `[]` or `null` "
                "for none. One listed reviewer must approve."
            )
        if key == "prevent_self_review":
            return (
                "`true` or `false`. It has an effect only when at least one reviewer "
                "is configured."
            )

    if "pages" in semantic:
        if key == "enabled":
            return (
                "`true` or `false`. Enabling a site that does not exist also requires "
                "`source` or `build_type`."
            )
        if key == "source":
            return (
                "A mapping containing both `branch` and `path`. `path` is `/` or "
                "`/docs`."
            )
        if key == "path" and "source" in semantic:
            return "`/` or `/docs`; creation defaults to `/`."
        if key == "cname":
            return "A custom-domain string, or `null` to remove the custom domain."

    if _contains_semantic(semantic, ("code_scanning", "default_setup")) or (
        _contains_semantic(semantic, ("code_quality", "setup"))
    ):
        is_code_quality = "code_quality" in semantic
        if key == "runner_type":
            return (
                "Writable values are `standard` and `labeled`. GitHub may export the "
                "additional read value `null`, which is not applied."
            )
        if key == "runner_label":
            return (
                "A runner-label string, or `null`. It is used only when `runner_type` "
                "is `labeled`."
            )
        if key == "languages":
            if is_code_quality:
                return (
                    "A YAML list containing `csharp`, `go`, `java-kotlin`, "
                    "`javascript-typescript`, `python`, or `ruby`. GitHub may export "
                    "the additional read value `rust`, which is not applied."
                )
            return (
                "A YAML list containing `actions`, `c-cpp`, `csharp`, `go`, "
                "`java-kotlin`, `javascript-typescript`, `python`, `ruby`, or `swift`. "
                "GitHub may export the legacy read values `javascript` and "
                "`typescript`, which are not applied."
            )

    if "code_security" in semantic and "reviewers" in semantic:
        if key == "reviewer_type":
            return "`TEAM` or `ROLE`."
        if key == "reviewer_id":
            return "The numeric ID of the team or organization role named by `reviewer_type`."
    if (
        "security_and_analysis" in semantic
        and "reviewers" in semantic
        and key in {"reviewer_type", "reviewer_id"}
    ):
        return (
            "`TEAM` or `ROLE`."
            if key == "reviewer_type"
            else "The numeric ID of the team or organization role named by `reviewer_type`."
        )
    if key == "dependabot_delegated_alert_dismissal" and "code_security" in semantic:
        return (
            "Writable values are `enabled`, `disabled`, and `not_set`; Dependabot "
            "alerts must be enabled. GitHub may export the additional read value "
            "`null`, which is not applied."
        )

    if "issue_fields" in semantic:
        if key == "options":
            return (
                "A YAML list required for `single_select` and `multi_select`, or "
                "`null` for other data types. An update replaces the entire option set."
            )
        if "options" in semantic and key == "id":
            return (
                "The integer ID of an existing option to retain or update. Omit it "
                "when adding a new option."
            )
        if "options" in semantic and key == "priority":
            return "A writable ordering integer. GitHub may export `null`."

    if "invitations" in semantic and is_organization:
        if key in {"login", "email"}:
            other = "email" if key == "login" else "login"
            return f"A string, or `null`. A new invitation requires either this key or `{other}`."
        if key == "teams":
            return "A YAML list of team slugs; use `[]` for none."

    if key == "events" and "hooks" in semantic:
        return (
            "A YAML list of webhook event names; include `*` to request all events. "
            "Updating the key replaces the entire subscription list. Hooks with the "
            "same configuration cannot subscribe to overlapping events. GitHub allows "
            "at most 20 hooks for the same event on one organization or repository."
        )

    return None


def _value_shape(value: Any) -> str:
    if isinstance(value, bool):
        return "`true` or `false`."
    if isinstance(value, int) and not isinstance(value, bool):
        return "An integer accepted by the linked GitHub endpoint."
    if isinstance(value, float):
        return "A number accepted by the linked GitHub endpoint."
    if isinstance(value, str):
        return "A string accepted by the linked GitHub endpoint."
    if value is None:
        return "`null`, or a value accepted by the linked GitHub endpoint."
    if isinstance(value, Mapping):
        return "A mapping containing the nested keys documented below."
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            return "A YAML list of strings; use `[]` for none."
        return "A YAML list; use `[]` for none."
    return "A YAML value accepted by the linked GitHub endpoint."


def _docs(path: ConfigPath) -> str:
    parts = _semantic_parts(path)
    if not parts:
        return _GENERAL_DOCS
    if parts[-1] == "api_version":
        return _VERSION_DOCS
    if parts[-1] == "events" and "hooks" in parts:
        return "https://docs.github.com/en/webhooks/webhook-events-and-payloads"
    if parts[0] == "organization":
        section = parts[1] if len(parts) > 1 else "settings"
        return _organization_docs(section, parts[2:])
    if parts[0] == "repositories":
        if len(parts) < 4:
            return f"{_REST}/repos/repos#update-a-repository"
        return _repository_docs(parts[3], parts[4:])
    return _GENERAL_DOCS


def _organization_docs(section: str, tail: tuple[str, ...]) -> str:
    if section == "app_installations" and "selected_repositories" in tail:
        return (
            f"{_REST}/apps/installations"
            "#list-repositories-accessible-to-the-user-access-token"
        )
    if section == "actions":
        return _actions_docs(tail)
    if section == "agents":
        return _agent_docs(tail)
    if section == "codespaces":
        return f"{_REST}/codespaces/organization-secrets"
    if section == "dependabot":
        page = "secrets" if "secrets" in tail else "repository-access"
        return f"{_REST}/dependabot/{page}"
    if section == "copilot":
        if "content_exclusion" in tail:
            return f"{_REST}/copilot/copilot-content-exclusion-management"
        if any(part.startswith("coding_agent") for part in tail):
            return f"{_REST}/copilot/copilot-coding-agent-management"
        return f"{_REST}/copilot/copilot-user-management"
    if section == "code_security":
        return f"{_REST}/code-security/configurations"
    if section == "secret_scanning":
        return _secret_scanning_docs(tail)
    if section == "hosted_compute":
        return f"{_REST}/orgs/network-configurations"
    if section == "teams":
        if "review_assignment" in tail:
            return f"{_GRAPHQL}/mutations#updateteamreviewassignment"
        if "external_group" in tail:
            return f"{_REST}/teams/external-groups"
        if "team_sync_groups" in tail:
            return f"{_REST}/teams/team-sync"
        page = "members" if "members" in tail else "teams"
        return f"{_REST}/teams/{page}"
    pages = {
        "announcement": f"{_REST}/announcement-banners/organization",
        "app_installations": f"{_REST}/orgs/orgs#list-app-installations-for-an-organization",
        "members": f"{_REST}/orgs/members",
        "invitations": f"{_REST}/orgs/members",
        "outside_collaborators": f"{_REST}/orgs/outside-collaborators",
        "personal_access_tokens": f"{_REST}/orgs/personal-access-tokens",
        "organization_roles": f"{_REST}/orgs/organization-roles",
        "custom_organization_roles": f"{_REST}/orgs/organization-roles",
        "custom_repository_roles": f"{_REST}/orgs/custom-repository-roles",
        "domains": f"{_GRAPHQL}/objects#verifiabledomain",
        "ip_allow_list": f"{_GRAPHQL}/objects#ipallowlistentry",
        "notification_restriction_enabled": (
            f"{_GRAPHQL}/mutations#updatenotificationrestrictionsetting"
        ),
        "pinned_items": f"{_GRAPHQL}/objects#organization",
        "saml_identity_provider": f"{_GRAPHQL}/objects#organizationidentityprovider",
        "security_manager_teams": f"{_REST}/orgs/security-managers",
        "blocked_users": f"{_REST}/orgs/blocking",
        "interaction_limit": f"{_REST}/interactions/orgs",
        "issue_types": f"{_REST}/orgs/issue-types",
        "issue_fields": f"{_REST}/orgs/issue-fields",
        "custom_properties": f"{_REST}/orgs/custom-properties",
        "custom_property_values": f"{_REST}/orgs/custom-properties-for-orgs#get-all-custom-property-values-for-an-organization",
        "credential_authorizations": f"{_REST}/orgs/orgs#list-saml-sso-authorizations-for-an-organization",
        "rulesets": f"{_REST}/orgs/rules",
        "hooks": f"{_REST}/orgs/webhooks",
        "budgets": f"{_REST}/billing/budgets",
        "private_registries": f"{_REST}/private-registries/organization-configurations",
        "immutable_releases": f"{_REST}/orgs/orgs#get-immutable-releases-settings-for-an-organization",
        "immutable_release_repositories": f"{_REST}/orgs/orgs#list-selected-repositories-for-immutable-releases-enforcement",
    }
    return pages.get(section, f"{_REST}/orgs/orgs#update-an-organization")


def _repository_docs(section: str, tail: tuple[str, ...]) -> str:
    if section == "settings" and "hash_algorithm" in tail:
        return f"{_REST}/repos/repos#get-the-hash-algorithm-for-a-repository"
    if section == "settings" and any(
        field in tail
        for field in (
            "has_discussions",
            "has_sponsorships",
            "issue_creation_policy",
        )
    ):
        return f"{_GRAPHQL}/mutations#updaterepository"
    if section == "actions":
        return _actions_docs(tail)
    if section == "agents":
        if "cloud_configuration" in tail:
            return f"{_REST}/copilot/copilot-coding-agent"
        return _agent_docs(tail)
    if section == "codespaces":
        return f"{_REST}/codespaces/repository-secrets"
    if section == "dependabot":
        return f"{_REST}/dependabot/secrets"
    if section == "code_scanning":
        return f"{_REST}/code-scanning/code-scanning"
    if section == "code_quality":
        return f"{_REST}/code-quality/code-quality"
    if section == "secret_scanning":
        return _secret_scanning_docs(tail)
    if section == "environments":
        if "variables" in tail:
            return f"{_REST}/actions/variables"
        if "secrets" in tail:
            return f"{_REST}/actions/secrets"
        if "branch_policies" in tail:
            return f"{_REST}/deployments/branch-policies"
        if "deployment_protection_rules" in tail:
            return f"{_REST}/deployments/protection-rules"
        return f"{_REST}/deployments/environments"
    if section == "security":
        security_pages = {
            "automated_security_fixes": "check-if-dependabot-security-updates-are-enabled-for-a-repository",
            "immutable_releases": "check-if-immutable-releases-are-enabled-for-a-repository",
            "private_vulnerability_reporting": "check-if-private-vulnerability-reporting-is-enabled-for-a-repository",
            "vulnerability_alerts": "check-if-vulnerability-alerts-are-enabled-for-a-repository",
        }
        setting = next((part for part in tail if part in security_pages), None)
        anchor = security_pages.get(setting or "")
        if anchor is not None:
            return f"{_REST}/repos/repos#{anchor}"
        return f"{_REST}/repos/repos"
    pages = {
        "topics": f"{_REST}/repos/repos#replace-all-repository-topics",
        "collaborators": f"{_REST}/collaborators/collaborators",
        "collaborator_invitations": f"{_REST}/collaborators/invitations",
        "rulesets": f"{_REST}/repos/rules",
        "branch_protections": f"{_REST}/branches/branch-protection",
        "branch_protection_rules": f"{_GRAPHQL}/objects#branchprotectionrule",
        "discussion_categories": f"{_GRAPHQL}/objects#discussioncategory",
        "hooks": f"{_REST}/repos/webhooks",
        "deploy_keys": f"{_REST}/deploy-keys/deploy-keys",
        "autolinks": f"{_REST}/repos/autolinks",
        "labels": f"{_REST}/issues/labels",
        "custom_properties": f"{_REST}/repos/custom-properties",
        "pages": f"{_REST}/pages/pages",
        "social_preview": f"{_GRAPHQL}/objects#repository",
        "workflow_states": f"{_REST}/actions/workflows",
        "interaction_limit": f"{_REST}/interactions/repos",
        "pull_request_creation_cap": (
            f"{_REST}/interactions/repos#get-pull-request-creation-cap-for-a-repository"
        ),
        "pull_request_creation_cap_bypass_users": (
            f"{_REST}/interactions/repos"
            "#get-pull-request-creation-cap-bypass-list-for-a-repository"
        ),
    }
    return pages.get(section, f"{_REST}/repos/repos#update-a-repository")


def _actions_docs(parts: tuple[str, ...]) -> str:
    pages = {
        "runner_groups": "self-hosted-runner-groups",
        "self_hosted_runners": "self-hosted-runners",
        "hosted_runners": "hosted-runners",
        "variables": "variables",
        "secrets": "secrets",
        "oidc_subject": "oidc",
        "oidc_custom_properties": "oidc",
        "cache_retention": "cache",
        "cache_storage": "cache",
    }
    for part in parts:
        if part in pages:
            return f"{_REST}/actions/{pages[part]}"
    return f"{_REST}/actions/permissions"


def _agent_docs(parts: tuple[str, ...]) -> str:
    page = "secrets" if "secrets" in parts else "variables"
    return f"{_REST}/agents/{page}"


def _secret_scanning_docs(parts: tuple[str, ...]) -> str:
    if "custom_patterns" in parts:
        return f"{_REST}/secret-scanning/custom-patterns"
    if "pattern_configurations" in parts:
        return f"{_REST}/secret-scanning/push-protection"
    return f"{_REST}/secret-scanning/secret-scanning"


def _semantic_parts(path: ConfigPath) -> tuple[str, ...]:
    parts: list[str] = []
    for index, part in enumerate(path):
        if isinstance(part, int):
            continue
        if (
            index >= 2
            and path[index - 1] == "items"
            and str(path[index - 2]) in _COLLECTION_NAMES
        ):
            parts.append("*")
        else:
            parts.append(str(part))
    return tuple(parts)


def _contains_semantic(parts: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    width = len(expected)
    return any(parts[index : index + width] == expected for index in range(len(parts)))


def _resource(path: ConfigPath) -> str:
    string_parts = tuple(str(part) for part in path if not isinstance(part, int))
    for index in range(len(string_parts) - 1, 0, -1):
        if string_parts[index - 1] == "items" and index >= 2:
            return _collection_name(string_parts[index - 2])
    if string_parts and string_parts[0] == "repositories":
        return "repository"
    if string_parts and string_parts[0] == "organization":
        return "organization"
    return "configuration"


def _is_collection_mode(path: ConfigPath) -> bool:
    return len(path) >= 2 and path[-1] == "mode" and str(path[-2]) in _COLLECTION_NAMES


def _is_collection_items(path: ConfigPath) -> bool:
    return len(path) >= 2 and path[-1] == "items" and str(path[-2]) in _COLLECTION_NAMES


def _is_collection_item(path: ConfigPath) -> bool:
    return len(path) >= 3 and path[-2] == "items" and str(path[-3]) in _COLLECTION_NAMES


def _is_personal_access_token_path(path: ConfigPath) -> bool:
    return len(path) >= 2 and path[:2] == ("organization", "personal_access_tokens")


def _is_app_installation_path(path: ConfigPath) -> bool:
    return len(path) >= 2 and path[:2] == ("organization", "app_installations")


def _is_credential_authorization_path(path: ConfigPath) -> bool:
    return len(path) >= 2 and path[:2] == (
        "organization",
        "credential_authorizations",
    )


def _is_repository_facts_path(path: ConfigPath) -> bool:
    return (
        len(path) >= 4 and path[:2] == ("repositories", "items") and path[3] == "_facts"
    )


def _is_copilot_content_exclusion_key(path: ConfigPath) -> bool:
    return len(path) == 4 and path[:3] == (
        "organization",
        "copilot",
        "content_exclusion",
    )


def _is_organization_member_public(path: ConfigPath) -> bool:
    return (
        len(path) == 5
        and path[:3] == ("organization", "members", "items")
        and path[-1] == "public"
    )


def _is_response_metadata(path: ConfigPath) -> bool:
    key = str(path[-1])
    semantic = _semantic_parts(path)
    return (
        (
            key in {"created_at", "updated_at"}
            and "issue_fields" in semantic
            and "options" in semantic
        )
        or (
            key == "security_configuration_id"
            and "code_security" in semantic
            and "reviewers" in semantic
        )
        or (
            key == "dependabot_security_updates" and "security_and_analysis" in semantic
        )
    )


def _collection_name(value: PathPart) -> str:
    key = str(value)
    return _COLLECTION_NAMES.get(key, _humanize(key).rstrip("s"))


def _humanize(value: str) -> str:
    return value.lstrip("_").replace("_", " ")


def _enum_values(values: Sequence[str]) -> str:
    return ", ".join(_code(value) for value in values) + "."


def _code(value: str) -> str:
    escaped: list[str] = []
    named_escapes = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}
    for character in value:
        if character == "`":
            escaped.append("'")
        elif character in named_escapes:
            escaped.append(named_escapes[character])
        elif category(character) in {"Cc", "Cf", "Cs"} or character in {
            "\u2028",
            "\u2029",
            "\ufffe",
            "\uffff",
        }:
            codepoint = ord(character)
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    sanitized = "".join(escaped)
    return f"`{sanitized}`"
