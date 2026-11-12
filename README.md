# Ethos Tools

A collection of tools and utilities built around [FrSky Ethos](https://www.frsky-rc.com/), covering areas such as simulation, audio, translations, and more.

## Contents

- [`simulation/`](simulation/README.md) — tooling to run the Ethos WebAssembly simulator build from the command line (see [`run_wasm.js`](simulation/run_wasm.js)), useful for scripted testing, macros, and CI. Its `--serve` mode allows interactive control, and the folder is also the `ethos-simulator` Claude Code plugin, which lets Claude navigate the radio UI.

### Claude Code plugins

This repository is a [Claude Code](https://claude.com/claude-code) plugin marketplace:

```text
/plugin marketplace add FrSkyRC/ethos-tools
/plugin install ethos-simulator@ethos-tools
```

More tools (audio processing, translation/localization helpers, etc.) will be added here over time, each in its own subdirectory with a dedicated README.

## License

Proprietary and confidential — see individual files for copyright notices.
