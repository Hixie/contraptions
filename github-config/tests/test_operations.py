from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

from nacl.public import PrivateKey, SealedBox

from github_config.operations import FieldChange, Operation, preflight_operations

from .fakes import FakeApi


class OperationTest(unittest.TestCase):
    def test_captures_string_id_from_a_nested_response(self) -> None:
        api = FakeApi(
            responses={
                ("POST", "/budgets"): {"budget": {"id": "budget-123"}},
            }
        )
        operation = Operation(
            "POST",
            "/budgets",
            [FieldChange("organization.budgets.actions", None, {}, "add")],
            body={},
            capture_id=("budgets", "actions"),
            capture_response_path=("budget", "id"),
        )
        ids: dict[tuple[str, ...], int | str] = {}
        operation.execute(api, ids)
        self.assertEqual(ids[("budgets", "actions")], "budget-123")

    def test_captures_additional_response_values_for_later_endpoints(self) -> None:
        api = FakeApi(
            responses={
                ("POST", "/orgs/acme/teams"): {
                    "id": 7,
                    "slug": "engineering-team",
                },
                ("PATCH", "/orgs/acme/teams/engineering-team"): {},
            }
        )
        create = Operation(
            "POST",
            "/orgs/acme/teams",
            [FieldChange("organization.teams.eng", None, {}, "add")],
            body={"name": "Engineering Team"},
            capture_id=("teams", "eng"),
            capture_response_values=[(("team_slugs", "eng"), ("slug",))],
        )
        update = Operation(
            "PATCH",
            "/orgs/acme/teams/__TEAM_SLUG__",
            [FieldChange("organization.teams.eng.permission", "pull", "admin")],
            body={"permission": "admin"},
            endpoint_id_references=[("__TEAM_SLUG__", ("team_slugs", "eng"))],
        )
        ids: dict[tuple[str, ...], int | str] = {}

        preflight_operations([create, update], ids)
        create.execute(api, ids)
        update.execute(api, ids)

        self.assertIsNone(update.blocked_reason)
        self.assertEqual(ids[("teams", "eng")], 7)
        self.assertEqual(ids[("team_slugs", "eng")], "engineering-team")
        self.assertEqual(api.requests[-1][1], "/orgs/acme/teams/engineering-team")

    def test_preflight_checks_every_reference_before_execution(self) -> None:
        operation = Operation(
            "PUT",
            "/orgs/acme/teams/__TEAM__",
            [FieldChange("organization.teams.platform", None, "add")],
            body={},
            endpoint_id_references=[("__TEAM__", ("teams", "platform"))],
            body_id_list_references=[(("repository_ids",), [("repositories", "api")])],
        )
        preflight_operations([operation], {("teams", "platform"): 1})
        self.assertEqual(
            operation.blocked_reason,
            "no GitHub ID is known for repositories/api",
        )

    def test_preflight_accepts_an_id_created_by_the_same_plan(self) -> None:
        create = Operation(
            "POST",
            "/orgs/acme/repos",
            [FieldChange("repositories.api", None, {}, "add")],
            body={"name": "api"},
            capture_id=("repositories", "api"),
        )
        use = Operation(
            "PUT",
            "/orgs/acme/actions/permissions/repositories",
            [FieldChange("organization.actions.repositories", [], ["api"])],
            body={},
            body_id_list_references=[
                (("selected_repository_ids",), [("repositories", "api")])
            ],
        )
        preflight_operations([create, use], {})
        self.assertIsNone(use.blocked_reason)

    def test_preflight_rejects_a_reference_before_its_create_operation(self) -> None:
        use = Operation(
            "PUT",
            "/orgs/acme/actions/permissions/repositories",
            [FieldChange("organization.actions.repositories", [], ["api"])],
            body={},
            body_id_list_references=[
                (("selected_repository_ids",), [("repositories", "api")])
            ],
        )
        create = Operation(
            "POST",
            "/orgs/acme/repos",
            [FieldChange("repositories.api", None, {}, "add")],
            body={"name": "api"},
            capture_id=("repositories", "api"),
        )
        preflight_operations([use, create], {})
        self.assertEqual(
            use.blocked_reason,
            "no GitHub ID is known for repositories/api",
        )

    def test_resolves_ids_and_environment_fields_without_putting_values_in_plan(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("PUT", "/resource"): None,
            }
        )
        operation = Operation(
            "PUT",
            "/resource",
            [FieldChange("resource", None, "configured", "add")],
            body={"reviewer": {"id": None}, "config": {}},
            body_id_references=[(("reviewer", "id"), ("users", "alice"))],
            environment_fields=[(("config", "secret"), "WEBHOOK_SECRET")],
        )
        with patch.dict(os.environ, {"WEBHOOK_SECRET": "hidden"}, clear=True):
            operation.execute(api, {("users", "alice"): 42})
        self.assertEqual(
            api.requests[-1][2],
            {"reviewer": {"id": 42}, "config": {"secret": "hidden"}},
        )
        self.assertNotIn("hidden", repr(operation))

    def test_encrypts_secret_with_github_public_key(self) -> None:
        private_key = PrivateKey.generate()
        public_key = base64.b64encode(bytes(private_key.public_key)).decode()
        api = FakeApi(
            responses={
                ("GET", "/secrets/public-key"): {"key": public_key, "key_id": "key-1"},
                ("PUT", "/secrets/TOKEN"): None,
            }
        )
        operation = Operation(
            "PUT",
            "/secrets/TOKEN",
            [FieldChange("secrets.TOKEN", None, "$TOKEN", "add", sensitive=True)],
            body={},
            secret_environment="TOKEN",
            secret_public_key_endpoint="/secrets/public-key",
        )
        with patch.dict(os.environ, {"TOKEN": "top secret"}, clear=True):
            operation.execute(api, {})
        body = api.requests[-1][2]
        decrypted = (
            SealedBox(private_key)
            .decrypt(base64.b64decode(body["encrypted_value"]))
            .decode()
        )
        self.assertEqual(decrypted, "top secret")
        self.assertEqual(body["key_id"], "key-1")

    def test_executes_graphql_with_resolved_input_ids_and_captures_node_id(
        self,
    ) -> None:
        api = FakeApi(
            responses={
                ("GRAPHQL", "CreateThing"): {
                    "createThing": {"thing": {"id": "THING_1"}}
                }
            }
        )
        operation = Operation(
            "POST",
            "/graphql#CreateThing",
            [FieldChange("organization.things.one", None, {}, "add")],
            body={"input": {"ownerId": None}},
            body_id_references=[(("input", "ownerId"), ("organization", "node_id"))],
            capture_id=("things", "one"),
            capture_response_path=("createThing", "thing", "id"),
            graphql_document=(
                "mutation CreateThing($input: CreateThingInput!) "
                "{ createThing(input: $input) { thing { id } } }"
            ),
        )
        ids: dict[tuple[str, ...], int | str] = {("organization", "node_id"): "ORG_1"}

        operation.execute(api, ids)

        self.assertEqual(
            api.requests[-1],
            ("GRAPHQL", "CreateThing", {"input": {"ownerId": "ORG_1"}}),
        )
        self.assertEqual(ids[("things", "one")], "THING_1")


if __name__ == "__main__":
    unittest.main()
