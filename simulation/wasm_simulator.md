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

All are optional; the firmware checks that each one exists before calling it. They run on the main thread, at most 100 times per second, except `onRemoteResult` which is called as soon as a command result is ready.

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
| `onStopped()` | Firmware stopped, after `stop()` |
| `onRemoteResult(id, status, data)` | Result of a remote control command (see Remote control) |
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

A `'string'` argument is copied on the wasm stack, which is 64 KB. A longer string overflows it and overwrites the memory after it: the firmware then crashes later, often with `RuntimeError: memory access out of bounds` in an unrelated call. Pass such a string (a model JSON for instance) on the heap: `Module.stringToNewUTF8(value)` copies it into a buffer allocated with `malloc`, the function takes its address as a `'number'`, and `Module._free(address)` releases it. `stringToNewUTF8`, `_malloc` and `_free` are exported for that.

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
    // the JSON is bigger than the stack: it is passed on the heap
    applyModelJson: (json) => {
      const address = Module.stringToNewUTF8(json);
      try {
        return Module.ccall('applyModelJson', 'boolean', ['number'], [address]);
      } finally {
        Module._free(address);
      }
    },
    modelJsonError: w('modelJsonError', 'string'),
    getChannelsOutputs: w('getChannelsOutputs', 'number'),
    setLanguage: w('setLanguage', null, ['string']),
    loadCustomTranslations: w('loadCustomTranslations', null, ['string']),
    // Telemetry
    injectSPortFrame: w('injectSPortFrame', null, ['number', 'number', 'number', 'number', 'number', 'number', 'number']),
    injectCrsfFrame: (frameId, payload) => Module.ccall('injectCrsfFrame', null, ['number', 'array', 'number'], [frameId, payload, payload.length]),
    // Macros and recording
    startMacro: w('startMacro', null, ['string', 'boolean']),
    stopMacro: w('stopMacro', null),
    pauseMacro: w('pauseMacro', null),
    resumeMacro: w('resumeMacro', null),
    stepMacro: w('stepMacro', null),
    setBreakpoints: w('setBreakpoints', null, ['string']),
    startRecord: w('startRecord', null),
    stopRecord: w('stopRecord', null),
    // Remote control: the commands return a Promise, see wrapRemote()
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

Stops the firmware, asynchronously: `stop()` returns right away, the page keeps running (screen, audio) while the remote control commands already sent are executed, the firmware tasks stop and the model and settings are written. Then Lua scripts are unloaded, the main loop stops and `Module.onStopped()` is called. To run the radio again, load a new module instance.

```javascript
await new Promise((resolve) => {
  Module.onStopped = resolve;
  sim.stop();
});
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

Replaces the loaded model with `json` (string), in the format `Module.setModelJson` delivers. Returns `false` when the JSON is invalid, `modelJsonError()` then gives the reason; a syntax error leaves the current model untouched. A model JSON is often bigger than the 64 KB wasm stack: pass it on the heap, as `wrapSimulator()` does (see [Calling conventions](#calling-conventions)), never as a `'string'` argument. The model is swapped between two loops of the main task, under the mixer lock, then its screens and widgets are rebuilt. The call can block briefly while the main task finishes its loop.

The `functions` array holds the model's special functions only: the global ones are part of the radio settings, they are neither in the JSON nor changed by `applyModelJson`. When `mixes` or `functions` is absent, the model's mixes or special functions are left unchanged.

Out-of-range values are not rejected: they are brought back to the nearest allowed value, as the radio's model setup would. An unknown model `type` becomes `99` (other). The model category settings (`aileronsCount`, `flapsCount`, `elevatorsCount`, `ruddersCount`, `tailType`, `swashType`, `steeringConfig`, `brakesCount`, `enginesCount`) are limited to their channel counts and made consistent with each other: a flying wing (`tailType` 0) has no elevator nor rudder and 2 ailerons at least, a V-tail (`tailType` 2) has `elevatorsCount` and `ruddersCount` set to 1, 3 ailerons, flaps or elevators become 4, and a stabilized receiver lowers the counts it cannot handle. `throttleDeadBand` is limited to 0-100, `modelId` to 0-63. Read the model back with `getModelJson()` to see the values kept.

A JSON exported by another radio is accepted: the arrays whose size depends on the hardware (`trims`, `crossTrims`, `rf.modules`, `preflightChecks.potValues`) are read up to the size of this radio, the extra elements are ignored and the missing ones keep their default value. The sources still reference the switches, trims and pots by index, they may designate other controls on a radio with a different hardware.

```javascript
let modelJson;
Module.setModelJson = (json) => { modelJson = json; };
sim.setModelJsonNeeded(true);

