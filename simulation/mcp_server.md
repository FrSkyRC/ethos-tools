# `mcp_server.js`

MCP server of the `ethos-simulator` Claude Code plugin. It gives Claude the same tools as the *Connect Claude* MCP server of the online simulator (<https://ethos-simulator.frsky-rc.com>), on a local simulator or radio: Claude calls `press`, `touch`, `screenshot`... directly, instead of running command lines.

It is a stdio server, in plain Node.js (18+, no dependency). The plugin declares it in [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json), so it starts with Claude Code once the plugin is installed. It relays each tool to the local instance that is serving:

1. [`radio_remote.py --serve`](radio_remote.md) on port `8766`: a radio connected over USB, when it answers;
2. else [`run_wasm.js --serve`](run_wasm.md#interactive-mode---serve) on port `8765`: the WASM simulator.

It starts neither of them: the [`ethos-navigate`](skills/ethos-navigate/SKILL.md) skill explains how to start the simulator (pick a build, a radio folder). Without one, every tool returns an error saying so. The ports can be changed with the `ETHOS_RADIO_PORT` and `ETHOS_SIMULATOR_PORT` environment variables.

## Tools

| Tool | Simulator | Radio | Effect |
| --- | --- | --- | --- |
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
| `listFiles(path)`, `readFile(path)`, `writeFile(path, text\|base64)`, `deleteFile(path)` | ✔ | ✔ | Files of the radio storage, paths from its root (`/scripts/...`, `SD:/...` on a radio) |
| `reload(SCRIPTS\|ALL)` | ✔ | ✔ | Reload the Lua scripts, or everything |
| `getChannelsOutputs()` | ✔ | ✔ | Mixer outputs, in % |
| `getModelJson()`, `applyModelJson(model)` | ✔ | – | Current model as JSON |
| `startMacro(code)` | ✔ | – | Run a Lua macro, return what it printed |

The online simulator also has `getLuaApi()`, which returns the Lua API of the downloaded releases: the [Ethos Lua documentation](https://github.com/FrSkyRC/ethos-lua-doc) is the reference here.

On a radio, the file tools change the user's files, and the input tools act on a radio that may be bound to a powered model.

## Testing it by hand

The server reads one JSON-RPC message per line on stdin and answers on stdout:

```text
node run_wasm.js X20S_FCC/X20S_FCC.js --serve &
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"getInformation","arguments":{}}}' | node mcp_server.js
```
