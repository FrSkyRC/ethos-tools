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
| `screenshot [out.png] [main\|top\|glasses]` | Save a display as PNG (800×480 on the X20S) |
| `press KEY [holdMs]` | Keys: `SYS` `MDL` `DISP` `PAGE` `RTN` `ENTER` `UP` `DOWN` `LEFT` `RIGHT`. Trims are keys too: `T1+`, `T1-` ... `T6-` |
| `tap X Y`, `longpress X Y`, `swipe X1 Y1 X2 Y2` | Touch input (screen pixels, top-left origin) |
| `wheel N` | Rotary encoder, N detents (negative turns the other way) |
| `text STRING` | Type text in the on-screen keyboard |
| `fswitch I [holdMs]` | Press function switch `FS<I>` |
| `ls [PATH]` | List a folder of the radio's storage (`/`, `/models`, `/scripts`...) |
| `read PATH [out]` | Read a file; saved to `out` when given |
| `write SRC PATH` | Write the local file `SRC` to `PATH` on the radio |
| `rm PATH` | Delete a file on the radio |
| `load PATH` | Load a model file |
| `trims` | Trim values, -100 to 100 |
| `log [n]`, `wait MS`, `quit` | Radio log lines, pause, stop the server |

Tried on the radio: `status`, `screenshot`, `press` (keys and trims), `tap`, `wheel`, `ls`, `read`, `write`, `rm`, `trims`. The others use the same payloads as the online simulator but have not been tried yet.

Input commands wait 300 ms (`settle`) before returning, so a following `screenshot` shows the result. Every command returns JSON: `{"ok": true, "status": "success", ...}`.

### Sticks, pots and switches

The protocol table in [wasm_simulator.md](wasm_simulator.md#remote-control) lists `SET_SWITCH` (`0x17`) and `SET_ANALOG` (`0x19`), but the online simulator (<https://ethos-simulator.frsky-rc.com/remote>) never sends them to a physical radio: its switch and stick controls only drive the WASM simulator. They are not in this tool and have not been tried on a radio. Test them with the RF module off: a radio bound to a powered model would move its servos or motor.

## Protocol notes for a physical radio

The frame format is the one in [wasm_simulator.md](wasm_simulator.md#remote-control): `0xE7`, command, sequence, 16-bit length, payload, CRC-16/XMODEM, little endian. With protocol version 6 a few payloads differ from that table. These come from the online simulator's client, and all but `CHANNELS_OUTPUTS` were checked on the radio:

| Command | Payload sent | Response |
| --- | --- | --- |
| `HELLO` (`0x01`) | version (1) = `6` | version (1), firmware major, minor, revision (1 each), board, region, git hash (strings) |
| `PRESS_KEY` (`0x10`) | duration ms (4), key name (string) | – |
| `PRESS_FUNCTION_SWITCH` (`0x16`) | duration ms (4), name `FS1`... (string) | – |
| `TOUCH` (`0x11`) | x (2), y (2), long press (1) | – |
| `TURN_ROTARY_ENCODER` (`0x13`) | steps (2, signed) | – |
| `RESET_INACTIVITY_TIMER` (`0x1B`) | – | `0` once the connection is accepted, `-2` before |
| `SCREENSHOT` (`0x20`) | display (1), full frame (1) | tiles, see below |
| `CHANNELS_OUTPUTS` (`0x50`) | full (1) | channel count (1), bitmap of changed channels, int16 values |
| `TRIMS` (`0x51`) | – | int16 values, ×100/256 for percent |

A response can come in several frames: each starts with status (int8) and flags, and flag `0x01` marks the last one. Concatenate the rest of the payloads.

**Connection events** arrive with sequence `0`: `0xC2` with `01` when the user accepts the prompt, `00` when they refuse it; `0xC3` carries a radio log line (text); `0xC1` the state (flight mode, RF active, backlight on). After `HELLO`, the online simulator sends `RESET_INACTIVITY_TIMER`: success means the connection is already accepted, otherwise it waits for `0xC2`.

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

The online simulator at <https://ethos-simulator.frsky-rc.com/remote> has a *Connect Claude* dialog. It gives an MCP URL (`claude mcp add --transport http ethos-simulator https://ethos-simulator.frsky-rc.com/mcp/<token>`) that the FrSky server relays to the open browser tab over `/ws/bridge/<token>`. With a radio connected to that tab by Web Serial, its `screenshot`, `pressKey`, `touch` and `swipe` tools reach the radio. `runMacro`, `getModelJson` and `applyModelJson` work only with the simulator. The same page can share a radio: whoever has it opens a `/remote/<id>` link, and the page that created the link drives it through the server. Both need a browser tab open and go through FrSky's server. `radio_remote.py` is local only.
