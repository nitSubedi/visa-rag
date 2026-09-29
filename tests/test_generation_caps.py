"""Every model call carries a generation cap.

Nothing set num_predict, so a model that never emits an end token generates until the
16k context fills. Small models at temperature 0 fall into repetition loops easily, and
plan_issues runs at temperature 0 — the likely cause of qwen2.5:1.5b grinding at 200%
CPU for over twenty minutes inside evals/run.py while finishing a single answer in 13s.
A cap turns a hang into a truncated reply, which every caller already survives.
"""

from __future__ import annotations

import io
import json

from visa import answer
from visa.config import settings


def _capture(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def fake_urlopen(req, timeout=None):
        # Only chat calls: stream_chat may first read the model's metadata.
        if req.full_url.endswith("/api/chat"):
            sent.append(json.loads(req.data))
        body = json.dumps({"message": {"content": "x"}, "done": True}).encode() + b"\n"
        return io.BytesIO(body)

    monkeypatch.setattr(answer.urllib.request, "urlopen", fake_urlopen)
    return sent


def test_every_call_is_capped_by_default(monkeypatch) -> None:
    sent = _capture(monkeypatch)
    list(answer.stream_chat([{"role": "user", "content": "q"}]))
    assert sent[0]["options"]["num_predict"] == settings.answer_reserve_tokens


def test_issue_planning_gets_a_tight_cap(monkeypatch) -> None:
    """Three to six short query lines. Anything past a few hundred tokens is a loop."""
    sent = _capture(monkeypatch)
    answer.plan_issues("I was unemployed for 60 days on OPT", {"status": "F-1"})
    cap = sent[0]["options"]["num_predict"]
    assert 0 < cap <= settings.plan_max_tokens


def test_the_answer_cap_matches_the_space_reserved_for_it() -> None:
    """build_prompt reserves answer_reserve_tokens of the window for the reply; a larger
    cap could overrun num_ctx, a much smaller one truncates good answers."""
    assert settings.plan_max_tokens < settings.answer_reserve_tokens < settings.num_ctx
