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
