---
name: navigate
description: Drive the FrSky Ethos radio firmware in its WASM simulator — boot a radio (X20S, X18, X14...), see its screen as PNG screenshots, and operate it with touch, rotary encoder, hardware keys (SYS/MDL/DISP/RTN/PAGE/ENTER), switches and sticks. Use when asked to navigate Ethos menus, check what a screen or Lua widget/tool looks like, reproduce a UI bug, configure a model or radio setting through the UI, or run a Lua macro in the simulator.
---

# Navigating Ethos in the WASM simulator

`run_wasm.js --serve` keeps an Ethos simulator running behind a local HTTP server.
You act on it with one-shot client commands and look at the result with `screenshot` + Read.

The script ships with this skill: it is `../../run_wasm.js` relative to this skill's base directory
(the plugin root). Resolve it to an absolute path once, and use that path wherever this page says `"$DRIVER"`.
Shell variables don't persist between Bash calls, so write the absolute path into each command.
Requires Node.js 18+.

## 0. With the MCP tools: `startSimulator`

When the plugin's MCP tools (`ethos`) are available, start the simulator with them instead of steps 1 to 3:

- `startSimulator(radio, release, rootDirectory)` downloads the simulator of any radio from the Ethos releases on GitHub (cached after the first time), starts `run_wasm.js --serve` and waits until it answers. `radio` is a board (`X20S`, the FCC version then) or `BOARD-PROTOCOL` (`X18-EU`, `X20S-FLEX`). `release` defaults to the newest prerelease. Only 26.2 or newer: the older simulators have no remote control.
- `listSimulators(release)` lists the releases and the radio names of one of them.
- `rootDirectory` follows the same rules as step 2 below. Without it, the radio is empty and in memory.
- `stopSimulator()` when finished, instead of `quit`.

The simulator is served on the default port, so the command lines of step 4 work on it too. Go to step 4.

## 1. Find a simulator build

Builds are `<BOARD>_<PROTOCOL>.js` + `.wasm` pairs (e.g. `X20S_FCC`). The Ethos VS Code extension caches them in:

```
%APPDATA%\Code\User\globalStorage\bsongis.ethos\cache\        (Git Bash: "$APPDATA/Code/User/globalStorage/bsongis.ethos/cache")
```

List it to see which boards are available. If the user names a board, use that one. Otherwise use the newest build, by file date. Older builds can crash on `startMacro` with "table index is out of bounds", and the builds without the remote control exports (`status` returns `"remoteControl": false`) can't run the input commands.
If nothing is cached, ask the user to run "Ethos: Start" once in VS Code, or to point you at a build.

## 2. Pick the radio storage (root directory)

`--root-directory DIR` mounts a real folder as the radio's storage (`radio.bin`, `models/`, `scripts/`, `audio/`...).
**The simulator writes to it** (settings, RAM backup, models), so:

- To explore or test without side effects, copy the folder to the scratchpad first and mount the copy.
- Mount the user's folder directly only when they want changes kept (e.g. "configure this model").
- With no `--root-directory`, an empty in-memory radio is used (factory state, no models or scripts).

The VS Code extension's per-radio folders are usually `<workspace>/simulator/<BOARD>_<PROTOCOL>` or `<workspace>/simulators/<BOARD>_<PROTOCOL>@<release>`.

## 3. Start the server (background)

Run in the background (Bash `run_in_background: true`):

```bash
node "$DRIVER" "<path>/X20S_FCC.js" --serve --root-directory "<radio dir>" --shots-dir "<scratchpad>/shots"
```

Then poll until it answers (boot takes a few seconds):

```bash
for i in $(seq 1 30); do node "$DRIVER" status && break; node -e "setTimeout(()=>{},1000)"; done
```

`status` returns the displays and their size, switch positions and trims.

Once it serves, the plugin's MCP server (`ethos` tools: `press`, `touch`, `screenshot`, `getLog`...) drives it too: prefer these tools to the command lines below when they are available. They act on a radio served by `radio_remote.py --serve` instead, when there is one. Use `--port N` on both the server and the client commands to run several radios at once (default 8765).

## 4. The navigation loop

1. `node "$DRIVER" screenshot <scratchpad>/shots/<name>.png`, then **Read** the PNG.
2. Decide one action from what is on screen.
3. Run the action. Every action waits for the screen to settle before returning.
4. Take another screenshot to confirm. Never assume an action worked.

Name screenshots after what they show (`system-menu.png`, `after-ok.png`) so the trail makes sense.

### Commands

