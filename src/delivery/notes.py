"""The note posted when a rollout is rolled back: what was seen, where, and what to look at next.

The evidence is the canary analysis's findings, numbered. Claude (`claude-opus-5-5`, low effort, refusal fallbacks)
writes the note through a strict tool and must cite findings by number. Code then checks the note: every cited
finding must exist, every segment named as affected must be one a finding flagged, and every number in the summary
must appear in the evidence. A note that fails a check is replaced by the plain note built from the findings, so a
rollback is never explained with something the analysis did not see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import anthropic
from anthropic.types.beta import BetaMessage, BetaOutputConfigParam, BetaTextBlockParam, BetaToolParam

from .rollout import Outcome

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

NOTE: BetaToolParam = {
    "name": "rollback_note",
    "description": "Post the rollback note for the on-call engineer and the change author.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "affected", "evidence", "next_steps"],
        "properties": {
            "summary": {"type": "string", "description": "Two or three sentences: what got worse, where, how much."},
            "affected": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Segments affected, exactly as named in the findings.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Numbers of the findings the note relies on.",
            },
            "next_steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to three concrete things for the author to check.",
            },
        },
    },
}


@dataclass
class Note:
    summary: str
    affected: list[str]
    evidence: list[int]
    next_steps: list[str]
    source: str  # claude | plain
    problems: list[str] = field(default_factory=list)  # why a Claude note was replaced


def evidence_lines(o: Outcome) -> list[str]:
    return [f"[{i}] {f.describe()}" for i, f in enumerate(o.findings, 1)]


def _where(segment: str) -> str:
    return "across the whole service" if segment == "whole service" else f"in {segment}"


def plain_note(o: Outcome) -> Note:
    if not o.findings:
        return Note(o.reason or "Rolled back.", [], [], ["Check the service alert and dashboards."], "plain")
    segs = list(dict.fromkeys(f.segment for f in o.findings))
    worst = min(o.findings, key=lambda f: f.p_value)
    return Note(
        f"Rolled back at {o.max_fraction:.0%} of traffic, {o.rollback_minute} minutes in. {worst.describe()}.",
        segs,
        list(range(1, len(o.findings) + 1)),
        [f"Compare the new version's {f.metric} {_where(f.segment)} with the old one." for f in o.findings[:3]],
        "plain",
    )


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def verify(note: Note, o: Outcome) -> list[str]:
    problems = []
    n = len(o.findings)
    if not note.evidence:
        problems.append("cites no findings")
    problems += [f"cites finding {i}, which does not exist" for i in note.evidence if not 1 <= i <= n]
    flagged = {f.segment for f in o.findings}
    problems += [f"names {s} as affected, which no finding flagged" for s in note.affected if s not in flagged]
    known = _numbers("\n".join(evidence_lines(o))) | _numbers(f"{o.max_fraction:.0%} {o.rollback_minute}")
    problems += [f"states {x}, which is not in the evidence" for x in sorted(_numbers(note.summary) - known)]
    return problems


def _items(v: object) -> list[object]:
    return list(v) if isinstance(v, list) else []


class Writer:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client if client is not None else anthropic.Anthropic(max_retries=2)

    def _create(self, o: Outcome, service: str) -> BetaMessage:
        system: list[BetaTextBlockParam] = [
            {
                "type": "text",
                "cache_control": {"type": "ephemeral"},
                "text": (
                    "You write rollback notes for a deployment platform. Use only the numbered findings you are given: "
                    "cite them by number, name affected segments exactly as they appear, and copy numbers exactly. "
                    "Call rollback_note once."
                ),
            }
        ]
        config: BetaOutputConfigParam = {"effort": "low"}
        body = (
            f"Service: {service}\nNew version rolled back at {o.max_fraction:.0%} of traffic, {o.rollback_minute} "
            f"minutes after the deploy started.\nFindings:\n" + "\n".join(evidence_lines(o))
        )
        return self.client.beta.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[NOTE],
            output_config=config,
            messages=[{"role": "user", "content": body}],
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def write(self, o: Outcome, service: str) -> Note:
        if not o.findings:
            return plain_note(o)
        resp = self._create(o, service)
        use = next((b for b in resp.content if b.type == "tool_use" and b.name == "rollback_note"), None)
        if resp.stop_reason == "refusal" or use is None:
            note = plain_note(o)
            note.problems = ["no note was written"]
            return note
        data = dict(use.input)
        note = Note(
            str(data.get("summary", "")),
            [str(x) for x in _items(data.get("affected"))],
            [int(x) for x in _items(data.get("evidence")) if isinstance(x, int | str) and str(x).lstrip("-").isdigit()],
            [str(x) for x in _items(data.get("next_steps"))][:3],
            "claude",
        )
        problems = verify(note, o)
        if problems:
            fallback = plain_note(o)
            fallback.problems = problems
            return fallback
        return note
