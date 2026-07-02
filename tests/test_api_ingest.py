import hashlib
import os


def test_add_file_preserves_raw_content_and_returns_batch(tmp_path):
    from mempalace.api_ingest import add_file

    content = "openapi: 3.0.0\npaths:\n  /api/v3/todo:\n    get: {}\n"

    result = add_file(
        palace_path=str(tmp_path),
        provider="Megaplan",
        filename="openapi.yaml",
        content=content,
    )

    assert result["success"] is True
    assert result["provider"] == "megaplan"
    assert result["batch_id"]
    assert result["raw_sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()

    raw_path = result["raw_path"]
    assert os.path.exists(raw_path)
    with open(raw_path, "rb") as fh:
        assert fh.read() == content.encode("utf-8")


def test_add_file_accepts_multiple_files_in_same_batch(tmp_path):
    from mempalace.api_ingest import add_file, batch_status

    first = add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        filename="openapi.yaml",
        content="openapi: 3.0.0\npaths: {}\n",
    )
    second = add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        batch_id=first["batch_id"],
        filename="quirks.md",
        content="# Quirks\nisCompleted silently resets the filter.\n",
    )

    assert second["success"] is True
    status = batch_status(str(tmp_path), first["batch_id"])
    assert status["success"] is True
    assert status["file_count"] == 2
    assert sorted(f["filename"] for f in status["files"]) == ["openapi.yaml", "quirks.md"]


def test_process_batch_writes_prepared_files_expected_wing_rooms_and_quirk_placeholder(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    first = add_file(
        palace_path=str(tmp_path),
        provider="Megaplan",
        filename="openapi.yaml",
        content="openapi: 3.0.0\npaths:\n  /api/v3/todo:\n    get: {}\n",
    )
    add_file(
        palace_path=str(tmp_path),
        provider="Megaplan",
        batch_id=first["batch_id"],
        filename="notes.md",
        content="# Authentication\nUse bearer tokens.\n",
    )

    result = process_batch(str(tmp_path), first["batch_id"])

    assert result["success"] is True
    assert result["wing"] == "api_megaplan"
    assert result["prepared_dir"].endswith(os.path.join(first["batch_id"], "prepared"))
    assert set(result["rooms"]) >= {"api_endpoints", "api_guides", "api_quirks"}
    assert len(result["prepared_files"]) == 3

    endpoint_file = next(f for f in result["prepared_files"] if f["room"] == "api_endpoints")
    with open(endpoint_file["path"], encoding="utf-8") as fh:
        body = fh.read()
    assert "Provider: megaplan" in body
    assert "Room: api_endpoints" in body
    assert "Source file: openapi.yaml" in body
    assert "/api/v3/todo" in body


def test_project_wing_override_is_sanitized_and_used(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="ideal13.megaplan.ru",
        filename="api.md",
        content="# API docs\n",
    )

    result = process_batch(str(tmp_path), added["batch_id"], wing="D CRM")

    assert result["success"] is True
    assert result["wing"] == "d_crm"


def test_process_batch_rebuilds_prepared_directory_from_manifest(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        filename="notes.md",
        content="# Authentication\nUse bearer tokens.\n",
    )
    first = process_batch(str(tmp_path), added["batch_id"])
    stale_path = next(f["path"] for f in first["prepared_files"] if f["room"] == "api_guides")
    assert os.path.exists(stale_path)

    add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        batch_id=added["batch_id"],
        filename="notes.md",
        content="openapi: 3.0.0\npaths: {}\n",
    )
    second = process_batch(str(tmp_path), added["batch_id"])

    assert second["success"] is True
    assert not os.path.exists(stale_path)
    assert "api_guides" not in second["rooms"]
    assert "api_endpoints" in second["rooms"]


