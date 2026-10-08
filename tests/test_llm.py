import json

import pytest

from mingjing import llm
from mingjing.cli import _semantic_filter_claims
from mingjing.models import AtomicClaim


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_v4_disables_thinking_and_discards_candidate_value(monkeypatch):
    seen = []

    def fake_urlopen(request, data=None, timeout=None):
        seen.append(json.loads(data.decode("utf-8")))
        return _Response({"choices": [{"finish_reason": "stop", "message": {
            "reasoning_content": "hidden reasoning",
            "content": '[{"source":"收入100万元","candidate_value":"999999","account":"REVENUE"}]',
        }}]})

    monkeypatch.setattr(llm, "urlopen", fake_urlopen)
    monkeypatch.setenv("MINGJING_LLM_BASE_URL", "https://example.test")
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "secret")
    monkeypatch.setenv("MINGJING_LLM_MODEL", "deepseek-v4-pro")
    claims, metadata = llm.decompose_with_metadata("收入100万元")
    assert claims == [{"source": "收入100万元", "account": "REVENUE"}]
    assert seen[0]["thinking"] == {"type": "disabled"}
    assert metadata["chunks"] == 1


def test_empty_content_and_truncated_response_are_failures(monkeypatch):
    monkeypatch.setattr(llm, "urlopen", lambda *a, **k: _Response({
        "choices": [{"finish_reason": "stop", "message": {"reasoning_content": "only"}}]
    }))
    monkeypatch.setenv("MINGJING_LLM_BASE_URL", "https://example.test")
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "secret")
    monkeypatch.setenv("MINGJING_LLM_MODEL", "deepseek-chat")
    with pytest.raises(llm.LLMUnavailable, match="empty"):
        llm.decompose("abc")

    monkeypatch.setattr(llm, "urlopen", lambda *a, **k: _Response({
        "choices": [{"finish_reason": "length", "message": {"content": "[]"}}]
    }))
    with pytest.raises(llm.LLMUnavailable, match="truncated"):
        llm.decompose("abc")


def test_semantic_filter_never_uses_model_numbers():
    claim = AtomicClaim("c1", "营业收入100万元", 0, 8, account="REVENUE", candidate_value="1000000")
    selected, count = _semantic_filter_claims("营业收入100万元", [claim], [{"source": "营业收入100万元", "candidate_value": "999999999"}])
    assert count == 1 and selected[0].candidate_value == "1000000"


def test_long_input_is_chunked(monkeypatch):
    calls = []

    def fake_urlopen(request, data=None, timeout=None):
        calls.append(json.loads(data.decode("utf-8")))
        return _Response({"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]})

    monkeypatch.setattr(llm, "urlopen", fake_urlopen)
    monkeypatch.setenv("MINGJING_LLM_BASE_URL", "https://example.test")
    monkeypatch.setenv("MINGJING_LLM_API_KEY", "secret")
    monkeypatch.setenv("MINGJING_LLM_MODEL", "deepseek-chat")
    _, metadata = llm.decompose_with_metadata("甲\n乙\n丙\n丁", max_chars=2)
    assert len(calls) == metadata["chunks"] >= 2
