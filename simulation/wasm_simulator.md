# Ethos WASM Simulator

## Overview

The WASM simulator is the Ethos firmware running in a web browser or in Node.js. The host, a web page or a Node.js script, loads it as a JavaScript module, feeds it inputs (mouse, keys, switches, sticks) and gets back the screen image, the audio and events.

- There is one simulator per radio, delivered as a .js + .wasm pair. The JS module is named `<BOARD>_<COUNTRY_PROTOCOL>` (for example `X20RS_FCC`).
- The radio file system is persisted in IndexedDB in a browser, or in a local folder under Node.js (NODEFS).
- It powers the online simulator, the model configuration tool (the "Ethos simulator" MCP) and step-by-step Lua macro execution.

## Embedding in a web page

The page loads `<BOARD>_<PROTOCOL>.js`, which exports a factory named `<BOARD>_<PROTOCOL>` (emscripten `MODULARIZE`). The page passes callbacks on the `Module` object, mounts the persistent storage, then calls `start()`.

```javascript
const Module = await X20RS_FCC({
  updateCanvas(width, height, ptr) { /* RGB565 pixels, width*height uint16 at ptr */ },
  audioPushBuffer(ptr, size) { /* audio samples */ },
  onMacroEnd() { /* macro finished */ },
});
const sim = wrapSimulator(Module);   // see Exported functions
Module.FS.mkdir('/persist');
Module.FS.mount(Module.IDBFS, {}, '/persist');
Module.FS.syncfs(true, () => sim.start());
```

In a browser, the simulator uses threads (`SharedArrayBuffer`), so the page must be served cross-origin isolated, with `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp`.

### Running under Node.js

The same `.js` + `.wasm` pair runs in Node.js, with the radio files in a local folder. Mount that folder at `/persist` with `NODEFS` before calling `start()`. No `syncfs(true)` is needed: files are read from and written to disk directly.

```javascript
const factory = require('./X20RS_FCC.js');
const Module = await factory({
  updateCanvas(width, height, ptr) { /* RGB565 pixels */ },
  onMacroEnd() { /* macro finished */ },
});
const sim = wrapSimulator(Module);   // see Exported functions
Module.FS.mkdir('/persist');
Module.FS.mount(Module.NODEFS, { root: '/path/to/radio' }, '/persist');
sim.start();
```

The local folder holds one sub-folder per radio, `<root>/<BOARD_NAME>/`, with the same layout as the radio storage. This mode suits automated runs: macros, screenshots and tests on local model files.

### File system

- User files live in `/persist/<BOARD_NAME>/` (models, `audio/`, `documents/`, `bitmaps/`, `scripts/`). System files (bitmaps, i18n, help, fonts) are embedded at `/`.
- The host chooses what backs `/persist`: `IDBFS` in a browser, `NODEFS` under Node.js, or `MEMFS` for a throwaway session.
- Each time the firmware writes a file, it calls `FS.syncfs(false)`, then `Module.syncToLocal()` if defined. With IDBFS this saves to IndexedDB; with NODEFS files are already on disk.
- `writeDefaultSettingsAndModel()` writes default radio settings and an empty `model00.bin`, for a first start.

### Callbacks the page provides

All are optional; the firmware checks that each one exists before calling it. They run on the main thread, at most 100 times per second.

| Callback | When |
| --- | --- |
| `updateCanvas(w, h, ptr)` | Main screen changed (RGB565, backlight dimming applied) |
| `updateCanvasTop(w, h, ptr)` | Top screen changed (radios with a top LCD) |
| `updateCanvasGlasses(w, h, ptr)` | ActiveLook glasses display changed |
| `audioPushBuffer(ptr, size)` | Audio buffer ready |
| `setSwitchesConfig(cfg)` / `setSwitchesPosition(ptr, count)` | Switch configuration or positions changed in the firmware |
| `setFunctionSwitchesState(state, count)` / `setTrimsValue(ptr, count)` / `setExtPotsEnable(mask)` | Function switches, trims, external pots changed |
| `setModelJson(json)` | Model changed, after `setModelJsonNeeded(true)` |
| `onDebugEvent(source, line)` | Macro paused on a line |
| `onRecordEvent(action, overwrite)` | New action recorded |
| `onMacroEnd()` | Macro finished |
| `onRemoteSend(bytes)` | Response from the remote control server (`Uint8Array`) |
| `syncToLocal()` | File system saved to IndexedDB |

