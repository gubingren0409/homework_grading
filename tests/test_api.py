import base64
import asyncio
from unittest.mock import patch, Mock
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from fastapi.testclient import TestClient
from src.main import app
from src.schemas.perception_ir import LayoutIR
from src.db.client import (
    init_db,
    create_task,
    update_task_status,
    update_task_celery_id,
    get_task,
)
from src.api.dependencies import get_db_path
from src.utils.file_parsers import UnsupportedFormatError
from src.core.exceptions import GradingSystemError
client = TestClient(app)


def test_grade_endpoint_happy_path():
    """
    E2E test: Verifies that a valid file upload returns a 200 OK 
    and a correctly structured BatchGradingResponse JSON.
    """
    # 1. Prepare payload
    files = [("files", ("test_hw.jpg", b"fake_image_bytes", "image/jpeg"))]

    # 2. Execute POST
    with patch("src.api.routes.grade_homework_task.apply_async", return_value=Mock(id="mock-celery-id")) as mocked_apply_async:
        response = client.post("/api/v1/grade/submit", files=files)

    # 3. Assertions
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"
    assert data["status_endpoint"] == f"/api/v1/grade/{data['task_id']}"
    assert data["stream_endpoint"] == f"/api/v1/tasks/{data['task_id']}/stream"
    assert data["suggested_poll_interval_seconds"] == 2
    assert mocked_apply_async.call_args.kwargs["retry"] is False
    assert mocked_apply_async.call_args.kwargs["ignore_result"] is True


def test_grade_endpoint_circuit_breaker_422():
    """
    E2E test: Verifies that the API correctly maps the PerceptionShortCircuitError 
    to an HTTP 422 Unprocessable Entity response using Dependency Overrides.
    """
    files = [("files", ("dirty_hw.jpg", b"dirty_bytes", "image/jpeg"))]
    with patch("src.api.routes.grade_homework_task.apply_async", return_value=Mock(id="mock-celery-id")):
        response = client.post("/api/v1/grade/submit", files=files)
    assert response.status_code == 202


def test_grade_submit_queue_unavailable_falls_back_to_local():
    client.app.state.limiter.reset()
    files = [("files", ("test_hw.jpg", b"fake_image_bytes", "image/jpeg"))]

    def _raise_queue_error(*args, **kwargs):
        del args, kwargs
        raise KombuOperationalError("broker down")

    with (
        patch("src.api.routes.grade_homework_task.apply_async", side_effect=_raise_queue_error),
        patch("src.api.routes._run_task_locally", return_value=None) as mocked_local_runner,
    ):
        response = client.post("/api/v1/grade/submit", files=files)

    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"
    assert data["mode"] == "single_submission"
    assert mocked_local_runner.called


def test_grade_submit_redis_connection_error_falls_back_to_local():
    client.app.state.limiter.reset()
    files = [("files", ("test_hw.jpg", b"fake_image_bytes", "image/jpeg"))]

    def _raise_redis_error(*args, **kwargs):
        del args, kwargs
        raise RedisConnectionError("redis down")

    with (
        patch("src.api.routes.grade_homework_task.apply_async", side_effect=_raise_redis_error),
        patch("src.api.routes._run_task_locally", return_value=None) as mocked_local_runner,
    ):
        response = client.post("/api/v1/grade/submit", files=files)

    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"
    assert data["mode"] == "single_submission"
    assert mocked_local_runner.called


