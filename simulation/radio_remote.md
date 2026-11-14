# `radio_remote.py`

Drives a **physical** Ethos radio from the command line over USB, with the same remote control protocol as the WASM simulator ([Remote control](wasm_simulator.md#remote-control)). It opens the radio's USB serial port, waits for the connection to be accepted on the radio screen, then keeps it open behind a small HTTP server. Run it again with a command name and it acts as a one-shot client, like [`run_wasm.js --serve`](run_wasm.md#interactive-mode---serve). An agent can then operate the radio in the same loop it uses on the simulator: take a screenshot, read it, press a key or tap, repeat.

Tested on an X20S running Ethos 26.1.3 (protocol version 6), on Windows.

**Firmware:** remote control of a physical radio needs Ethos 26 from the nightly builds of 2026-10-16 or later.

## Prerequisites

- Python 3.8+
- `pyserial` (`pip install pyserial`)
- `hidapi` (`pip install hidapi`), only for the `mode` command
- A radio running an Ethos 26 nightly from 2026-10-16 or later, connected by USB

## Usage

```text
python radio_remote.py mode serial|storage
python radio_remote.py --serve [--com COMx] [--port N] [--shots-dir DIR] [--accept-timeout S]
python radio_remote.py <command> [args] [--port N]
```

| Option | Description |
| --- | --- |
| `--serve` | Connects to the radio and serves commands on `http://127.0.0.1:PORT` |
| `--com COMx` | Serial port (`COM6`, `/dev/ttyACM0`...). Default: the first port with the Ethos USB id |
| `--port N` | HTTP port for `--serve` and for client commands (default `8766`, so it can run next to `run_wasm.js` on `8765`) |
| `--shots-dir DIR` | Default folder for `screenshot` files (default `./screenshots`) |
| `--accept-timeout S` | Seconds to wait for the connection to be accepted on the radio screen (default `60`) |

## Connecting

1. **USB mode.** The radio must expose its serial port: set its USB mode to *Serial* when plugging it in, or run `python radio_remote.py mode serial`. That command sends a USB HID request, so it works whatever menu the radio is in (see [USB modes](#usb-modes-over-hid)).
2. **Start the server.** `python radio_remote.py --serve` sends `HELLO` and prints the board and firmware.
3. **Accept on the radio.** The radio shows a prompt asking to allow remote control. Commands other than `HELLO` are refused (`-2`) until it is accepted. A new serial connection has to be accepted again, which is why the tool keeps one connection open instead of reconnecting for each command.

```text
python radio_remote.py --serve &
python radio_remote.py press SYS
python radio_remote.py screenshot system.png
python radio_remote.py quit
```

## Commands

| Command | Effect |
| --- | --- |
| `status` | Board, firmware, region and protocol version from `HELLO` |
| `screenshot [out.png] [MAIN\|TOP\|GLASSES]` | Save a display as PNG (800×480 on the X20S) |
| `press NAME [ms]` | Keys: `SYS` `MDL` `DISP` `PAGE` `RTN` `ENTER` `UP` `DOWN` `LEFT` `RIGHT`, trims `T1+`, `T1-` ... `T6-`, function switches `FS1`, `FS2`...; 20 ms by default, from 540 ms a long press |
| `touch X Y [ms]`, `swipe X1 Y1 X2 Y2` | Touch input (screen pixels, top-left origin); 20 ms by default, from 1000 ms a long press |
| `turnRotaryEncoder N` | Rotary encoder, N detents (negative turns the other way) |
| `enterText TEXT` | Type text in the on-screen keyboard |
| `loadModel PATH` | Load a model file |
| `resetBacklightTimer` | Turn the screen back on |
| `getInformation` | Board, firmware, RF protocol, protocol version, display sizes (once a screenshot was taken), drives, state (flight mode, RF active, backlight on) |
| `getLog [since]` | Radio log lines: the last 50, or the ones since `next` returned by the previous call, 200 at most (`more` is then true: call again with `next`) |
| `listFiles [PATH]` | List a folder of the radio's storage (`/`, `/models`, `/scripts`, `SD:/`...) |
| `readFile PATH [out]` | Read a file: its text (or base64) in the result, or saved to `out` |
| `writeFile PATH SRC` | Write the local file `SRC` to `PATH` on the radio (over HTTP also `text` or `base64` in a `POST` body) |
| `createDirectory PATH` | Create a folder on the radio, its parent must exist (protocol version 10) |
| `deleteFile PATH` | Delete a file, or an empty folder, on the radio |
| `reload [SCRIPTS\|ALL]` | Reload the Lua scripts, or everything (protocol version 7) |
| `getChannelsOutputs` | Mixer outputs, in % |
| `getTrims` | Trim values, -100 to 100 |
| `getControls [since] [timeout]` | Switches (`{"SA": -1, ...}`: -1 up, 0 middle, 1 down), trims in %, function switches (`{"FS1": true, ...}`, as their LEDs) and `version`, as sent by the radio when they change (`EVENT_CONTROLS`, protocol version 11). With `since` (a `version`), waits until they change, `timeout` ms at most (30000). It doesn't wait for the other commands |
| `resetFunctionSwitches` | Turn all the function switches of the current model off (protocol version 11) |
| `wait MS`, `quit` | Pause, stop the server |

The input commands have the names and arguments of the firmware's remote control functions, as the `simulator` Lua API, [`run_wasm.js`](run_wasm.md#interactive-mode---serve) and the ethos-simulator MCP server. With a firmware older than the protocol version 9, `press FSn` is sent as `PRESS_FUNCTION_SWITCH` and `touch` as a long press flag (from 1000 ms).

Tried on the radio (protocol version 6, with the previous names of the commands): `status`, `screenshot`, `press` (keys and trims), `touch`, `turnRotaryEncoder`, `listFiles`, `readFile`, `writeFile`, `deleteFile`, `getTrims`. The others, and the payloads of the protocol version 9, have not been tried yet.

In Git Bash, write the radio paths without the leading `/` (`scripts/main.lua`): Git Bash turns an argument starting with `/` into a Windows path, which the client refuses.

The radio answers an input command once its UI loop has handled it and repainted the screen, so a following `screenshot` shows the result. `settle` (ms, 0 by default) adds a delay before returning, for an animation. Every command returns JSON: `{"ok": true, "status": "success", ...}`.

### Sticks, pots and switches

The remote control protocol has no command for them: on a radio they would override the physical controls. `setSwitch` and `setAnalog` only exist in the simulator ([`run_wasm.js`](run_wasm.md#interactive-mode---serve), Lua `simulator.setSwitch()` / `setAnalog()`).

## Protocol notes for a physical radio

The frame format is the one in [wasm_simulator.md](wasm_simulator.md#remote-control): `0xE7`, command, sequence, 16-bit length, payload, CRC-16/XMODEM, little endian. The payloads used by this tool, checked on the radio with the protocol version 6, except `CHANNELS_OUTPUTS` and the version 9 ones:

| Command | Payload sent | Response |
| --- | --- | --- |
| `HELLO` (`0x01`) | version (1) = `11` | version (1), firmware major, minor, revision (1 each), board, region, git hash, version suffix (strings: `RC11`..., empty for a release, since the version 11) |
| `PRESS` (`0x10`, `PRESS_KEY` before version 9) | duration ms (4), name (string): key, trim, and since version 9 function switch `FS1`... | – |
| `PRESS_FUNCTION_SWITCH` (`0x16`) | duration ms (4), name `FS1`... (string); used before version 9 | – |
| `TOUCH` (`0x11`) | x (2), y (2), duration ms (4) since version 9; before: long press (1) | – |
| `RESET_BACKLIGHT_TIMER` (`0x15`) | – | – |
| `CREATE_DIRECTORY` (`0x34`, version 10) | path (string), its parent must exist | – |
| `TURN_ROTARY_ENCODER` (`0x13`) | steps (2, signed) | – |
| `RESET_INACTIVITY_TIMER` (`0x1B`) | – | `0` once the connection is accepted, `-2` before |
| `SCREENSHOT` (`0x20`) | display (1), full frame (1) | tiles, see below |
| `CHANNELS_OUTPUTS` (`0x50`) | full (1) | channel count (1), bitmap of changed channels, int16 values |
| `TRIMS` (`0x51`) | – | int16 values, ×100/256 for percent |
| `RESET_FUNCTION_SWITCHES` (`0x53`, version 11) | – | – |

A response can come in several frames: each starts with status (int8) and flags, and flag `0x01` marks the last one. Concatenate the rest of the payloads.

**Connection events** arrive with sequence `0`: `0xC2` with `01` when the user accepts the prompt, `00` when they refuse it; `0xC3` carries a radio log line (text); `0xC1` the state (flight mode, RF active, backlight on); `0xC4` (version 11) the controls, once the connection is accepted, then when they change (every 50 ms at most): switches count (1), positions (1 each, -1 up, 0 middle, 1 down, -128 not installed), trims count (1), trims (int16 each), function switches count (1), states (1, bit i = FS(i + 1) on). After `HELLO`, the online simulator sends `RESET_INACTIVITY_TIMER`: success means the connection is already accepted, otherwise it waits for `0xC2`.

**Screenshots** only send what changed. The request's second byte asks for a full frame (`1`) or only the tiles changed since the previous screenshot (`0`). The response is width (2), height (2), tile size (1), a bitmap with one bit per tile (row-major, LSB first), then a single [QOI](https://qoiformat.org/) image holding the changed tiles stacked vertically (tile size wide, tile size × changed tiles high). Paste each tile into the previous frame. Keep one previous frame per display, and ask for a full frame again if decoding fails.

## USB modes over HID

The radio always exposes a USB HID interface (VID `0x0483`, PID `0x5750`, interface 0), next to the serial port (interface 1) when it is in Serial mode. An output report switches the USB mode, as the Ethos suite updaters do:

| Report | Mode |
| --- | --- |
| `00 81 68` | Serial (debug): the serial port appears |
| `00 81 69` | Storage: the radio mounts as a drive and the serial port disappears |

The radio drops off the bus and enumerates again after a switch, so wait a few seconds before opening the port.

## Transport notes

- **Windows:** open the port with overlapped I/O, as pyserial does. A synchronous handle (for example Node's `fs.open('\\\\.\\COM6')`) gets `HELLO` through, then later commands time out: a pending read blocks the writes on the same handle.
- **Baud rate:** the port is USB CDC, the value is ignored. The online simulator uses 115200.
- **Browser:** the online simulator connects with Web Serial (Chrome or Edge) and filters on `usbVendorId 1155, usbProductId 22352`.

## Other ways in

The online simulator at <https://ethos-simulator.frsky-rc.com/remote> has a *Connect Claude* dialog. It gives an MCP URL (`claude mcp add --transport http ethos-simulator https://ethos-simulator.frsky-rc.com/mcp/<token>`) that the FrSky server relays to the open browser tab over `/ws/bridge/<token>`. With a radio connected to that tab by Web Serial, its tools reach the radio (`screenshot`, `press`, `touch`, `swipe`, `turnRotaryEncoder`, `enterText`, `loadModel`, `resetBacklightTimer`, `getInformation`, `getLog`, `listFiles`, `readFile`, `writeFile`, `createDirectory`, `deleteFile`, `reload`, `getChannelsOutputs`), except `startMacro`, `getModelJson` and `applyModelJson`, which work only with the simulator. The same page can share a radio: whoever has it opens a `/remote/<id>` link, and the page that created the link drives it through the server. Both need a browser tab open and go through FrSky's server. `radio_remote.py` is local only.