## Exported functions

Every function below can be called from the host once the module is loaded. Inputs (touch, keys, switches, sticks) can be sent at any time after `start()`; the firmware picks them up on its next cycle.

### Calling conventions

All examples in this reference go through typed wrappers made with `Module.cwrap(name, returnType, argTypes)`. Each wrapper declares its C signature once, then is called like a normal JavaScript function: emscripten converts strings, booleans and byte arrays on every call.

| C type | `cwrap` type | JavaScript value |
| --- | --- | --- |
| `int`, `uint8_t`, `uint16_t`, `uint32_t` | `'number'` | integer |
| `bool` | `'boolean'` | `true` / `false` |
| `const char *` argument or return | `'string'` | JavaScript string |
| `const uint8_t *` + size | `'array'`, `'number'` | `Uint8Array` and its length |
| `const int16_t *` return | `'number'` | address in `Module.HEAP16` (divide by 2 to index) |
| no return value | `null` |  |

Copy this function into the host. It wraps every exported function; the rest of this reference calls them as `sim.<name>(...)`.

```javascript
function wrapSimulator(Module) {
  const w = (name, ret, args = []) => Module.cwrap(name, ret, args);
  return {
    // Lifecycle
    start: w('start', null),
    stop: w('stop', null),
    reload: w('reload', null, ['boolean']),
    reloadScripts: w('reloadScripts', null),
    writeDefaultSettingsAndModel: w('writeDefaultSettingsAndModel', null),
    // Touch screen and rotary encoder
    onMouseDown: w('onMouseDown', null, ['number', 'number']),
    onMouseUp: w('onMouseUp', null, ['number', 'number']),
    onMouseMove: w('onMouseMove', 'boolean', ['number', 'number']),
    onMouseLongPress: w('onMouseLongPress', null),
    onMouseWheel: w('onMouseWheel', null, ['number']),
    // Keys and controls
    onKeyEvent: w('onKeyEvent', 'boolean', ['number', 'string', 'boolean', 'boolean']),
    setSwitchPosition: w('setSwitchPosition', null, ['number', 'number']),
    setAnalogPosition: w('setAnalogPosition', null, ['number', 'number']),
    setFunctionSwitchPressed: w('setFunctionSwitchPressed', null, ['number', 'boolean']),
    setTrimPressed: w('setTrimPressed', null, ['number', 'number']),
    // Model, outputs and settings
    getCurrentModel: w('getCurrentModel', 'string'),
    setModelJsonNeeded: w('setModelJsonNeeded', null, ['boolean']),
    applyModelJson: w('applyModelJson', 'boolean', ['string']),
    getChannelsOutputs: w('getChannelsOutputs', 'number'),
    setLanguage: w('setLanguage', null, ['string']),
    loadCustomTranslations: w('loadCustomTranslations', null, ['string']),
    // Telemetry
    injectSPortFrame: w('injectSPortFrame', null, ['number', 'number', 'number', 'number', 'number', 'number', 'number']),
    // Macros and recording
    startMacro: w('startMacro', null, ['string', 'boolean']),
    stopMacro: w('stopMacro', null),
    pauseMacro: w('pauseMacro', null),
    resumeMacro: w('resumeMacro', null),
    stepMacro: w('stepMacro', null),
    setBreakpoints: w('setBreakpoints', null, ['string']),
    startRecord: w('startRecord', null),
    stopRecord: w('stopRecord', null),
    // Remote control
    remoteReceive: w('remoteReceive', null, ['array', 'number']),
  };
}

const sim = wrapSimulator(Module);
```

`onSyncFsDone` and `main` are also exported but are internal: never call them.

### Lifecycle

#### `start()`

