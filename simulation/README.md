# Ethos Tools — simulation

## `run_wasm.js`

A small Node.js runner for executing the Ethos simulator's **WebAssembly** (Emscripten) build from the command line, without a browser. It loads the `simulator.js` module, starts the simulator, and can optionally drive a Lua macro in an automated way (tests, regression checks, CI...).

### Prerequisites

- Node.js
- An Ethos WASM build with a "simulator" file (e.g. `X20S_FCC.js`) alongside its `.wasm`

### Usage

```text
node run_wasm.js <simulator.js> [--root-directory DIR] [--macro path[.lua]] [--macro-timeout MS]
```

#### Positional argument

- `<simulator.js>` — path to the simulator file (resolved to an absolute path). This is the only required argument.

#### Options

| Option | Description |
| --- | --- |
| `--root-directory DIR` | Mounts `DIR` (the host's real filesystem, via `NODEFS`) at the current model's location in the simulator's virtual filesystem. If omitted, an in-memory `MEMFS` (not persisted) is used instead. |
| `--macro path[.lua]`, `--exec path[.lua]` | Path to a Lua macro to run automatically once the simulator has started. The script waits for the macro to pause at its first line, resumes it (`resumeMacro`), then waits for it to finish before exiting. |
| `--macro-timeout MS` | Maximum delay (in ms) tolerated for each of the two waits above (initial pause and macro end). If exceeded, the script fails with a timeout error. Without this option, the wait is unbounded. |
| `-h`, `--help` | Prints usage and exits. |

### Behavior

1. Loads and instantiates the simulator module (`simulator.js`), passing it `print`/`printErr` redirected to stdout/stderr.
2. Creates `/persist` as well as the current model's directory in the simulator's virtual filesystem, then mounts either `--root-directory` (NODEFS) or a `MEMFS` by default.
3. Starts the simulator (`start`).
4. If `--macro` is given, runs the macro and waits for it to finish (see `--macro-timeout`).
5. Exits with code `0` on success.

### Exit codes

- `0` — completed successfully.
- `1` — unhandled error during execution (see the message printed to stderr), e.g. a macro timeout.
- `2` — missing `<simulator.js>` positional argument.
- `-1` — the directory passed to `--root-directory` doesn't exist.

### Examples

Start the simulator with an in-memory filesystem, no macro:

```text
node run_wasm.js X20S_FCC/X20S_FCC.js
```

Mount a real models directory and run a Lua macro with a 10s timeout:
```text
node run_wasm.js X20S_FCC/X20S_FCC.js --root-directory . --macro USER:/macros/x20s.lua --macro-timeout 10000
```

## `sim_driver.js`

An interactive driver for the same WASM build. `serve` boots the simulator and keeps it running behind a small HTTP server on `127.0.0.1`. Every other invocation is a one-shot client that sends one input or request and prints the JSON result. It is the backend of the [`ethos-navigate`](skills/ethos-navigate/SKILL.md) Claude Code skill (see below).

```text
node sim_driver.js serve <simulator.js> [--root-directory DIR] [--port N] [--shots-dir DIR]
node sim_driver.js <command> [args] [--port N]
```

| Command | Effect |
| --- | --- |
| `status` | Simulator state: frame size, switch positions, trims |
| `screenshot [out.png]` | Save the current screen as PNG |
| `tap X Y`, `longpress X Y`, `swipe X1 Y1 X2 Y2 [ms]` | Touch input (screen pixels, top-left origin) |
| `wheel N` | Rotary encoder, N detents |
| `press KEY [holdMs]`, `keydown KEY`, `keyup KEY` | Keys: `SYS` `MDL` `DISP` `RTN` `PAGE` `ENTER`, or any browser key name |
| `switch I V`, `fswitch I V`, `analog I V`, `trim I V` | Switches, function switches, analogs, trims |
| `macro PATH [timeoutMs]` | Run a Lua macro to completion |
| `wait MS`, `log [n]`, `quit` | Pause, last console lines, stop the simulator |

Input commands wait for the display to settle before returning, so a following `screenshot` shows the result.

Example:

```text
node sim_driver.js serve X20S_FCC/X20S_FCC.js --root-directory ./radio &
node sim_driver.js press SYS
node sim_driver.js screenshot system.png
node sim_driver.js quit
```

## Claude Code plugin: `ethos-simulator`

This folder is also a [Claude Code](https://claude.com/claude-code) plugin. Its [`ethos-navigate`](skills/ethos-navigate/SKILL.md) skill teaches Claude to start `sim_driver.js` and operate the radio. Claude takes a screenshot, reads it, taps or presses a key, and repeats. You can then ask things like *"open the Mixes page and show me what it looks like"* or *"check that my widget renders on the X18"*.

Install it from any Claude Code session:

```text
/plugin marketplace add FrSkyRC/ethos-tools
/plugin install ethos-simulator@ethos-tools
```

Inside a clone of this repository, the project settings (`.claude/settings.json`) already list the marketplace, and Claude Code offers to install the plugin when you trust the folder.

Requirements: Node.js 18+ and an Ethos WASM build (`<BOARD>_<PROTOCOL>.js` + `.wasm`). The skill finds builds that the Ethos VS Code extension has already downloaded.
