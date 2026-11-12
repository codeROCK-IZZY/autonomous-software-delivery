"""The rollback note (Claude through the real SDK, canned responses), the pipeline and the web app."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from delivery.app import create_app
from delivery.fleet import Regression
from delivery.notes import FALLBACK_BETA, MODEL, Writer, plain_note, verify
from delivery.platform import demo
from delivery.rollout import Outcome, run


@pytest.fixture(scope="module")
def rolled_back() -> Outcome:
    o = run(11, Regression("region_errors", 4.0, region="us-east"), "progressive")
    assert o.rolled_back and o.findings
    return o


def message(*content: dict[str, Any], stop: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": list(content),
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class Script:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses: Iterator[dict[str, Any]] = iter(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx2.Response(200, json=next(self.responses))


def writer(script: Script) -> Writer:
    client = anthropic.Anthropic(
        api_key="test",
        base_url="http://api.test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(script)),
    )
    return Writer(client)


def note_call(summary: str, affected: list[str], evidence: list[int]) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": "t",
        "name": "rollback_note",
        "input": {
            "summary": summary,
            "affected": affected,
            "evidence": evidence,
            "next_steps": ["Check the us-east connection pool settings."],
        },
    }


def test_claude_note_that_matches_the_evidence_is_kept(rolled_back: Outcome) -> None:
    f = rolled_back.findings[-1]
    summary = f"Errors rose in {f.segment}: {f.canary:.2%} on the new version against {f.control:.2%}."
    script = Script([message(note_call(summary, [f.segment], [len(rolled_back.findings)]))])
    note = writer(script).write(rolled_back, "checkout-api")
    assert note.source == "claude" and note.problems == []
    req = script.requests[0]
    assert req["model"] == MODEL and req["tools"][0]["strict"] is True
    assert req["output_config"] == {"effort": "low"}
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in req and "tool_choice" not in req
    assert "[1]" in req["messages"][0]["content"]
    assert FALLBACK_BETA in script.headers[0]["anthropic-beta"]


def test_claude_note_with_invented_details_is_replaced(rolled_back: Outcome) -> None:
    script = Script([message(note_call("Errors rose 37.5% in ap-south after a config push.", ["ap-south"], [9]))])
    note = writer(script).write(rolled_back, "checkout-api")
    assert note.source == "plain"
    joined = " ".join(note.problems)
    assert "finding 9" in joined and "ap-south" in joined and "37.5" in joined


def test_refusal_falls_back_to_the_plain_note(rolled_back: Outcome) -> None:
    script = Script([message({"type": "text", "text": "No."}, stop="refusal")])
    note = writer(script).write(rolled_back, "checkout-api")
    assert note.source == "plain" and note.problems == ["no note was written"]


def test_plain_note_passes_its_own_checks(rolled_back: Outcome) -> None:
    assert verify(plain_note(rolled_back), rolled_back) == []


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(demo()))


def test_app_pages_and_api(client: TestClient) -> None:
    h = client.get("/healthz").json()
    assert h["status"] == "ok" and h["deploys"] == 10 and h["claude"] is False
    deploys = client.get("/api/deploys").json()
    statuses = {d["status"] for d in deploys}
    assert statuses == {"promoted", "rolled back", "blocked"}
    rb = next(d for d in deploys if d["status"] == "rolled back")
    assert rb["findings"] and rb["note"]["source"] == "plain" and rb["max_fraction"] < 1
    assert "Deploy" in client.get("/").text
    page = client.get(f"/deploys/{rb['id']}").text
    assert "<svg" in page and "rollback" in page and "What the analysis found" in page
    blocked = next(d for d in deploys if d["status"] == "blocked")
    assert "error budget" in client.get(f"/deploys/{blocked['id']}").text
    assert client.get("/deploys/D-9999").status_code == 404


def test_shipping_through_the_api(client: TestClient) -> None:
    r = client.post(
        "/api/deploys",
        json={
            "service": "search-api",
            "title": "Bad build",
            "modules": ["apps/app0/m110"],
            "regression": "errors",
            "factor": 4.0,
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "rolled back"
    assert client.post("/api/deploys", json={"service": "nope", "title": "x"}).status_code == 422
    assert (
        client.post("/api/deploys", json={"service": "search-api", "title": "x", "region": "mars"}).status_code == 422
    )