Starts the firmware: radio tasks, 10 ms timer and remote control server. Call it once, after `/persist` is mounted (and, in a browser, after `FS.syncfs(true)` has loaded IndexedDB).

```javascript
sim.start();
```

#### `stop()`

Stops all firmware tasks, unloads Lua scripts and stops the main loop. After `stop()` the main loop no longer runs: to run the radio again, load a new module instance.

```javascript
sim.stop();
```

#### `reload(emergency)`

Restarts the firmware as after a power cycle, keeping the files. `emergency` (`bool`): `true` to restart in emergency mode.

```javascript
sim.reload(false);
```

#### `reloadScripts()`

Unloads and reloads all Lua scripts, for example after the host has changed a file in `scripts/`.

```javascript
Module.FS.writeFile('/persist/X20RS/scripts/mytool/main.lua', source);
sim.reloadScripts();
```

#### `writeDefaultSettingsAndModel()`

Writes default radio settings (calibration done) and an empty model `model00.bin`. Use it before `start()` when the storage is empty, so the radio starts with a known, calibrated configuration.

```javascript
if (!Module.FS.analyzePath('/persist/X20RS').exists) {
  Module.FS.mkdir('/persist/X20RS');
  sim.writeDefaultSettingsAndModel();
}
sim.start();
```

### Touch screen and rotary encoder

Coordinates are screen pixels, (0, 0) at the top left, the same size as the canvas given to `updateCanvas`. On radios without a touch screen the touch functions do nothing.

#### `onMouseDown(x, y)`

Finger down at (x, y). Also wakes the radio up (backlight, inactivity).

```javascript
canvas.addEventListener('pointerdown', (e) => sim.onMouseDown(e.offsetX, e.offsetY));
```

#### `onMouseUp(x, y)`

Finger up. Without a move in between, it is a tap at the position given to `onMouseDown`.

```javascript
canvas.addEventListener('pointerup', (e) => sim.onMouseUp(e.offsetX, e.offsetY));
```

#### `onMouseMove(x, y)` → `bool`

Finger moved while down. Once the move passes a few pixels it becomes a slide (scroll, swipe). Returns `true` while a slide is in progress, so the host can stop the browser from scrolling the page.

```javascript
canvas.addEventListener('pointermove', (e) => {
  if (e.buttons && sim.onMouseMove(e.offsetX, e.offsetY)) e.preventDefault();
});
```

#### `onMouseLongPress()`

Turns the current press into a long press (context menus). The host decides the delay, typically 1 s after `onMouseDown` without a slide.

```javascript
let longPressTimer;
canvas.addEventListener('pointerdown', () => {
  longPressTimer = setTimeout(() => sim.onMouseLongPress(), 1000);
});
canvas.addEventListener('pointerup', () => clearTimeout(longPressTimer));
```

#### `onMouseWheel(steps)`

Turns the rotary encoder by `steps` detents; the sign gives the direction. Does nothing on radios without a rotary encoder.

```javascript
canvas.addEventListener('wheel', (e) => {
  sim.onMouseWheel(e.deltaY > 0 ? 1 : -1);
  e.preventDefault();
});
```

### Keys and controls

#### `onKeyEvent(keyCode, key, shiftKey, pressed)` → `bool`

Forwards a PC keyboard event: call it on both `keydown` and `keyup`. Parameters come from the browser `KeyboardEvent`: `keyCode` (number), `key` (string), `shiftKey` (bool); `pressed` is `true` on key down. Returns `false` when the key is not used, so the host can let the browser handle it.

| Key | Radio action |
| --- | --- |
| `Enter`, arrows | ENTER and navigation keys (radios with keys) |
| `PageUp` / `PageDown` | PAGE key |
| `a`, `b`, `c` … | Switch SA, SB, SC … (3-position steps, 2-position toggles, momentary while held) |
| `F1` … `F6` | Function switches FS1 … FS6 |
| `1` … `6` | Trims T1, T2, T3, T4, T5, T6 up; with Shift, down |
| letters, digits, space, `Home`, `End`, `Backspace`, `Delete` | Typed in the on-screen keyboard, when it is open |

