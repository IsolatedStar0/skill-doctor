import json
from types import SimpleNamespace

from scripts import aime_skill_hook


def test_push_to_skill_doctor_preserves_standard_business_result(
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


def test_push_to_skill_doctor_wraps_legacy_business_result_shape(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(
        endpoint: str,
        api_key: str | None,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[int, str]:
        del endpoint, api_key, timeout
        captured.update(payload)
        return 200, json.dumps({"run_id": "lg-test", "status": "passed"})

    monkeypatch.setattr(aime_skill_hook, "_post", fake_post)

    aime_skill_hook.push_to_skill_doctor(
        skill_id="puck-rule-rca",
        runtime_events=[
            {"stage": "aime.done", "status": "completed", "message": "done"}
        ],
        business_result={
            "rca_filter": False,
            "rca_content": "当前异常不建议降噪\n- default:历史不同，不降噪",
            "rca_detail": [{"group_detail_name": "default"}],
            "confidence": 0.58,
        },
        endpoint="https://doctor.example",
        api_key="secret-token",
    )

    normalized = captured["business_result"]
    assert normalized["verdict"] == "当前异常不建议降噪"
    assert normalized["verdict_type"] == "warning"
    assert normalized["confidence"] == 0.58
    assert normalized["details"][0] == {
        "name": "business_result",
        "status": "warning",
        "reason": "当前异常不建议降噪",
    }
    assert normalized["extra"]["raw_business_result"]["rca_filter"] is False
    assert normalized["extra"]["raw_business_result"]["rca_detail"] == [{"group_detail_name": "default"}]


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


def test_write_trace_dir_records_aime_run_for_cli_ingest(tmp_path) -> None:
    trace_dir = tmp_path / "aime-run-001"
    written = aime_skill_hook.write_trace_dir(
        trace_dir,
        skill_id="puck-rule-rca",
        skill_content="# puck-rule-rca\nKeep confidence calibrated.",
        runtime_events=[
            {"stage": "aime.start", "status": "completed", "message": "started"},
            {"stage": "aime.noise_judge", "status": "completed", "message": "confidence=0.64"},
        ],
        tool_calls=[{"name": "fetch_metric", "status": "completed", "arguments": {"metric": "uv"}}],
        model_messages=[{"role": "assistant", "content": "rca_filter=true confidence=0.64"}],
        business_result={"rca_filter": True, "confidence": 0.64},
        task="diagnose metric anomaly",
        skill_version="1.0.0",
        trace_metadata={"aime_session": "session-123", "aime_assistant": "ear-agent"},
    )

    assert written == trace_dir
    metadata = json.loads((trace_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata == {
        "schema_version": "1.0",
        "skill_id": "puck-rule-rca",
        "trace_metadata": {
            "source": "aime_trace_dir",
            "skill_runtime": "aime",
            "aime_session": "session-123",
            "aime_assistant": "ear-agent",
        },
        "task": "diagnose metric anomaly",
        "skill_version": "1.0.0",
    }
    assert (trace_dir / "skill_content.md").read_text(encoding="utf-8") == (
        "# puck-rule-rca\nKeep confidence calibrated."
    )
    runtime_events = [
        json.loads(line)
        for line in (trace_dir / "runtime_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["stage"] for item in runtime_events] == ["aime.start", "aime.noise_judge"]
    tool_calls = [
        json.loads(line)
        for line in (trace_dir / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert tool_calls[0]["name"] == "fetch_metric"
    model_messages = [
        json.loads(line)
        for line in (trace_dir / "model_messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert model_messages[0]["role"] == "assistant"
    business_result = json.loads((trace_dir / "business_result.json").read_text(encoding="utf-8"))
    assert business_result == {
        "verdict": '{"confidence": 0.64, "rca_filter": true}',
        "verdict_type": "pass",
        "confidence": 0.64,
        "details": [
            {
                "name": "business_result",
                "status": "pass",
                "reason": '{"confidence": 0.64, "rca_filter": true}',
            }
        ],
        "extra": {"raw_business_result": {"rca_filter": True, "confidence": 0.64}},
    }


def test_write_trace_dir_skips_empty_trace(tmp_path, capsys) -> None:
    written = aime_skill_hook.write_trace_dir(tmp_path / "empty", skill_id="demo")

    assert written is None
    assert not (tmp_path / "empty").exists()
    assert "skip trace-dir" in capsys.readouterr().err


def test_bridge_aime_run_writes_trace_dir_from_context(tmp_path) -> None:
    ctx = SimpleNamespace(
        artifact_dir=str(tmp_path / "artifacts"),
        skill_id="puck-rule-rca",
        skill_body="# skill\nKeep confidence calibrated.",
        runtime_events=[{"stage": "aime.done", "status": "completed", "message": "done"}],
        tool_calls=[{"name": "fetch_metric", "status": "completed"}],
        model_messages=[{"role": "assistant", "content": "done"}],
        final_output={"rca_filter": True, "confidence": 0.91},
        user_query="diagnose anomaly",
        skill_version="1.0.0",
        session_id="session-123",
        assistant_id="assistant-123",
        trace_id="trace-123",
        run_id="run-123",
    )

    result = aime_skill_hook.bridge_aime_run(ctx, mode="trace-dir")

    trace_dir = tmp_path / "artifacts" / "skilldoctor-trace"
    assert result["mode"] == "trace-dir"
    assert result["status"] == "trace_dir_written"
    assert result["trace_dir"] == str(trace_dir)
    metadata = json.loads((trace_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["skill_id"] == "puck-rule-rca"
    assert metadata["trace_metadata"]["aime_session"] == "session-123"
    assert metadata["trace_metadata"]["aime_assistant"] == "assistant-123"
    assert metadata["trace_metadata"]["aime_trace_id"] == "trace-123"
    assert metadata["trace_metadata"]["aime_run_id"] == "run-123"


def test_bridge_aime_run_supports_http_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_push(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"run_id": "lg-test", "status": "passed"}

    monkeypatch.setattr(aime_skill_hook, "push_to_skill_doctor", fake_push)

    ctx = {
        "skill_id": "puck-rule-rca",
        "runtime_events": [{"stage": "aime.done", "status": "completed", "message": "done"}],
        "tool_calls": [],
        "model_messages": [],
        "final_output": {"answer": "pong"},
        "session_id": "session-http",
        "assistant_id": "assistant-http",
        "trace_id": "trace-http",
        "run_id": "run-http",
    }

    result = aime_skill_hook.bridge_aime_run(
        ctx,
        mode="http",
        endpoint="https://doctor.example",
        api_key="secret-token",
        timeout=12.5,
    )

    assert result == {
        "mode": "http",
        "trace_dir": None,
        "snapshot": {"run_id": "lg-test", "status": "passed"},
        "status": "pushed",
    }
    assert captured["skill_id"] == "puck-rule-rca"
    assert captured["endpoint"] == "https://doctor.example"
    assert captured["api_key"] == "secret-token"
    assert captured["timeout"] == 12.5
    assert captured["trace_metadata"] == {
        "aime_session": "session-http",
        "aime_assistant": "assistant-http",
        "aime_trace_id": "trace-http",
        "aime_run_id": "run-http",
    }


def test_bridge_aime_run_respects_off_mode_and_never_calls_bridge(monkeypatch) -> None:
    monkeypatch.setattr(
        aime_skill_hook,
        "write_trace_dir",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("write_trace_dir should not be called")),
    )
    monkeypatch.setattr(
        aime_skill_hook,
        "push_to_skill_doctor",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("push_to_skill_doctor should not be called")),
    )

    result = aime_skill_hook.bridge_aime_run({"skill_id": "demo"}, mode="off")

    assert result == {
        "mode": "off",
        "trace_dir": None,
        "snapshot": None,
        "status": "disabled",
    }


def test_bridge_aime_run_uses_env_trace_dir_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_TRACE_DIR", str(tmp_path / "override-trace"))
    ctx = {
        "skill_id": "puck-rule-rca",
        "runtime_events": [{"stage": "aime.done", "status": "completed", "message": "done"}],
    }

    result = aime_skill_hook.bridge_aime_run(ctx, mode="trace-dir")

    assert result["trace_dir"] == str((tmp_path / "override-trace").expanduser())
    assert (tmp_path / "override-trace" / "metadata.json").exists()