// later
const model = JSON.parse(modelJson);
model.name = 'Glider';
if (!sim.applyModelJson(JSON.stringify(model))) {
  console.error('Invalid model JSON:', sim.modelJsonError());
}
```

#### `modelJsonError()` → `string`

Why the last `applyModelJson()` failed: the message of the exception caught while parsing or reading the JSON, for instance `[json.exception.out_of_range.403] key 'name' not found`. Empty after a success.

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

#### `injectCrsfFrame(frameId, payload)`

Feeds one CRSF telemetry frame to the firmware, as if the external module had sent it. Only the frame ID and the payload are given: the address (`0xEA`), the length and the CRC are added by the firmware. Sensors are discovered and updated exactly as on a real radio, and the frame is forwarded to the Lua scripts listening with `crsf.getSensor()`. The frame is ignored when the payload is longer than 124 bytes.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `frameId` | `uint8` | CRSF frame type, for example `0x08` for a battery frame |
| `payload` | `Uint8Array` or array of bytes | Frame payload, without frame ID and CRC |

The C export is `injectCrsfFrame(frameId, payload, payloadLength)`. The `payload` pointer must be passed with the `'array'` type of `ccall`, as the wrapper above does.

```javascript
// Battery frame: 12.6 V (0x007E, unit 0.1 V), 1.0 A (0x000A, unit 0.1 A), 500 mAh (0x0001F4), 80 %
sim.injectCrsfFrame(0x08, new Uint8Array([0x00, 0x7E, 0x00, 0x0A, 0x00, 0x01, 0xF4, 0x50]));
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

These commands act on the radio the way a user does (keys, touch screen, rotary encoder) and load models. They are the same functions as the `simulator` Lua API and as the remote control protocol of the radio USB serial port.

A command waits in the firmware until the radio UI has handled it, so it can't return its result right away. Each export takes a request id as first argument, queues the command and returns. A firmware task executes it, then calls `Module.onRemoteResult(id, status, data)` on the main thread as soon as it is done (`data` is `null`, reserved for commands returning data).

Status codes: `0` success, `-1` unsupported, `-2` refused, `-3` invalid argument, `-4` I/O error, `-5` busy (a macro is running), `-6` timeout, `-7` woke up: the screen was off, and as for the user the input (`press()` of a key, `touch()`, `swipe()`, `turnRotaryEncoder()`) only turned it back on, send it again.

Copy this function into the host. It turns every command into a function that returns a Promise of `{ status, data }`:

```javascript
function wrapRemote(Module) {
  let nextId = 1;
  const pending = new Map();
  Module.onRemoteResult = (id, status, data) => {
    pending.get(id)?.({ status, data });
    pending.delete(id);
  };
  const command = (name, argTypes = []) => (...args) => new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    Module.ccall(name, null, ['number', ...argTypes], [id, ...args]);
  });
  return {
    press: command('press', ['string', 'number']),
    pressFunctionSwitch: command('pressFunctionSwitch', ['string', 'number']),
    touch: command('touch', ['number', 'number', 'number']),
    swipe: command('swipe', ['number', 'number', 'number', 'number']),
    turnRotaryEncoder: command('turnRotaryEncoder', ['number']),
    enterText: command('enterText', ['string']),
    resetBacklightTimer: command('resetBacklightTimer'),
    resetInactivityTimer: command('resetInactivityTimer'),
    setDateTime: command('setDateTime', ['number', 'number', 'number', 'number', 'number', 'number', 'boolean']),
    loadModel: command('loadModel', ['string']),
    resetFunctionSwitches: command('resetFunctionSwitches'),
  };
}

const remote = wrapRemote(Module);
```

| Function | Arguments | Action |
| --- | --- | --- |
| `press(key, duration)` | `key`: `"ENTER"`, `"RTN"`, `"SYS"`, `"MDL"`, `"DISP"`, `"PAGE"`, `"UP"`, `"DOWN"`, `"LEFT"`, `"RIGHT"`, trims `"T1-"`, `"T1+"`… `"T6+"`, function switches `"FS1"`, `"FS2"`… (those the radio has, status `-3` otherwise); `duration` in ms, 540 ms or more is a long press | Press a key, a trim or a function switch |
| `pressKey(key, duration)` | as `press()` | Same as `press()`, kept for the existing pages |
| `pressFunctionSwitch(name, duration)` | `name`: `"FS1"`, `"FS2"`…; `duration` in ms | Press a function switch (same as `press()`, kept for the existing pages) |
| `touch(x, y, duration)` | screen pixels, (0, 0) top left; `duration` in ms (as `press()`), 1000 ms or more is a long press | Tap the touch screen |
| `swipe(x1, y1, x2, y2)` | screen pixels | Slide a finger on the touch screen |
| `turnRotaryEncoder(steps)` | detents, the sign gives the direction | Turn the rotary encoder |
| `enterText(text)` | text | Type in the virtual keyboard |
| `resetBacklightTimer()` | – | Turn the screen back on |
| `resetInactivityTimer()` | – | Reset the inactivity alarm timer |
| `setDateTime(year, month, day, hour, min, sec, lock)` | `month`: 0–11; `lock`: freeze the clock | Set the clock |
| `loadModel(path)` | radio path, e.g. `/models/model01.bin` | Load a model |
| `resetFunctionSwitches()` | – | Turn all the function switches of the current model off, as for a new model (status `-1` without function switches) |