```javascript
for (const type of ['keydown', 'keyup']) {
  window.addEventListener(type, (e) => {
    if (sim.onKeyEvent(e.keyCode, e.key, e.shiftKey, type === 'keydown')) e.preventDefault();
  });
}
```

#### `setSwitchPosition(index, value)`

Sets a switch. `index`: 0 = SA, 1 = SB, … `value`: `-100`, `0` or `100` (one value per switch position).

```javascript
sim.setSwitchPosition(0, 100);   // SA to its 100 position
sim.setSwitchPosition(1, 0);     // SB middle
```

#### `setAnalogPosition(index, value)`

Sets a stick axis, pot or slider. `index`: analog input, in the radio's hardware order. `value`: `-100` to `100`, clamped. The position holds until changed.

```javascript
sim.setAnalogPosition(0, -100);  // first stick axis to minimum
sim.setAnalogPosition(0, 0);     // back to center
```

#### `setFunctionSwitchPressed(index, pressed)`

Presses or releases a function switch. `index`: 0 = FS1. Does nothing on radios without function switches.

```javascript
sim.setFunctionSwitchPressed(0, true);
setTimeout(() => sim.setFunctionSwitchPressed(0, false), 100);
```

#### `setTrimPressed(index, value)`

Presses a trim button. `value`: negative = down, positive = up, `0` = released. The button stays pressed until released, so the trim keeps moving. `index`: 0 = T4, 1 = T3, 2 = T2, 3 = T1, 4 = T5, 5 = T6.

```javascript
sim.setTrimPressed(3, 1);        // T1 up
setTimeout(() => sim.setTrimPressed(3, 0), 50);
```

### Model, outputs and settings

File paths given to these functions are radio paths: an absolute path such as `/models/model00.bin` points inside `/persist/<BOARD_NAME>/`.

#### `getCurrentModel()` → `string`

Returns the file system path of the loaded model.

```javascript
const path = sim.getCurrentModel();   // "/persist/X20RS/models/model00.bin"
const bytes = Module.FS.readFile(path);
```

#### `setModelJsonNeeded(enabled)`

With `true`, the firmware sends the loaded model as JSON to `Module.setModelJson(json)` right away, then again each time the model changes. With `false`, it stops.

```javascript
Module.setModelJson = (json) => {
  const model = JSON.parse(json);
  console.log(model.name);
};
sim.setModelJsonNeeded(true);
```

#### `applyModelJson(json)` → `bool`

Replaces the loaded model with `json` (string), in the format `Module.setModelJson` delivers. Returns `false` when the JSON is invalid; a syntax error leaves the current model untouched. The model is swapped between two loops of the main task, under the mixer lock, then its screens and widgets are rebuilt. The call can block briefly while the main task finishes its loop.

```javascript
let modelJson;
Module.setModelJson = (json) => { modelJson = json; };
sim.setModelJsonNeeded(true);

// later
const model = JSON.parse(modelJson);
model.name = 'Glider';
if (!sim.applyModelJson(JSON.stringify(model))) {
  console.error('Invalid model JSON');
}
```

#### `getChannelsOutputs()` → pointer

Returns the address of an `int16` array: element 0 is the number of channels, then one value per channel. `-1024` to `1024` is -100 % to +100 %. The array is overwritten at each call, so read it right away.

```javascript
const ptr = sim.getChannelsOutputs() >> 1;   // int16 index
const count = Module.HEAP16[ptr];
const outputs = Array.from(Module.HEAP16.subarray(ptr + 1, ptr + 1 + count));
const percent = outputs.map((v) => v * 100 / 1024);
```

#### `setLanguage(code)`

Sets the radio language and saves the radio settings. `code`: two-letter language code, such as `"en"`, `"fr"`, `"de"`. Call it after `start()`, then `reload(false)` so every screen uses the new language.

```javascript
sim.setLanguage('fr');
sim.reload(false);
```

#### `loadCustomTranslations(path)`

Loads a translation CSV, in the Ethos translation file format, on top of the built-in texts. Use it to preview a translation in progress. If the file does not exist, custom translations are cleared.

