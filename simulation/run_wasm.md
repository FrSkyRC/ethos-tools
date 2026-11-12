# `run_wasm.js`

A small Node.js runner for executing the Ethos simulator's **WebAssembly** (Emscripten) build from the command line, without a browser. It loads the `simulator.js` module, starts the simulator, and can optionally drive a Lua macro in an automated way (tests, regression checks, CI...). With `--serve` it keeps the simulator running so it can be operated interactively: screenshots, touch, keys, switches (see [Interactive mode](#interactive-mode---serve)).

## Prerequisites

- Node.js 18+
- An Ethos WASM build with a "simulator" file (e.g. `X20S_FCC.js`) alongside its `.wasm`

## Usage

```text
node run_wasm.js <simulator.js> [--root-directory DIR] [--macro path[.lua]] [--macro-timeout MS] [--serve [--port N] [--shots-dir DIR]]
node run_wasm.js <command> [args] [--port N]
```

### Positional argument

- `<simulator.js>` — path to the simulator file (resolved to an absolute path). This is the only required argument.

### Options

| Option | Description |
| --- | --- |
| `--root-directory DIR` | Mounts `DIR` (the host's real filesystem, via `NODEFS`) at the current model's location in the simulator's virtual filesystem. If omitted, an in-memory `MEMFS` (not persisted) is used instead. |
| `--macro path[.lua]`, `--exec path[.lua]` | Path to a Lua macro to run automatically once the simulator has started. The script starts it (`startMacro(path, false)`), then waits for it to finish before exiting. |
| `--macro-timeout MS` | Maximum delay (in ms) tolerated for the macro to finish. If exceeded, the script fails with a timeout error. Without this option, the wait is unbounded. |
| `--serve` | Keeps the simulator running after start (and after `--macro`, if given) and accepts commands on `http://127.0.0.1:PORT`. See [Interactive mode](#interactive-mode---serve). |
| `--port N` | Port for `--serve` and for client commands (default `8765`). |
| `--shots-dir DIR` | Default folder for `screenshot` files (default `./screenshots`). |
| `-h`, `--help` | Prints usage and exits. |

## Behavior

1. Loads and instantiates the simulator module (`simulator.js`), passing it `print`/`printErr` redirected to stdout/stderr.
2. Creates `/persist` as well as the current model's directory in the simulator's virtual filesystem, then mounts either `--root-directory` (NODEFS) or a `MEMFS` by default.
3. Starts the simulator (`start`).
4. If `--macro` is given, runs the macro and waits for it to finish (see `--macro-timeout`).
5. Exits with code `0` on success, or, with `--serve`, keeps running until the `quit` command.

## Exit codes

- `0` — completed successfully.
- `1` — unhandled error during execution (see the message printed to stderr), e.g. a macro timeout.
- `2` — missing `<simulator.js>` positional argument.
- `-1` — the directory passed to `--root-directory` doesn't exist.

## Examples

Start the simulator with an in-memory filesystem, no macro:

```text
node run_wasm.js X20S_FCC/X20S_FCC.js
```

Mount a real models directory and run a Lua macro with a 10s timeout:
```text
node run_wasm.js X20S_FCC/X20S_FCC.js --root-directory . --macro USER:/macros/x20s.lua --macro-timeout 10000
```

## Interactive mode (`--serve`)

With `--serve`, the simulator keeps running behind a small HTTP server on `127.0.0.1`. Run `run_wasm.js` again with a command name instead of a simulator path, and it acts as a one-shot client: it sends that one command and prints the JSON result. This mode is the backend of the [`ethos:navigate`](skills/navigate/SKILL.md) Claude Code skill (see the [README](README.md#claude-code-plugin-ethos)).

| Command | Effect |
| --- | --- |
| `status` | Simulator state: displays and their size, switch positions, trims, remote control available |
| `screenshot [out.png] [MAIN\|TOP\|GLASSES]` | Save a display (the main one by default) as PNG |
| `press NAME [ms]` | Keys `ENTER` `RTN` `SYS` `MDL` `DISP` `PAGE` `UP` `DOWN` `LEFT` `RIGHT` (those the radio has), trims `T1-` `T1+` ... `T6+`, function switches `FS1` `FS2`...; 20 ms by default, from 540 ms a long press |
| `touch X Y [ms]`, `swipe X1 Y1 X2 Y2` | Touch input (screen pixels, top-left origin); 20 ms by default, from 1000 ms a long press |
| `turnRotaryEncoder N` | Rotary encoder, N detents (negative turns the other way) |
| `enterText TEXT` | Type text in the on-screen keyboard |
| `loadModel PATH` | Load a model file |
| `resetBacklightTimer` | Turn the screen back on (the frames are dimmed with the backlight) |
| `setSwitch I V`, `setAnalog I V` | Switches (-100 / 0 / 100), sticks, pots and sliders (-100 to 100): simulator only |
| `startMacro PATH [timeoutMs]` | Run a Lua macro to completion |
| `getInformation` | Board, firmware version, RF protocol, display sizes, drives |
| `getLog [since]` | Log lines (Lua `print()` and errors): the last 50, or the ones since `next` returned by the previous call |
| `listFiles [PATH]`, `readFile PATH [out]`, `writeFile PATH SRC`, `deleteFile PATH` | Files of the radio storage (`--root-directory`), radio paths from its root; `readFile` returns the text (or base64) when no `out` is given |
| `reload [SCRIPTS\|ALL]` | Reload the Lua scripts, or everything |
| `getChannelsOutputs` | Mixer outputs, in % |
| `getModelJson [out.json]`, `applyModelJson SRC.json` | Current model as JSON |
| `wait MS`, `quit` | Pause, stop the simulator |

Over HTTP, the arguments too big for a URL can be sent as a JSON object in the body of a `POST`: `writeFile` (`path`, and `text` or `base64`), `startMacro` (`code` instead of `path`: the macro is written to `/macros` while it runs, and the result has its output), `applyModelJson` (`model`). This is what the plugin's [MCP server](mcp_server.md) does.

In Git Bash, write the radio paths without the leading `/` (`scripts/main.lua`): Git Bash turns an argument starting with `/` into a Windows path, which the client refuses.

The commands are those of the firmware's remote control, with the same names and arguments as the `simulator` Lua API, the radio USB protocol ([`radio_remote.py`](radio_remote.md)) and the ethos-simulator MCP server. They call the remote control exports of the simulator (see [wasm_simulator.md](wasm_simulator.md#remote-control)), which return once the radio UI handled them: `{"ok": true, "status": "success"}`, or the failure status (`invalid argument`, `unsupported`, `busy` while a macro runs...). The builds older than the `press()` export get `pressKey()` / `pressFunctionSwitch()` and a long press flag for `touch` instead.

Input commands then wait for the display to settle before returning, so a following `screenshot` shows the result.

Example:

```text
node run_wasm.js X20S_FCC/X20S_FCC.js --root-directory ./radio --serve &
node run_wasm.js press SYS
node run_wasm.js screenshot system.png
node run_wasm.js quit
```