def test_raml_resource_methods_are_classified_as_api_endpoints(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        filename="api_v3.raml",
        content=(
            "#%RAML 1.0\n"
            "types:\n"
            "  ContractorCompany:\n"
            "    properties:\n"
            "      name: string\n"
            "/contractorCompany:\n"
            "    post:\n"
            "        description: 'Создать нового клиента'\n"
            "        body:\n"
            "            type: ContractorCompany\n"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    assert "api_endpoints" in result["rooms"]
    endpoint_file = next(f for f in result["prepared_files"] if f["room"] == "api_endpoints")
    with open(endpoint_file["path"], encoding="utf-8") as fh:
        body = fh.read()
    assert "/contractorCompany:" in body
    assert "Создать нового клиента" in body


def test_openapi_fastapi_spec_splits_paths_and_component_schemas(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="fastapi",
        filename="openapi.json",
        content=(
            "{"
            '"openapi":"3.1.0",'
            '"info":{"title":"FastAPI","version":"1.0"},'
            '"paths":{'
            '"/items":{"get":{"operationId":"list_items","summary":"List items"}},'
            '"/items/{item_id}":{"post":{"operationId":"update_item","summary":"Update item"}}'
            "},"
            '"components":{"schemas":{"Item":{"type":"object","properties":{"name":{"type":"string"}}}}}'
            "}"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    endpoint_files = [f for f in result["prepared_files"] if f["room"] == "api_endpoints"]
    schema_files = [f for f in result["prepared_files"] if f["room"] == "api_schemas"]
    assert len(endpoint_files) == 2
    assert len(schema_files) == 1

    endpoint_bodies = []
    for entry in endpoint_files:
        with open(entry["path"], encoding="utf-8") as fh:
            endpoint_bodies.append(fh.read())
    assert any("GET /items" in body and "list_items" in body for body in endpoint_bodies)
    assert any("POST /items/{item_id}" in body and "update_item" in body for body in endpoint_bodies)


def test_raml_spec_splits_resources_and_types(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="megaplan",
        filename="api_v3.raml",
        content=(
            "#%RAML 1.0\n"
            "title: Megaplan\n"
            "types:\n"
            "  ContractorCompany:\n"
            "    properties:\n"
            "      name: string\n"
            "  Task:\n"
            "    properties:\n"
            "      subject: string\n"
            "/contractorCompany:\n"
            "  get:\n"
            "    description: Получение списка клиентов\n"
            "/task:\n"
            "  post:\n"
            "    description: Создать задачу\n"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    endpoint_files = [f for f in result["prepared_files"] if f["room"] == "api_endpoints"]
    schema_files = [f for f in result["prepared_files"] if f["room"] == "api_schemas"]
    assert len(endpoint_files) == 2
    assert len(schema_files) >= 1

    bodies = []
    for entry in endpoint_files + schema_files:
        with open(entry["path"], encoding="utf-8") as fh:
            bodies.append(fh.read())
    assert any("/contractorCompany:" in body for body in bodies)
    assert any("/task:" in body for body in bodies)
    assert any("ContractorCompany" in body and "Task" in body for body in bodies)


def test_xml_wsdl_splits_operations_messages_and_schemas(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="soap",
        filename="service.wsdl",
        content=(
            "<?xml version='1.0'?>"
            "<wsdl:definitions xmlns:wsdl='http://schemas.xmlsoap.org/wsdl/' "
            "xmlns:xsd='http://www.w3.org/2001/XMLSchema'>"
            "<wsdl:message name='GetClientRequest'/>"
            "<wsdl:portType name='ClientPort'>"
            "<wsdl:operation name='GetClient'><wsdl:input message='GetClientRequest'/></wsdl:operation>"
            "</wsdl:portType>"
            "<wsdl:types><xsd:schema><xsd:element name='Client' type='xsd:string'/></xsd:schema></wsdl:types>"
            "</wsdl:definitions>"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    endpoint_files = [f for f in result["prepared_files"] if f["room"] == "api_endpoints"]
    schema_files = [f for f in result["prepared_files"] if f["room"] == "api_schemas"]
    assert endpoint_files
    assert schema_files

    bodies = []
    for entry in endpoint_files + schema_files:
        with open(entry["path"], encoding="utf-8") as fh:
            bodies.append(fh.read())
    assert any("GetClient" in body for body in bodies)
    assert any("Client" in body and "xsd:element" in body for body in bodies)


def test_html_docs_are_split_by_headings_and_bounded_fallback_chunks(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    huge_section = "A" * 260_000
    added = add_file(
        palace_path=str(tmp_path),
        provider="htmlapi",
        filename="docs.html",
        content=(
            "<html><body>"
            "<h1>Authentication</h1><p>Use bearer tokens.</p>"
            "<h2>Clients</h2><p>GET /clients</p>"
            f"<h2>Huge Reference</h2><pre>{huge_section}</pre>"
            "</body></html>"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    prepared = [f for f in result["prepared_files"] if f["source_file"] == "docs.html"]
    assert len(prepared) >= 4
    sizes = [os.path.getsize(f["path"]) for f in prepared]
    assert max(sizes) < 160_000


def test_html_endpoint_blocks_are_normalized_to_readable_text(tmp_path):
    from mempalace.api_ingest import add_file, process_batch

    added = add_file(
        palace_path=str(tmp_path),
        provider="apidox",
        filename="docs.html",
        content=(
            "<html><body>"
            "<h2><div class='protocol'><span>POST</span></div>/api/v3/client</h2>"
            "<div class='apidox-reference'>Описание</div>"
            "<div class='apidox-description'>Создать клиента</div>"
            "<table><tr><th>Параметр</th><th>Тип</th></tr>"
            "<tr><td>name</td><td>string</td></tr></table>"
            "<pre>{\"name\":\"Alice\"}</pre>"
            "</body></html>"
        ),
    )

    result = process_batch(str(tmp_path), added["batch_id"])

    assert result["success"] is True
    endpoint_file = next(f for f in result["prepared_files"] if f["room"] == "api_endpoints")
    with open(endpoint_file["path"], encoding="utf-8") as fh:
        body = fh.read()

    assert "Block: POST /api/v3/client" in body
    assert "Создать клиента" in body
    assert "Параметр | Тип" in body
    assert "name | string" in body
    assert '{"name":"Alice"}' in body
    assert "<div" not in body
    assert "<table" not in body



def test_invalid_provider_returns_structured_error(tmp_path):
    from mempalace.api_ingest import add_file

    result = add_file(
        palace_path=str(tmp_path),
        provider="../bad",
        filename="api.md",
        content="text",
    )

    assert result["success"] is False
    assert result["error_class"] == "ValueError"
