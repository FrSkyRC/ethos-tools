# `mcp_server.js`

MCP server of the `ethos` Claude Code plugin. It gives Claude the same tools as the *Connect Claude* MCP server of the online simulator (<https://ethos-simulator.frsky-rc.com>), on a local simulator or radio: Claude calls `press`, `touch`, `screenshot`... directly, instead of running command lines.

It is a stdio server, in plain Node.js (18+, no dependency). The plugin declares it in [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), so it starts with Claude Code once the plugin is installed. It relays each tool to the local instance that is serving:

1. [`radio_remote.py --serve`](radio_remote.md) on port `8766`: a radio connected over USB, when it answers;
2. else [`run_wasm.js --serve`](run_wasm.md#interactive-mode---serve) on port `8765`: the WASM simulator.

When neither serves, the tools return an error saying so, except the three tools that start and stop a simulator (below).

## Starting a simulator

`startSimulator(radio, release, rootDirectory)` starts the simulator itself: it downloads the `<RADIO>-WebSimulator.zip` asset of a [FrSkyRC/ETHOS-Feedback-Community](https://github.com/FrSkyRC/ETHOS-Feedback-Community/releases) release, extracts it to a cache folder and starts `run_wasm.js --serve` with it, then waits until it answers. So Claude can open any radio of any release, without the Ethos VS Code extension.

- `radio`: a board, e.g. `X20S` (the FCC version then), or `BOARD-PROTOCOL`, e.g. `X18-EU`, `X20S-FLEX` (`X20S_FCC`, `x18 lbt` work too).
- `release`: e.g. `26.2.0-RC10`. By default, the newest prerelease (the newest release when there is none). The releases older than **26.2** are refused: their simulators have no remote control exports, so `press()`, `touch()`... can't work.
- `rootDirectory`: a folder mounted as the radio storage (`radio.bin`, `models/`, `scripts/`...), created if missing. The simulator writes to it. Without it, the radio is empty and in memory.

`listSimulators(release)` lists the releases 26.2 or newer that have simulators, and the radios of one of them. `stopSimulator()` stops the simulator served on the port, whoever started it. The one started by `startSimulator()` is also stopped with the MCP server, at the end of the Claude session.

A downloaded simulator is reused until its asset changes on GitHub (the assets of a release candidate can be uploaded again). Without network, a simulator already downloaded for the requested release is used. The cache folder is `%LOCALAPPDATA%\ethos-tools` on Windows, `~/Library/Caches/ethos-tools` on macOS, `~/.cache/ethos-tools` elsewhere; it also holds `simulator.log`, the output of the last simulator started. The release code is in `simulators.js`, shared with [`run_wasm.js --radio`](run_wasm.md#simulators-of-the-releases) and the [tests](tests/README.md): they use the same cache.

The radio is never started here: run [`radio_remote.py --serve`](radio_remote.md) to connect one.

## Environment variables

| Variable | Default | Use |
| --- | --- | --- |
| `ETHOS_SIMULATOR_PORT` | `8765` | Port of `run_wasm.js --serve` |
| `ETHOS_RADIO_PORT` | `8766` | Port of `radio_remote.py --serve` |
| `ETHOS_CACHE_DIR` | see above | Cache folder of the downloaded simulators |
| `GITHUB_TOKEN` | – | Token for the GitHub API, when its rate limit (60 requests per hour without a token) is reached |

## Tools

| Tool | Simulator | Radio | Effect |
| --- | --- | --- | --- |
| `listSimulators(release?)` | – | – | Releases with simulators (26.2 or newer) and the radios of one of them |
| `startSimulator(radio, release?, rootDirectory?)` | – | – | Download (once) and start the simulator of a radio |
| `stopSimulator()` | ✔ | – | Stop the simulator |
| `getInformation()` | ✔ | ✔ | Target, board, firmware version, RF protocol, display sizes, drives; for a radio its state (flight mode, RF active, backlight on) |
| `getLog(since?)` | ✔ | ✔ | Log lines (Lua `print()` and errors): the last 50, or the ones since `next` of the previous call |
| `screenshot(display)` | ✔ | ✔ | PNG of `MAIN`, `TOP` or `GLASSES` |
| `resetBacklightTimer()` | ✔ | ✔ | Turn the screen back on |
| `press(name, duration)` | ✔ | ✔ | Key (`ENTER`, `RTN`, `SYS`...), trim (`T1+`...) or function switch (`FS1`...); from 540 ms a long press |
| `touch(x, y, duration)` | ✔ | ✔ | Touch; from 1000 ms a long press |
| `swipe(x1, y1, x2, y2)` | ✔ | ✔ | Slide a finger |
| `turnRotaryEncoder(steps)` | ✔ | ✔ | Rotary encoder |
| `enterText(text)` | ✔ | ✔ | Type in the text field being edited |
| `loadModel(path)` | ✔ | ✔ | Load a model file |
| `listFiles(path)`, `readFile(path)`, `writeFile(path, text\|base64)`, `createDirectory(path)`, `deleteFile(path)` | ✔ | ✔ | Files of the radio storage, paths from its root (`/scripts/...`, `SD:/...` on a radio) |
| `reload(SCRIPTS\|ALL)` | ✔ | ✔ | Reload the Lua scripts, or everything |
| `getChannelsOutputs()` | ✔ | ✔ | Mixer outputs, in % |
| `getControls(since?, timeout?)` | ✔ | ✔ | Switches (-1 up, 0 middle, 1 down), trims in %, function switches, as sent by the radio when they change; with `since` (the `version` of a previous call), waits until they change |
| `resetFunctionSwitches()` | ✔ | ✔ | Turn all the function switches of the current model off |

The resource `ethos://controls` has the same content as `getControls()`. A client subscribed to it (`resources/subscribe`) gets `notifications/resources/updated` when the controls change: the server waits for them with `getControls` (long poll). The notification goes to the MCP client, not to the model: Claude sees the changes through `getControls(since)`.
| `getModelJson()`, `applyModelJson(model)` | ✔ | – | Current model as JSON |
| `startMacro(code)` | ✔ | – | Run a Lua macro, return what it printed |

The online simulator also has `getLuaApi()`, which returns the Lua API of the downloaded releases: the [Ethos Lua documentation](https://github.com/FrSkyRC/ethos-lua-doc) is the reference here.

On a radio, the file tools change the user's files, and the input tools act on a radio that may be bound to a powered model.

## Testing it by hand

The server reads one JSON-RPC message per line on stdin and answers on stdout. It handles the messages as they come, without waiting for the previous answer, so send a tool call only once the previous one answered:

```text
node run_wasm.js X20S_FCC/X20S_FCC.js --serve &
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"getInformation","arguments":{}}}' | node mcp_server.js
```
