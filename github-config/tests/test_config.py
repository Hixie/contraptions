from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode

from github_config.comments import _comment_for
from github_config.config import (
    ConfigError,
    deep_merge,
    desired_repositories,
    dump_config,
    load_config,
    validate_config_semantics,
)


def _join_wrapped_comment_lines(document: str) -> str:
    joined: list[str] = []
    previous_comment_indentation: str | None = None
    for line in document.splitlines():
        stripped = line.lstrip()
        indentation = line[: len(line) - len(stripped)]
        if stripped.startswith("# "):
            content = stripped[2:]
            starts_new_comment = content.startswith(("Values: ", "http://", "https://"))
            previous_is_documentation = bool(
                joined and joined[-1].lstrip().startswith(("# http://", "# https://"))
            )
            if (
                joined
                and previous_comment_indentation == indentation
                and not starts_new_comment
                and not previous_is_documentation
            ):
                joined[-1] += f" {content}"
            else:
                joined.append(line)
            previous_comment_indentation = indentation
        else:
            joined.append(line)
            previous_comment_indentation = None
    return "\n".join(joined)


def _load_document(document: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(document, encoding="utf-8")
        return load_config(path)


class ConfigTest(unittest.TestCase):
    def test_commented_configuration_explains_every_mapping_key(self) -> None:
        config = {
            "version": 1,
            "organization": {"settings": {"default_repository_permission": "read"}},
            "repositories": {
                "mode": "merge",
                "items": {
                    "widget": {
                        "environments": {
                            "mode": "exact",
                            "items": {
                                "production": {
                                    "settings": {
                                        "reviewers": [
                                            {"type": "team", "name": "release"}
                                        ]
                                    }
                                }
                            },
                        }
                    }
                },
            },
        }

        output = dump_config(config, comments=True)

        self.assertEqual(yaml.safe_load(output), config)
        self.assertIn(
            "# Omitting a setting does not reset or delete it; github-config leaves its\n"
            "# current GitHub value unchanged.\n"
            "# Omitting an entire collection also leaves it unchanged. Inside an included\n"
            "# collection, mode: merge keeps omitted items; mode: exact removes them when\n"
            "# github-config manages removal for that collection.\n"
            "# Values named *_from_env refer to environment variable names; secret values are\n"
            "# never exported.\n"
            "\n"
            "# The github-config file format version.",
            output,
        )
        self.assertIn(
            "# The base repository permission granted to organization members.\n"
            "    # Values: Writable values are `read`, `write`, `admin`, and `none`. GitHub\n"
            "    # may export the additional read value `null`, which is not applied.\n"
            "    # https://docs.github.com/en/rest/orgs/orgs#update-an-organization\n"
            "    default_repository_permission: read",
            output,
        )
        document = yaml.compose(output, Loader=yaml.SafeLoader)
        self.assertIsNotNone(document)
        if document is not None:
            self._assert_every_key_has_comments(document, output.splitlines())

    def test_key_comments_can_be_disabled(self) -> None:
        output = dump_config({"version": 1}, comments=False)

        self.assertNotIn("# The github-config file format version.", output)
        self.assertNotIn("# Values:", output)
        self.assertNotIn("# https://docs.github.com/", output)

    def test_key_descriptions_are_noun_phrases(self) -> None:
        organization = _comment_for(("organization",), {})
        collection_mode = _comment_for(("organization", "teams", "mode"), "exact")
        repository = _comment_for(("repositories", "items", "TestRepository"), {})

        self.assertEqual(
            organization.description,
            "Organization-wide settings and resources.",
        )
        self.assertEqual(
            collection_mode.description,
            "Whether this collection keeps or removes GitHub entries omitted from "
            "items.",
        )
        self.assertEqual(
            repository.description,
            "The repository named `TestRepository` and the values attached to it.",
        )

    def test_comment_wrapping_splits_long_names_but_keeps_docs_links(self) -> None:
        repository_name = "r" * 100
        config = {
            "version": 1,
            "repositories": {
                "mode": "merge",
                "items": {repository_name: {}},
            },
        }

        output = dump_config(config, comments=True)

        self.assertEqual(yaml.safe_load(output), config)
        for line in output.splitlines():
            if line.lstrip().startswith("# ") and "# https://" not in line:
                self.assertLessEqual(len(line), 80)
        self.assertIn(
            "    # https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            f"    {repository_name}: {{}}",
            output,
        )

    def test_dynamic_item_names_do_not_select_setting_value_rules(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "teams": {
                    "mode": "exact",
                    "items": {
                        "platform": {
                            "members": {
                                "mode": "exact",
                                "items": {"role": "maintainer"},
                            }
                        }
                    },
                }
            },
            "repositories": {
                "mode": "merge",
                "items": {"visibility": {}},
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "# Values: `member`, `maintainer`.\n"
            "            # https://docs.github.com/en/rest/teams/members\n"
            "            role: maintainer",
            output,
        )
        self.assertIn(
            "# The repository named `visibility` and the values attached to it.\n"
            "    # Values: A mapping containing the nested keys documented below.\n"
            "    # https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            "    visibility: {}",
            output,
        )

    def test_nested_version_uses_its_github_value_type(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "actions": {
                    "hosted_runners": {
                        "mode": "exact",
                        "items": {
                            "linux": {"image": {"version": "latest"}},
                        },
                    }
                }
            },
            "repositories": {"mode": "merge", "items": {}},
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "# Values: A custom image version string, or `null`. It is used only when `source` is `custom`.\n"
            "            # https://docs.github.com/en/rest/actions/hosted-runners\n"
            "            version: latest",
            output,
        )

    def test_comments_use_specialized_values_and_documentation(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "agents": {
                    "variables": {
                        "mode": "exact",
                        "items": {"REGION": {"value": "west"}},
                    }
                },
                "private_registries": {
                    "mode": "exact",
                    "items": {
                        "NPM_REGISTRY_SECRET": {
                            "auth_type": "token",
                            "visibility": "private",
                        }
                    },
                },
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {
                            "Default": {
                                "secret_scanning_delegated_bypass_options": {
                                    "reviewers": [{"mode": "ALWAYS"}]
                                }
                            }
                        },
                    }
                },
            },
            "repositories": {
                "mode": "merge",
                "items": {
                    "actions": {
                        "settings": {"visibility": "private"},
                        "custom_properties": {"visibility": "engineering"},
                    },
                    "secrets": {"settings": {"visibility": "internal"}},
                },
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn("https://docs.github.com/en/rest/agents/variables", output)
        self.assertIn(
            "https://docs.github.com/en/rest/private-registries/organization-configurations",
            output,
        )
        self.assertIn(
            "# Values: `ALWAYS`, `EXEMPT`.\n"
            "            # https://docs.github.com/en/rest/code-security/configurations\n"
            "            - mode: ALWAYS",
            output,
        )
        self.assertIn(
            "# https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            "        visibility: private",
            output,
        )
        self.assertIn(
            "# Values: `public`, `private`, `internal`.\n"
            "        # https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            "        visibility: internal",
            output,
        )
        self.assertIn(
            "# The value of the custom property named `visibility`.\n"
            "        # Values: A string, list of strings, or `null`, as allowed by the property's schema.\n"
            "        # https://docs.github.com/en/rest/repos/custom-properties\n"
            "        visibility: engineering",
            output,
        )

    def test_comments_preserve_yaml_line_breaks_and_escape_dynamic_names(self) -> None:
        repository_name = "line\u2028separator\ufffe\uffff"
        config = {
            "version": 1,
            "organization": {
                "settings": {
                    "description": "next\x85line\u2028and\u2029paragraph",
                    "email": "after@example.com",
                }
            },
            "repositories": {
                "mode": "merge",
                "items": {repository_name: {}},
            },
        }

        output = dump_config(config, comments=True)

        self.assertEqual(yaml.safe_load(output), config)
        self.assertIn("named `line\\u2028separator\\ufffe\\uffff`", output)
        self.assertIn("# Explicitly managed repositories by repository name.", output)

    def test_comments_describe_reviewed_setting_values_and_links(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "blocked_users": ["spammer"],
                "immutable_releases": {"enforced_repositories": "selected"},
                "immutable_release_repositories": ["widget"],
                "invitations": {
                    "mode": "exact",
                    "items": {
                        "recruiter": {
                            "login": "recruiter",
                            "role": "hiring_manager",
                        }
                    },
                },
                "personal_access_tokens": {
                    "mode": "exact",
                    "items": {
                        "alice:automation": {
                            "owner": "alice",
                            "token_name": "automation",
                            "repository_selection": "subset",
                            "repositories": ["widget"],
                            "permissions": {"contents": "read"},
                            "token_expired": False,
                            "token_expires_at": "2026-08-01T00:00:00Z",
                        }
                    },
                },
                "issue_types": {
                    "mode": "exact",
                    "items": {
                        "Bug": {
                            "is_enabled": True,
                            "description": "Unexpected behavior",
                            "color": None,
                        }
                    },
                },
                "issue_fields": {
                    "mode": "exact",
                    "items": {
                        "Services": {
                            "data_type": "multi_select",
                            "visibility": "organization_members_only",
                            "options": [
                                {"name": "API", "color": "purple", "priority": 1}
                            ],
                        }
                    },
                },
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {
                            "Default": {
                                "default_for_new_repos": "private_and_internal",
                                "advanced_security": "code_security",
                            }
                        },
                    }
                },
            },
            "repositories": {
                "mode": "exact",
                "items": {
                    "widget": {
                        "actions": {
                            "secrets": {
                                "mode": "exact",
                                "items": {"DEPLOY_TOKEN": {}},
                            }
                        },
                        "collaborator_invitations": {
                            "mode": "exact",
                            "items": {"pending": "write"},
                        },
                        "code_scanning": {
                            "default_setup": {"threat_model": "remote_and_local"}
                        },
                        "custom_properties": {"service": ["api", "worker"]},
                        "pull_request_creation_cap": {
                            "enabled": True,
                            "max_open_pull_requests": 25,
                        },
                        "pull_request_creation_cap_bypass_users": ["alice"],
                        "deploy_keys": {
                            "mode": "exact",
                            "items": {
                                "automation": {
                                    "title": "automation",
                                    "key": "ssh-ed25519 AAAA",
                                    "read_only": True,
                                }
                            },
                        },
                        "security": {
                            "automated_security_fixes": True,
                            "immutable_releases": True,
                            "private_vulnerability_reporting": True,
                            "vulnerability_alerts": True,
                        },
                    }
                },
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "# Values: `all`, `none`, `private_and_internal`, `public`.\n"
            "          # https://docs.github.com/en/rest/code-security/configurations\n"
            "          default_for_new_repos: private_and_internal",
            output,
        )
        self.assertIn(
            "# Values: `enabled`, `disabled`, `code_security`, `secret_protection`.\n"
            "          # https://docs.github.com/en/rest/code-security/configurations\n"
            "          advanced_security: code_security",
            output,
        )
        self.assertIn(
            "# Values: `remote`, `remote_and_local`.\n"
            "          # https://docs.github.com/en/rest/code-scanning/code-scanning\n"
            "          threat_model: remote_and_local",
            output,
        )
        self.assertIn(
            "# Values: `text`, `date`, `single_select`, `multi_select`, `number`. This value is create-only; changing it requires replacing the issue field.\n"
            "        # https://docs.github.com/en/rest/orgs/issue-fields\n"
            "        data_type: multi_select",
            output,
        )
        self.assertIn(
            "# Values: `organization_members_only`, `all`.\n"
            "        # https://docs.github.com/en/rest/orgs/issue-fields\n"
            "        visibility: organization_members_only",
            output,
        )
        self.assertIn(
            "# Values: Writable values are `gray`, `blue`, `green`, `yellow`, `orange`, `red`, `pink`, `purple`. GitHub may export the additional read value `null`.\n"
            "          # https://docs.github.com/en/rest/orgs/issue-fields\n"
            "          color: purple",
            output,
        )
        self.assertIn(
            "# Values: `gray`, `blue`, `green`, `yellow`, `orange`, `red`, `pink`, `purple`, or `null`.\n"
            "        # https://docs.github.com/en/rest/orgs/issue-types\n"
            "        color: null",
            output,
        )
        self.assertIn(
            "# Values: Writable values are `direct_member`, `admin`, `billing_manager`, and `reinstate`. GitHub may export the read-only `hiring_manager` role.\n"
            "        # https://docs.github.com/en/rest/orgs/members\n"
            "        role: hiring_manager",
            output,
        )
        self.assertIn(
            "# Values: `read`, `write`, `triage`, `maintain`, `admin`.\n"
            "          # https://docs.github.com/en/rest/collaborators/invitations\n"
            "          pending: write",
            output,
        )
        self.assertIn(
            "# Values: A string, list of strings, or `null`, as allowed by the property's schema.\n"
            "        # https://docs.github.com/en/rest/repos/custom-properties\n"
            "        service:",
            output,
        )
        self.assertIn(
            "# Whether omitted personal access token grants remain active or are revoked.\n"
            "    # Values: `merge` leaves omitted grants active; `exact` revokes omitted grants.\n"
            "    # https://docs.github.com/en/rest/orgs/personal-access-tokens\n"
            "    mode: exact",
            output,
        )
        self.assertIn(
            "# The observed personal access token grant named `alice:automation`, retained when present and revoked when omitted in exact mode.\n"
            "      # Values: A mapping of read-only grant details. Keep the item to retain the grant, or omit it in exact mode to revoke the grant.\n"
            "      # https://docs.github.com/en/rest/orgs/personal-access-tokens\n"
            "      alice:automation:",
            output,
        )
        self.assertIn(
            "# The grant's observed owner, a read-only value not changed by apply.\n"
            "        # Values: A string accepted by the linked GitHub endpoint. This read-only observation is not changed by apply.\n"
            "        # https://docs.github.com/en/rest/orgs/personal-access-tokens\n"
            "        owner: alice",
            output,
        )
        self.assertIn(
            "# Values: `{}` records an existing secret without replacing its write-only value. A mapping with `value_from_env: ENV_NAME` creates or replaces the secret.\n"
            "            # https://docs.github.com/en/rest/actions/secrets\n"
            "            DEPLOY_TOKEN: {}",
            output,
        )
        self.assertIn(
            "# Values: An integer from `1` through `1000`.\n"
            "        # https://docs.github.com/en/rest/interactions/repos#get-pull-request-creation-cap-for-a-repository\n"
            "        max_open_pull_requests: 25",
            output,
        )
        self.assertIn(
            "# Values: A YAML list of up to 100 GitHub user logins; use `[]` for none.\n"
            "      # https://docs.github.com/en/rest/interactions/repos#get-pull-request-creation-cap-bypass-list-for-a-repository\n"
            "      pull_request_creation_cap_bypass_users:",
            output,
        )
        self.assertIn("https://docs.github.com/en/rest/orgs/blocking", output)
        self.assertIn(
            "https://docs.github.com/en/rest/orgs/orgs#get-immutable-releases-settings-for-an-organization",
            output,
        )
        self.assertIn(
            "https://docs.github.com/en/rest/orgs/orgs#list-selected-repositories-for-immutable-releases-enforcement",
            output,
        )
        self.assertIn("https://docs.github.com/en/rest/deploy-keys/deploy-keys", output)
        for anchor in (
            "check-if-dependabot-security-updates-are-enabled-for-a-repository",
            "check-if-immutable-releases-are-enabled-for-a-repository",
            "check-if-private-vulnerability-reporting-is-enabled-for-a-repository",
            "check-if-vulnerability-alerts-are-enabled-for-a-repository",
        ):
            self.assertIn(
                f"https://docs.github.com/en/rest/repos/repos#{anchor}", output
            )

    def test_comments_describe_ruleset_enums(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "rulesets": {
                    "mode": "exact",
                    "items": {
                        "Protected": {
                            "target": "branch",
                            "enforcement": "active",
                            "bypass_actors": [
                                {
                                    "actor_id": 7,
                                    "actor_type": "Team",
                                    "bypass_mode": "pull_request",
                                }
                            ],
                            "conditions": {
                                "repository_property": {
                                    "include": [
                                        {
                                            "name": "service",
                                            "property_values": ["api"],
                                            "source": "custom",
                                        }
                                    ]
                                }
                            },
                            "rules": [
                                {
                                    "type": "pull_request",
                                    "parameters": {
                                        "allowed_merge_methods": ["merge", "squash"],
                                        "dismissal_restriction": {
                                            "allowed_actors": [
                                                {"id": 7, "type": "Team"}
                                            ]
                                        },
                                    },
                                },
                                {
                                    "type": "commit_message_pattern",
                                    "parameters": {
                                        "operator": "regex",
                                        "pattern": "^change:",
                                    },
                                },
                                {
                                    "type": "code_scanning",
                                    "parameters": {
                                        "code_scanning_tools": [
                                            {
                                                "tool": "CodeQL",
                                                "alerts_threshold": "errors",
                                                "security_alerts_threshold": "high_or_higher",
                                            }
                                        ]
                                    },
                                },
                            ],
                        }
                    },
                }
            },
            "repositories": {
                "mode": "merge",
                "items": {
                    "widget": {
                        "rulesets": {
                            "mode": "exact",
                            "items": {
                                "Repository rules": {
                                    "target": "branch",
                                    "enforcement": "active",
                                    "rules": [
                                        {
                                            "type": "merge_queue",
                                            "parameters": {"merge_method": "SQUASH"},
                                        }
                                    ],
                                }
                            },
                        }
                    }
                },
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "# Values: `Integration`, `OrganizationAdmin`, `RepositoryRole`, `Team`, `DeployKey`, `User`.\n"
            "          # https://docs.github.com/en/rest/orgs/rules\n"
            "          actor_type: Team",
            output,
        )
        self.assertIn(
            "# Values: `always`, `pull_request`, or `exempt`. `pull_request` is valid only for branch rulesets and is invalid for `DeployKey`.",
            output,
        )
        self.assertIn("# Values: `custom`, `system`.", output)
        lines = output.splitlines()
        organization_type_line = next(
            index
            for index, line in enumerate(lines)
            if line.strip().endswith("type: pull_request")
        )
        repository_type_line = next(
            index
            for index, line in enumerate(lines)
            if line.strip().endswith("type: merge_queue")
        )
        self.assertIn(
            "`creation`, `update`, `deletion`", lines[organization_type_line - 2]
        )
        self.assertNotIn("`merge_queue`", lines[organization_type_line - 2])
        self.assertIn("`merge_queue`", lines[repository_type_line - 2])
        self.assertIn(
            "# Values: `User`, `Team`, `IntegrationInstallation`, `RepositoryRole`.",
            output,
        )
        self.assertIn(
            "# Values: `starts_with`, `ends_with`, `contains`, `regex`.", output
        )
        self.assertIn(
            "# Values: A YAML list containing one or more of `merge`, `squash`, and `rebase`.",
            output,
        )
        self.assertIn("# Values: `MERGE`, `SQUASH`, `REBASE`.", output)
        self.assertIn(
            "# Values: `none`, `errors`, `errors_and_warnings`, `all`.", output
        )
        self.assertIn(
            "# Values: `none`, `critical`, `high_or_higher`, `medium_or_higher`, `all`.",
            output,
        )

    def test_comments_match_constrained_and_partially_writable_settings(self) -> None:
        config = {
            "version": 1,
            "organization": {
                "members": {
                    "mode": "exact",
                    "items": {"alice": {"role": "member", "public": True}},
                },
                "teams": {
                    "mode": "exact",
                    "items": {"platform": {"settings": {"permission": "admin"}}},
                },
                "interaction_limit": {
                    "enabled": True,
                    "limit": "contributors_only",
                    "expiry": "one_week",
                },
                "actions": {
                    "secrets": {
                        "mode": "exact",
                        "items": {"DEPLOY_TOKEN": {"visibility": "private"}},
                    },
                    "hosted_runners": {
                        "mode": "exact",
                        "items": {
                            "linux": {
                                "name": "linux",
                                "image": {
                                    "id": "ubuntu-latest",
                                    "source": "github",
                                    "version": None,
                                },
                            }
                        },
                    },
                },
                "budgets": {
                    "mode": "exact",
                    "items": {
                        "Actions": {
                            "budget_scope": "organization",
                            "budget_type": "ProductPricing",
                        }
                    },
                },
                "custom_properties": {
                    "mode": "exact",
                    "items": {
                        "service": {
                            "value_type": "single_select",
                            "description": None,
                            "default_value": None,
                            "allowed_values": ["api", "worker"],
                        }
                    },
                },
                "hosted_compute": {
                    "network_configurations": {
                        "mode": "exact",
                        "items": {
                            "private": {
                                "compute_service": "actions",
                                "network_settings_ids": ["network-1"],
                                "failover_network_settings_ids": [],
                            }
                        },
                    }
                },
                "private_registries": {
                    "mode": "exact",
                    "items": {
                        "NPM_REGISTRY_SECRET": {
                            "auth_type": "token",
                            "visibility": "private",
                        }
                    },
                },
                "issue_fields": {
                    "mode": "exact",
                    "items": {
                        "Service": {
                            "data_type": "single_select",
                            "options": [
                                {
                                    "name": "API",
                                    "color": None,
                                    "priority": 1,
                                    "created_at": "2026-01-01T00:00:00Z",
                                    "updated_at": "2026-01-02T00:00:00Z",
                                }
                            ],
                        }
                    },
                },
                "secret_scanning": {
                    "pattern_configurations": {
                        "provider_pattern_settings": [
                            {
                                "token_type": "TOKEN",
                                "push_protection_setting": "not-set",
                            }
                        ],
                        "custom_pattern_settings": [
                            {
                                "token_type": "cp_1",
                                "push_protection_setting": "enabled",
                            }
                        ],
                    },
                    "custom_patterns": {
                        "mode": "exact",
                        "items": {
                            "Token": {"name": "Token", "pattern": "token_[0-9]+"}
                        },
                    },
                },
                "code_security": {
                    "configurations": {
                        "mode": "exact",
                        "items": {
                            "Default": {
                                "description": None,
                                "dependabot_delegated_alert_dismissal": None,
                                "code_scanning_default_setup_options": {
                                    "runner_type": None,
                                    "runner_label": None,
                                },
                                "code_scanning_options": {"allow_advanced": None},
                                "secret_scanning_delegated_bypass_options": {
                                    "reviewers": [
                                        {
                                            "reviewer_id": 7,
                                            "reviewer_type": "TEAM",
                                            "mode": "ALWAYS",
                                            "security_configuration_id": 4,
                                        }
                                    ]
                                },
                            }
                        },
                    }
                },
            },
            "repositories": {
                "mode": "merge",
                "items": {
                    "widget": {
                        "settings": {
                            "security_and_analysis": {
                                "advanced_security": {"status": "enabled"}
                            }
                        },
                        "branch_protections": {
                            "mode": "exact",
                            "items": {
                                "main": {
                                    "required_pull_request_reviews": {
                                        "required_approving_review_count": 6
                                    }
                                }
                            },
                        },
                        "environments": {
                            "mode": "exact",
                            "items": {
                                "production": {"settings": {"wait_timer": 43200}}
                            },
                        },
                        "workflow_states": {
                            "mode": "exact",
                            "items": {
                                ".github/workflows/ci.yml": "disabled_inactivity"
                            },
                        },
                        "rulesets": {
                            "mode": "exact",
                            "items": {
                                "Repository rules": {
                                    "target": "branch",
                                    "enforcement": "active",
                                    "rules": [
                                        {
                                            "type": "pull_request",
                                            "parameters": {
                                                "required_approving_review_count": 10
                                            },
                                        }
                                    ],
                                }
                            },
                        },
                        "custom_properties": {"_facts": "managed"},
                    }
                },
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "Only the authenticated user's own membership is writable", output
        )
        self.assertIn(
            "Creating a secret requires `visibility` and `value_from_env`", output
        )
        self.assertIn(
            "create-only; changing it requires replacing the registry", output
        )
        self.assertIn(
            "create-only; changing it requires replacing the issue field", output
        )
        self.assertIn(
            "exact mode reporting omissions and apply blocking their deletion", output
        )
        self.assertIn("replacing the pattern is required to change it", output)
        self.assertIn("Writable values are `active` and `disabled_manually`", output)
        self.assertIn("github-config never deleting workflow files", output)
        self.assertIn("# The value of the custom property named `_facts`.", output)
        self.assertNotIn(
            "The repository's observed facts value for policy selectors", output
        )
        self.assertIn("# Values: An integer from `0` through `6`.", output)
        self.assertIn(
            "# Values: An integer from `0` through `43200`, measured in minutes.",
            output,
        )
        self.assertIn(
            "# Values: `enabled` or `disabled`. Advanced Security is unavailable",
            output,
        )
        self.assertIn("GitHub may export the additional read value `null`", output)
        self.assertIn("# Values: `TEAM` or `ROLE`.", output)
        self.assertIn("This response metadata is not applied", output)
        self.assertIn(
            "# Values: `one_day`, `three_days`, `one_week`, `one_month`, `six_months`.",
            output,
        )
        self.assertIn(
            "Writable values are `organization`, `repository`, `multi_user_customer`, and `user`",
            output,
        )
        self.assertIn(
            "# Values: `BundlePricing`, `ProductPricing`, or `SkuPricing`.", output
        )
        self.assertIn(
            "Writable values are `none` and `actions`. GitHub may export the "
            "additional read value `codespaces`, which is not applied.",
            output,
        )
        self.assertIn("A YAML list containing exactly one network settings ID", output)
        self.assertIn("A YAML list containing zero or one network settings ID", output)
        self.assertIn("# Values: `github`, `partner`, `custom`.", output)
        self.assertIn("A custom image version string, or `null`", output)
        self.assertIn(
            "A YAML list of strings, or `null` when every value is allowed", output
        )
        self.assertIn("A string, a YAML list of strings, or `null`", output)
        self.assertIn("# Values: `not-set`, `disabled`, `enabled`.", output)
        self.assertIn(
            "# Values: `pull` or `push` when creating a team; `admin` can be set after creation.",
            output,
        )
        self.assertIn("# Values: An integer from `0` through `10`.", output)
        self.assertIn(
            "# Values: `branch`, `tag`, `push`.\n"
            "            # https://docs.github.com/en/rest/repos/rules\n"
            "            target: branch",
            output,
        )

    def test_comments_explain_cross_field_and_nullable_constraints(self) -> None:
        cases: tuple[tuple[tuple[str | int, ...], object, str], ...] = (
            (
                ("organization", "settings", "members_can_create_repositories"),
                True,
                "members_allowed_repository_creation_type` takes precedence",
            ),
            (
                ("organization", "settings", "default_repository_permission"),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "settings",
                    "members_can_fork_private_repositories",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "teams",
                    "items",
                    "api",
                    "settings",
                    "privacy",
                ),
                "secret",
                "team with a parent or child must use `closed`",
            ),
            (
                (
                    "organization",
                    "code_security",
                    "configurations",
                    "items",
                    "Default",
                    "description",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "code_security",
                    "configurations",
                    "items",
                    "Default",
                    "dependabot_delegated_alert_dismissal",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "code_security",
                    "configurations",
                    "items",
                    "Default",
                    "code_scanning_default_setup_options",
                    "runner_type",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "hosted_compute",
                    "network_configurations",
                    "items",
                    "codespaces",
                    "compute_service",
                ),
                "codespaces",
                "additional read value `codespaces`, which is not applied",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "code_scanning",
                    "default_setup",
                    "runner_type",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "code_scanning",
                    "default_setup",
                    "languages",
                ),
                ["javascript"],
                "which are not applied",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "code_quality",
                    "setup",
                    "languages",
                ),
                ["rust"],
                "additional read value `rust`, which is not applied",
            ),
            (
                ("organization", "budgets", "items", "Inherited", "budget_scope"),
                "enterprise",
                "Inherited scopes are not applied",
            ),
            (
                (
                    "organization",
                    "rulesets",
                    "items",
                    "Repository policy",
                    "conditions",
                ),
                None,
                "may export `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "dependabot",
                    "repository_access",
                    "default_level",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "settings",
                    "secret_scanning_push_protection_custom_link",
                ),
                "https://security.example",
                "additional read value `null`, which is not applied",
            ),
            (
                ("repositories", "items", "api", "settings", "description"),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "organization",
                    "teams",
                    "items",
                    "platform",
                    "settings",
                    "description",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "labels",
                    "items",
                    "bug",
                    "description",
                ),
                None,
                "additional read value `null`, which is not applied",
            ),
            (
                ("repositories", "items", "api", "settings", "has_projects"),
                True,
                "enabled for the organization",
            ),
            (
                (
                    "organization",
                    "actions",
                    "allowed_actions",
                    "patterns_allowed",
                ),
                ["acme/*"],
                "allowed_actions: selected",
            ),
            (
                ("organization", "actions", "selected_repositories"),
                ["api"],
                "organization.actions.permissions.enabled_repositories",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "actions",
                    "oidc_subject",
                    "include_claim_keys",
                ),
                ["repository_id"],
                "only letters, numbers, and underscores",
            ),
            (
                (
                    "organization",
                    "actions",
                    "runner_groups",
                    "items",
                    "linux",
                    "settings",
                    "selected_workflows",
                ),
                ["acme/api/.github/workflows/ci.yml@main"],
                "branch, tag, or full commit SHA",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "actions",
                    "artifact_and_log_retention",
                    "days",
                ),
                30,
                "private or internal repository",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "actions",
                    "cache_retention",
                    "max_cache_retention_days",
                ),
                30,
                "cannot exceed `organization.actions.cache_retention",
            ),
            (
                (
                    "organization",
                    "actions",
                    "variables",
                    "items",
                    "REGION",
                    "selected_repositories",
                ),
                ["api"],
                "valid only when `visibility` is `selected`",
            ),
            (
                ("organization", "budgets", "items", "AI", "budget_scope"),
                "organization",
                "Writable values are `organization`, `repository`",
            ),
            (
                ("organization", "budgets", "items", "AI", "budget_amount"),
                50,
                "whole-dollar integer of at least `0`",
            ),
            (
                (
                    "organization",
                    "private_registries",
                    "items",
                    "NPM_REGISTRY_SECRET",
                    "registry_type",
                ),
                "npm_registry",
                "uppercased value followed by `_SECRET`",
            ),
            (
                (
                    "organization",
                    "private_registries",
                    "items",
                    "NPM_REGISTRY_SECRET",
                    "value_from_env",
                ),
                "NPM_TOKEN",
                "must be omitted for OIDC authentication",
            ),
            (
                (
                    "organization",
                    "copilot",
                    "content_exclusion",
                    "visibility",
                ),
                ["/private/**"],
                "excluded path strings",
            ),
            (
                ("organization", "organization_roles", "mode"),
                "exact",
                "omitting a role revokes all of its assignments",
            ),
            (
                (
                    "organization",
                    "secret_scanning",
                    "custom_patterns",
                    "items",
                    "Token",
                    "must_match",
                ),
                ["token_[0-9]+"],
                "writable YAML list of regular-expression strings",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "secret_scanning",
                    "custom_patterns",
                    "items",
                    "Token",
                    "start_delimiter",
                ),
                None,
                "writable regular-expression string",
            ),
            (
                (
                    "organization",
                    "rulesets",
                    "items",
                    "Protected",
                    "bypass_actors",
                    0,
                    "actor_id",
                ),
                None,
                "`null` for `DeployKey`",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "rulesets",
                    "items",
                    "Protected",
                    "conditions",
                ),
                {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                "Repository rulesets do not accept repository selectors",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "branch_protections",
                    "items",
                    "main",
                    "required_status_checks",
                    "checks",
                    0,
                    "app_id",
                ),
                None,
                "GitHub may export `null`, which is not applied",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "environments",
                    "items",
                    "production",
                    "settings",
                    "deployment_branch_policy",
                ),
                None,
                "exactly one of `protected_branches`",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "pages",
                    "enabled",
                ),
                True,
                "also requires `source` or `build_type`",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "code_scanning",
                    "default_setup",
                    "languages",
                ),
                ["javascript-typescript"],
                "legacy read values `javascript` and `typescript`",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "code_quality",
                    "setup",
                    "languages",
                ),
                ["rust"],
                "additional read value `rust`",
            ),
            (
                (
                    "organization",
                    "issue_fields",
                    "items",
                    "Status",
                    "options",
                ),
                [],
                "update replaces the entire option set",
            ),
            (
                (
                    "organization",
                    "invitations",
                    "items",
                    "alice",
                    "login",
                ),
                "alice",
                "either this key or `email`",
            ),
            (
                (
                    "repositories",
                    "items",
                    "api",
                    "hooks",
                    "items",
                    "https://example.test/hook",
                    "events",
                ),
                ["push"],
                "replaces the entire subscription list",
            ),
        )

        for path, value, expected in cases:
            with self.subTest(path=path):
                self.assertIn(expected, _comment_for(path, value).values)

        content_exclusion_comment = _comment_for(
            ("organization", "copilot", "content_exclusion", "visibility"),
            ["/private/**"],
        )
        self.assertIn(
            "repository selector named `visibility`",
            content_exclusion_comment.description,
        )
        self.assertNotIn(
            "`public`, `private`, `internal`", content_exclusion_comment.values
        )

        event_comment = _comment_for(
            (
                "repositories",
                "items",
                "api",
                "hooks",
                "items",
                "https://example.test/hook",
                "events",
            ),
            ["push"],
        )
        self.assertEqual(
            event_comment.docs,
            "https://docs.github.com/en/webhooks/webhook-events-and-payloads",
        )

    def test_repository_mode_and_facts_comments_match_apply_behavior(self) -> None:
        config = {
            "version": 1,
            "repositories": {
                "mode": "exact",
                "items": {
                    "widget": {"_facts": {"archived": True, "visibility": "private"}}
                },
            },
        }

        output = _join_wrapped_comment_lines(dump_config(config, comments=True))

        self.assertIn(
            "# The merge behavior for explicitly listed repositories, with repositories omitted from items always left unchanged.\n"
            "  # Values: `merge` and `exact` are accepted. Both leave unlisted repositories unchanged because github-config never deletes repositories.\n"
            "  # https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            "  mode: exact",
            output,
        )
        self.assertIn(
            "# The repository's observed archived value for policy selectors, a read-only value not applied.\n"
            "        # Values: `true` or `false`. This read-only observation is not applied.\n"
            "        # https://docs.github.com/en/rest/repos/repos#update-a-repository\n"
            "        archived: true",
            output,
        )
        self.assertNotIn("Whether the repository is archived", output)

    def _assert_every_key_has_comments(self, node: Node, lines: list[str]) -> None:
        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                line = key_node.start_mark.line
                self.assertGreaterEqual(line, 3)
                self.assertIn("# https://docs.github.com/", lines[line - 1])
                self.assertNotIn("# Docs:", lines[line - 1])

                values_line = line - 2
                while values_line >= 0 and not lines[values_line].lstrip().startswith(
                    "# Values: "
                ):
                    self.assertTrue(lines[values_line].lstrip().startswith("# "))
                    values_line -= 1
                self.assertGreaterEqual(values_line, 1)
                self.assertIn("# Values: ", lines[values_line])

                description_line = values_line - 1
                self.assertTrue(lines[description_line].lstrip().startswith("# "))
                self.assertNotIn("# Controls:", lines[description_line])

                comment_line = description_line
                while comment_line > 0 and lines[comment_line - 1].lstrip().startswith(
                    "# "
                ):
                    comment_line -= 1
                for wrapped_line in lines[comment_line:line]:
                    if "# https://" not in wrapped_line:
                        self.assertLessEqual(len(wrapped_line), 80)
                    self.assertNotIn("# Controls:", wrapped_line)
                    self.assertNotIn("# Docs:", wrapped_line)
                self._assert_every_key_has_comments(value_node, lines)
        elif isinstance(node, SequenceNode):
            for item in node.value:
                self._assert_every_key_has_comments(item, lines)

    def test_dumped_configuration_round_trips(self) -> None:
        config = {
            "version": 1,
            "organization": {"settings": {"default_repository_permission": "read"}},
            "repositories": {"mode": "merge", "items": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(dump_config(config), encoding="utf-8")
            self.assertEqual(load_config(path), config)

    def test_unquoted_numeric_label_color_is_rejected(self) -> None:
        document = """\
version: 1
repositories:
  items:
    TestRepository:
      labels:
        items:
          xxx:
            color: 000000
"""

        with self.assertRaises(ConfigError) as raised:
            _load_document(document)

        self.assertIn(
            "repositories.items.TestRepository.labels.items.xxx.color must be a string",
            str(raised.exception),
        )
        self.assertIn(
            "quote the value if it is meant to be text", str(raised.exception)
        )

    def test_quoted_numeric_label_color_is_a_string(self) -> None:
        config = _load_document(
            """\
version: 1
repositories:
  items:
    TestRepository:
      labels:
        items:
          xxx:
            color: "000000"
"""
        )

        color = config["repositories"]["items"]["TestRepository"]["labels"]["items"][
            "xxx"
        ]["color"]
        self.assertEqual(color, "000000")

    def test_allow_forking_rejects_public_repository_visibility(self) -> None:
        document = """\
version: 1
repositories:
  items:
    widget:
      settings:
        allow_forking: false
        visibility: public
"""

        with self.assertRaises(ConfigError) as raised:
            _load_document(document)

        self.assertIn(
            "allow_forking can be managed only for organization-owned "
            "private or internal repositories",
            str(raised.exception),
        )

    def test_allow_forking_requires_the_organization_policy(self) -> None:
        document = """\
version: 1
organization:
  settings:
    members_can_fork_private_repositories: false
repositories:
  items:
    widget:
      settings:
        allow_forking: true
        visibility: private
"""

        with self.assertRaises(ConfigError) as raised:
            _load_document(document)

        self.assertIn(
            "repositories.items.widget.settings.allow_forking also requires "
            "organization.settings.members_can_fork_private_repositories to be "
            "true; it is false",
            str(raised.exception),
        )

    def test_allow_forking_false_can_accompany_organization_disablement(self) -> None:
        document = """\
version: 1
organization:
  settings:
    members_can_fork_private_repositories: false
repositories:
  items:
    widget:
      settings:
        allow_forking: false
        visibility: private
"""

        self.assertEqual(_load_document(document)["version"], 1)

    def test_repository_projects_require_the_organization_setting(self) -> None:
        document = """\
version: 1
organization:
  settings:
    has_repository_projects: false
repositories:
  items:
    widget:
      settings:
        has_projects: true
"""

        with self.assertRaises(ConfigError) as raised:
            _load_document(document)

        self.assertIn(
            "repositories.items.widget.settings.has_projects requires "
            "organization.settings.has_repository_projects to be true; it is false",
            str(raised.exception),
        )

    def test_allow_forking_accepts_private_or_unresolved_visibility(self) -> None:
        documents = (
            """\
version: 1
repositories:
  items:
    widget:
      settings:
        allow_forking: true
        visibility: private
""",
            """\
version: 1
repositories:
  items:
    widget:
      settings:
        allow_forking: true
      _facts:
        visibility: public
""",
            """\
version: 1
repositories:
  items:
    widget:
      settings:
        allow_forking: true
        visibility: internal
""",
            """\
version: 1
repository_policies:
  - match:
      visibility: public
    set:
      settings:
        allow_forking: true
        visibility: private
""",
        )

        for document in documents:
            with self.subTest(document=document):
                self.assertEqual(_load_document(document)["version"], 1)

    def test_allow_forking_comment_states_the_repository_requirement(self) -> None:
        output = _join_wrapped_comment_lines(
            dump_config(
                {
                    "version": 1,
                    "repositories": {
                        "items": {
                            "widget": {
                                "settings": {
                                    "allow_forking": True,
                                    "visibility": "private",
                                }
                            }
                        }
                    },
                },
                comments=True,
            )
        )

        self.assertIn(
            "# Values: `true` or `false`. This setting can be managed only for an "
            "organization-owned private or internal repository whose organization "
            "policy allows repository forking.",
            output,
        )

    def test_known_setting_scalar_types_are_checked(self) -> None:
        invalid_documents = {
            "boolean": (
                """\
version: 1
repositories:
  items:
    widget:
      settings:
        has_wiki: "false"
""",
                "repositories.items.widget.settings.has_wiki must be true or false",
            ),
            "integer": (
                """\
version: 1
organization:
  actions:
    artifact_and_log_retention:
      days: "30"
""",
                "organization.actions.artifact_and_log_retention.days must be an integer",
            ),
            "null boolean": (
                """\
version: 1
repositories:
  items:
    widget:
      settings:
        has_wiki: null
""",
                "repositories.items.widget.settings.has_wiki must be true or false",
            ),
            "null integer": (
                """\
version: 1
organization:
  actions:
    artifact_and_log_retention:
      days: null
""",
                "organization.actions.artifact_and_log_retention.days must be an integer",
            ),
            "null string": (
                """\
version: 1
repositories:
  items:
    widget:
      settings:
        visibility: null
""",
                "repositories.items.widget.settings.visibility must be a string",
            ),
            "list item": (
                """\
version: 1
repositories:
  items:
    widget:
      topics: [service, 17]
""",
                "repositories.items.widget.topics[1] must be a string",
            ),
            "custom property": (
                """\
version: 1
repositories:
  items:
    widget:
      custom_properties:
        service: 17
""",
                "repositories.items.widget.custom_properties.service must be a string",
            ),
            "code security enum": (
                """\
version: 1
organization:
  code_security:
    configurations:
      items:
        Default:
          private_vulnerability_reporting: 1
""",
                (
                    "organization.code_security.configurations.items.Default."
                    "private_vulnerability_reporting must be a string"
                ),
            ),
            "collection shorthand": (
                """\
version: 1
organization:
  teams:
    items:
      eng:
        members:
          items:
            alice: 17
""",
                "organization.teams.items.eng.members.items.alice must be a string",
            ),
            "mapping": (
                """\
version: 1
organization:
  settings: nope
""",
                "organization.settings must be a mapping",
            ),
            "nested mapping": (
                """\
version: 1
organization:
  budgets:
    items:
      bad:
        budget_alerting: yes
""",
                "organization.budgets.items.bad.budget_alerting must be a mapping",
            ),
            "mapping list": (
                """\
version: 1
repositories:
  items:
    widget:
      branch_protections:
        items:
          main:
            required_status_checks:
              checks: nope
""",
                (
                    "repositories.items.widget.branch_protections.items.main."
                    "required_status_checks.checks must be a list of mappings"
                ),
            ),
            "collection items mapping": (
                """\
version: 1
organization:
  teams:
    items: nope
""",
                "organization.teams.items must be a mapping",
            ),
            "organization setting string": (
                """\
version: 1
organization:
  settings:
    members_allowed_repository_creation_type: 17
""",
                (
                    "organization.settings.members_allowed_repository_creation_type "
                    "must be a string"
                ),
            ),
            "code security string": (
                """\
version: 1
organization:
  code_security:
    configurations:
      items:
        Default:
          code_scanning_default_setup: 17
""",
                (
                    "organization.code_security.configurations.items.Default."
                    "code_scanning_default_setup must be a string"
                ),
            ),
            "code security delegated string": (
                """\
version: 1
organization:
  code_security:
    configurations:
      items:
        Default:
          code_scanning_delegated_alert_dismissal: 17
""",
                (
                    "organization.code_security.configurations.items.Default."
                    "code_scanning_delegated_alert_dismissal must be a string"
                ),
            ),
            "security analysis child": (
                """\
version: 1
repositories:
  items:
    widget:
      settings:
        security_and_analysis:
          advanced_security: enabled
""",
                (
                    "repositories.items.widget.settings.security_and_analysis."
                    "advanced_security must be a mapping"
                ),
            ),
            "ruleset condition list": (
                """\
version: 1
repositories:
  items:
    widget:
      rulesets:
        items:
          Protected:
            conditions:
              ref_name:
                include: [7]
""",
                (
                    "repositories.items.widget.rulesets.items.Protected.conditions."
                    "ref_name.include[0] must be a string"
                ),
            ),
            "ruleset required checks": (
                """\
version: 1
repositories:
  items:
    widget:
      rulesets:
        items:
          Protected:
            rules:
              - type: required_status_checks
                parameters:
                  required_status_checks:
                    context: ci
""",
                (
                    "repositories.items.widget.rulesets.items.Protected.rules[0]."
                    "parameters.required_status_checks must be a list of mappings"
                ),
            ),
            "branch protection required checks": (
                """\
version: 1
repositories:
  items:
    widget:
      branch_protections:
        items:
          main:
            required_status_checks: []
""",
                (
                    "repositories.items.widget.branch_protections.items.main."
                    "required_status_checks must be a mapping"
                ),
            ),
            "ruleset parameter list": (
                """\
version: 1
repositories:
  items:
    widget:
      rulesets:
        items:
          Protected:
            rules:
              - type: file_path_restriction
                parameters:
                  restricted_file_paths: [7]
""",
                (
                    "repositories.items.widget.rulesets.items.Protected.rules[0]."
                    "parameters.restricted_file_paths[0] must be a string"
                ),
            ),
            "coding agent repository list": (
                """\
version: 1
organization:
  copilot:
    coding_agent_repositories: [widget, 17]
""",
                "organization.copilot.coding_agent_repositories[1] must be a string",
            ),
            "pages source mapping": (
                """\
version: 1
repositories:
  items:
    widget:
      pages:
        source: main
""",
                "repositories.items.widget.pages.source must be a mapping",
            ),
            "pages source null": (
                """\
version: 1
repositories:
  items:
    widget:
      pages:
        source: null
""",
                "repositories.items.widget.pages.source must be a mapping",
            ),
            "secret scanning mapping": (
                """\
version: 1
repositories:
  items:
    widget:
      secret_scanning: enabled
""",
                "repositories.items.widget.secret_scanning must be a mapping",
            ),
            "collaborators mapping": (
                """\
version: 1
repositories:
  items:
    widget:
      collaborators: enabled
""",
                "repositories.items.widget.collaborators must be a mapping",
            ),
            "custom property named items": (
                """\
version: 1
repositories:
  items:
    widget:
      custom_properties:
        items: 17
""",
                "repositories.items.widget.custom_properties.items must be a string",
            ),
            "personal access token permission": (
                """\
version: 1
organization:
  personal_access_tokens:
    items:
      alice:automation:
        permissions:
          repository:
            actions: 17
""",
                (
                    "organization.personal_access_tokens.items.alice:automation."
                    "permissions.repository.actions must be a string"
                ),
            ),
            "repository policy key": (
                """\
version: 1
repository_policies:
  - 7: value
    set: {}
""",
                "repository_policies[0] must use string keys",
            ),
            "repository policy selector key": (
                """\
version: 1
repository_policies:
  - match:
      7: value
    set: {}
""",
                "repository_policies[0].match must use string keys",
            ),
        }

        for label, (document, message) in invalid_documents.items():
            with self.subTest(label=label):
                with self.assertRaises(ConfigError) as raised:
                    _load_document(document)
                self.assertIn(message, str(raised.exception))

    def test_repository_name_does_not_change_setting_type_context(self) -> None:
        config = _load_document(
            """\
version: 1
repositories:
  items:
    configurations:
      secret_scanning:
        custom_patterns:
          items: {}
"""
        )

        self.assertIn("configurations", config["repositories"]["items"])

    def test_repository_policy_selectors_accept_string_lists(self) -> None:
        config = _load_document(
            """\
version: 1
repository_policies:
  - match:
      name: [api-*, web-*]
      exclude: [archive-*]
      topics_all: [service]
    set:
      settings:
        has_wiki: false
"""
        )

        self.assertEqual(
            config["repository_policies"][0]["match"]["name"],
            ["api-*", "web-*"],
        )

    def test_overloaded_setting_names_use_their_full_paths(self) -> None:
        config = _load_document(
            """\
version: 1
organization:
  actions:
    permissions:
      allowed_actions: selected
    allowed_actions:
      github_owned_allowed: true
repositories:
  items:
    widget:
      custom_properties:
        mode: [one, two]
        items: "17"
      security:
        immutable_releases: true
"""
        )

        self.assertEqual(
            config["organization"]["actions"]["permissions"]["allowed_actions"],
            "selected",
        )
        self.assertTrue(
            config["repositories"]["items"]["widget"]["security"]["immutable_releases"]
        )
        self.assertEqual(
            config["repositories"]["items"]["widget"]["custom_properties"]["mode"],
            ["one", "two"],
        )

    def test_personal_access_token_permission_names_are_dynamic(self) -> None:
        config = _load_document(
            """\
version: 1
organization:
  personal_access_tokens:
    items:
      alice:automation:
        permissions:
          organization:
            members: read
          repository:
            actions: write
            codespaces: read
            environments: write
            pages: write
            secrets: read
            workflows: write
"""
        )

        permissions = config["organization"]["personal_access_tokens"]["items"][
            "alice:automation"
        ]["permissions"]
        self.assertEqual(permissions["repository"]["workflows"], "write")

    def test_app_installation_repository_selection_types_are_checked(self) -> None:
        with self.assertRaisesRegex(
            ConfigError,
            "selected_repositories\\[0\\] must be a string",
        ):
            _load_document(
                """\
version: 1
organization:
  app_installations:
    items:
      deploy:
        selected_repositories: [17]
"""
            )

        with self.assertRaisesRegex(
            ConfigError,
            "permissions.contents must be a string",
        ):
            _load_document(
                """\
version: 1
organization:
  app_installations:
    items:
      deploy:
        permissions:
          contents: true
"""
            )

    def test_repository_policies_apply_in_order_and_explicit_values_win(self) -> None:
        current = {
            "api": {
                "settings": {"visibility": "private", "archived": False},
                "topics": ["service"],
                "_facts": {"fork": False},
            },
            "old": {
                "settings": {"visibility": "private", "archived": True},
                "topics": ["service"],
                "_facts": {"fork": True},
            },
        }
        config = {
            "repository_policies": [
                {
                    "match": {"name": "*", "visibility": "private", "archived": False},
                    "set": {
                        "settings": {"delete_branch_on_merge": True, "has_wiki": False}
                    },
                },
                {
                    "match": {"topics_all": ["service"]},
                    "set": {"settings": {"has_wiki": True}},
                },
            ],
            "repositories": {
                "items": {"api": {"settings": {"has_wiki": False}}},
            },
        }
        desired = desired_repositories(config, current)
        self.assertEqual(
            desired["api"]["settings"],
            {"delete_branch_on_merge": True, "has_wiki": False},
        )
        self.assertEqual(desired["old"]["settings"], {"has_wiki": True})

    def test_unknown_selector_is_rejected(self) -> None:
        config = {
            "repository_policies": [
                {"match": {"owner": "acme"}, "set": {"settings": {}}}
            ],
        }
        with self.assertRaisesRegex(ConfigError, "unknown selectors"):
            desired_repositories(config, {"repo": {"settings": {}}})

    def test_fork_selector_uses_observed_repository_facts(self) -> None:
        config = {
            "repository_policies": [
                {
                    "match": {"fork": True},
                    "set": {"settings": {"has_wiki": False}},
                }
            ]
        }
        current = {
            "fork": {"settings": {}, "_facts": {"fork": True}},
            "source": {"settings": {}, "_facts": {"fork": False}},
        }
        self.assertEqual(
            desired_repositories(config, current),
            {"fork": {"settings": {"has_wiki": False}}},
        )

    def test_renamed_explicit_repository_absorbs_matching_policy_settings(
        self,
    ) -> None:
        config = {
            "repository_policies": [
                {
                    "match": {"name": "*"},
                    "set": {"settings": {"has_wiki": False}},
                }
            ],
            "repositories": {"items": {"old-name": {"settings": {"name": "new-name"}}}},
        }
        current = {
            "new-name": {"settings": {"name": "new-name"}, "_facts": {}},
        }
        self.assertEqual(
            desired_repositories(config, current),
            {"old-name": {"settings": {"has_wiki": False, "name": "new-name"}}},
        )

    def test_deep_merge_does_not_mutate_inputs(self) -> None:
        base = {"a": {"b": 1}}
        overlay = {"a": {"c": 2}}
        self.assertEqual(deep_merge(base, overlay), {"a": {"b": 1, "c": 2}})
        self.assertEqual(base, {"a": {"b": 1}})

    def test_collection_rejects_misspelled_wrapper_key(self) -> None:
        config = {
            "version": 1,
            "repositories": {"mods": "exact", "items": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(dump_config(config), encoding="utf-8")
            loaded = load_config(path)
            with self.assertRaisesRegex(ConfigError, "unknown collection keys"):
                desired_repositories(loaded, {})

    def test_repository_rejects_both_branch_protection_representations(
        self,
    ) -> None:
        config = {
            "version": 1,
            "repositories": {
                "items": {
                    "api": {
                        "branch_protections": {"items": {}},
                        "branch_protection_rules": {"items": {}},
                    }
                }
            },
        }

        with self.assertRaisesRegex(
            ConfigError,
            "cannot contain both branch_protections and branch_protection_rules",
        ):
            validate_config_semantics(config)

    def test_export_comments_label_read_only_values_and_incomplete_exports(
        self,
    ) -> None:
        config = {
            "version": 1,
            "organization": {
                "settings": {"two_factor_requirement_enabled": True},
                "ip_allow_list": {"enabled": True},
            },
        }

        output = dump_config(
            config,
            comments=True,
            read_only_fields={
                ("organization", "settings", "two_factor_requirement_enabled"): (
                    "GitHub does not provide a public update operation."
                )
            },
            caveats={
                ("organization", "ip_allow_list"): (
                    "The user-level enforcement input is write-only."
                )
            },
        )

        self.assertIn(
            "# Read-only. GitHub does not provide a public update operation.",
            output,
        )
        self.assertIn(
            "# Export caveat. The user-level enforcement input is write-only.",
            output,
        )
        self.assertTrue(
            all(
                len(line) <= 80
                for line in output.splitlines()
                if line.lstrip().startswith("#")
            )
        )

    def test_credential_authorization_comments_explain_one_way_revocation(
        self,
    ) -> None:
        key = "alice:SSH key:SHA256:example"
        config = {
            "version": 1,
            "organization": {
                "credential_authorizations": {
                    "mode": "exact",
                    "items": {
                        key: {
                            "login": "alice",
                            "credential_type": "SSH key",
                        }
                    },
                }
            },
        }

        output = _join_wrapped_comment_lines(
            dump_config(
                config,
                comments=True,
                read_only_fields={
                    (
                        "organization",
                        "credential_authorizations",
                        "items",
                        key,
                        "credential_type",
                    ): "GitHub can only revoke the whole authorization."
                },
            )
        )

        self.assertIn("exact` revokes omitted authorizations", output)
        self.assertIn(
            "# Read-only. GitHub can only revoke the whole authorization.",
            output,
        )
        self.assertIn("the owner must authorize the credential again", output)


if __name__ == "__main__":
    unittest.main()
