"""The code against its own contract: every operation is a function, every answer matches openapi.yaml."""

import json
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).parent.parent
OPENAPI = yaml.safe_load((ROOT / "openapi.yaml").read_text())


def assert_matches(response, operation_id: str) -> dict:
    """The response's status, media type and body are what the contract says for this operation."""
    op = next(op for item in OPENAPI["paths"].values() for op in item.values() if isinstance(op, dict) and op.get("operationId") == operation_id)
    declared = op["responses"].get(str(response.status_code))
    assert declared, f"{operation_id} does not declare {response.status_code}"
    (media, content), = declared["content"].items()
    assert response.mimetype == media
    body = json.loads(response.get_body())
    errors = list(Draft7Validator({**content["schema"], "components": OPENAPI["components"]}).iter_errors(body))
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors]
    return body


def test_every_operation_is_a_function():
    import function_app
    names = {f.get_function_name() for f in function_app.app.get_functions()}
    operations = {op["operationId"] for item in OPENAPI["paths"].values() for op in item.values() if isinstance(op, dict)}
    assert operations == names
