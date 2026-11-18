"""LAMCP-Archicad: Lambda MCP server bridging LLMs to Archicad via Tapir.

Architecture::

    Claude ──MCP stdio──▶ lamcp-archicad (this process, Python 3.10+)
                              │
                              │  HTTP POST  (Archicad's own JSON API, port 19723+)
                              ▼
                      Archicad + Tapir Add-On
                          ├─ official commands   "API.<Name>"
                          └─ Tapir commands      API.ExecuteAddOnCommand / TapirCommand

Unlike LAMCP for Grasshopper, there is no bridge to install: Archicad already
serves a JSON API, and the Tapir Add-On extends it with several hundred
commands. Archicad runs every command on its own main thread, so nothing we
send can cause a cross-thread crash.

The "lambda" is :func:`run_python_script`: it exec()s code **in this process**
with ``tapir()`` / ``archicad()`` helpers pre-bound, so one tool call can read,
filter and change many elements instead of one command per tool call.

The Archicad URL is read from ``LAMCP_ARCHICAD_URL``
(default ``http://127.0.0.1:19723``) and can be switched at runtime with
:func:`select_instance`.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.utilities.types import Image

DEFAULT_URL = "http://127.0.0.1:19723"
DEFAULT_TIMEOUT = 60.0
# Archicad takes the first free port of this range, one per running instance.
PORT_RANGE = range(19723, 19745)
TAPIR_NAMESPACE = "TapirCommand"
COMMANDS_URL = os.environ.get("LAMCP_ARCHICAD_COMMANDS_URL", "https://archicad-api.com/commands.json")
CACHE_DIR = Path(os.environ.get("LAMCP_ARCHICAD_CACHE", Path.home() / ".cache" / "lamcp-archicad"))
CACHE_TTL = 24 * 3600

_state: dict[str, Any] = {"url": os.environ.get("LAMCP_ARCHICAD_URL", DEFAULT_URL)}


class ArchicadError(Exception):
    """A command failed, or Archicad could not be reached."""


# ---------------------------------------------------------------------------
# JSON API client
# ---------------------------------------------------------------------------


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or "Unknown error"
        code = error.get("code")
        return f"{message} (code {code})" if code is not None else message
    return str(error)


def _post(command: str, parameters: dict | None, url: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = url or _state["url"]
    try:
        response = httpx.post(url, json={"command": command, "parameters": parameters or {}}, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise ArchicadError(
            f"Cannot reach Archicad at {url}: {exc!r}. Is Archicad running with a project open?"
        ) from exc
    except ValueError as exc:
        raise ArchicadError(f"Archicad at {url} returned invalid JSON: {exc}") from exc
    if not body.get("succeeded", False):
        raise ArchicadError(f"{command}: {_error_text(body.get('error'))}")
    return body.get("result") or {}


def archicad(command: str, parameters: dict | None = None) -> dict:
    """Run an official Archicad JSON command (``"API."`` is added when missing)."""
    if not command.startswith("API."):
        command = "API." + command
    return _post(command, parameters)


def tapir(command: str, parameters: dict | None = None) -> dict:
    """Run a Tapir Add-On command and return its response."""
    result = archicad(
        "API.ExecuteAddOnCommand",
        {
            "addOnCommandId": {"commandNamespace": TAPIR_NAMESPACE, "commandName": command},
            "addOnCommandParameters": parameters or {},
        },
    )
    response = result.get("addOnCommandResponse")
    if response is None:
        raise ArchicadError(f"{command} returned no response. Is the Tapir Add-On installed?")
    if isinstance(response, dict) and "error" in response:
        raise ArchicadError(f"{command}: {_error_text(response['error'])}")
    return response


def run(command: str, parameters: dict | None = None) -> dict:
    """``"API.X"`` → official command, anything else → Tapir command."""
    if command.startswith("API."):
        return archicad(command, parameters)
    return tapir(command, parameters)


def batched(items: list, size: int = 1000):
    """Yield ``items`` in slices of ``size`` so one call doesn't get too large."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ---------------------------------------------------------------------------
# Command reference (Tapir's published commands.json)
# ---------------------------------------------------------------------------

_reference: dict[str, Any] = {}


def _load_reference() -> dict:
    """Tapir's command reference, cached on disk for a day."""
    if _reference:
        return _reference
    cache = CACHE_DIR / "commands.json"
    data = None
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(cache.read_text(encoding="utf-8"))
    if data is None:
        try:
            response = httpx.get(COMMANDS_URL, timeout=30, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
            with contextlib.suppress(OSError):
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(data), encoding="utf-8")
        except (httpx.HTTPError, ValueError) as exc:
            if cache.exists():  # stale beats nothing
                data = json.loads(cache.read_text(encoding="utf-8"))
            else:
                raise ArchicadError(f"Cannot download the Tapir command reference from {COMMANDS_URL}: {exc}") from exc
    _reference["commands"] = {c["name"]: c for c in data.get("commands", [])}
    _reference["definitions"] = data.get("schemaDefinitions", {})
    return _reference


