import os
import base64
import time


def _patch(monkeypatch, config):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)


def test_api_ingest_tools_registered():
    from mempalace import mcp_server

    assert "mempalace_api_ingest_add_file" in mcp_server.TOOLS
    assert "mempalace_api_ingest_process" in mcp_server.TOOLS
    assert "mempalace_api_ingest_job_status" in mcp_server.TOOLS
    assert mcp_server.TOOLS["mempalace_api_ingest_add_file"]["handler"] is (
        mcp_server.tool_api_ingest_add_file
    )
    assert mcp_server.TOOLS["mempalace_api_ingest_process"]["handler"] is (
        mcp_server.tool_api_ingest_process
    )
    assert mcp_server.TOOLS["mempalace_api_ingest_job_status"]["handler"] is (
        mcp_server.tool_api_ingest_job_status
    )


def test_api_ingest_add_file_tool_stages_without_mining(monkeypatch, config):
    from mempalace import mcp_server

    _patch(monkeypatch, config)
    result = mcp_server.tool_api_ingest_add_file(
        provider="megaplan",
        filename="openapi.yaml",
        content="openapi: 3.0.0\npaths: {}\n",
    )

    assert result["success"] is True
    assert result["batch_id"]
    assert os.path.exists(result["raw_path"])


def test_api_ingest_process_tool_prepares_and_delegates_to_mine(monkeypatch, config):
    from mempalace import mcp_server

    _patch(monkeypatch, config)
    added = mcp_server.tool_api_ingest_add_file(
        provider="megaplan",
        filename="openapi.yaml",
        content="openapi: 3.0.0\npaths: {}\n",
    )

    calls = []

    def _fake_tool_mine(**kwargs):
        calls.append(kwargs)
        return {"success": True, "mode": kwargs["mode"], "dry_run": kwargs["dry_run"], "output": "ok"}

    monkeypatch.setattr(mcp_server, "tool_mine", _fake_tool_mine)
    result = mcp_server.tool_api_ingest_process(batch_id=added["batch_id"])

    assert result["success"] is True
    assert result["mine"]["success"] is True
    assert result["wing"] == "api_megaplan"
    assert "api_endpoints" in result["rooms"]
    assert calls == [
        {
            "source": result["prepared_dir"],
            "mode": "projects",
            "wing": "api_megaplan",
            "agent": "api_ingest",
            "limit": 0,
            "dry_run": False,
        }
    ]


def test_api_ingest_content_upload_uses_palace_owned_storage(monkeypatch, config):
    from mempalace import mcp_server

    _patch(monkeypatch, config)
    raw = b"#%RAML 1.0\n/contractorCompany:\n  post:\n    description: create client\n"
    added = mcp_server.tool_api_ingest_add_file(
        provider="megaplan",
        filename="api_v3.raml",
        content_base64=base64.b64encode(raw).decode("ascii"),
        source_url="https://ideal13.megaplan.ru/api/v3/",
    )

    assert added["success"] is True
    assert added["size_bytes"] == len(raw)
    assert added["raw_sha256"]
    assert config.palace_path in added["raw_path"]

    calls = []

    def _fake_tool_mine(**kwargs):
        calls.append(kwargs)
        return {"success": True, "mode": kwargs["mode"], "dry_run": kwargs["dry_run"], "output": "ok"}

    monkeypatch.setattr(mcp_server, "tool_mine", _fake_tool_mine)
    result = mcp_server.tool_api_ingest_process(batch_id=added["batch_id"])

    assert result["success"] is True
    assert config.palace_path in result["prepared_dir"]
    assert calls[0]["source"] == result["prepared_dir"]
    assert calls[0]["source"] != "https://ideal13.megaplan.ru/api/v3/"


def test_api_ingest_process_tool_returns_structured_error_on_processor_exception(
    monkeypatch, config
):
    from mempalace import mcp_server

    _patch(monkeypatch, config)

    def _boom(*args, **kwargs):
        raise RuntimeError("processor exploded")

    monkeypatch.setattr("mempalace.api_ingest.process_batch", _boom)
    result = mcp_server.tool_api_ingest_process(batch_id="missing")

    assert result["success"] is False
    assert result["error_class"] == "RuntimeError"
    assert "processor exploded" in result["error"]


def test_api_ingest_process_background_returns_job_and_status_completes(
    monkeypatch, config
):
    from mempalace import mcp_server

    _patch(monkeypatch, config)
    added = mcp_server.tool_api_ingest_add_file(
        provider="megaplan",
        filename="openapi.yaml",
        content="openapi: 3.0.0\npaths:\n  /todo:\n    get: {}\n",
    )

    def _fake_tool_mine(**kwargs):
        return {
            "success": True,
            "mode": kwargs["mode"],
            "dry_run": kwargs["dry_run"],
            "output": "background ok",
        }

    monkeypatch.setattr(mcp_server, "tool_mine", _fake_tool_mine)
    result = mcp_server.tool_api_ingest_process(
        batch_id=added["batch_id"],
        background=True,
    )

    assert result["success"] is True
    assert result["accepted"] is True
    assert result["status"] in {"queued", "running"}
    assert result["job_id"]
    assert result["batch_id"] == added["batch_id"]

    status = None
    for _ in range(50):
        status = mcp_server.tool_api_ingest_job_status(job_id=result["job_id"])
        if status["status"] == "succeeded":
            break
        time.sleep(0.02)

    assert status["success"] is True
    assert status["status"] == "succeeded"
    assert status["result"]["success"] is True
    assert status["result"]["mine"]["output"] == "background ok"


def test_api_ingest_job_status_reports_unknown_job():
    from mempalace import mcp_server

    result = mcp_server.tool_api_ingest_job_status(job_id="missing-job")

    assert result["success"] is False
    assert result["error_class"] == "NotFound"
    assert "missing-job" in result["error"]
