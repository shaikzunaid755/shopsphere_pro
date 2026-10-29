"""swagger / openapi checks and the postman collection"""
import json

from app.main import app
from scripts.export_postman import build_collection


def test_swagger_ui_and_openapi_load(client):
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"]
    assert len(spec["paths"]) >= 250


def test_every_endpoint_is_documented_with_a_tag():
    spec = app.openapi()
    missing = [f"{method.upper()} {path}" for path, ops in spec["paths"].items()
               for method, op in ops.items() if path != "/" and not op.get("tags")]
    assert missing == []


def test_module_sections_are_present():
    tags = {t for ops in app.openapi()["paths"].values() for op in ops.values() for t in op.get("tags", [])}
    for n in range(37, 50):
        assert any(t.startswith(f"{n} ") for t in tags), f"module {n} not in swagger"


def test_postman_collection_covers_every_endpoint():
    collection = build_collection(app.openapi())
    requests = [r for folder in collection["item"] for r in folder["item"]]
    spec_ops = sum(len(ops) for ops in app.openapi()["paths"].values())
    assert len(requests) == spec_ops
    assert collection["info"]["schema"].endswith("collection.json")
    json.dumps(collection)  # valid json
