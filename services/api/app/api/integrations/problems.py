"""RFC 9457 problem details for the integration API only."""

import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

INTEGRATION_PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "code",
        "requestId",
        "retryable",
    ],
    "properties": {
        "type": {"type": "string", "format": "uri"},
        "title": {"type": "string"},
        "status": {"type": "integer", "minimum": 400, "maximum": 599},
        "detail": {"type": "string"},
        "instance": {"type": "string"},
        "code": {"type": "string"},
        "requestId": {"type": "string"},
        "retryable": {"type": "boolean"},
    },
}


def _problem_contract(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": INTEGRATION_PROBLEM_SCHEMA,
            }
        },
    }


# Applied at the Integration router boundary so every generated SDK operation
# has one closed RFC 9457 error shape. Route-specific success responses remain
# authoritative and FastAPI does not add its raw validation-error schema.
INTEGRATION_PROBLEM_RESPONSES = {
    400: _problem_contract("Invalid request"),
    401: _problem_contract("Authentication required"),
    403: _problem_contract("Insufficient scope"),
    404: _problem_contract("Resource not found"),
    409: _problem_contract("Resource state conflict"),
    412: _problem_contract("Precondition failed"),
    422: _problem_contract("Request validation failed"),
    429: _problem_contract("Organization limit reached"),
    503: _problem_contract("Service temporarily unavailable"),
}


class IntegrationProblem(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable
        self.headers = headers or {}
        self.extensions = extensions or {}
        super().__init__(code)


def problem_response(request: Request, problem: IntegrationProblem) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
    content = {
        "type": f"https://api.instadescribe.com/problems/{problem.code}",
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        "code": problem.code,
        "requestId": request_id,
        "retryable": problem.retryable,
        **problem.extensions,
    }
    return JSONResponse(
        status_code=problem.status,
        content=content,
        headers=problem.headers,
        media_type="application/problem+json",
    )


def not_found(resource: str = "Resource") -> IntegrationProblem:
    return IntegrationProblem(404, "not_found", "Not found", f"{resource} was not found.")
