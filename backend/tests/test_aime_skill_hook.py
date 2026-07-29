import json

from scripts import aime_skill_hook


def test_push_to_skill_doctor_sends_business_result_as_top_level_payload(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(
        endpoint: str,
        api_key: str | None,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[int, str]:
        captured.update(
            {
                "endpoint": endpoint,
                "api_key": api_key,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return 200, json.dumps({"run_id": "lg-test", "status": "passed"})

    monkeypatch.setattr(aime_skill_hook, "_post", fake_post)

    business_result = {
        "verdict": "需要修订 Skill",
        "verdict_type": "fail",
        "confidence": 0.86,
        "details": [
            {
                "name": "业务断言",
                "status": "fail",
                "reason": "输出缺少降噪依据。",
            }
        ],
        "extra": {"rule_id": "noise-judge"},
    }

    snapshot = aime_skill_hook.push_to_skill_doctor(
        skill_id="puck-rule-rca",
        skill_content="Always explain the noise judgment.",
        runtime_events=[
            {"stage": "aime.done", "status": "completed", "message": "done"}
        ],
        business_result=business_result,
        endpoint="https://doctor.example",
        api_key="secret-token",
        timeout=12.5,
    )

    payload = captured["payload"]
    assert snapshot == {"run_id": "lg-test", "status": "passed"}
    assert captured["endpoint"] == "https://doctor.example"
    assert captured["api_key"] == "secret-token"
    assert captured["timeout"] == 12.5
    assert payload["business_result"] == business_result
    assert "business_result" not in payload["trace_metadata"]


def test_push_to_skill_doctor_standardizes_trace_metadata_without_dropping_aime_ids(
    monkeypatch,
) -> None:
    captured_payload: dict[str, object] = {}

    def fake_post(
        endpoint: str,
        api_key: str | None,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[int, str]:
        del endpoint, api_key, timeout
        captured_payload.update(payload)
        return 200, json.dumps({"run_id": "lg-test", "status": "failed"})

    monkeypatch.setattr(aime_skill_hook, "_post", fake_post)

    aime_skill_hook.push_to_skill_doctor(
        skill_id="puck-rule-rca",
        skill_content="Check metric context before final answer.",
        runtime_events=[
            {
                "stage": "aime.noise_judge",
                "status": "failed",
                "message": "missing holiday rule",
            }
        ],
        trace_metadata={
            "aime_trace_id": "trace-20260729-001",
            "aime_session": "session-123",
            "aime_assistant": "ear-agent",
        },
        endpoint="https://doctor.example",
        api_key="secret-token",
    )

    metadata = captured_payload["trace_metadata"]
    assert metadata["source"] == "aime_on_finish_hook"
    assert metadata["skill_runtime"] == "aime"
    assert metadata["aime_trace_id"] == "trace-20260729-001"
    assert metadata["aime_session"] == "session-123"
    assert metadata["aime_assistant"] == "ear-agent"


def test_push_to_skill_doctor_preserves_explicit_source_metadata(
    monkeypatch,
) -> None:
    captured_payload: dict[str, object] = {}

    def fake_post(
        endpoint: str,
        api_key: str | None,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[int, str]:
        del endpoint, api_key, timeout
        captured_payload.update(payload)
        return 200, json.dumps({"run_id": "lg-test", "status": "passed"})

    monkeypatch.setattr(aime_skill_hook, "_post", fake_post)

    aime_skill_hook.push_to_skill_doctor(
        skill_id="custom-runtime-skill",
        skill_content="Use custom runtime metadata.",
        runtime_events=[
            {"stage": "custom.done", "status": "completed", "message": "done"}
        ],
        trace_metadata={"source": "custom_aime_bridge", "skill_runtime": "aime-canary"},
        endpoint="https://doctor.example",
        api_key="secret-token",
    )

    metadata = captured_payload["trace_metadata"]
    assert metadata["source"] == "custom_aime_bridge"
    assert metadata["skill_runtime"] == "aime-canary"
