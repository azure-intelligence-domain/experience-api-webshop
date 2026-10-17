"""What every Experience API needs from the platform, the same in each Experience API repository:
responses (HAL, Problem Details), the journey (Correlation-Id, traceparent), the contract as the
validator, and the Process and System APIs behind the gateway, called as this API's runtime
identity with an Entra token (ADR 0014, 0017). No keys anywhere. The channel's own logic lives next
to this file.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache, wraps
from pathlib import Path

import azure.functions as func
import yaml
from azure.identity import DefaultAzureCredential
from jsonschema import Draft7Validator

SPEC = yaml.safe_load(Path(__file__).with_name("openapi.yaml").read_text())
REPOSITORY = SPEC["info"]["contact"]["url"]
PROBLEMS = REPOSITORY + "/blob/main/docs/problems/"


@cache
def credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"))


# --- The contract is the validator --------------------------------------------------------------


def _validator(schema: dict) -> Draft7Validator:
    return Draft7Validator({**schema, "components": SPEC["components"]}, format_checker=Draft7Validator.FORMAT_CHECKER)


SCHEMAS = {name: _validator(schema) for name, schema in SPEC["components"]["schemas"].items()}
CORRELATION = _validator(SPEC["components"]["parameters"]["correlationId"]["schema"])


def violations(validator: Draft7Validator, document) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or 'body'}: {e.message}"
            for e in sorted(validator.iter_errors(document), key=lambda e: list(map(str, e.absolute_path)))]


# --- Responses ----------------------------------------------------------------------------------


def hal(status: int, body: dict, headers: dict | None = None) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/hal+json", headers=headers)


def problem(status: int, title: str, detail: str, slug: str, req: func.HttpRequest) -> func.HttpResponse:
    body = {"type": PROBLEMS + slug + ".md", "title": title, "status": status, "detail": detail, "instance": req.url}
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/problem+json")


class Refused(Exception):
    """A request the contract or the business refuses; carries its Problem Details response."""

    def __init__(self, response: func.HttpResponse):
        self.response = response


def refused(status: int, title: str, detail: str, slug: str, req: func.HttpRequest) -> Refused:
    return Refused(problem(status, title, detail, slug, req))


def invalid(detail: str, req: func.HttpRequest) -> Refused:
    return refused(400, "Invalid Request", detail, "invalid-request", req)


def refusals(handler):
    @wraps(handler)
    def handle(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Refused as r:
            return r.response
    return handle


# --- Requests -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Journey:
    correlation_id: str
    traceparent: str | None = None


def journey(req: func.HttpRequest) -> Journey:
    """The Correlation-Id (required) and the W3C trace context to pass on: one trace per journey."""
    correlation_id = req.headers.get("Correlation-Id", "")
    if violations(CORRELATION, correlation_id):
        raise invalid("The Correlation-Id header is missing or invalid, for example ord-4711.", req)
    logging.info("Correlation-Id %s: %s %s", correlation_id, req.method, req.url)
    return Journey(correlation_id, req.headers.get("traceparent"))


def body_of(req: func.HttpRequest, schema: str) -> dict:
    try:
        body = req.get_json()
    except ValueError:
        raise invalid("The body is not JSON.", req) from None
    errors = violations(SCHEMAS[schema], body)
    if errors:
        raise invalid("; ".join(errors), req)
    return body


# --- The platform's APIs, through the gateway ---------------------------------------------------


@dataclass(frozen=True)
class Answer:
    status: int
    body: dict

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Platform:
    """Process and System APIs on apim-intplat-demo, as this API's runtime identity (products
    process-apis and system-apis). Every call carries the journey."""

    def __init__(self, gateway: str, audience: str, journey: Journey):
        self.gateway, self.scope, self.journey = gateway.rstrip("/"), f"{audience}/.default", journey

    @classmethod
    def from_environment(cls, journey: Journey) -> "Platform":
        return cls(os.environ["GATEWAY_URL"], os.environ["PLATFORM_APIS_AUDIENCE"], journey)

    def call(self, method: str, path: str, body=None, query: dict | None = None, headers: dict | None = None) -> Answer:
        query = {k: v for k, v in (query or {}).items() if v is not None}
        url = self.gateway + path + ("?" + urllib.parse.urlencode(query) if query else "")
        token = credential().get_token(self.scope).token
        sent = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                "Correlation-Id": self.journey.correlation_id, **(headers or {})}
        if self.journey.traceparent:
            sent["traceparent"] = self.journey.traceparent
        request = urllib.request.Request(url, method=method, headers=sent,
                                         data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return Answer(response.status, json.loads(response.read() or b"{}"))
        except urllib.error.HTTPError as error:
            return Answer(error.code, json.loads(error.read() or b"{}"))

    def all(self, *calls: tuple) -> list[Answer]:
        """Several reads at once: (method, path[, body[, query]]) each."""
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
            return list(pool.map(lambda c: self.call(*c), calls))


def passed_on(answer: Answer, req: func.HttpRequest) -> Refused:
    """A refusal of the API behind us, in our own words where it is the caller's business."""
    if answer.status in (400, 404, 409, 412):
        body = answer.body
        slug = {400: "invalid-request", 404: "not-found", 409: "conflict", 412: "conflict"}[answer.status]
        return refused(answer.status, body.get("title", "Refused"), body.get("detail", ""), slug, req)
    logging.error("Behind the gateway: %s %s", answer.status, answer.body)
    return refused(502, "Downstream Failure", "A Process or System API behind the gateway failed; try again later.", "downstream-failure", req)
