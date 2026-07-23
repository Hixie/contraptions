from __future__ import annotations

from collections.abc import Mapping
from typing import Any

API_ROOTS = (
    "/organizations/{org}",
    "/orgs/{org}",
    "/repos/{owner}/{repo}",
)


MANAGED_PREFIXES = (
    "/organizations/{org}/actions/cache",
    "/orgs/{org}/actions/hosted-runners",
    "/orgs/{org}/actions/oidc/customization",
    "/orgs/{org}/actions/permissions",
    "/orgs/{org}/actions/runner-groups",
    "/orgs/{org}/actions/runners",
    "/orgs/{org}/actions/secrets",
    "/orgs/{org}/actions/variables",
    "/orgs/{org}/agents/secrets",
    "/orgs/{org}/agents/variables",
    "/orgs/{org}/blocks",
    "/orgs/{org}/code-security/configurations",
    "/orgs/{org}/codespaces/secrets",
    "/orgs/{org}/copilot/billing",
    "/orgs/{org}/copilot/coding-agent/permissions",
    "/orgs/{org}/copilot/content_exclusion",
    "/orgs/{org}/dependabot/repository-access",
    "/orgs/{org}/dependabot/secrets",
    "/orgs/{org}/hooks",
    "/orgs/{org}/interaction-limits",
    "/orgs/{org}/invitations",
    "/orgs/{org}/issue-fields",
    "/orgs/{org}/issue-types",
    "/orgs/{org}/members",
    "/orgs/{org}/memberships",
    "/orgs/{org}/organization-roles",
    "/orgs/{org}/outside_collaborators",
    "/orgs/{org}/personal-access-tokens",
    "/orgs/{org}/private-registries",
    "/orgs/{org}/properties",
    "/orgs/{org}/public_members",
    "/orgs/{org}/repos",
    "/orgs/{org}/rulesets",
    "/orgs/{org}/secret-scanning/custom-patterns",
    "/orgs/{org}/secret-scanning/pattern-configurations",
    "/orgs/{org}/security-managers",
    "/orgs/{org}/settings/immutable-releases",
    "/orgs/{org}/settings/network-configurations",
    "/orgs/{org}/teams",
    "/repos/{owner}/{repo}/actions/cache",
    "/repos/{owner}/{repo}/actions/oidc/customization",
    "/repos/{owner}/{repo}/actions/permissions",
    "/repos/{owner}/{repo}/actions/runners",
    "/repos/{owner}/{repo}/actions/secrets",
    "/repos/{owner}/{repo}/actions/variables",
    "/repos/{owner}/{repo}/actions/workflows",
    "/repos/{owner}/{repo}/agents/secrets",
    "/repos/{owner}/{repo}/agents/variables",
    "/repos/{owner}/{repo}/autolinks",
    "/repos/{owner}/{repo}/automated-security-fixes",
    "/repos/{owner}/{repo}/branches/{branch}/protection",
    "/repos/{owner}/{repo}/code-quality/setup",
    "/repos/{owner}/{repo}/code-scanning/default-setup",
    "/repos/{owner}/{repo}/codespaces/secrets",
    "/repos/{owner}/{repo}/collaborators",
    "/repos/{owner}/{repo}/dependabot/secrets",
    "/repos/{owner}/{repo}/environments",
    "/repos/{owner}/{repo}/hooks",
    "/repos/{owner}/{repo}/immutable-releases",
    "/repos/{owner}/{repo}/interaction-limits",
    "/repos/{owner}/{repo}/invitations",
    "/repos/{owner}/{repo}/keys",
    "/repos/{owner}/{repo}/labels",
    "/repos/{owner}/{repo}/pages",
    "/repos/{owner}/{repo}/private-vulnerability-reporting",
    "/repos/{owner}/{repo}/properties/values",
    "/repos/{owner}/{repo}/rulesets",
    "/repos/{owner}/{repo}/secret-scanning/custom-patterns",
    "/repos/{owner}/{repo}/topics",
    "/repos/{owner}/{repo}/vulnerability-alerts",
    "/user/installations/{installation_id}/repositories",
)


OPTIONAL_MANAGED_PREFIXES = (
    # These endpoint families differ between GitHub OpenAPI descriptions.
    "/organizations/{org}/org-properties/values",
    "/organizations/{org}/settings/billing/budgets",
    "/orgs/{org}/announcement",
    "/orgs/{org}/credential-authorizations",
    "/orgs/{org}/custom-repository-roles",
    "/orgs/{org}/custom_roles",
)


