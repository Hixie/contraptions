# Configuration reference

The configuration root must contain `version: 1`.

`github-config export` annotates every YAML key with its purpose, possible
values, and relevant GitHub documentation by default. Pass `--no-comments` to
produce the same configuration without per-key annotations. Comments do not
affect diff or apply behavior.

Unknown top-level keys, organization sections, repository sections, and setting
names are rejected. This makes spelling mistakes fail before the command sends a
write request.

Configuration value types are checked before the command loads credentials or
queries GitHub. Quote text that YAML would otherwise interpret as another type.
For example, write a label color as `color: "000000"`; unquoted `000000` is an
integer rather than a string.

Relationships between settings are checked at the same stage when the file
contains the needed facts. For example, `settings.allow_forking` is rejected
when the same repository entry declares `visibility: public`. GitHub permits
that setting only for an organization-owned private or internal repository. The
organization setting `members_can_fork_private_repositories` must also be
enabled while a repository-specific value is changed. A file can disable
repository forking and the organization policy together; the repository changes
are applied first.

Likewise, a repository can use `settings.has_projects: true` only when
`organization.settings.has_repository_projects` is enabled. When one file
enables an organization prerequisite and its dependent repository setting, the
organization change is applied first. Exports omit a reported
`has_projects: true` value when the organization prerequisite is known to be
disabled.

A partial file can manage `allow_forking` without repeating `visibility`. Diff
and apply then check the repository's current visibility after reading GitHub
and before making any write. Exports omit `allow_forking` for public
repositories and when the organization-level forking policy is known to be
disabled. GitHub can report the value in both cases even though it cannot be
managed. When the token cannot read an organization prerequisite, the setting is
retained and GitHub performs the prerequisite check if an update is needed.

An archived repository cannot change unless its repository entry explicitly sets
`archived: false`. This includes repository access and assignments managed
through organization sections, such as team access and selected-repository
lists. Apply sends the requested unarchive before the remaining changes. Keeping
the repository archived requires a later apply that sets `archived: true` after
the other changes have succeeded. GitHub's documented exception for enabling
secret scanning on an archived repository does not require an unarchive. That
exception sends only the secret-scanning setting, even when the input is a full
export. Organization code-security configurations can also be assigned to
archived repositories without unarchiving them.

## Managed and authoritative values

Mappings are partial. Only keys present in the file are managed.

Named collections have this form:

```yaml
mode: merge
items:
  item-name:
    setting: value
```

`merge` creates missing items and updates listed items. `exact` also removes
items that exist on GitHub but are absent from `items`.

An exported file uses `exact` for members, teams, rulesets, webhooks, variables,
secret names, environments, labels, deploy keys, custom properties, runners,
budgets, and similar named resources. Change an exported collection to `merge`
when the file should manage only the entries it names.

The `repositories` collection never removes repositories, regardless of its
mode.

GitHub exposes some configuration without a public write operation. Export
comments label those values as read-only. A changed read-only value blocks the
complete apply before the first request. An omitted value remains unmanaged,
like any other omitted field. `--force` ignores requested changes to read-only
values. It does not turn a read-only value into a writable one.

Enterprise-inherited organization members, teams, team memberships, repository
grants, runner groups, and custom properties remain in the export. Their item
comments identify them as read-only. Omitting one from an exact collection
reports a blocked removal instead of attempting to change the enterprise-owned
state.

An export comment also calls out a partial representation. This includes
write-only GitHub inputs and connections that GitHub could not paginate
completely. Diff repeats a warning when a writable operation can replace values
that GitHub does not return.

## Organization sections

`organization.settings` contains fields accepted by GitHub's organization update
endpoint. This includes repository creation policy, default repository
permission, project settings, new-repository security defaults, fork policy,
Pages policy, and the organization's public profile.

The same mapping also contains organization values that GitHub only exposes for
reading. Their export comments identify them individually. They cannot be
changed unless GitHub adds a public write operation.

The remaining organization tree is:

```text
organization
  members
  teams
    settings
    members
    repositories
    review_assignment
    external_group
    team_sync_groups
  invitations
  outside_collaborators
  personal_access_tokens
  credential_authorizations
  organization_roles
  custom_organization_roles
  custom_repository_roles
  announcement
  notification_restriction_enabled
  ip_allow_list
    entries
  domains
  saml_identity_provider
  pinned_items
  app_installations
    selected_repositories
  security_manager_teams
  blocked_users
  interaction_limit
  issue_types
  issue_fields
  custom_properties
  custom_property_values
  rulesets
  hooks
  budgets
  private_registries
  immutable_releases
  immutable_release_repositories
  actions
    permissions
    selected_repositories
    allowed_actions
    workflow_permissions
    artifact_and_log_retention
    fork_pull_request_approval
    private_fork_pull_request_workflows
    self_hosted_runner_permissions
    self_hosted_runner_repositories
    oidc_subject
    oidc_custom_properties
    cache_retention
    cache_storage
    variables
    secrets
    runner_groups
    self_hosted_runners
    hosted_runners
  agents
    variables
    secrets
  codespaces
    secrets
  dependabot
    repository_access
    secrets
  copilot
    coding_agent_permissions
    coding_agent_repositories
    content_exclusion
    seats
    policies
  code_security
    configurations
  secret_scanning
    pattern_configurations
    custom_patterns
  hosted_compute
    network_configurations
```

