"""
Builds a Postman collection (v2.1) from the app's OpenAPI schema, one folder per module.

run from project root:
    python -m scripts.export_postman
writes postman/ShopSphere_Pro.postman_collection.json
"""
import json
import os

SAMPLE = {"string": "text", "integer": 1, "number": 100.0, "boolean": False}


def _resolve(schema, spec):
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return spec["components"]["schemas"][name]
    if "anyOf" in schema:
        first = next((s for s in schema["anyOf"] if s.get("type") != "null"), {})
        return _resolve(first, spec)
    return schema


def example(schema, spec, depth=0):
    schema = _resolve(schema, spec)
    if depth > 3:
        return None
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: example(v, spec, depth + 1) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [example(schema.get("items", {}), spec, depth + 1)]
    if schema.get("format") == "email":
        return "rahul@example.com"
    if schema.get("format") == "date":
        return "2026-10-29"
    return SAMPLE.get(kind, "text")


def build_collection(spec):
    folders = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            tag = (op.get("tags") or ["Other"])[0]
            url_path = path.replace("{", "{{").replace("}", "}}")
            request = {
                "method": method.upper(),
                "header": [{"key": "Authorization", "value": "Bearer {{token}}"}],
                "url": {"raw": "{{baseUrl}}" + url_path, "host": ["{{baseUrl}}"],
                        "path": [p for p in url_path.split("/") if p],
                        "query": [{"key": q["name"], "value": "", "disabled": True}
                                  for q in op.get("parameters", []) if q["in"] == "query"]},
                "description": op.get("description") or op.get("summary", ""),
            }
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                request["header"].append({"key": "Content-Type", "value": "application/json"})
                request["body"] = {"mode": "raw", "raw": json.dumps(example(body["schema"], spec), indent=2)}
            folders.setdefault(tag, []).append({"name": op.get("summary") or f"{method.upper()} {path}",
                                                "request": request})
    return {
        "info": {"name": "ShopSphere Pro API",
                 "description": "Login with POST /auth/login (customers) or /auth/staff/login (staff), "
                                "copy access_token into the 'token' collection variable.",
                 "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "variable": [{"key": "baseUrl", "value": "http://127.0.0.1:8000"}, {"key": "token", "value": ""}],
        "item": [{"name": tag, "item": items} for tag, items in sorted(folders.items())],
    }


if __name__ == "__main__":
    from app.main import app

    os.makedirs("postman", exist_ok=True)
    out = os.path.join("postman", "ShopSphere_Pro.postman_collection.json")
    collection = build_collection(app.openapi())
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(collection, fh, indent=2)
    print("saved", out, "-", sum(len(f["item"]) for f in collection["item"]), "requests")
