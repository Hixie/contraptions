from __future__ import annotations

import os
import unittest
from typing import cast
from unittest.mock import patch

from github_config.api import (
    ApiError,
    GitHubApi,
    GraphqlData,
    Response,
    _graphql_operation_name,
    _graphql_url,
    _next_link,
    _with_per_page,
    ambient_api,
)


class ApiHelpersTest(unittest.TestCase):
    def test_builds_graphql_urls_for_github_and_github_enterprise_server(
        self,
    ) -> None:
        self.assertEqual(
            _graphql_url("https://api.github.com"),
            "https://api.github.com/graphql",
        )
        self.assertEqual(
            _graphql_url("https://github.example/api/v3"),
            "https://github.example/api/graphql",
        )

    def test_extracts_graphql_operation_name(self) -> None:
        self.assertEqual(
            _graphql_operation_name("mutation UpdateThing($input: ID!) { x }"),
            "UpdateThing",
        )

    def test_graphql_query_retains_partial_data_and_errors(self) -> None:
        api = GitHubApi("token")
        response = Response(
            200,
            {
                "data": {"organization": {"login": "acme"}},
                "errors": [{"message": "SAML settings are not accessible"}],
            },
            {},
        )
        with patch.object(api, "request", return_value=response):
            data = api.graphql(
                'query Organization { organization(login: "acme") { login } }'
            )

        self.assertIsInstance(data, GraphqlData)
        self.assertEqual(data["organization"]["login"], "acme")
        self.assertEqual(
            cast(GraphqlData, data).errors,
            ("SAML settings are not accessible",),
        )

    def test_graphql_mutation_rejects_partial_errors(self) -> None:
        api = GitHubApi("token")
        response = Response(
            200,
            {
                "data": {"updateRepository": None},
                "errors": [{"message": "not allowed"}],
            },
            {},
        )
        with (
            patch.object(api, "request", return_value=response),
            self.assertRaises(ApiError),
        ):
            api.graphql(
                "mutation UpdateRepository { updateRepository(input: {}) "
                "{ repository { id } } }"
            )

    def test_adds_per_page_without_losing_existing_query(self) -> None:
        self.assertEqual(
            _with_per_page("/orgs/acme/repos?type=all"),
            "/orgs/acme/repos?type=all&per_page=100",
        )

    def test_reads_next_pagination_link_case_insensitively(self) -> None:
        headers = {
            "LINK": '<https://api.github.com/items?page=2>; rel="next", '
            '<https://api.github.com/items?page=4>; rel="last"'
        }
        self.assertEqual(_next_link(headers), "https://api.github.com/items?page=2")

    def test_ambient_token_accepts_matching_environment_tokens(self) -> None:
        environment = {
            "GH_TOKEN": "same-token",
            "GITHUB_TOKEN": "same-token",
            "GH_API_URL": "https://github.example/api/v3",
        }
        with patch.dict(os.environ, environment, clear=True):
            api = ambient_api()
        self.assertIsInstance(api, GitHubApi)
        self.assertEqual(api.api_url, "https://github.example/api/v3")

    def test_ambient_token_rejects_conflicting_environment_tokens(self) -> None:
        environment = {
            "GH_TOKEN": "first-token",
            "GITHUB_TOKEN": "second-token",
        }

        with (
            patch.dict(os.environ, environment, clear=True),
            self.assertRaises(RuntimeError) as raised,
        ):
            ambient_api()

        self.assertIn(
            "GH_TOKEN and GITHUB_TOKEN are both set but have different values",
            str(raised.exception),
        )
        self.assertNotIn("first-token", str(raised.exception))
        self.assertNotIn("second-token", str(raised.exception))

    def test_empty_environment_tokens_are_treated_as_unset(self) -> None:
        cases = (
            ({"GH_TOKEN": "", "GITHUB_TOKEN": "usable-token"}, "usable-token", False),
            ({"GH_TOKEN": "usable-token", "GITHUB_TOKEN": ""}, "usable-token", False),
            ({"GH_TOKEN": "", "GITHUB_TOKEN": ""}, "gh-token", True),
        )

        for environment, expected, uses_gh in cases:
            with self.subTest(environment=environment):
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch(
                        "github_config.api._token_from_gh", return_value="gh-token"
                    ) as token_from_gh,
                ):
                    api = ambient_api()

                self.assertEqual(api._token, expected)
                self.assertEqual(token_from_gh.called, uses_gh)


if __name__ == "__main__":
    unittest.main()