| Command | Effect |
|---|---|
| `screenshot [out.png] [MAIN\|TOP\|GLASSES]` | Saves a display (the main screen by default) as a PNG and returns its path and size |
| `press NAME [ms]` | Press and release a key, a trim or a function switch (see below). Default 20 ms, from 540 ms a long press |
| `touch X Y [ms]` | Touch at screen pixel (top-left origin, same coordinates as the screenshot). Default 20 ms, from 1000 ms a long press: context menus, e.g. on a widget or a model |
| `swipe X1 Y1 X2 Y2` | Drag: scroll lists, change menu page, adjust sliders |
| `turnRotaryEncoder N` | Rotary encoder N detents (+ clockwise / next, − back) |
| `enterText TEXT` | Type text in the on-screen keyboard (a text field must be being edited) |
| `loadModel PATH` | Load a model file, e.g. `/models/model01.bin` |
| `resetBacklightTimer` | Turn the screen back on (the screenshots are dimmed with the backlight) |
| `setSwitch I V` | Physical switch I to -100 / 0 / 100 (`status` shows the positions) |
| `setAnalog I V` | Stick / pot / slider I value, -100 to 100 |
| `startMacro PATH [timeoutMs]` | Runs a Lua macro (e.g. `USER:/macros/x.lua`) to completion |
| `getInformation` | Board, firmware version, display sizes |
| `getLog [since]` | Simulator log: Lua errors and `print` show up here. The result has `next`: `getLog NEXT` then only returns the newer lines, 200 at most per call (`more` is then true: call again with the new `next`) |
| `listFiles [PATH]`, `readFile PATH [out]`, `writeFile PATH SRC`, `createDirectory PATH`, `deleteFile PATH` | Files of the radio folder, paths from its root. In Git Bash, without the leading `/` (`scripts/main.lua`) |
| `reload [SCRIPTS\|ALL]` | Reload the Lua scripts after changing one, or everything |
| `getChannelsOutputs` | Mixer outputs in %, to check a model change |
| `getModelJson [out.json]`, `applyModelJson SRC.json` | Current model as JSON |
| `wait MS` | Pause, e.g. for an animation or a timed alert |
| `quit` | Stop the simulator. **Always do this when finished** |

### Keys

| Radio button | NAME | What it opens |
|---|---|---|
| SYS | `SYS` | System menu |
| MDL | `MDL` | Model menu |
| DISP | `DISP` | Configure screens |
| RTN | `RTN` | Back / close dialog |
| PAGE | `PAGE` | Next page or tab |
| Encoder press | `ENTER` | Select / edit the focused item |

Also `UP`, `DOWN`, `LEFT`, `RIGHT` on the radios that have them, the trims `T1-`, `T1+` ... `T6+` (the number written on the radio) and the function switches `FS1`, `FS2`... A name the radio doesn't have returns `"status": "invalid argument"`.

Every input command returns `{"ok": true, "status": "success"}` once the radio UI handled it, or the failure status (`busy` while a macro runs, `unsupported` e.g. `touch` without a touchscreen).

## Screen layout (X20 family, 800×480)

- Top bar (y ≈ 0–60): back arrow `<` at about (30, 33), page title, then status icons.
- Home screen bottom bar (y ≈ 448): Home (78), Model ✈ (225), Screens ⊞ (372), System ⚙ (518), clock.
- Menus are 4×2 tile grids: tile centres at x ≈ 148 / 316 / 483 / 650, y ≈ 165 / 330. The dots at y ≈ 75 show the page. Swipe horizontally or press `PAGE` to change page.
- Settings pages are lists of label/value rows about 58 px tall. Tap the value to edit it, and swipe vertically to scroll.

Other boards have different resolutions (X18/Twin X Lite 480×320, X14 640×360...). Check `width`/`height` from `screenshot` and work from the image, not from these numbers.

## Tips

- SYS / MDL / DISP only jump to their menu from the top level. From inside a settings page they are ignored, even though `press` still returns `"status": "success"`. Press `RTN` until you're back at Home (or a menu grid), then press the menu key.
- Don't chain several actions blindly. If one misses (above), the rest land on the wrong screen. Screenshot between steps unless the path was already verified in this session.
- A radio folder with no models (or no `--root-directory`) boots into the **Create model** wizard, where the menu keys are ignored. Finish or cancel it first.
- Booting usually shows a **Checklist warning** dialog (throttle, failsafe). Dismiss it with its **OK** button at the bottom right, or with `RTN`.
- Touch is usually the most direct way in. Use `turnRotaryEncoder` + `press ENTER` when a target is small or only reachable by focus.
- Lua errors show up in `getLog`, not on screen. Check `getLog` when a widget or tool looks blank.
- Don't send input while `startMacro` runs. It blocks until the macro ends, and the input commands are refused (`busy`) meanwhile.
- Screenshots dark? The backlight went off: `resetBacklightTimer`, then take the screenshot again. As on the radio, the first `press`, `touch`, `swipe` or encoder step on a dark screen only turns it back on: it then returns `"status": "woke up ..."`, send it again.
- Report what you saw: include the key screenshot paths in your answer so the user can open them.