MANAGED_EXACT_PATHS = {
    "/orgs/{org}",
    "/repos/{owner}/{repo}",
}


NON_DECLARATIVE_PREFIXES = (
    "/orgs/{org}/actions/hosted-runners/images/custom",
    "/orgs/{org}/actions/hosted-runners/limits",
    "/orgs/{org}/actions/hosted-runners/machine-sizes",
    "/orgs/{org}/actions/hosted-runners/platforms",
    "/orgs/{org}/codespaces",
    "/orgs/{org}/failed_invitations",
    "/orgs/{org}/hooks/{hook_id}/deliveries",
    "/orgs/{org}/members/{username}/codespaces",
    "/orgs/{org}/personal-access-token-requests",
    "/orgs/{org}/projectsV2",
    "/repos/{owner}/{repo}/actions/artifacts",
    "/repos/{owner}/{repo}/actions/caches",
    "/repos/{owner}/{repo}/actions/jobs",
    "/repos/{owner}/{repo}/actions/runs",
    "/repos/{owner}/{repo}/branches",
    "/repos/{owner}/{repo}/check-runs",
    "/repos/{owner}/{repo}/check-suites",
    "/repos/{owner}/{repo}/code-scanning/analyses",
    "/repos/{owner}/{repo}/code-scanning/codeql/databases",
    "/repos/{owner}/{repo}/codespaces",
    "/repos/{owner}/{repo}/hooks/{hook_id}/deliveries",
    "/repos/{owner}/{repo}/milestones",
)


CONTENT_OR_ACTIVITY_PARTS = (
    "/alerts",
    "/artifacts/metadata",
    "/attestations",
    "/campaigns",
    "/comments",
    "/commits",
    "/contents",
    "/copilot-spaces",
    "/dependency-graph",
    "/deployments",
    "/dismissal-requests",
    "/discussions",
    "/events",
    "/forks",
    "/git/",
    "/import",
    "/issues",
    "/merges",
    "/migrations",
    "/notifications",
    "/packages",
    "/pulls",
    "/bypass-requests",
    "/reactions",
    "/releases",
    "/security-advisories",
    "/stargazers",
    "/stats/",
    "/statuses",
    "/subscribers",
    "/subscription",
    "/traffic/",
)


def candidate_configuration_paths(description: Mapping[str, Any]) -> set[str]:
    raw_paths = description.get("paths", {})
    if not isinstance(raw_paths, Mapping):
        raise TypeError("OpenAPI description does not contain a paths mapping")
    paths = {
        str(path): item
        for path, item in raw_paths.items()
        if isinstance(path, str) and isinstance(item, Mapping)
    }
    candidates: set[str] = set()
    for path, item in paths.items():
        if not path.startswith(API_ROOTS) or "get" not in item:
            continue
        direct_write = any(
            method in item for method in ("post", "put", "patch", "delete")
        )
        child_write = any(
            other_path.startswith(f"{path.rstrip('/')}/")
            and any(
                method in other_item for method in ("post", "put", "patch", "delete")
            )
            for other_path, other_item in paths.items()
        )
        if direct_write or child_write:
            candidates.add(path)
    return candidates


def unclassified_configuration_paths(description: Mapping[str, Any]) -> set[str]:
    return {
        path
        for path in candidate_configuration_paths(description)
        if classify_path(path) is None
    }


def classify_path(path: str) -> str | None:
    matches: list[tuple[int, str]] = []
    if path in MANAGED_EXACT_PATHS:
        matches.append((len(path), "managed"))
    for prefix in MANAGED_PREFIXES:
        if _under(path, prefix):
            matches.append((len(prefix), "managed"))
    for prefix in OPTIONAL_MANAGED_PREFIXES:
        if _under(path, prefix):
            matches.append((len(prefix), "managed"))
    for prefix in NON_DECLARATIVE_PREFIXES:
        if _under(path, prefix):
            matches.append((len(prefix), "non-declarative"))
    if any(part in path for part in CONTENT_OR_ACTIVITY_PARTS):
        matches.append((1, "content-or-activity"))
    return max(matches)[1] if matches else None


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")