def _resolve_refs(schema: Any, definitions: dict, seen: frozenset = frozenset()) -> Any:
    """Inline ``{"$ref": "#/Name"}`` from Tapir's schemaDefinitions (cycle-safe)."""
    if isinstance(schema, list):
        return [_resolve_refs(s, definitions, seen) for s in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        name = ref[2:]
        if name in seen or name not in definitions:
            return schema  # leave recursive / unknown refs as they are
        extra = {k: v for k, v in schema.items() if k != "$ref"}
        return {**_resolve_refs(definitions[name], definitions, seen | {name}), **extra}
    return {k: _resolve_refs(v, definitions, seen) for k, v in schema.items()}


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

INSTRUCTIONS = """\
Controls a running Archicad through the Tapir Add-On's JSON commands.

Workflow:
- Start with bridge_health. If several Archicads run, list_instances + select_instance.
- Find commands with list_commands, then read describe_command before calling one:
  never guess parameter names.
- Use run_command for single calls; use run_python_script for anything that loops,
  filters or chains commands (one script beats many tool calls).
- Lengths are in meters, angles in radians. Elements are addressed as
  {"elementId": {"guid": "..."}}.

Safety:
- Before using a name taken from the user's words (story, property, classification,
  layer, building material, composite, surface, zone category, element type), look it up
  with the Tapir command ResolveNames. found: use the returned ids. closeMatches: ask the
  user which one they mean. notFound: say the project has no such item. Never guess.
- Before a command that deletes or replaces, run it with "dryRun": true if it has that
  input, and show the user what would change.
- Model changes can be undone in Archicad (Ctrl+Z), but prefer reading first.
"""

mcp = FastMCP("lamcp-archicad", instructions=INSTRUCTIONS)


def _error(exc: Exception) -> dict:
    return {"error": str(exc)}


@mcp.tool()
def bridge_health() -> dict:
    """Check that Archicad and the Tapir Add-On are reachable.

    Returns
    -------
    dict
        ``{"url", "archicad": {version, buildNumber, ...} | None,
        "tapir_version": str | None, "error": str | None}``.
    """
    return _health()


def _health() -> dict:
    out: dict[str, Any] = {"url": _state["url"], "archicad": None, "tapir_version": None, "error": None}
    try:
        out["archicad"] = archicad("API.GetProductInfo")
        out["tapir_version"] = tapir("GetAddOnVersion").get("version")
    except ArchicadError as exc:
        out["error"] = str(exc)
    return out


@mcp.tool()
def list_instances() -> list[dict]:
    """Scan ports 19723-19744 for running Archicad instances.

    Returns
    -------
    list of dict
        ``[{"port", "url", "archicad": {...}, "selected": bool}]``.
    """

    def probe(port: int) -> dict | None:
        url = f"http://127.0.0.1:{port}"
        try:
            info = _post("API.GetProductInfo", {}, url=url, timeout=1.0)
        except ArchicadError:
            return None
        return {"port": port, "url": url, "archicad": info, "selected": url == _state["url"]}

    # In parallel: on Windows a refused connection takes ~0.5 s per port.
    with ThreadPoolExecutor(max_workers=len(PORT_RANGE)) as pool:
        return [hit for hit in pool.map(probe, PORT_RANGE) if hit]


@mcp.tool()
def select_instance(port: int) -> dict:
    """Send all further commands to the Archicad listening on ``port``."""
    _state["url"] = f"http://127.0.0.1:{port}"
    return _health()


@mcp.tool()
def list_commands(search: str | None = None, group: str | None = None) -> dict:
    """List the Tapir commands the installed Add-On offers.

    Comes from the live Add-On (``GetCommands``), so it matches the installed
    version. Official commands (``API.*``) are not listed; see Graphisoft's JSON
    API reference for those.

    Parameters
    ----------
    search : str, optional
        Case-insensitive filter on name and description, e.g. ``"wall"``.
    group : str, optional
        Case-insensitive filter on the command group, e.g. ``"Element Commands"``.

    Returns
    -------
    dict
        ``{"count", "commands": [{"name", "group", "description"}]}``.
    """
    try:
        commands = tapir("GetCommands").get("commands", [])
    except ArchicadError as exc:
        return _error(exc)
    if search:
        needle = search.lower()
        commands = [c for c in commands if needle in c["name"].lower() or needle in c.get("description", "").lower()]
    if group:
        commands = [c for c in commands if group.lower() in c.get("group", "").lower()]
    rows = [{"name": c["name"], "group": c.get("group"), "description": c.get("description")} for c in commands]
    return {"count": len(rows), "commands": rows}


@mcp.tool()
def describe_command(name: str, include_output_schema: bool = False, include_example: bool = False) -> dict:
    """Full reference of one Tapir command: description and input schema.

    Read this before calling a command you haven't used in this session.
    Schema ``$ref``s are resolved inline.

    Parameters
    ----------
    name : str
        Tapir command name, e.g. ``"CreateWalls"``.
    include_output_schema : bool
        Also return the output schema (can be long).
    include_example : bool
        Also return Tapir's example script for the command, if any.
    """
    try:
        ref = _load_reference()
    except ArchicadError as exc:
        return _error(exc)
    command = ref["commands"].get(name)
    if command is None:
        close = [n for n in ref["commands"] if name.lower() in n.lower()][:20]
        return {"error": f"No Tapir command named {name!r}.", "similar": close}
    out = {
        "name": command["name"],
        "group": command.get("group"),
        "since_tapir": command.get("version"),
        "description": command.get("description"),
        "inputSchema": _resolve_refs(command.get("inputSchema"), ref["definitions"]),
    }
    if include_output_schema:
        out["outputSchema"] = _resolve_refs(command.get("outputSchema"), ref["definitions"])
    if include_example and command.get("example"):
        out["example"] = command["example"].get("code")
    return out


@mcp.tool()
def run_command(command: str, parameters: dict | None = None) -> dict:
    """Run one Archicad command and return its result.

    Parameters
    ----------
    command : str
        A Tapir command name (``"GetStories"``, ``"CreateWalls"``, ...) or an
        official command prefixed with ``"API."`` (``"API.GetAllElements"``).
    parameters : dict, optional
        The command's input, as in ``describe_command``.

    Returns
    -------
    dict
        The command's response, or ``{"error": str}``.
    """
    try:
        return run(command, parameters)
    except ArchicadError as exc:
        return _error(exc)


_globals: dict[str, Any] = {}


def _script_globals() -> dict:
    if not _globals:
        _globals.update(
            {
                "__name__": "__lamcp_archicad__",
                "tapir": tapir,
                "archicad": archicad,
                "run": run,
                "batched": batched,
                "ArchicadError": ArchicadError,
                "json": json,
            }
        )
    return _globals


def _to_result(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


@mcp.tool()
def run_python_script(code: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Execute Python code that drives Archicad, in the MCP server's process.

    Pre-bound helpers (all raise ``ArchicadError`` on failure):

    - ``tapir(name, params=None) -> dict``: run a Tapir command
    - ``archicad(name, params=None) -> dict``: run an official ``API.*`` command
    - ``run(name, params=None)``: dispatch on the ``"API."`` prefix
    - ``batched(items, size=1000)``: chunk long element lists
    - ``json``

    Globals persist between calls, so state accumulates (fetch once, reuse).
    To return a value, assign it to ``_``; JSON-serialisable values come back
    as-is, anything else as its ``repr``.

    Example::

        walls = tapir("GetElementsByType", {"elementType": "Wall"})["elements"]
        _ = len(walls)

    Parameters
    ----------
    code : str
        Python source.
    timeout : float, optional
        Seconds to wait. On timeout the script keeps running in the background.

    Returns
    -------
    dict
        ``{"stdout", "stderr", "result", "error"}``; ``error`` is a traceback or None.
    """
    g = _script_globals()
    g.pop("_", None)
    out, err = io.StringIO(), io.StringIO()
    box: dict[str, Any] = {"error": None}

    def _target():
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                exec(compile(code, "<lamcp-archicad>", "exec"), g, g)
        except BaseException:
            box["error"] = traceback.format_exc()

    worker = threading.Thread(target=_target, daemon=True, name="lamcp-archicad-script")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        box["error"] = f"Timed out after {timeout}s; the script is still running in the background."
    return {
        "stdout": out.getvalue(),
        "stderr": err.getvalue(),
        "result": _to_result(g.get("_")),
        "error": box["error"],
    }


@mcp.tool()
def capture_view(source: str = "3DWindow", image_format: str = "png", save_path: str | None = None):
    """Screenshot what Archicad's 3D window (or PhotoRendering window) shows.

    Uses Tapir's ``GetCurrentViewImage`` (Archicad 30+, 3D window must be open).

    Parameters
    ----------
    source : str
        ``"3DWindow"`` or another ``ViewImageSource`` value (see
        ``describe_command("GetCurrentViewImage")``).
    image_format : str
        ``"png"`` or ``"jpg"``.
    save_path : str, optional
        Also write the image to this file.
    """
    try:
        response = tapir("GetCurrentViewImage", {"source": source, "format": image_format})
    except ArchicadError as exc:
        return _error(exc)
    data = base64.b64decode(response["image"])
    if save_path:
        Path(save_path).write_bytes(data)
    return Image(data=data, format="jpeg" if image_format == "jpg" else image_format)


def main():
    """Run the MCP server on stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