Team membership roles are `member` and `maintainer`. Organization membership
roles are `member` and `admin`. Repository permissions use GitHub's built-in
permission names or a custom repository role name.

Team `review_assignment` contains `enabled`, `algorithm`, `member_count`, and
`notify_team`. GitHub accepts four additional inputs but does not return them.
An export explains this gap. Updating a returned field would reset the hidden
inputs to GitHub's documented defaults, so diff and apply block the update
unless `--force` explicitly accepts those resets.

`ip_allow_list.entries` is keyed by its address or CIDR range. Organization
domains are keyed by domain name. Domain approval and verification can move from
false to true. GitHub has no inverse operation, so the opposite change is
blocked unless `--force` ignores it.

Custom role definition collections are separate from `organization_roles`, which
manages the user and team assignments attached to roles. Enterprise and
predefined organization roles are read-only.

GitHub App installation metadata is read-only. For an installation whose
`repository_selection` is already `selected`, the `selected_repositories` list
is writable. GitHub returns only repositories the ambient user can access, so an
export may omit selected repositories outside that user's access. Updates
require a classic personal access token with the `repo` scope. Removing a
repository from an installation is blocked unless `--force` explicitly
authorizes the removal from an incomplete source list. Exact mode cannot
uninstall an omitted app; it reports the read-only removal instead.

Pending organization invitations include `login` or `email`, `role`, and team
slugs. Outside collaborators are keyed by login. Pending repository invitations
are keyed by login and contain the requested repository permission.

Fine-grained personal access token grants are readable only to GitHub Apps. An
exact collection can revoke grants that are omitted. GitHub does not provide an
API for creating or changing a grant directly, so additions and edits are
blocked before apply unless `--force` ignores them. A diff warns when a removal
would revoke a grant because the API cannot undo the operation.

SAML SSO credential authorizations have the same one-way shape. Their exported
details are read-only. Omitting an authorization from an exact collection
revokes it, and a diff warns that the credential owner must authorize it again
to restore access.

`custom_property_values` contains enterprise custom property values assigned to
the organization. Values are strings, lists of strings, or `null`. Exact mode
sets omitted current values to `null`, which removes those assignments.

Code-security configuration entries can contain `repositories` and
`default_for_new_repos` in addition to the security settings returned by GitHub.
Repository attachment changes use repository names.

Runner groups contain a `settings` mapping plus optional `repositories` and
`runners` lists. A hosted runner names its `runner_group`. A runner group names
its hosted-compute `network_configuration`. The command resolves those names to
the target organization's IDs during apply.

Self-hosted runner entries contain custom labels. A missing configured runner is
blocked because the runner must register itself. Removing a runner from an exact
collection unregisters it.

When GitHub permits duplicate resource names, exported keys end in
`#github-id-<id>`. These keys stay stable within one organization. A plan blocks
changes to them when `_observed.organization` names a different organization,
because an ID cannot identify the corresponding resource in another
organization.

## Repository sections

Every item under `repositories.items` and every repository policy `set` mapping
uses this tree:

```text
repository
  settings
  topics
  collaborators
  collaborator_invitations
  rulesets
  branch_protections
  branch_protection_rules
  discussion_categories
  social_preview
  hooks
  deploy_keys
  autolinks
  labels
  environments
    settings
    pinned
    pinned_position
    branch_policies
    deployment_protection_rules
    variables
    secrets
  custom_properties
  security
  pages
  workflow_states
  interaction_limit
  pull_request_creation_cap
  pull_request_creation_cap_bypass_users
  actions
    permissions
    allowed_actions
    workflow_permissions
    access
    artifact_and_log_retention
    fork_pull_request_approval
    private_fork_pull_request_workflows
    oidc_subject
    cache_retention
    cache_storage
    self_hosted_runners
    variables
    secrets
  agents
    variables
    secrets
    cloud_configuration
  codespaces
    secrets
  dependabot
    secrets
  code_scanning
    default_setup
  code_quality
    setup
  secret_scanning
    custom_patterns
```