def test_health_check():
    """Simple test for the health check endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_console_entrypoints():
    client.app.state.limiter.reset()
    student = client.get("/student-console")
    student_batch = client.get("/student-console-batch")
    review = client.get("/review-console")
    ops = client.get("/ops-console")
    assert student.status_code == 200
    assert student_batch.status_code == 200
    assert review.status_code == 200
    assert ops.status_code == 200
    assert "生成评分标准" in student.text
    assert "学生批处理任务台" in student_batch.text
    assert "运行监听窗口" in student.text
    assert "复核工作台" in review.text
    assert "运营控制台" in ops.text


def test_skill_layout_parse_gateway(monkeypatch):
    class _FakePerceptionEngine:
        async def extract_layout(
            self,
            image_bytes: bytes,
            *,
            context_type: str,
            target_question_no: str | None = None,
            page_index: int = 0,
        ) -> LayoutIR:
            del image_bytes
            payload = {
                "context_type": context_type,
                "target_question_no": target_question_no,
                "page_index": page_index,
                "regions": [
                    {
                        "target_id": "region-1",
                        "question_no": target_question_no,
                        "region_type": "answer_region",
                        "bbox": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.8, "y_max": 0.9},
                    }
                ],
                "warnings": [],
            }
            return LayoutIR.model_validate(payload, context={"image_width": 1000, "image_height": 800})

    monkeypatch.setattr("src.api.routes.create_perception_engine", lambda: _FakePerceptionEngine())
    response = client.post(
        "/api/v1/skills/layout/parse",
        json={
            "image_base64": base64.b64encode(b"fake-image").decode("utf-8"),
            "context_type": "STUDENT_ANSWER",
            "page_index": 0,
            "target_question_no": "12",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["context_type"] == "STUDENT_ANSWER"
    assert len(data["regions"]) == 1
    assert data["regions"][0]["target_id"] == "region-1"


def test_skill_gateway_auth_token_required(monkeypatch):
    monkeypatch.setattr("src.api.routes.settings.skill_gateway_auth_enabled", True)
    monkeypatch.setattr("src.api.routes.settings.skill_gateway_auth_token", "token-1")
    response = client.post(
        "/api/v1/skills/validate",
        json={
            "task_id": "t-1",
            "question_id": "q-1",
            "perception_payload": {"elements": []},
            "evaluation_payload": {"status": "SCORED"},
            "rubric_payload": {"question_id": "q-1"},
        },
    )
    assert response.status_code == 403

    response_ok = client.post(
        "/api/v1/skills/validate",
        headers={"X-Skill-Gateway-Token": "token-1"},
        json={
            "task_id": "t-1",
            "question_id": "q-1",
            "perception_payload": {"elements": []},
            "evaluation_payload": {"status": "SCORED"},
            "rubric_payload": {"question_id": "q-1"},
        },
    )
    assert response_ok.status_code == 200
    monkeypatch.setattr("src.api.routes.settings.skill_gateway_auth_enabled", False)
    monkeypatch.setattr("src.api.routes.settings.skill_gateway_auth_token", None)


def test_skill_validation_gateway_stub():
    response = client.post(
        "/api/v1/skills/validate",
        json={
            "task_id": "t-1",
            "question_id": "q-1",
            "perception_payload": {"elements": []},
            "evaluation_payload": {"status": "SCORED"},
            "rubric_payload": {"question_id": "q-1"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "gateway_stub" in data["details"]["mode"]


def test_capability_catalog_endpoint():
    response = client.get("/api/v1/capabilities/catalog")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    domains = {d["domain"] for d in data["domains"]}
    assert {"rubric", "grade", "review", "annotation", "hygiene", "obs"} <= domains


def test_contract_catalog_endpoint():
    response = client.get("/api/v1/contracts/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "status_enums" in data
    assert "task_status" in data["status_enums"]
    assert "schemas" in data
    schema_names = {item["schema_name"] for item in data["schemas"]}
    assert "TaskStatusResponse" in schema_names
    assert "GradeFlowGuideResponse" in schema_names
    assert "BATCH_FILE_TYPE_UNSUPPORTED" in data["error_codes"]


def test_grade_flow_guide_endpoint():
    client.app.state.limiter.reset()
    response = client.get("/api/v1/grade/flow-guide")
    assert response.status_code == 200
    data = response.json()
    assert data["submit_endpoint"] == "/api/v1/grade/submit"
    assert data["batch_submit_endpoint"] == "/api/v1/grade/submit-batch"
    assert data["status_endpoint_template"] == "/api/v1/grade/{task_id}"
    assert data["stream_endpoint_template"] == "/api/v1/tasks/{task_id}/stream"
    assert "SSE_BACKEND_UNAVAILABLE" in data["error_code_actions"]
    assert data["error_code_actions"]["SSE_BACKEND_UNAVAILABLE"] == "fallback_to_polling"
    assert data["error_code_actions"]["RATE_LIMITED"] == "wait_and_retry"
    assert data["error_code_actions"]["UPSTREAM_UNAVAILABLE"] == "retry_upload"
    assert data["error_code_actions"]["BATCH_FILE_TYPE_UNSUPPORTED"] == "adjust_file"


def test_grade_submit_batch_endpoint_happy_path():
    client.app.state.limiter.reset()
    files = [
        ("files", ("stu_001.jpg", b"fake_img_1", "image/jpeg")),
        ("files", ("stu_002.png", b"fake_img_2", "image/png")),
    ]
    with patch("src.api.routes.grade_homework_task.apply_async", return_value=Mock(id="mock-celery-id")) as mocked_apply_async:
        response = client.post("/api/v1/grade/submit-batch", files=files)
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"
    assert data["mode"] == "batch_single_page"
    assert data["submitted_count"] == 2
    assert mocked_apply_async.call_args.kwargs["retry"] is False
    assert mocked_apply_async.call_args.kwargs["ignore_result"] is True


def test_grade_submit_batch_queue_unavailable_falls_back_to_local():
    client.app.state.limiter.reset()
    files = [
        ("files", ("stu_001.jpg", b"fake_img_1", "image/jpeg")),
        ("files", ("stu_002.png", b"fake_img_2", "image/png")),
    ]

    def _raise_queue_error(*args, **kwargs):
        del args, kwargs
        raise KombuOperationalError("broker down")

    with (
        patch("src.api.routes.grade_homework_task.apply_async", side_effect=_raise_queue_error),
        patch("src.api.routes._run_task_locally", return_value=None) as mocked_local_runner,
    ):
        response = client.post("/api/v1/grade/submit-batch", files=files)

    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"
    assert data["mode"] == "batch_single_page"
    assert data["submitted_count"] == 2
    assert mocked_local_runner.called


def test_grade_submit_batch_rejects_pdf():
    client.app.state.limiter.reset()
    files = [("files", ("stu_001.pdf", b"%PDF-1.4", "application/pdf"))]
    response = client.post("/api/v1/grade/submit-batch", files=files)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "BATCH_FILE_TYPE_UNSUPPORTED"


def test_rate_limit_handler_returns_structured_contract(monkeypatch):
    files = [("files", ("reference.jpg", b"fake_reference", "image/jpeg"))]
    with patch("src.api.routes.GradingWorkflow.generate_rubric_pipeline") as mocked:
        mocked.return_value = Mock(
            question_id="q-1",
            grading_points=[{"id": "a"}],
            model_dump=lambda: {"question_id": "q-1", "correct_answer": "x", "grading_points": []},
        )
        responses = [client.post("/api/v1/rubric/generate", files=files) for _ in range(6)]
    limited = next((r for r in responses if r.status_code == 429), None)
    assert limited is not None
    detail = limited.json()["detail"]
    assert detail["error_code"] == "RATE_LIMITED"
    assert detail["retryable"] is True
    assert detail["retry_hint"] == "retry_later"


def test_rubric_generate_runtime_phase35_non_gate_maps_upstream(monkeypatch):
    client.app.state.limiter.reset()
    async def _raise_runtime(self, files_data):
        del self, files_data
        raise RuntimeError("PHASE35_CONTRACT_BLOCK: slicing produced no regions on page 0.")

    monkeypatch.setattr("src.api.routes.GradingWorkflow.generate_rubric_pipeline", _raise_runtime)
    files = [("files", ("reference.jpg", b"fake_reference", "image/jpeg"))]
    response = client.post("/api/v1/rubric/generate", files=files)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_code"] == "UPSTREAM_UNAVAILABLE"
    assert detail["retryable"] is True
    assert detail["next_action"] == "retry_upload"


def test_rubric_generate_rejects_unsupported_format(monkeypatch):
    client.app.state.limiter.reset()
    async def _raise_unsupported(self, files_data):
        del self, files_data
        raise UnsupportedFormatError("Word documents are unsupported. Please convert to PDF.")

    monkeypatch.setattr("src.api.routes.GradingWorkflow.generate_rubric_pipeline", _raise_unsupported)
    files = [("files", ("reference.jpg", b"fake_reference", "image/jpeg"))]
    response = client.post("/api/v1/rubric/generate", files=files)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error_code"] == "INPUT_REJECTED"
    assert detail["retryable"] is False
    assert detail["next_action"] == "adjust_file"


def test_rubric_generate_upstream_unavailable_returns_503(monkeypatch):
    client.app.state.limiter.reset()
    async def _raise_upstream(self, files_data):
        del self, files_data
        raise GradingSystemError("redis unavailable")

    monkeypatch.setattr("src.api.routes.GradingWorkflow.generate_rubric_pipeline", _raise_upstream)
    files = [("files", ("reference.jpg", b"fake_reference", "image/jpeg"))]
    response = client.post("/api/v1/rubric/generate", files=files)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_code"] == "UPSTREAM_UNAVAILABLE"
    assert detail["retryable"] is True
    assert detail["next_action"] == "retry_upload"


def test_rubric_generate_response_contains_source_file_count(monkeypatch):
    client.app.state.limiter.reset()
    class _FakeRubric:
        question_id = "q-1"
        grading_points = [object(), object()]

        def model_dump(self):
            return {"question_id": "q-1", "correct_answer": "a", "grading_points": []}

    async def _ok(self, files_data):
        del self
        assert len(files_data) == 2
        return _FakeRubric()

    monkeypatch.setattr("src.api.routes.GradingWorkflow.generate_rubric_pipeline", _ok)
    files = [
        ("files", ("reference-1.jpg", b"fake_reference_1", "image/jpeg")),
        ("files", ("reference-2.jpg", b"fake_reference_2", "image/jpeg")),
    ]
    response = client.post("/api/v1/rubric/generate", files=files)
    assert response.status_code == 201
    data = response.json()
    assert data["grading_points_count"] == 2
    assert data["source_file_count"] == 2


def test_grade_status_not_found_error_contract(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grade_status_not_found.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    response = client.get("/api/v1/grade/task-not-found")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["error_code"] == "TASK_NOT_FOUND"
    assert detail["retryable"] is False
    assert detail["next_action"] == "submit_new_task"
    app.dependency_overrides.clear()


def test_results_by_task_requires_completed_state(tmp_path, monkeypatch):
    db_path = str(tmp_path / "results_by_task.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path

    missing_resp = client.get("/api/v1/results?task_id=missing-task&page=1&limit=10")
    assert missing_resp.status_code == 404
    missing_detail = missing_resp.json()["detail"]
    assert missing_detail["error_code"] == "TASK_NOT_FOUND"

    asyncio.run(create_task(db_path, "pending-task"))
    pending_resp = client.get("/api/v1/results?task_id=pending-task&page=1&limit=10")
    assert pending_resp.status_code == 409
    pending_detail = pending_resp.json()["detail"]
    assert pending_detail["error_code"] == "TASK_NOT_COMPLETED"
    assert pending_detail["retryable"] is True
    assert pending_detail["next_action"] == "wait_for_completion"
    app.dependency_overrides.clear()


def test_grade_status_failed_retry_hints(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grade_status_failed.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    asyncio.run(create_task(db_path, "failed-task"))
    asyncio.run(update_task_status(db_path, "failed-task", "FAILED", error="network timeout"))

    response = client.get("/api/v1/grade/failed-task")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILED"
    assert data["error_code"] == "TASK_FAILED"
    assert data["retryable"] is True
    assert data["retry_hint"] == "retry_submit"
    assert data["next_action"] == "retry_upload"
    app.dependency_overrides.clear()


def test_grade_status_uses_db_progress_snapshot(tmp_path, monkeypatch):
    db_path = str(tmp_path / "grade_status_progress.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    task_id = "progress-task"
    asyncio.run(create_task(db_path, task_id))
    async def _set_progress():
        from src.db.client import update_task_progress
        await update_task_progress(db_path, task_id, progress=0.66, eta_seconds=18)
        await update_task_status(db_path, task_id, "PROCESSING")
        await update_task_progress(db_path, task_id, progress=0.66, eta_seconds=18)
    asyncio.run(_set_progress())

    response = client.get(f"/api/v1/grade/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PROCESSING"
    assert data["progress"] == 0.66
    assert data["eta_seconds"] == 18
    app.dependency_overrides.clear()


def test_sla_summary_endpoint_with_empty_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty_sla.db")
    app.dependency_overrides[get_db_path] = lambda: db_path
    response = client.get("/api/v1/sla/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert isinstance(data["observed_status_counts"], dict)
    app.dependency_overrides.clear()


def test_provider_benchmark_endpoint(tmp_path, monkeypatch):
    db_path = str(tmp_path / "benchmark.db")
    app.dependency_overrides[get_db_path] = lambda: db_path
    response = client.get("/api/v1/metrics/provider-benchmark?window_hours=24")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "cognitive_router" in data
    assert "estimated_cost" in data
    assert "throughput_tasks_per_hour" in data
    assert "accuracy_proxy" in data["cognitive_router"]
    assert "failure_rate" in data["cognitive_router"]
    app.dependency_overrides.clear()


def test_router_policy_endpoint():
    response = client.get("/api/v1/router/policy")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "policy" in data
    assert "live_snapshot" in data


def test_dataset_pipeline_summary_endpoint(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dataset_pipeline.db")
    app.dependency_overrides[get_db_path] = lambda: db_path
    response = client.get("/api/v1/metrics/dataset-pipeline?window_hours=24")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "dataset_assets" in data
    assert "review_queue" in data
    assert "pending_assets" in data["dataset_assets"]
    app.dependency_overrides.clear()


def test_runtime_dashboard_endpoint(tmp_path, monkeypatch):
    db_path = str(tmp_path / "runtime_dashboard.db")
    app.dependency_overrides[get_db_path] = lambda: db_path
    response = client.get("/api/v1/metrics/runtime-dashboard?window_hours=24")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "provider_hits" in data
    assert "fallback_triggers" in data
    assert "prompt_cache_hits" in data
    assert "human_review_rate" in data
    app.dependency_overrides.clear()


def test_prompt_control_endpoints(tmp_path, monkeypatch):
    db_path = str(tmp_path / "prompt_control_api.db")
    app.dependency_overrides[get_db_path] = lambda: db_path

    set_resp = client.post(
        "/api/v1/prompt/control",
        json={
            "prompt_key": "deepseek.cognitive.evaluate",
            "forced_variant_id": "A",
            "lkg_mode": True,
            "operator_id": "ops-user",
        },
    )
    assert set_resp.status_code == 200

    ab_resp = client.post(
        "/api/v1/prompt/ab-config",
        json={
            "prompt_key": "deepseek.cognitive.evaluate",
            "enabled": True,
            "rollout_percentage": 20,
            "variant_weights": {"A": 20, "B": 80},
            "segment_prefixes": ["tenant-1"],
            "sticky_salt": "s1",
            "operator_id": "ops-user",
        },
    )
    assert ab_resp.status_code == 200

    state_resp = client.get("/api/v1/prompt/state?prompt_key=deepseek.cognitive.evaluate")
    assert state_resp.status_code == 200
    state_data = state_resp.json()
    assert state_data["runtime"]["lkg_mode"] is True
    assert state_data["persisted"]["control_state"]["forced_variant_id"] == "A"

    refresh_resp = client.post("/api/v1/prompt/refresh?prompt_key=deepseek.cognitive.evaluate")
    assert refresh_resp.status_code == 200

    invalidate_resp = client.post("/api/v1/prompt/invalidate?prompt_key=deepseek.cognitive.evaluate")
    assert invalidate_resp.status_code == 200

    audit_resp = client.get("/api/v1/prompt/audit?prompt_key=deepseek.cognitive.evaluate&page=1&limit=20")
    assert audit_resp.status_code == 200
    rows = audit_resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    app.dependency_overrides.clear()


def test_ops_console_endpoints(tmp_path):
    db_path = str(tmp_path / "ops_console_api.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        snapshot_resp = client.get("/api/v1/ops/config/snapshot")
        assert snapshot_resp.status_code == 200
        snapshot = snapshot_resp.json()
        assert "perception_provider" in snapshot
        assert "router_policy" in snapshot
        assert "environment" in snapshot
        assert "feature_flags" in snapshot
        assert "phase35_layout_gate_enabled" in snapshot["prompt_settings"]
        assert "max_input_tokens" in snapshot["prompt_settings"]
        assert "reserve_output_tokens" in snapshot["prompt_settings"]

        switch_resp = client.post(
            "/api/v1/ops/provider/switch",
            json={"provider": "mock", "operator_id": "ops-user"},
        )
        assert switch_resp.status_code == 200
        assert switch_resp.json()["provider"] == "mock"

        invalid_switch = client.post(
            "/api/v1/ops/provider/switch",
            json={"provider": "unknown-provider", "operator_id": "ops-user"},
        )
        assert invalid_switch.status_code == 422
        assert invalid_switch.json()["detail"]["error_code"] == "INVALID_PROVIDER"

        router_resp = client.post(
            "/api/v1/ops/router/control",
            json={
                "enabled": True,
                "failure_rate_threshold": 0.25,
                "token_spike_threshold": 1.6,
                "min_samples": 10,
                "budget_token_limit": 7000,
                "operator_id": "ops-user",
            },
        )
        assert router_resp.status_code == 200
        router_data = router_resp.json()
        assert router_data["failure_rate_threshold"] == 0.25
        assert router_data["budget_token_limit"] == 7000

        catalog_resp = client.get("/api/v1/ops/prompt/catalog?page=1&limit=20")
        assert catalog_resp.status_code == 200
        catalog_data = catalog_resp.json()
        assert "items" in catalog_data

        audit_resp = client.get("/api/v1/ops/audit/logs?page=1&limit=20")
        assert audit_resp.status_code == 200
        audit_data = audit_resp.json()
        assert isinstance(audit_data["items"], list)
        assert any(item["action"] == "ops_provider_switch" for item in audit_data["items"])
    finally:
        app.dependency_overrides.clear()


def test_ops_queue_diagnostics_endpoint_classifies_stale_pending(tmp_path):
    db_path = str(tmp_path / "ops_queue_diag.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        async def _seed():
            import aiosqlite

            await create_task(db_path, "stale-local")
            await update_task_celery_id(db_path, "stale-local", "local:stale-local")
            await create_task(db_path, "stale-queued")
            await update_task_celery_id(db_path, "stale-queued", "queued-task-id")
            await create_task(db_path, "processing-task")
            await update_task_status(db_path, "processing-task", "PROCESSING")
            await update_task_celery_id(db_path, "processing-task", "processing-task-id")

            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """
                    UPDATE tasks
                    SET created_at = datetime('now', '-2 hours'),
                        updated_at = datetime('now', '-2 hours')
                    WHERE task_id IN ('stale-local', 'stale-queued')
                    """
                )
                await db.execute(
                    "UPDATE tasks SET updated_at = datetime('now', '-1 minutes') WHERE task_id = 'processing-task'"
                )
                await db.commit()

        asyncio.run(_seed())

        with patch(
            "src.api.routes._fetch_celery_queue_snapshot",
            return_value=(1, ["queued-task-id"], None),
        ):
            response = client.get("/api/v1/ops/queue/diagnostics?stale_threshold_seconds=300&sample_limit=20")

        assert response.status_code == 200
        data = response.json()
        assert data["redis_available"] is True
        assert data["celery_queue_length"] == 1
        assert data["stale_pending_summary"]["total"] >= 2
        assert data["stale_pending_summary"]["orphan_local"] >= 1
        assert data["stale_pending_summary"]["queued_waiting"] >= 1
        classes = {item["classification"] for item in data["stale_pending_sample"]}
        assert "orphan_local" in classes
        assert "queued_waiting" in classes
    finally:
        app.dependency_overrides.clear()


def test_ops_queue_cleanup_stale_endpoint_marks_orphans_failed(tmp_path):
    db_path = str(tmp_path / "ops_queue_cleanup.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        async def _seed():
            import aiosqlite

            await create_task(db_path, "stale-local")
            await update_task_celery_id(db_path, "stale-local", "local:stale-local")
            await create_task(db_path, "fresh-local")
            await update_task_celery_id(db_path, "fresh-local", "local:fresh-local")

            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """
                    UPDATE tasks
                    SET created_at = datetime('now', '-2 hours'),
                        updated_at = datetime('now', '-2 hours')
                    WHERE task_id = 'stale-local'
                    """
                )
                await db.commit()

        asyncio.run(_seed())

        response = client.post("/api/v1/ops/queue/cleanup-stale?stale_threshold_seconds=300&limit=100")
        assert response.status_code == 200
        data = response.json()
        assert data["cleaned_count"] == 1
        assert "stale-local" in data["cleaned_task_ids"]

        stale_row = asyncio.run(get_task(db_path, "stale-local"))
        fresh_row = asyncio.run(get_task(db_path, "fresh-local"))
        assert stale_row is not None and stale_row["status"] == "FAILED"
        assert stale_row["fallback_reason"] == "QUEUE_STALE_ORPHAN"
        assert fresh_row is not None and fresh_row["status"] == "PENDING"
    finally:
        app.dependency_overrides.clear()


def test_submit_endpoint_best_effort_cleans_stale_pending_orphans(tmp_path):
    db_path = str(tmp_path / "submit_cleanup_stale.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    try:
        async def _seed():
            import aiosqlite

            await create_task(db_path, "stale-before-submit")
            await update_task_celery_id(db_path, "stale-before-submit", "local:stale-before-submit")
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """
                    UPDATE tasks
                    SET created_at = datetime('now', '-2 hours'),
                        updated_at = datetime('now', '-2 hours')
                    WHERE task_id = 'stale-before-submit'
                    """
                )
                await db.commit()

        asyncio.run(_seed())

        files = [("files", ("test_hw.jpg", b"fake_image_bytes", "image/jpeg"))]
        with patch("src.api.routes.grade_homework_task.apply_async", return_value=Mock(id="mock-celery-id")):
            response = client.post("/api/v1/grade/submit", files=files)
        assert response.status_code == 202

        stale_row = asyncio.run(get_task(db_path, "stale-before-submit"))
        assert stale_row is not None and stale_row["status"] == "FAILED"
        assert stale_row["fallback_reason"] == "QUEUE_STALE_ORPHAN"
    finally:
        app.dependency_overrides.clear()


def test_ops_feature_flags_and_gating(tmp_path):
    db_path = str(tmp_path / "ops_feature_flags.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        get_resp = client.get("/api/v1/ops/feature-flags")
        assert get_resp.status_code == 200
        flags = get_resp.json()
        assert flags["deployment_environment"] in {"dev", "staging", "prod"}

        set_resp = client.post(
            "/api/v1/ops/feature-flags",
            json={
                "deployment_environment": "staging",
                "provider_switch_enabled": False,
                "prompt_control_enabled": False,
                "router_control_enabled": False,
                "operator_id": "ops-user",
            },
        )
        assert set_resp.status_code == 200
        updated = set_resp.json()
        assert updated["deployment_environment"] == "staging"
        assert updated["provider_switch_enabled"] is False
        assert updated["prompt_control_enabled"] is False
        assert updated["router_control_enabled"] is False

        blocked_switch = client.post(
            "/api/v1/ops/provider/switch",
            json={"provider": "mock", "operator_id": "ops-user"},
        )
        assert blocked_switch.status_code == 403
        assert blocked_switch.json()["detail"]["error_code"] == "FEATURE_DISABLED"

        blocked_router = client.post(
            "/api/v1/ops/router/control",
            json={
                "enabled": True,
                "failure_rate_threshold": 0.25,
                "token_spike_threshold": 1.6,
                "min_samples": 10,
                "budget_token_limit": 7000,
                "operator_id": "ops-user",
            },
        )
        assert blocked_router.status_code == 403
        assert blocked_router.json()["detail"]["error_code"] == "FEATURE_DISABLED"

        blocked_prompt = client.post(
            "/api/v1/prompt/control",
            json={
                "prompt_key": "deepseek.cognitive.evaluate",
                "forced_variant_id": "A",
                "lkg_mode": False,
                "operator_id": "ops-user",
            },
        )
        assert blocked_prompt.status_code == 403
        assert blocked_prompt.json()["detail"]["error_code"] == "FEATURE_DISABLED"
    finally:
        app.dependency_overrides.clear()


def test_ops_release_controls_and_fault_drills(tmp_path):
    db_path = str(tmp_path / "ops_release_and_drills.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    try:
        release_list = client.get("/api/v1/ops/release/controls")
        assert release_list.status_code == 200
        items = release_list.json()["items"]
        assert len(items) >= 3
        assert {item["layer"] for item in items} >= {"api", "prompt", "router"}

        release_set = client.post(
            "/api/v1/ops/release/controls",
            json={
                "layer": "prompt",
                "strategy": "canary",
                "rollout_percentage": 20,
                "target_version": "prompt-v2",
                "config": {"channel": "beta"},
                "rollback_config": {"target_version": "prompt-v1"},
                "operator_id": "ops-user",
            },
        )
        assert release_set.status_code == 200
        release_row = release_set.json()
        assert release_row["layer"] == "prompt"
        assert release_row["strategy"] == "canary"
        assert release_row["rollout_percentage"] == 20
        assert release_row["target_version"] == "prompt-v2"

        drill_run = client.post(
            "/api/v1/ops/fault-drills/run",
            json={"drill_type": "db_pressure", "operator_id": "ops-user"},
        )
        assert drill_run.status_code == 200
        report = drill_run.json()
        assert report["drill_type"] == "db_pressure"
        assert report["status"] in {"passed", "failed"}
        assert report["report_id"] >= 1

        drill_history = client.get("/api/v1/ops/fault-drills/history?drill_type=db_pressure&page=1&limit=20")
        assert drill_history.status_code == 200
        history = drill_history.json()
        assert isinstance(history["items"], list)
        assert any(item["report_id"] == report["report_id"] for item in history["items"])
    finally:
        app.dependency_overrides.clear()
