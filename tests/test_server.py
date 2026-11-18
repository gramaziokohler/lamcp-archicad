"""Tests against a fake Archicad JSON API (no Archicad needed)."""

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastmcp import Client

from lamcp_archicad import server

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()

TAPIR = {
    "GetAddOnVersion": lambda p: {"version": "2.0.0"},
    "GetCommands": lambda p: {
        "commands": [
            {"name": "CreateWalls", "group": "Element Commands", "version": "1.4.0", "description": "Creates walls."},
            {"name": "GetStories", "group": "Project Commands", "version": "1.0.0", "description": "Lists stories."},
        ]
    },
    "GetStories": lambda p: {"stories": [{"index": 0, "name": "Ground"}, {"index": 1, "name": "First"}]},
    "CreateWalls": lambda p: {"elements": [{"elementId": {"guid": f"G{i}"}} for i, _ in enumerate(p["wallsData"])]},
    "Broken": lambda p: {"error": {"code": 42, "message": "nope"}},
    "GetCurrentViewImage": lambda p: {"image": PNG, "width": 1, "height": 1},
}


class FakeArchicad(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeArchicad.calls.append(body)
        command, params = body["command"], body["parameters"]
        if command == "API.GetProductInfo":
            reply = {"succeeded": True, "result": {"version": 29, "buildNumber": 3000, "languageCode": "INT"}}
        elif command == "API.ExecuteAddOnCommand":
            name = params["addOnCommandId"]["commandName"]
            handler = TAPIR.get(name)
            if handler is None:
                reply = {"succeeded": False, "error": {"code": 4000, "message": f"Unknown command {name}"}}
            else:
                response = handler(params["addOnCommandParameters"])
                reply = {"succeeded": True, "result": {"addOnCommandResponse": response}}
        else:
            reply = {"succeeded": False, "error": {"code": 4000, "message": f"Unknown command {command}"}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(autouse=True)
def archicad():
    httpd = HTTPServer(("127.0.0.1", 0), FakeArchicad)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    old = server._state["url"]
    server._state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    FakeArchicad.calls.clear()
    server._globals.clear()
    yield httpd
    server._state["url"] = old
    httpd.shutdown()


def test_health():
    health = server.bridge_health()
    assert health["error"] is None
    assert health["archicad"]["version"] == 29
    assert health["tapir_version"] == "2.0.0"


def test_health_unreachable():
    server._state["url"] = "http://127.0.0.1:1"
    health = server.bridge_health()
    assert "Cannot reach Archicad" in health["error"]


def test_run_command_tapir_wire_format():
    assert server.run_command("GetStories")["stories"][1]["name"] == "First"
    sent = FakeArchicad.calls[-1]
    assert sent["command"] == "API.ExecuteAddOnCommand"
    assert sent["parameters"]["addOnCommandId"] == {"commandNamespace": "TapirCommand", "commandName": "GetStories"}


def test_run_command_official():
    assert server.run_command("API.GetProductInfo")["version"] == 29


def test_run_command_errors():
    assert server.run_command("Broken") == {"error": "Broken: nope (code 42)"}
    assert "Unknown command" in server.run_command("API.Nope")["error"]


def test_list_commands_filter():
    assert server.list_commands()["count"] == 2
    assert [c["name"] for c in server.list_commands(search="wall")["commands"]] == ["CreateWalls"]
    assert server.list_commands(group="project")["count"] == 1


def test_script_helpers_and_persistent_globals():
    first = server.run_python_script(
        "walls = tapir('CreateWalls', {'wallsData': [{}, {}, {}]})['elements']\nprint('made', len(walls))"
    )
    assert first["error"] is None
    assert first["stdout"] == "made 3\n"
    second = server.run_python_script("_ = [w['elementId']['guid'] for w in walls]")
    assert second["result"] == ["G0", "G1", "G2"]


def test_script_error_and_non_json_result():
    failed = server.run_python_script("tapir('Broken')")
    assert "ArchicadError" in failed["error"]
    assert server.run_python_script("_ = {1, 2}")["result"] == "{1, 2}"


def test_script_timeout():
    result = server.run_python_script("import time; time.sleep(2)", timeout=0.1)
    assert "Timed out" in result["error"]


def test_describe_command_resolves_refs(monkeypatch):
    server._reference.clear()
    server._reference.update(
        {
            "commands": {
                "GetCurrentViewImage": {
                    "name": "GetCurrentViewImage",
                    "group": "Project Commands",
                    "version": "1.7.3",
                    "description": "Screenshot.",
                    "inputSchema": {"type": "object", "properties": {"source": {"$ref": "#/Src", "description": "d"}}},
                    "example": {"code": "print(1)"},
                }
            },
            "definitions": {
                "Src": {"type": "string", "enum": ["3DWindow"]},
                "Loop": {"type": "array", "items": {"$ref": "#/Loop"}},
            },
        }
    )
    out = server.describe_command("GetCurrentViewImage", include_example=True)
    assert out["inputSchema"]["properties"]["source"] == {"type": "string", "enum": ["3DWindow"], "description": "d"}
    assert out["example"] == "print(1)"
    assert server.describe_command("view")["similar"] == ["GetCurrentViewImage"]
    # recursive definitions don't loop forever
    assert server._resolve_refs({"$ref": "#/Loop"}, server._reference["definitions"])["items"] == {"$ref": "#/Loop"}
    server._reference.clear()


def test_capture_view(tmp_path):
    target = tmp_path / "view.png"
    image = server.capture_view(save_path=str(target))
    assert target.read_bytes().startswith(b"\x89PNG")
    assert image.data.startswith(b"\x89PNG")


def test_tools_registered_over_mcp():
    async def names():
        async with Client(server.mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert asyncio.run(names()) == {
        "bridge_health",
        "list_instances",
        "select_instance",
        "list_commands",
        "describe_command",
        "run_command",
        "run_python_script",
        "capture_view",
    }
