# github-config

Export, compare, and apply the configuration of a GitHub organization.

`github-config` reads the organization, its teams, its repositories, and the
settings attached to them. It does not fetch repository files, pull requests,
issues, discussions, security alerts, advisories, or webhook deliveries.

The exported YAML is both a snapshot of the managed settings the current token
can read and a policy language for managing selected settings across many
repositories.

The configuration language does not expose GitHub's REST and GraphQL boundary.
The command uses both APIs internally because neither one covers every setting.

## Install

This project requires Python 3.10 or newer.

Install it as an isolated command with `pipx`:

```sh
pipx install .
```

For development, create a virtual environment from this directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Authenticate

The command uses the first available ambient credential:

1. `GH_TOKEN`
2. `GITHUB_TOKEN`
3. The token returned by `gh auth token`

If `GH_TOKEN` and `GITHUB_TOKEN` are both set, they must contain the same token.
The command fails without revealing either value when they differ.

For GitHub Enterprise Server, set `GH_HOST`. You can set `GH_API_URL` or
`GITHUB_API_URL` when the API has a nonstandard URL.

GitHub grants each settings endpoint separately. A classic personal access token
normally needs `repo`, `admin:org`, `admin:org_hook`, and the relevant Actions,
Codespaces, Copilot, and security scopes for complete coverage. A fine-grained
token needs read access for export and write access for apply on the
corresponding organization and repositories.

## Export

```sh
github-config export ORG -o organization.github-config.yaml
```

Use `-` as the output path to write YAML to standard output.

Exports include a comment before every YAML key. Each comment explains what the
key controls, its possible values, and links to the relevant GitHub
documentation. Use `--no-comments` for compact output intended for machines or
when comments are maintained separately:

```sh
github-config export ORG --no-comments -o organization.github-config.yaml
```

The export continues when GitHub denies an optional settings endpoint. The
generated `_observed.unavailable` list records every section that the token
could not read. Those sections are omitted from the desired configuration and
remain unmanaged.

This also covers optional endpoints that return HTTP 402 because the account
lacks billing information or a paid entitlement.

Diff and apply block a change when its current state could not be read. This
prevents an inaccessible collection from being mistaken for an empty one.

Comments label values that GitHub exposes through a read-only API. Changing one
of those values is a fatal plan error. Omitting its key leaves it unmanaged.
Pass `--force` to `diff` or `apply` to ignore a requested change while still
applying writable changes. Comments also explain settings whose export is
incomplete, including API inputs that GitHub never returns.

Diff marks one-way revocations, such as personal access token grants and SAML
SSO credential authorizations, with a warning. GitHub cannot recreate either
grant through the API after apply removes it.

`--force` also authorizes the few writes for which GitHub cannot return enough
state to plan a lossless update. These include removing a repository from a
GitHub App installation and updating team review assignment. The diff blocks
them without `--force` and explains the hidden state that may be removed or
reset.

## Diff

```sh
github-config diff organization.github-config.yaml ORG
```

Use `--force` only when a configuration intentionally differs in read-only or
one-way fields, or when the diff identifies an incomplete-state write that you
have reviewed. The diff reports how many read-only changes it ignored and warns
about every authorized incomplete-state write.

The diff uses repository names, team slugs, and user logins for references that
GitHub exposes by name. Nested GitHub payloads that only expose an ID, such as a
ruleset bypass actor or hosted-compute network setting, retain that ID.

The command exits with status 0 when there are no changes. It exits with status
2 when it finds changes. It exits with status 1 for an error or a plan that
contains a blocked operation.

## Apply

```sh
github-config apply organization.github-config.yaml ORG
```

Apply prints the same diff and asks for confirmation. Use `--yes` in a
non-interactive environment:

```sh
github-config apply organization.github-config.yaml ORG --yes
```

The command validates the configuration and the complete plan before making the
first write. A missing secret value, an unknown resource name, or an API setting
that cannot be used for the target repository prevents the apply.

Writes run in dependency order. Repositories and parent teams are created before
settings that refer to them. The command stops at the first GitHub API error and
reports how many requests completed.

The tool can create a repository named explicitly under `repositories.items`. It
never deletes a repository. Repository files and other repository content are
outside the reconciliation model.

An existing repository can be renamed with `settings.name`. Its collection key
remains the logical key for that configuration entry, so the same file converges
after GitHub starts returning the new name. Repository references in access
lists use the configured GitHub name.

## Configuration model

The language has three rules:

- An omitted field is unmanaged.
- A collection with `mode: merge` adds or updates named items and keeps other
  items.
- A collection with `mode: exact` also removes items that are not listed.

Collections default to `merge` in a hand-written file. Exported collections use
`exact` when GitHub exposes create and delete operations. The top-level
repository collection always behaves as `merge`, because repositories are never
deleted.

For example:

```yaml
version: 1

organization:
  settings:
    default_repository_permission: read
    members_can_create_repositories: false

  actions:
    permissions:
      enabled_repositories: all
      allowed_actions: selected
    allowed_actions:
      github_owned_allowed: true
      verified_allowed: true
      patterns_allowed:
        - acme/*@v*
    secrets:
      mode: merge
      items:
        DEPLOY_TOKEN:
          visibility: selected
          selected_repositories: [api, worker]
          value_from_env: DEPLOY_TOKEN

  teams:
    mode: merge
    items:
      platform:
        settings:
          name: Platform
          privacy: closed
          notification_setting: notifications_enabled
          parent: null
        members:
          mode: exact
          items:
            alice: maintainer
            bob: member
        repositories:
          mode: merge
          items:
            api: maintain

repository_policies:
  - name: safe merge defaults
    match:
      name: "*"
      exclude: [archive-*, vendor-*]
      visibility: private
      archived: false
    set:
      settings:
        delete_branch_on_merge: true
        allow_merge_commit: false
        allow_squash_merge: true
      actions:
        workflow_permissions:
          default_workflow_permissions: read
          can_approve_pull_request_reviews: false

repositories:
  mode: merge
  items:
    api:
      topics: [service, production]
      settings:
        has_wiki: false
```

Policies are evaluated in file order. A later matching policy overrides an
earlier policy. A repository entry under `repositories.items` overrides every
policy.

Available selectors are `name`, `exclude`, `visibility`, `archived`, `fork`,
`template`, `topics_all`, and `custom_properties`. Name selectors use shell
wildcards.

See [docs/configuration.md](docs/configuration.md) for the full configuration
tree and collection behavior. The
[endpoint inventory](docs/endpoint-inventory.md) records how current GitHub REST
families map to managed configuration, content, activity, and API state that
cannot be reconciled safely.

## Secrets

GitHub never returns secret values. An export records secret names, visibility,
and selected repositories. Those fields remain stable in a diff.

To create or rotate a secret, set `value_from_env` to the name of an environment
variable. The value is read only during apply. It is encrypted with the public
key returned by GitHub before it leaves the process.

Webhook secrets use `config.secret_from_env`. Private registry credentials use
`value_from_env`.

The diff displays write-only values as redacted text. The YAML, plan, and
progress output never contain the value.

## API version

The default REST API version is `2026-03-10`. GraphQL has no corresponding
version header. Override the REST version with `GITHUB_API_VERSION` or
`--api-version`.

## Test

```sh
python -m unittest discover
ruff format --check github_config tests
ruff check github_config tests
mypy github_config tests
```