```javascript
await remote.press('PAGE', 20);
await remote.touch(400, 240, 20);
const { status } = await remote.swipe(600, 240, 200, 240);
```

There is no screenshot command: the page already gets every frame (see Screenshots below).

Switches, sticks, files and the screen have their own exports or callbacks (`setSwitchPosition()`, `setAnalogPosition()`, `Module.FS`), so they are not repeated here. A command sent while a macro runs is refused with status `-5`. Commands sent before `stop()` are executed first; commands sent after are refused with status `-2`.

### Screenshots

The screen comes with every frame in `updateCanvas(width, height, ptr)` (and `updateCanvasTop` for the top LCD): `ptr` points to a static buffer of the firmware, so it stays valid, and its content up to date, as long as the module runs. The pixels are RGB565, with the rows bottom-up. A command result arrives after the UI has handled it, so the buffer already shows its effect.

```javascript
let mainScreen;
Module.updateCanvas = (width, height, ptr) => {
  mainScreen = { width, height, ptr };
  // ... draw it
};

// PNG data URL of the main screen
function screenshot() {
  const { width, height, ptr } = mainScreen;
  const pixels = new Uint16Array(Module.HEAPU16.buffer, ptr, width * height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d');
  const image = context.createImageData(width, height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const pixel = pixels[(height - 1 - y) * width + x];   // bottom-up rows
      const r = (pixel >> 11) & 0x1F, g = (pixel >> 5) & 0x3F, b = pixel & 0x1F;
      image.data.set([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), 255], (y * width + x) * 4);
    }
  }
  context.putImageData(image, 0, 0);
  return canvas.toDataURL('image/png');
}
```

The frames are dimmed while the backlight is dimmed: call `resetBacklightTimer()` first for a full brightness screenshot.

The input commands act as the user: `press()`, `touch()`, `swipe()`, `turnRotaryEncoder()` and `enterText()` wake the backlight up, and while the screen was off (the navigation is then disabled), a key press, a touch or an encoder step only turns it back on, as on the radio, and returns the status `-7` (the `simulator` Lua functions then send it again, so a macro is not affected). `resetBacklightTimer()` turns it on without any other effect.

## Simulator Lua API

The global `simulator` table drives the simulated radio from a Lua macro. Every function raises an error when called outside a macro.

| Function | Since | Purpose |
| --- | --- | --- |
| `touch(x, y, [duration])` | 1.5.0 | Tap the touch screen; duration (26.2.0) in seconds, default 0.02, 1 s or more is a long press. Error on radios without one |
| `swipe(x1, y1, x2, y2)` | 26.1.3 | Slide a finger on the touch screen |
| `press(name, [duration])` | 26.2.0 | Press a key, a trim or a function switch by its name as in the remote control (`"ENTER"`, `"T1+"`, `"FS1"`…, error if the radio has no such key); duration in seconds, default 0.02, 0.54 s or more is a long press |
| `pressKey(key, [duration])` | 1.5.0 | Press a key by its index `KEY_xxx`; duration in seconds, default 0.02 |
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
| `injectCrsfFrame(frameId, payload)` | 26.2.0 | Inject a CRSF telemetry frame from the external module; length and CRC are computed |
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

- **RF and telemetry:** no RF module is connected. Telemetry exists only through `injectSPortFrame()` and `injectCrsfFrame()`.
- **Bluetooth:** no real device. A device appears in a scan only with `simulator.advertizeBluetooth()`. ActiveLook glasses are drawn through `updateCanvasGlasses`.
- **USB:** simulated with `simulator.connectUsb()`.
- **Battery:** the main voltage is fixed.
- **Clock:** taken from the browser, unless set with `simulator.setDateTime()`.
- **Refresh rate:** callbacks run at most 100 times per second, on the browser main thread (`onRemoteResult` excepted, see Remote control).
- **Storage:** in a browser, files live in IndexedDB, per origin, and clearing site data erases models and settings. Under Node.js they are plain files in the mounted folder.
