# Endpoint inventory

`github-config` presents one declarative configuration model. It uses REST and
GraphQL internally. Neither GitHub API covers all of the settings in that model.
The transport choice does not appear in the YAML, diff, or apply behavior.

The checked OpenAPI inventory covers organization settings, access grants,
teams, repositories, Actions and agent settings, runners, secrets and variables,
rulesets, webhooks, environments, security settings, custom properties, and the
other REST sections in the configuration reference. The GraphQL audit is
recorded below because GitHub does not publish those fields in the OpenAPI
description.

The inventory separates these API families:

- Repository files, pull requests, issues, discussions, projects, releases,
  deployments, packages, advisories, alerts, and similar user content are out of
  scope.
- Logs, webhook deliveries, workflow runs, caches, analyses, metrics, and other
  activity records describe events rather than desired settings.
- Pending fine-grained personal access token requests are approval work. The API
  cannot create the corresponding request, so they are not steady configuration.
  Approved grants are managed because the API can read and revoke them.
- SAML SSO credential authorizations are managed as a one-way collection. The
  API can read and revoke them, but a credential owner must authorize the
  credential again after revocation.
- Codespaces organization access has write endpoints but no read endpoint.
  Applying it would require guessing the current policy, so it is not managed.
- Custom hosted-runner images can be listed and deleted through REST, but REST
  cannot create the image definition. They are build artifacts rather than a
  reproducible settings collection.
- Available IdP and enterprise external-group catalogs are lookup data. Team
  mappings to those groups are managed; the directory catalogs and their member
  lists are not copied into configuration.
- Enterprise-inherited members, teams, team grants, runner groups, and custom
  properties are exported as read-only state. Organization endpoints cannot
  change their enterprise ownership.
- Write-only settings are intentionally absent. These include webhook basic
  authentication, four advanced team review-assignment inputs, user-level IP
  allow-list enforcement, and the aggregate `code_security` and
  `secret_protection` inputs for code-security configurations.
- Security bypass requests and alert-dismissal requests are security report
  workflow rather than desired configuration, so they remain out of scope.

Enterprise custom property values assigned to the organization are fully
managed. The general GitHub OpenAPI description and the Enterprise Cloud
description do not contain identical endpoint sets. Budget management is
therefore classified as an optional managed family while remaining active
whenever that endpoint is available.

## GraphQL additions

GraphQL supplies repository Discussions and sponsorship switches, issue creation
policy, the canonical classic branch protection rule collection including
wildcard patterns, and environment pin state and order. It also supplies team
review assignment, organization IP allow lists, notification restriction,
verified and approved domains, and custom property regular expressions.

GraphQL exposes several read-only values: organization SAML identity provider
details and pinned profile items, repository discussion categories and social
preview state, and organization settings without public mutations. REST adds
other read-only values, including Copilot policies, Copilot cloud agent
configuration, GitHub App installation metadata, anonymous Git access, and the
repository object hash algorithm.

The user-installation REST API reads and changes the repository set for GitHub
App installations that already use selected-repository access. It returns only
repositories the ambient user can access and requires a classic personal access
token with the `repo` scope for changes. Export comments identify the possible
gap. The tool does not install or uninstall apps and treats the remaining
installation metadata as read-only.

Wildcard branch protection actor connections return at most 100 entries per
page. Export follows their cursors. If GitHub reports another page without a
usable cursor, the affected actor list is omitted and an export caveat explains
that it remains unmanaged. A status-check or actor list missing from a partial
GraphQL response is omitted instead of represented as empty. Partially returned
authoritative collections use merge mode so a later apply cannot delete entries
that were missing from the export.

Team review assignment has four write-only inputs. Its exported section explains
the gap. Updates are blocked unless `--force` accepts resetting those inputs to
GitHub's documented defaults.

[`github_config/endpoint_inventory.py`](../github_config/endpoint_inventory.py)
contains the machine-readable classifications. Check them against an official
GitHub OpenAPI description with:

```sh
PYTHONPATH=. python scripts/check_endpoint_inventory.py /path/to/github-openapi.json
```

The command fails when a managed endpoint family disappears or a new readable
and writable family has no classification. This makes API coverage changes an
explicit code review decision.
