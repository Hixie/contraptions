from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_config.cli import run

from .fakes import FakeApi


def api() -> FakeApi:
    return FakeApi(
        responses={
            ("GET", "/orgs/acme"): {
                "id": 1,
                "login": "acme",
                "default_repository_permission": "write",
            },
            ("PATCH", "/orgs/acme"): None,
        },
        lists={"/orgs/acme/repos?type=all&sort=full_name": []},
    )


class CliTest(unittest.TestCase):
    def test_no_command_prints_help(self) -> None:
        stdout = io.StringIO()

        status = run([], stdout=stdout, stderr=io.StringIO())

        self.assertEqual(status, 0)
        self.assertIn("usage: github-config", stdout.getvalue())
        self.assertIn("GH_TOKEN", stdout.getvalue())
        self.assertIn("GITHUB_TOKEN", stdout.getvalue())
        self.assertIn("gh auth token", stdout.getvalue())

    def test_conflicting_environment_tokens_fail_without_revealing_them(self) -> None:
        stderr = io.StringIO()
        environment = {
            "GH_TOKEN": "first-secret-token",
            "GITHUB_TOKEN": "second-secret-token",
        }

        with patch.dict(os.environ, environment, clear=True):
            status = run(
                ["export", "acme"],
                stdout=io.StringIO(),
                stderr=stderr,
            )

        self.assertEqual(status, 1)
        self.assertIn("GH_TOKEN and GITHUB_TOKEN", stderr.getvalue())
        self.assertIn("different values", stderr.getvalue())
        self.assertNotIn("first-secret-token", stderr.getvalue())
        self.assertNotIn("second-secret-token", stderr.getvalue())

    def test_export_includes_key_comments_by_default(self) -> None:
        stdout = io.StringIO()
        with patch("github_config.cli._api_from_args", return_value=api()):
            status = run(
                ["export", "acme"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        self.assertEqual(status, 0)
        self.assertIn(
            "# The base repository permission granted to organization members.",
            stdout.getvalue(),
        )
        self.assertIn(
            "# https://docs.github.com/en/rest/orgs/orgs#update-an-organization",
            stdout.getvalue(),
        )
        self.assertNotIn("# Controls:", stdout.getvalue())
        self.assertNotIn("# Docs:", stdout.getvalue())

    def test_export_no_comments_emits_compact_yaml(self) -> None:
        stdout = io.StringIO()
        with patch("github_config.cli._api_from_args", return_value=api()):
            status = run(
                ["export", "acme", "--no-comments"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        self.assertEqual(status, 0)
        self.assertNotIn("# The github-config file format version.", stdout.getvalue())
        self.assertNotIn("# Values:", stdout.getvalue())
        self.assertNotIn("# https://docs.github.com/", stdout.getvalue())

    def test_diff_returns_one_when_current_state_is_unavailable(self) -> None:
        fake_api = api()
        config = """\
version: 1
organization:
  actions:
    secrets:
      items:
        NEW_TOKEN:
          visibility: all
          value_from_env: NEW_TOKEN
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(config, encoding="utf-8")
            stdout = io.StringIO()
            with patch("github_config.cli._api_from_args", return_value=fake_api):
                status = run(
                    ["diff", str(path), "acme", "--color", "never"],
                    stdout=stdout,
                    stderr=io.StringIO(),
                )
        self.assertEqual(status, 1)
        self.assertIn("current GitHub state was unavailable", stdout.getvalue())

    def test_diff_prints_human_readable_change_and_returns_two(self) -> None:
        fake_api = api()
        config = """\
version: 1
organization:
  settings:
    default_repository_permission: read
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(config, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("github_config.cli._api_from_args", return_value=fake_api):
                status = run(
                    ["diff", str(path), "acme", "--color", "never"],
                    stdout=stdout,
                    stderr=stderr,
                )
        self.assertEqual(status, 2)
        self.assertIn(
            "organization.settings.default_repository_permission", stdout.getvalue()
        )
        self.assertIn('"write"  ->  "read"', stdout.getvalue())

    def test_diff_and_apply_validate_config_before_loading_credentials(self) -> None:
        config = """\
version: 1
repositories:
  items:
    TestRepository:
      labels:
        items:
          xxx:
            color: 000000
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(config, encoding="utf-8")
            for command in ("diff", "apply"):
                with self.subTest(command=command):
                    stderr = io.StringIO()
                    with patch("github_config.cli._api_from_args") as api_factory:
                        status = run(
                            [command, str(path), "acme", "--color", "never"],
                            stdout=io.StringIO(),
                            stderr=stderr,
                        )

                    self.assertEqual(status, 1)
                    api_factory.assert_not_called()
                    self.assertIn(
                        "repositories.items.TestRepository.labels.items.xxx.color "
                        "must be a string",
                        stderr.getvalue(),
                    )

    def test_diff_and_apply_validate_setting_requirements_before_credentials(
        self,
    ) -> None:
        config = """\
version: 1
repositories:
  items:
    TestingRepository:
      settings:
        allow_forking: false
        visibility: public
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(config, encoding="utf-8")
            for command in ("diff", "apply"):
                with self.subTest(command=command):
                    stderr = io.StringIO()
                    with patch("github_config.cli._api_from_args") as api_factory:
                        status = run(
                            [command, str(path), "acme", "--color", "never"],
                            stdout=io.StringIO(),
                            stderr=stderr,
                        )

                    self.assertEqual(status, 1)
                    api_factory.assert_not_called()
                    self.assertIn(
                        "repositories.items.TestingRepository.settings."
                        "allow_forking can be managed only for "
                        "organization-owned private or internal repositories",
                        stderr.getvalue(),
                    )

    def test_apply_executes_the_reviewed_plan(self) -> None:
        fake_api = api()
        config = """\
version: 1
organization:
  settings:
    default_repository_permission: read
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(config, encoding="utf-8")
            with patch("github_config.cli._api_from_args", return_value=fake_api):
                status = run(
                    ["apply", str(path), "acme", "--yes", "--color", "never"],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
        self.assertEqual(status, 0)
        self.assertIn(
            ("PATCH", "/orgs/acme", {"default_repository_permission": "read"}),
            fake_api.requests,
        )


if __name__ == "__main__":
    unittest.main()