```javascript
Module.FS.writeFile('/persist/X20RS/my_translation.csv', csvText);
sim.loadCustomTranslations('/my_translation.csv');
```

### Telemetry

#### `injectSPortFrame(module, band, rx, physId, primId, appId, value)`

Feeds one S.Port telemetry frame to the firmware, as if a receiver had sent it. Sensors are discovered and updated exactly as on a real radio. The frame is ignored when that module band is disabled in the model.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `module` | `uint8` | `0` = internal module, `1` = external module |
| `band` | `uint8` | RF band of the module (`0` for single-band modules) |
| `rx` | `uint8` | Receiver index on that module (`0` = first receiver) |
| `physId` | `uint8` | S.Port physical ID of the sensor |
| `primId` | `uint8` | Frame type, `0x10` for a data frame |
| `appId` | `uint16` | Sensor data ID |
| `value` | `uint32` | Raw sensor value |

```javascript
// VFAS voltage sensor (appId 0x0210, unit 0.01 V): 12.60 V, sent every 100 ms
setInterval(() => {
  sim.injectSPortFrame(0, 0, 0, 0x1B, 0x10, 0x0210, 1260);
}, 100);
```

### Macros and recording

Only one macro runs at a time. See [Macros](#macros) for the full flow.

#### `startMacro(path, pauseOnFirstLine)`

Runs a Lua macro in its own task. `path` (string): file system path of the `.lua` file, as seen in `Module.FS`; the macro's working directory is its folder. `pauseOnFirstLine` (bool): `true` to stop before the first line and wait for `resumeMacro()` or `stepMacro()`. The end is signalled by `Module.onMacroEnd()`.

```javascript
Module.onMacroEnd = () => console.log('macro done');
Module.FS.writeFile('/persist/X20RS/macros/demo.lua', 'simulator.setSwitch(0, 100)');
sim.startMacro('/persist/X20RS/macros/demo.lua', false);
```

#### `pauseMacro()`

Pauses the macro at its next line; `Module.onDebugEvent(source, line)` reports where.

```javascript
Module.onDebugEvent = (source, line) => console.log(`paused at ${source}:${line}`);
sim.pauseMacro();
```

#### `resumeMacro()`

Resumes a paused macro until the next breakpoint or the end.

```javascript
sim.resumeMacro();
```

#### `stepMacro()`

Runs one line of a paused macro, then pauses again and calls `onDebugEvent`.

```javascript
sim.stepMacro();
```

#### `stopMacro()`

Stops the macro at its next line, with the error "Macro stopped". `onMacroEnd()` is called.

```javascript
sim.stopMacro();
```

#### `setBreakpoints(list)`

Replaces all breakpoints. `list` (string): comma-separated `source:line` entries, where `source` is the name reported by `onDebugEvent` without its leading `@`. For the macro file itself, that is its file name. An empty string clears all breakpoints.

```javascript
sim.setBreakpoints('demo.lua:3,demo.lua:10');
sim.setBreakpoints('');   // clear
```

#### `startRecord()`

Starts recording the user's actions (touches, keys, rotary encoder, switches, sticks, function switches). Each action arrives in `Module.onRecordEvent(action, overwrite)` as a line of Lua macro code. When `overwrite` is `true`, it replaces the previous line, for example while a stick keeps moving.

```javascript
const lines = [];
Module.onRecordEvent = (action, overwrite) => {
  if (overwrite) lines[lines.length - 1] = action; else lines.push(action);
};
sim.startRecord();
```

#### `stopRecord()`

Stops the recording. The collected lines make a macro that replays the session.

```javascript
sim.stopRecord();
Module.FS.writeFile('/persist/X20RS/macros/recorded.lua', lines.join('\n'));
```

### Remote control

#### `remoteReceive(data, size)`

Sends bytes of the remote control protocol to the simulator. The radio's USB serial port speaks the same protocol, so one client can drive either. Responses and events arrive as `Uint8Array` chunks in `Module.onRemoteSend(bytes)`; a chunk can hold part of a frame or several frames. The simulator accepts the connection without the on-screen confirmation that a real radio asks for.

Frame format, multi-byte values little endian:

| Field | Size | Content |
| --- | --- | --- |
| SYNC | 1 | `0xE7` |
| COMMAND | 1 | Request code; the response uses `COMMAND \| 0x80` |
| SEQ | 1 | `1`–`255` in requests, echoed in the response; `0` for events sent by the radio |
| LENGTH | 2 | Payload size, at most 1024 |
| PAYLOAD | LENGTH | Command arguments; a response starts with status (int8, `0` = success) and flags (`0x01` = last chunk) |
| CRC16 | 2 | CRC-16/XMODEM (polynomial `0x1021`, initial value `0`) over COMMAND to the end of PAYLOAD |

This example sends `HELLO` (`0x01`, payload = protocol version `1`). The response (`0x81`) carries the protocol version, the firmware version (major, minor, revision) and the board name.

```javascript
function crc16(bytes) {
  let crc = 0;
  for (const b of bytes) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
  }
  return crc;
}

function remoteFrame(command, seq, payload = []) {
  const body = [command, seq, payload.length & 0xFF, payload.length >> 8, ...payload];
  const crc = crc16(body);
  return new Uint8Array([0xE7, ...body, crc & 0xFF, crc >> 8]);
}

Module.onRemoteSend = (bytes) => console.log('radio ->', bytes);
const frame = remoteFrame(0x01, 1, [1]);   // HELLO, seq 1, version 1
sim.remoteReceive(frame, frame.length);
```

The protocol also covers keys, touch, switches, analogs, screenshots and file transfer, the same on the simulator and on the radio over USB. Model JSON and macros exist only in the simulator, so they use exported functions instead (applyModelJson, setModelJsonNeeded, startMacro). Its command list is in the next table.

| Code | Command | Payload |
| --- | --- | --- |
| `0x01` | HELLO | version (1) → version (1), firmware major (1), minor (1), revision (1), board name (string), keys |
| `0x10` | PRESS\_KEY | key (1), duration ms (4) |
| `0x11` | TOUCH | x (2), y (2), long press (1) |
| `0x12` | SWIPE | x1 (2), y1 (2), x2 (2), y2 (2) |
| `0x13` | TURN\_ROTARY\_ENCODER | steps (2, signed) |
| `0x14` | ENTER\_TEXT | text (string) |
| `0x15` | RESET\_BACKLIGHT\_TIMER | – |
| `0x16` | PRESS\_FUNCTION\_SWITCH | index (1), duration ms (4) |
| `0x17` | SET\_SWITCH | index (1), position (2, signed) |
| `0x18` | RESET\_SWITCHES | – |
| `0x19` | SET\_ANALOG | index (1), value (1, signed) |
| `0x1A` | RESET\_ANALOGS | – |
| `0x1B` | RESET\_INACTIVITY\_TIMER | – |
| `0x20` | SCREENSHOT | display (1) → RLE RGB565 image chunks |
| `0x30` | LIST\_FILES | path → entries: is directory (1), size (4), name (string) |
| `0x31` | READ\_FILE | offset (4), path → data chunks |
| `0x32` | WRITE\_FILE | offset (4), path (string), data |
| `0x33` | DELETE\_FILE | path |
| `0x40` | LOAD\_MODEL | path |
| `0x70` | SET\_DATE\_TIME | year (2), month (0–11), day, hour, min, sec, lock (1 each) |

Strings are NUL-terminated. Status codes: `0` success, `-1` unsupported, `-2` refused, `-3` invalid argument, `-4` I/O error, `-5` busy, `-6` timeout. The radio also sends events with SEQ `0`: `0xC1` state (flight mode, RF active, backlight on) and `0xC2` connection confirmed.

## Simulator Lua API

The global `simulator` table drives the simulated radio from a Lua macro. Every function raises an error when called outside a macro.

| Function | Since | Purpose |
| --- | --- | --- |
| `touch(x, y)` | 1.5.0 | Tap the touch screen. Error on radios without one |
| `swipe(x1, y1, x2, y2)` | 26.1.3 | Slide a finger on the touch screen |
| `pressKey(key, [duration])` | 1.5.0 | Press a key; duration in seconds, default 0.02 |
| `pressFunctionSwitch(index, [duration])` | 26.1.0 | Press a function switch; duration in seconds, default 0.02 |
| `turnRotaryEncoder(steps)` | 1.5.0 | Turn the rotary encoder |
| `enterText(text)` | 26.1.0 | Type text in the virtual keyboard |
| `setSwitch(switch, position)` | 1.5.0 | Set a switch position |
| `resetSwitches()` | 26.1.0 | Put all switches back to their default position |
| `setAnalog(analog, value)` | 1.5.0 | Set a stick or pot, -100 to 100 |
| `resetAnalogs()` | 26.1.0 | Center all sticks and pots |
| `loadModel(path)` | 1.5.0 | Load a model file |
| `setDateTime({sec, min, hour, day, month, year, lock})` | 1.5.0 | Set the clock; missing fields keep the current time |
| `screenshot(path, [rect])` | 1.5.0 | Save the screen, or a `{x, y, w, h}` part of it, as PNG |
| `sleep(seconds)` | 1.5.0 | Wait while the radio keeps running |
| `connectUsb([state])` | 1.5.0 | Simulate a USB connect or disconnect |
| `advertizeBluetooth(name, [address])` | 26.1.0 | Make a Bluetooth device appear during a scan; address as `AA:BB:CC:DD:EE:FF` |
| `injectSPortFrame({module, band, rx, physId, primId, appId, value})` | 26.1.0 | Inject an S.Port telemetry frame |
| `setReadOnly(on)` | 1.5.0 | Stop the simulator from writing files |
| `resetInactivityTimer()` | 26.1.0 | Reset the inactivity alarm timer |
| `resetBacklightTimer()` | 26.1.0 | Keep the backlight on |
| `reloadScripts()` | 26.1.0 | Unload and reload all Lua scripts |
| `setDebug("malloc", on)` | 26.1.0 | Turn Lua memory allocation tracing on or off |

```lua
simulator.setSwitch(0, 100)
simulator.setAnalog(2, -100)
simulator.sleep(0.5)
simulator.screenshot("/persist/X20RS/documents/throttle.png")
```

## Macros

A macro is a Lua script run in its own task, with a line-by-line debugger the page controls. It is the way to automate the simulator: test scenarios, screenshots for the manual, model setup.

1. Optionally call `setBreakpoints("path/script.lua:12,path/script.lua:40")` (comma-separated `file:line` list).
2. Call `startMacro(path, pauseOnFirstLine)`. The working directory becomes the script folder.
3. When execution stops on a line (first line, breakpoint or step), the page gets `onDebugEvent(source, line)`.
4. Drive it with `resumeMacro()`, `stepMacro()` (run one line, then pause), `pauseMacro()` or `stopMacro()`.
5. When the script returns, fails or is stopped, the page gets `onMacroEnd()`. Lua errors are written to the console.

While the macro is paused, the radio keeps running: screens refresh and the page inputs still work.

### Recording

`startRecord()` records what the user does in the page (touches, keys, rotary encoder, switches, sticks, function switches) as macro actions. Each action is sent to `onRecordEvent(action, overwrite)`. When `overwrite` is true, the action replaces the previous one, for example when a stick keeps moving. `stopRecord()` ends the recording.

## Limitations

The simulator runs the real firmware logic, but everything outside the radio is simulated or absent.

- **RF and telemetry:** no RF module is connected. Telemetry exists only through `injectSPortFrame()`.
- **Bluetooth:** no real device. A device appears in a scan only with `simulator.advertizeBluetooth()`. ActiveLook glasses are drawn through `updateCanvasGlasses`.
- **USB:** simulated with `simulator.connectUsb()`.
- **Battery:** the main voltage is fixed.
- **Clock:** taken from the browser, unless set with `simulator.setDateTime()`.
- **Refresh rate:** callbacks run at most 100 times per second, on the browser main thread.
- **Storage:** in a browser, files live in IndexedDB, per origin, and clearing site data erases models and settings. Under Node.js they are plain files in the mounted folder.
