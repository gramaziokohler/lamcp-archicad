# LAMCP-Archicad

[![PyPI](https://img.shields.io/pypi/v/lamcp-archicad)](https://pypi.org/project/lamcp-archicad/)
[![Python](https://img.shields.io/pypi/pyversions/lamcp-archicad)](https://pypi.org/project/lamcp-archicad/)
[![build](https://github.com/gramaziokohler/lamcp-archicad/actions/workflows/build.yml/badge.svg)](https://github.com/gramaziokohler/lamcp-archicad/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/gramaziokohler/lamcp-archicad/blob/main/LICENSE)

**LA**mbda **MCP** for Archicad: teach your LLM to do Archicad tricks.

LAMCP-Archicad lets Claude Code (or any MCP client) work in a live
Archicad project: read elements and properties, draw walls, slabs, roofs,
doors and windows, change parameters, take screenshots, and run short Python
scripts that chain many commands in one go.

Everything goes through the
[Tapir Add-On](https://github.com/ENZYME-APD/tapir-archicad-automation) and
Archicad's own JSON API, so there is no bridge to install and nothing to
compile.

Sibling of [LAMCP](https://github.com/gramaziokohler/lamcp) for Grasshopper.

> **Try it on a copy first.** The tools can create, change and delete model
> elements. Archicad's Undo (Ctrl+Z) reverts model changes, but not file
> operations such as saving or Teamwork send.

## Quick start

Already have Archicad with the Tapir Add-On, [uv](https://docs.astral.sh/uv/)
and Claude Code? One command:

```bash
claude mcp add lamcp-archicad --scope user -- uvx lamcp-archicad
```

Open a project in Archicad, start a new Claude chat and ask:
*"Use lamcp-archicad to check the connection to Archicad."*

Starting from scratch? Follow the four steps below.

## What you need

| Requirement                    | Notes                                                                 |
| ------------------------------ | --------------------------------------------------------------------- |
| **Archicad 25–30**             | Windows or macOS. `capture_view` needs Archicad 30.                   |
| **Tapir Add-On**               | The latest release. See step 1 below.                                 |
| **uv**                         | Runs the server and fetches Python for you. See step 2 below.         |
| **Claude Code** or another MCP client | Claude Code in the terminal, VS Code or the desktop app, Claude Desktop, etc. |

You don't need to install Python yourself, because uv takes care of it.

## Installation

### 1. Install the Tapir Add-On in Archicad

Download from the
[latest Tapir release](https://github.com/ENZYME-APD/tapir-archicad-automation/releases/latest).
Use either method:

- **Installer (Windows, easiest).** Close Archicad, then run
  `TapirInstaller_Win.exe` from the release page. It asks for administrator
  rights because it copies files into the Archicad folder.
- **Manual.** Download the `.apx` (Windows) or `.bundle` (macOS) file that
  matches your Archicad version, for example `TapirAddOn_AC29_Win.apx`, and
  save it somewhere permanent (not Downloads). In Archicad, go to
  **Options → Add-On Manager → Edit List of Available Add-Ons → Add**, pick
  the file, then click **OK**.

**Check:** after you restart Archicad, a **Tapir** menu appears in the menu
bar. **Tapir → Tapir Palette** shows the installed version.

### 2. Install uv

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Afterwards, open a new terminal so `uv` and `uvx` are on your PATH. See the
[uv docs](https://docs.astral.sh/uv/getting-started/installation/) for
other options.

### 3. Register the server with Claude Code

```bash
claude mcp add lamcp-archicad --scope user -- uvx lamcp-archicad
```

`--scope user` makes the tools available in every project. Restart Claude
Code, or in VS Code run **Developer: Reload Window**, then start a new chat.
Run `claude mcp list` to check that `lamcp-archicad` is listed.

`uvx` downloads the package from [PyPI](https://pypi.org/project/lamcp-archicad/)
on first use and runs it in its own isolated environment, so nothing is
installed into your system Python.

<details>
<summary>Install with pip instead of uv</summary>

If you'd rather manage Python yourself (3.10 or newer), install the package
into a virtual environment, or with `pipx`:

```bash
pip install lamcp-archicad
# or
pipx install lamcp-archicad
```

This puts a `lamcp-archicad` command on your PATH. Register that command
instead of `uvx`:

```bash
claude mcp add lamcp-archicad --scope user -- lamcp-archicad
```

If you used a virtual environment, give the full path to the command inside
it (for example `C:\path\to\venv\Scripts\lamcp-archicad.exe`), because
Claude Code doesn't activate the environment for you.

</details>

<details>
<summary>Install the latest development version from GitHub instead</summary>

```bash
claude mcp add lamcp-archicad --scope user -- uvx --from git+https://github.com/gramaziokohler/lamcp-archicad lamcp-archicad
```

</details>

<details>
<summary>Other MCP clients (Claude Desktop, Cursor, …)</summary>

The server talks MCP over stdio. Add it to your client's MCP configuration,
for example in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lamcp-archicad": {
      "command": "uvx",
      "args": ["lamcp-archicad"]
    }
  }
}
```

</details>

### 4. Open a project and try it

1. Start Archicad and **open a project**. Archicad only serves its JSON API
   while a project is open.
2. Ask Claude something like:
   - *"Use lamcp-archicad to check the connection to Archicad."*
   - *"List the stories and count the walls on each."*
   - *"Draw a 6 × 4 m room on the ground floor with a slab, a hip roof, a
     door and a window in each wall."*

If the first request reports Archicad's version and the Tapir version,
you're set up.

### Updating and uninstalling

`uvx` caches the version it first downloaded. To pick up a new release,
clear that cache and restart Claude Code:

```bash
uv cache clean lamcp-archicad
```

To always run the newest release instead, register the server with
`uvx lamcp-archicad@latest`. It then checks PyPI on every start, which
is slightly slower. With pip, run `pip install -U lamcp-archicad`
(or `pipx upgrade lamcp-archicad`).

To remove it from Claude Code:

```bash
claude mcp remove lamcp-archicad --scope user
```

## Troubleshooting

| Symptom                                                                 | Cause and fix                                                                                                                                                       |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No Archicad found, or connection refused                                | Archicad isn't running or has no project open. Open a project. With several Archicads open, ask Claude to `list_instances` and `select_instance`.                   |
| `Invalid program status (ongoing user input)` (code 4001)               | Archicad is in the middle of an input: a half-drawn element, an open dialog, or an edit mode (curtain wall, stair…). Press **Esc**, close dialogs, and try again.  |
| Commands hang, then time out                                            | A modal dialog is open in Archicad. Close it.                                                                                                                       |
| `Archicad does not have the registered Add-On command …` (code 4010)    | Tapir isn't loaded, or the command is newer than your Tapir. Check the **Tapir** menu, then update Tapir.                                                           |
| `Invalid command parameters … additionalProperties … path '#/…/<field>'` (code 4002) | Your Tapir is older than the command reference `describe_command` reads, so it doesn't accept that field yet. Update Tapir, or leave the field out.                  |
| `capture_view`: `Available from Archicad 30`                            | Screenshots need Archicad 30 and an open 3D window.                                                                                                                 |
| `uvx` or `claude` is not recognized                                     | Open a new terminal after installing, or add their install folder to your PATH.                                                                                     |

## How it works

```text
LLM ──MCP stdio──▶ lamcp-archicad (Python 3.10+)
                          │
                          │  HTTP POST  {"command": "API.ExecuteAddOnCommand", ...}
                          ▼
                  Archicad JSON API on 127.0.0.1:19723 (+1 per extra instance)
                     ├─ official commands  (API.*)
                     └─ Tapir Add-On       (several hundred commands)
```

Unlike LAMCP, this needs no bridge, because Archicad already serves a JSON
API and runs every command on its own main thread. The "lambda" part,
`run_python_script`, runs your code **in the MCP server's process** and
sends commands to Archicad. A broken script can't crash Archicad; only the
commands it sends reach Archicad.

## Tools

| Tool                | Purpose                                                                    |
| ------------------- | -------------------------------------------------------------------------- |
| `bridge_health`     | Checks that Archicad and Tapir are reachable, and reports their versions.  |
| `list_instances`    | Finds every running Archicad (ports 19723–19744).                          |
| `select_instance`   | Sends further commands to the Archicad on a given port.                    |
| `list_commands`     | Lists the commands the installed Tapir offers, filterable by text or group. |
| `describe_command`  | Shows one command's description and input schema (optionally its output schema and an example). |
| `run_command`       | Runs one Tapir command, or an official one as `API.<Name>`.                |
| `run_python_script` | Runs Python with `tapir()` / `archicad()` helpers; globals persist between calls. |
| `capture_view`      | Takes a screenshot of the 3D window as an inline image (Archicad 30+).     |

### `run_python_script`

These helpers are available in the script's globals:

| Name                          | Does                                                     |
| ----------------------------- | -------------------------------------------------------- |
| `tapir(name, params=None)`    | Runs a Tapir command and returns its response dict.      |
| `archicad(name, params=None)` | Runs an official command (`API.` is added when missing). |
| `run(name, params=None)`      | Sends `API.*` to `archicad` and anything else to `tapir`. |
| `batched(items, size=1000)`   | Splits long element lists into chunks.                   |
| `ArchicadError`               | Raised by the helpers when a command fails.              |

Assign to `_` to return a value. Globals persist between calls:

```python
# call 1
walls = tapir("GetElementsByType", {"elementType": "Wall"})["elements"]
# call 2
_ = len(walls)   # `walls` is still bound
```

Every call returns `{"stdout", "stderr", "result", "error"}`. `result` is
`_` itself when it's JSON-serialisable, otherwise its `repr`. `error` is a
traceback, or `null` when the script succeeded.

### Command reference vs. installed Add-On

`describe_command` reads the latest published Tapir reference (cached for a
day), while `list_commands` asks your installed Add-On. If a command is
described but not listed, or a field is rejected as an
`additionalProperties` error, your Tapir is older than the reference.
Update Tapir.

## Configuration

| Variable                      | Default                                   | Purpose                           |
| ----------------------------- | ----------------------------------------- | --------------------------------- |
| `LAMCP_ARCHICAD_URL`          | `http://127.0.0.1:19723`                  | The Archicad JSON API to talk to. |
| `LAMCP_ARCHICAD_COMMANDS_URL` | `https://archicad-api.com/commands.json`  | The Tapir command reference.      |
| `LAMCP_ARCHICAD_CACHE`        | `~/.cache/lamcp-archicad`                 | Where the reference is cached.    |

To set one with Claude Code, use `-e`:

```bash
claude mcp add lamcp-archicad --scope user -e LAMCP_ARCHICAD_URL=http://127.0.0.1:19724 -- uvx lamcp-archicad
```

## Security

`run_python_script` runs arbitrary Python with your user's rights, like any
code Claude Code runs. The Archicad JSON API listens on `127.0.0.1` only and
has no authentication; that is Archicad's design, not ours.

## Development

```bash
git clone https://github.com/gramaziokohler/lamcp-archicad
cd lamcp-archicad
uv venv && uv pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
```

The tests run against a fake Archicad (`tests/`), so you don't need
Archicad to run them.

To point Claude Code at your working copy instead of the published package:

```bash
claude mcp add lamcp-archicad --scope user -- uv run --directory /path/to/lamcp-archicad lamcp-archicad
```

Releases are tag-driven: push a `v*` tag and CI publishes to PyPI.

## License

MIT