`security` contains the boolean settings `automated_security_fixes`,
`immutable_releases`, `private_vulnerability_reporting`, and
`vulnerability_alerts`.

`branch_protection_rules` is the canonical classic branch protection collection.
Its keys are branch names or wildcard patterns. Actor references use
`user:LOGIN`, `team:SLUG`, and `app:SLUG`. A required status check can name a
GitHub App slug, use `app: any` to accept that check from any App, or use
`app: recent` to select the App that most recently supplied the check. GitHub
exports `app: null` when it cannot distinguish the latter two states. An
unrelated rule edit preserves that ambiguous list, while changing the list
requires replacing every `null` with an explicit value.

When the GraphQL rule collection cannot be read, export falls back to
`branch_protections`. That REST representation uses concrete branch names and
can flatten a wildcard rule onto every matching branch. Its export comment
identifies this incomplete fallback. A repository configuration cannot contain
both collections because they control the same GitHub state. If a later diff can
read the canonical collection, it blocks reconciliation of the fallback and asks
for a fresh export instead of translating rules without their identities.

An environment reviewer has a type and name:

```yaml
reviewers:
  - type: user
    name: alice
  - type: team
    name: release-managers
```

The command resolves reviewer names to GitHub IDs. A deployment protection rule
uses the GitHub App slug as its key.

Environment `pinned` and `pinned_position` values control the pinned environment
list. GitHub's GraphQL operations manage those values while the remaining
environment settings use REST. That implementation detail does not appear in the
configuration language.

`pages.enabled` controls whether a Pages site exists. The remaining fields are
`build_type`, `source`, `cname`, `https_enforced`, and `public`.

Repository `settings.has_discussions`, `settings.has_sponsorships`, and
`settings.issue_creation_policy` are writable settings. Discussion categories,
the social preview image, the repository object `hash_algorithm`, and Copilot
cloud agent configuration are read-only because GitHub does not publish write
operations for them.

Workflow states are keyed by workflow path. `active` enables a workflow, and
`disabled_manually` disables it. Workflow files are never read or changed.
Removing a workflow from an `exact` collection produces a blocked operation
because removing the corresponding file would change repository content.

GitHub can also report `disabled_fork` and `deleted`. These values, together
with `disabled_inactivity`, round-trip as observed states. A plan blocks an
attempt to set one of them because the API only provides explicit enable and
manual-disable operations.

`repositories.items.<key>.settings.name` renames an existing repository. The
item key remains its logical identity in that configuration file. Settings and
child resources continue to use that key, while access lists and other
repository references use the configured GitHub name. A new repository uses its
GitHub name as the item key.

Removing a secret-scanning custom pattern is blocked. GitHub requires every
pattern deletion to resolve or reopen the pattern's existing alerts, and alerts
are outside the configuration model.

## Repository policies

A policy has `match` and `set` mappings. `name` is optional.

```yaml
repository_policies:
  - name: private services
    match:
      name: [api-*, worker-*]
      exclude: [archive-*]
      visibility: private
      archived: false
      fork: false
      template: false
      topics_all: [service]
      custom_properties:
        lifecycle: production
    set:
      settings:
        delete_branch_on_merge: true
```

`name` and `exclude` accept one shell pattern or a list of patterns. All other
selectors in a policy must match. Policies only select repositories that already
exist. An explicit entry under `repositories.items` can create a repository.

## Write-only values

Secret values use an environment variable name:

```yaml
secrets:
  mode: merge
  items:
    TOKEN:
      value_from_env: TOKEN_VALUE
```

Organization secrets can also contain `visibility` and `selected_repositories`.
Repository and environment secrets contain no readable settings beyond their
names.

A private registry can use `value_from_env` for its token or password. An OIDC
private registry does not need a write-only value.

Private registry keys use the secret name generated by GitHub. For example,
`registry_type: npm_registry` uses `NPM_REGISTRY_SECRET`. Selected repository
access is represented by repository names.

A webhook uses this form:

```yaml
config:
  url: https://example.test/github
  content_type: json
  insecure_ssl: "0"
  secret_from_env: WEBHOOK_SECRET
```

Providing a write-only environment reference intentionally plans a replacement
on every diff. GitHub does not expose a value or digest that could prove the
remote value is already equal.

## Observed metadata

Keys beginning with an underscore are observations, not desired settings.

`_observed` records the organization, API version, and unavailable endpoints.
Repository `_facts` supplies read-only facts to policy selectors. An interaction
limit `_expires_at` records GitHub's absolute expiration time without renewing
the restriction during each apply. A desired setting whose current endpoint is
listed as unavailable produces a blocked operation instead of an unsafe diff.
