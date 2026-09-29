"""Models without a system role get the rules at the start of the first user turn."""

from __future__ import annotations

import io
import json

import pytest

from visa import answer


def _show(monkeypatch, details: dict | None, chats: list[dict]) -> None:
    answer._SYSTEM_ROLE.clear()

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/api/show"):
            if details is None:
                raise OSError("ollama down")
            return io.BytesIO(json.dumps({"details": details}).encode())
        chats.append(json.loads(req.data))
        return io.BytesIO(b'{"message": {"content": "x"}, "done": true}\n')

    monkeypatch.setattr(answer.urllib.request, "urlopen", fake_urlopen)


CONVO = [
    {"role": "system", "content": "RULES"},
    {"role": "user", "content": "Q1"},
    {"role": "assistant", "content": "A1"},
    {"role": "user", "content": "Q2"},
]


def test_gemma_gets_the_rules_inside_the_first_user_turn(monkeypatch) -> None:
    chats: list[dict] = []
    _show(monkeypatch, {"family": "gemma3", "families": ["gemma3"]}, chats)
    list(answer.stream_chat(CONVO, model="gemma3:4b"))
    assert chats[0]["messages"] == [
        {"role": "user", "content": "RULES\n\nQ1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
    ]


@pytest.mark.parametrize("details", [{"family": "qwen2", "families": ["qwen2"]}, None])
def test_other_or_unknown_models_are_left_alone(monkeypatch, details) -> None:
    chats: list[dict] = []
    _show(monkeypatch, details, chats)
    list(answer.stream_chat(CONVO, model="some:model"))
    assert chats[0]["messages"] == CONVO


def test_a_trailing_system_message_is_not_dropped() -> None:
    convo = [{"role": "user", "content": "Q"}, {"role": "system", "content": "S"}]
    out = answer.fold_system(convo)
    assert out == [{"role": "user", "content": "Q"}, {"role": "user", "content": "S"}]
