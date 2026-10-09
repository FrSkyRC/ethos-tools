# Ethos Tools — simulation

Tools and documentation for the Ethos simulator.

| Document | Content |
| --- | --- |
| [Ethos WASM Simulator](wasm_simulator.md) | Reference of the WASM simulator: embedding in a web page or Node.js, exported functions, callbacks, remote control protocol, Lua `simulator` API, macros |
| [`run_wasm.js`](run_wasm.md) | Node.js runner for the WASM simulator: run it from the command line and drive a Lua macro (tests, CI), or keep it running and operate it interactively (`--serve`) |

## Claude Code plugin: `ethos-simulator`

This folder is also a [Claude Code](https://claude.com/claude-code) plugin. Its [`ethos-navigate`](skills/ethos-navigate/SKILL.md) skill teaches Claude to start [`run_wasm.js --serve`](run_wasm.md#interactive-mode---serve) and operate the radio. Claude takes a screenshot, reads it, taps or presses a key, and repeats. You can then ask things like *"open the Mixes page and show me what it looks like"* or *"check that my widget renders on the X18"*.

Install it from any Claude Code session:

```text
/plugin marketplace add FrSkyRC/ethos-tools
/plugin install ethos-simulator@ethos-tools
```

Inside a clone of this repository, the project settings (`.claude/settings.json`) already list the marketplace, and Claude Code offers to install the plugin when you trust the folder.

Requirements: Node.js 18+ and an Ethos WASM build (`<BOARD>_<PROTOCOL>.js` + `.wasm`). The skill finds builds that the Ethos VS Code extension has already downloaded.
