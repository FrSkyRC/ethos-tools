/*
 * Copyright (C) FrSky RC, Inc - All Rights Reserved
 *
 * Unauthorized copying of this file, via any medium is strictly prohibited
 * Proprietary and confidential
 *
 */

'use strict'

// MCP server (stdio, no dependency) of the ethos Claude Code plugin.
// It has the same tools as the ethos-simulator MCP server of the online
// simulator, and relays them to the local instance which is serving:
//  - radio_remote.py --serve (a radio connected over USB), when it answers,
//  - else run_wasm.js --serve (the WASM simulator).
// startSimulator() downloads the WASM simulator of a radio from the assets of
// a FrSkyRC/ETHOS-Feedback-Community release (<RADIO>-WebSimulator.zip), keeps
// it in a cache folder and starts run_wasm.js --serve with it. radio_remote.py
// is never started here (see radio_remote.md).
// Environment: ETHOS_RADIO_PORT (8766), ETHOS_SIMULATOR_PORT (8765),
// ETHOS_CACHE_DIR (the downloaded simulators), GITHUB_TOKEN (optional, for the
// GitHub API rate limit).

const fs = require('fs')
const os = require('os')
const path = require('path')
const readline = require('readline')
const { spawn } = require('child_process')
const { MIN_VERSION, cacheDir, recentReleases, findRelease, simulatorAssets, radioName, resolveSimulator } = require('./simulators')

const SERVER_INFO = { name: 'ethos', version: '0.3.0' }
const DEFAULT_PROTOCOL_VERSION = '2025-06-18'
const RADIO_PORT = Number(process.env.ETHOS_RADIO_PORT || 8766)
const SIMULATOR_PORT = Number(process.env.ETHOS_SIMULATOR_PORT || 8765)
const STATUS_TIMEOUT_MS = 1000
const COMMAND_TIMEOUT_MS = 120000
const BOOT_TIMEOUT_MS = 60000

const INSTRUCTIONS = [
  'Tools to drive a FrSky Ethos radio: the WASM simulator served by run_wasm.js --serve, or a radio',
  'connected over USB and served by radio_remote.py --serve (the radio is used when both are served).',
  'If no tool answers, start a simulator with startSimulator(radio, release): it downloads the simulator',
  `of any radio from the Ethos releases (${ MIN_VERSION.join('.') } or newer, listSimulators() lists them).`,
  'Call stopSimulator() when finished.',
  'Call getInformation() first to know the board, its firmware and the size of its displays.',
  'Use screenshot() to check what the radio displays, resetBacklightTimer() if the screen went dark,',
  'and press(), touch(), swipe() and turnRotaryEncoder() to navigate (e.g. press PAGE to switch between',
  'the main views), enterText() to type in a text field being edited and loadModel() to load a model file.',
  'Lua errors and print() don\'t show on the screen: read them with getLog().',
  'listFiles(), readFile(), writeFile(), createDirectory() and deleteFile() work on the radio storage (e.g. /scripts):',
  'call reload() after changing a Lua script. getChannelsOutputs() returns the mixer outputs, getControls() the switches,',
  'trims and function switches (with since, it waits until they change; resetFunctionSwitches() turns the function',
  'switches off). The resource ethos://controls has them too, its subscribers are notified of the changes.',
  'Change the model through its JSON: getModelJson(), edit the result, send the full document back',
  'with applyModelJson(), then call getModelJson() again to check. startMacro() runs Lua macros.',
  'The model JSON tools and startMacro() only work with the simulator. On a radio, the file tools',
  'change the files of the user\'s radio: only write or delete what the user asked for.',
].join(' ')

const integer = (description) => ({ type: 'integer', description })
const string = (description) => ({ type: 'string', description })

// The tools: their command on the served instance, and the arguments sent to it,
// or the local function which handles them (run)
const TOOLS = [
  {
    name: 'listSimulators',
    description: `List the Ethos releases whose WASM simulator can be started by startSimulator() (${ MIN_VERSION.join('.') } or newer, newest first; prerelease is true for the release candidates and betas), and the radios of one of them: BOARD-PROTOCOL names such as X20S-FCC, X18-EU (EU is LBT), X20S-FLEX.`,
    properties: { release: string('Release whose radios are listed, e.g. 26.2.0-RC10 (default: the newest prerelease)') },
    run: (args) => listSimulators(args),
  },
  {
    name: 'startSimulator',
    description: 'Start the Ethos WASM simulator of a radio, on this computer: it is downloaded from the assets of the release on GitHub the first time, then cached. radio is a board, e.g. X20S (FCC then), or BOARD-PROTOCOL, e.g. X18-EU (listSimulators() gives the names). release is e.g. 26.2.0-RC10, the newest prerelease by default. rootDirectory is a folder of this computer mounted as the radio storage (radio.bin, models/, scripts/...), created if missing; the simulator writes to it (settings, models), so mount a copy of the user\'s folder unless they want the changes kept. Without rootDirectory, the radio is empty and in memory (factory state: it boots into the Create model wizard). Fails when a simulator is already served: call stopSimulator() first. Then use screenshot() to see the screen.',
    properties: {
      radio: string('Board or BOARD-PROTOCOL, e.g. X20S, X18-EU'),
      release: string('Ethos release, e.g. 26.2.0-RC10 (default: the newest prerelease)'),
      rootDirectory: string('Absolute path of the folder mounted as the radio storage (default: an empty in-memory radio)'),
    },
    required: ['radio'],
    run: (args) => startSimulator(args),
  },
  {
    name: 'stopSimulator',
    description: 'Stop the simulator served on this computer (started by startSimulator() or run_wasm.js --serve). Call it when finished.',
    properties: {},
    run: () => stopSimulator(),
  },
  {
    name: 'getInformation',
    description: 'Return what the tools act on: target (simulator, or usb for a radio), board (e.g. X20S), firmware version, RF protocol, the size of each display (MAIN, and TOP or GLASSES when there is one, in the screenshot() and touch() coordinates), the storage drives (paths for listFiles()) and, for a radio, its state (flight mode, RF active, backlight on).',
    properties: {},
    command: 'getInformation',
  },
  {
    name: 'getLog',
    description: 'Return the system log lines: Lua print() output and Lua errors, which are not shown on the screen. Without since, the last 50 lines. The result has next: pass it as since on the following call to only get the lines added meanwhile (e.g. call getLog() before an action, then getLog(since=next) after it). truncated is true when lines were skipped (200 lines at most per call).',
    properties: { since: integer('Number returned as next by the previous call') },
    command: 'getLog',
  },
  {
    name: 'screenshot',
    description: 'Return a PNG screenshot of a display, to check what the radio currently shows. display is MAIN (default), TOP (top LCD, radios that have one) or GLASSES.',
    properties: { display: { type: 'string', enum: ['MAIN', 'TOP', 'GLASSES'], default: 'MAIN' } },
    command: 'screenshot',
    image: true,
  },
  {
    name: 'resetBacklightTimer',
    description: 'Turn the screen back on when it went to sleep (backlight off after the radio\'s inactivity delay). Call it before screenshot() if the capture is dark.',
    properties: {},
    command: 'resetBacklightTimer',
  },
  {
    name: 'press',
    description: 'Press a key of the radio, as the user would: SYS, MDL, DISP, PAGE, RTN, ENTER, and UP, DOWN, LEFT, RIGHT on the radios which have them; a trim: T1-, T1+ ... T6+ (the number written on the radio); or a function switch: FS1, FS2... A name the radio doesn\'t have fails. duration is the press duration in ms (default 20, a short press); the radio detects a long press from 540 ms, so use e.g. 1000 for a long press. When the screen was off, it only turns it back on, as for the user, and fails with the status woke up: call it again. Use screenshot() afterwards to check the result.',
    properties: { name: string('Key, trim or function switch name'), duration: { type: 'integer', default: 20 } },
    required: ['name'],
    command: 'press',
    args: ({ name, duration }) => ({ name: String(name).toUpperCase(), ms: duration ?? 20 }),
  },
  {
    name: 'touch',
    description: 'Touch the screen at (x, y), in screen pixels - the same coordinates as in screenshot() images, (0, 0) being the top-left corner. duration is the touch duration in ms (default 20, a tap); from 1000 ms it is a long press (context menus). When the screen was off, it only turns it back on, as for the user, and fails with the status woke up: call it again. Use screenshot() afterwards to check the result.',
    properties: { x: integer('Pixels from the left'), y: integer('Pixels from the top'), duration: { type: 'integer', default: 20 } },
    required: ['x', 'y'],
    command: 'touch',
    args: ({ x, y, duration }) => ({ x, y, ms: duration ?? 20 }),
  },
  {
    name: 'swipe',
    description: 'Slide a finger on the screen from (x1, y1) to (x2, y2), in screen pixels (same coordinates as screenshot() images). E.g. on a 800x480 screen, (600, 240) -> (200, 240) slides left and shows the next page, (400, 360) -> (400, 120) scrolls down a list. Use screenshot() afterwards to check the result.',
    properties: { x1: integer(''), y1: integer(''), x2: integer(''), y2: integer('') },
    required: ['x1', 'y1', 'x2', 'y2'],
    command: 'swipe',
  },
  {
    name: 'turnRotaryEncoder',
    description: 'Turn the rotary encoder of the radio by steps detents: positive moves the focus to the next item (or increases the value being edited), negative to the previous one. Press ENTER to select or edit the focused item. Use screenshot() afterwards to check the result.',
    properties: { steps: integer('Detents, the sign gives the direction') },
    required: ['steps'],
    command: 'turnRotaryEncoder',
  },
  {
    name: 'enterText',
    description: 'Type text in the text field being edited (its on-screen keyboard must be open: touch the field first). Fails when no text field is being edited.',
    properties: { text: string('Text to type') },
    required: ['text'],
    command: 'enterText',
    post: true,
  },
  {
    name: 'loadModel',
    description: 'Load a model file of the radio, e.g. /models/model01.bin, as the current model. Use screenshot() afterwards to check the result.',
    properties: { path: string('Model file, from the radio root') },
    required: ['path'],
    command: 'loadModel',
  },
  {
    name: 'listFiles',
    description: 'List a folder of the radio storage: path from its root, e.g. /, /scripts, /models (getInformation() gives the drives, e.g. SD:/ for the SD card of a radio). Returns the files with their name, isDirectory and size.',
    properties: { path: { type: 'string', default: '/' } },
    command: 'listFiles',
  },
  {
    name: 'readFile',
    description: 'Read a file of the radio storage (e.g. /scripts/mywidget/main.lua): text when it is UTF-8, else base64. 512 KB at most. Model files (.bin) are binary: use getModelJson() for the current model.',
    properties: { path: string('File, from the radio root') },
    required: ['path'],
    command: 'readFile',
  },
  {
    name: 'writeFile',
    description: 'Create or replace a file of the radio storage with text (UTF-8) or base64 content (binary files). The folder must exist. On a connected radio this changes the user\'s files: only write what the user asked for. After changing a Lua script, call reload() so that it is loaded again.',
    properties: { path: string('File, from the radio root'), text: string('Text content'), base64: string('Binary content, in base64') },
    required: ['path'],
    command: 'writeFile',
    post: true,
  },
  {
    name: 'createDirectory',
    description: 'Create a folder of the radio storage, e.g. /scripts/mywidget before writing its main.lua with writeFile(). Its parent folder must exist. A radio needs a firmware with the remote control protocol version 10 (getInformation() gives it).',
    properties: { path: string('Folder, from the radio root') },
    required: ['path'],
    command: 'createDirectory',
  },
  {
    name: 'deleteFile',
    description: 'Delete a file, or an empty folder, of the radio storage. On a connected radio this deletes the user\'s file: only delete what the user asked for.',
    properties: { path: string('File or empty folder, from the radio root') },
    required: ['path'],
    command: 'deleteFile',
  },
  {
    name: 'reload',
    description: 'Reload the Lua scripts (SCRIPTS, e.g. after writeFile() in /scripts), or everything: Lua scripts and current model (ALL). Check getLog() afterwards for Lua errors.',
    properties: { what: { type: 'string', enum: ['SCRIPTS', 'ALL'], default: 'SCRIPTS' } },
    command: 'reload',
  },
  {
    name: 'getChannelsOutputs',
    description: 'Return the mixer outputs of the channels used by the model, in % (-100 to 100, CH1 first): to check the result of a model change (mixes, curves, switches...).',
    properties: {},
    command: 'getChannelsOutputs',
  },
  {
    name: 'getControls',
    description: 'Return the switches, trims and function switches, as the radio (or the simulator) sent them when they changed: switches { SA: -1 (up), 0 (middle) or 1 (down), ... } (the installed ones), trims in % (-100 to 100) in the order rudder, elevator, throttle, aileron, then T5, T6, functionSwitches { FS1: true (on) / false (off), ... } as their LEDs, and version. To wait for a change (e.g. after asking the user to move a switch), pass the version of a previous call as since: the call returns when they changed (changed: true), or after timeout seconds (changed: false).',
    properties: {
      since: integer('version returned by a previous call: wait until the controls change'),
      timeout: { type: 'integer', description: 'seconds to wait for a change with since (default 30, at most 110)', default: 30 },
    },
    command: 'getControls',
    args: ({ since, timeout }) => ({ since, timeout: Math.min(timeout ?? 30, 110) * 1000 }),
  },
  {
    name: 'resetFunctionSwitches',
    description: 'Turn all the function switches of the current model off, as for a new model (press() of a function switch turns it on or off, depending on the function switches mode of the model).',
    properties: {},
    command: 'resetFunctionSwitches',
  },
  {
    name: 'getModelJson',
    description: 'Return the model currently loaded in the simulator, as JSON (channels, timers, etc.). Call this before making changes so indices/names match the real model. Simulator only.',
    properties: {},
    command: 'getModelJson',
    simulatorOnly: true,
  },
  {
    name: 'applyModelJson',
    description: 'Apply a model JSON document to the model loaded in the simulator. Must be the FULL document - call getModelJson() first, edit the result, and send the whole thing back; a partial document (e.g. just one changed field) will fail, since this goes through the firmware\'s own model deserializer, which expects every field to be present. Simulator only.',
    properties: { model: { type: 'object', additionalProperties: true, description: 'The full model JSON document' } },
    required: ['model'],
    command: 'applyModelJson',
    post: true,
    simulatorOnly: true,
  },
  {
    name: 'startMacro',
    description: 'Run a Lua macro in the simulator and return what it printed, once it ended. Only for building macros (e.g. scripted interactions with the radio): the simulator Lua API (simulator.press(), simulator.touch()...), and only documented functions - never invent names. Simulator only.',
    properties: { code: string('Lua code of the macro') },
    required: ['code'],
    command: 'startMacro',
    post: true,
    simulatorOnly: true,
  },
]

/* ---------- The served instances ---------- */

async function get(port, command, params = {}, timeoutMs = COMMAND_TIMEOUT_MS) {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [k, String(v)]))
  const res = await fetch(`http://127.0.0.1:${ port }/${ command }?${ query }`, { signal: AbortSignal.timeout(timeoutMs) })
  return res.json()
}

async function post(port, command, body) {
  const res = await fetch(`http://127.0.0.1:${ port }/${ command }`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(COMMAND_TIMEOUT_MS),
  })
  return res.json()
}

// The radio when radio_remote.py serves, else the simulator
async function findTarget() {
  for (const [kind, port] of [['radio', RADIO_PORT], ['simulator', SIMULATOR_PORT]]) {
    try {
      const status = await get(port, 'status', {}, STATUS_TIMEOUT_MS)
      if (status && status.ok) {
        return { kind, port }
      }
    } catch {
      // not serving
    }
  }
  return undefined
}

async function serving(port) {
  try {
    const status = await get(port, 'status', {}, STATUS_TIMEOUT_MS)
    return !!(status && status.ok)
  } catch {
    return false
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function listSimulators({ release } = {}) {
  const releases = await recentReleases()
  const listed = await findRelease(release)
  return {
    ok: true,
    releases: releases.map((r) => ({ release: r.tag_name, date: (r.published_at || '').slice(0, 10), prerelease: r.prerelease })),
    release: listed.tag_name,
    radios: simulatorAssets(listed).map(radioName),
  }
}

let simulatorProcess // the run_wasm.js started by startSimulator()

async function startSimulator({ radio, release, rootDirectory } = {}) {
  if (!radio) {
    return { ok: false, error: 'radio is required, e.g. X20S or X18-EU' }
  }
  if (await serving(SIMULATOR_PORT)) {
    return { ok: false, error: `A simulator is already served on port ${ SIMULATOR_PORT }: call stopSimulator() first` }
  }
  const build = await resolveSimulator(radio, release)

  const args = [path.join(__dirname, 'run_wasm.js'), build.js, '--serve', '--port', String(SIMULATOR_PORT), '--shots-dir', path.join(os.tmpdir(), 'ethos-shots')]
  let storage = 'empty radio in memory'
  if (rootDirectory) {
    storage = path.resolve(rootDirectory)
    fs.mkdirSync(storage, { recursive: true })
    args.push('--root-directory', storage)
  }
  const logFile = path.join(cacheDir(), 'simulator.log')
  fs.mkdirSync(path.dirname(logFile), { recursive: true })
  const log = fs.openSync(logFile, 'w')
  const child = spawn(process.execPath, args, { stdio: ['ignore', log, log], windowsHide: true })
  fs.closeSync(log)
  let exitCode
  child.on('exit', (code, signal) => {
    exitCode = code === null ? signal : code
    if (simulatorProcess === child) {
      simulatorProcess = undefined
    }
  })
  simulatorProcess = child

  const deadline = Date.now() + BOOT_TIMEOUT_MS
  while (exitCode === undefined && Date.now() < deadline) {
    if (await serving(SIMULATOR_PORT)) {
      const information = await get(SIMULATOR_PORT, 'getInformation')
      const notes = [build.warning]
      if (await serving(RADIO_PORT)) {
        notes.push(`A radio is served on port ${ RADIO_PORT } (radio_remote.py): the other tools act on it, not on this simulator.`)
      }
      if (information.remoteControl === false) {
        notes.push('This simulator has no remote control exports: only screenshot(), the file tools and startMacro() work.')
      }
      return {
        ok: true,
        release: build.tag,
        prerelease: build.prerelease,
        radio: build.radio,
        downloaded: build.downloaded,
        storage,
        ...(notes.some(Boolean) ? { notes: notes.filter(Boolean) } : {}),
        information,
      }
    }
    await sleep(500)
  }
  child.kill()
  const output = fs.existsSync(logFile) ? fs.readFileSync(logFile, 'utf8').slice(-2000) : ''
  return { ok: false, error: `The simulator did not start (${ exitCode === undefined ? 'timeout' : `exit code ${ exitCode }` })`, output }
}

async function stopSimulator() {
  if (!(await serving(SIMULATOR_PORT))) {
    return { ok: false, error: `No simulator is served on port ${ SIMULATOR_PORT }` }
  }
  await get(SIMULATOR_PORT, 'quit', {}, STATUS_TIMEOUT_MS).catch(() => undefined)
  for (let i = 0; i < 20 && await serving(SIMULATOR_PORT); i++) {
    await sleep(250)
  }
  return { ok: true }
}

/* ---------- The tools ---------- */

const text = (value) => ({ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 1) })

async function callTool(name, args = {}) {
  const tool = TOOLS.find((t) => t.name === name)
  if (!tool) {
    return { content: [text(`Unknown tool ${ name }`)], isError: true }
  }
  if (tool.run) {
    const result = await tool.run(args)
    return { content: [text(result)], isError: !result.ok }
  }
  const target = await findTarget()
  if (!target) {
    return {
      content: [text(`No Ethos simulator or radio is served: call startSimulator() (e.g. radio X20S), or connect a radio with radio_remote.py --serve (port ${ RADIO_PORT }).`)],
      isError: true,
    }
  }
  if (tool.simulatorOnly && target.kind !== 'simulator') {
    return { content: [text(`${ name }() only works with the simulator, and a radio is connected (radio_remote.py --serve).`)], isError: true }
  }
  const params = tool.args ? tool.args(args) : args
  if (tool.image) {
    const out = path.join(os.tmpdir(), `ethos-mcp-${ process.pid }-${ Date.now() }.png`)
    const result = await get(target.port, tool.command, { ...params, out })
    if (!result.ok) {
      return { content: [text(result)], isError: true }
    }
    try {
      return { content: [{ type: 'image', data: fs.readFileSync(out).toString('base64'), mimeType: 'image/png' }] }
    } finally {
      fs.rmSync(out, { force: true })
    }
  }
  const result = tool.post ? await post(target.port, tool.command, params) : await get(target.port, tool.command, params)
  if (name === 'getModelJson' && result.ok) {
    return { content: [text(JSON.stringify(result.model))] }
  }
  return { content: [text(result)], isError: !result.ok }
}

/* ---------- The resource ethos://controls: switches, trims, function switches ---------- */

const CONTROLS_URI = 'ethos://controls'
const CONTROLS_RESOURCE = {
  uri: CONTROLS_URI,
  name: 'controls',
  title: 'Switches, trims and function switches',
  description: 'The switches, trims and function switches of the radio or the simulator served (as getControls()). Its subscribers are notified when they change.',
  mimeType: 'application/json',
}
let controlsWatch // the loop notifying the subscribers, while there is one

async function readControls() {
  const target = await findTarget()
  if (!target) {
    throw new Error('No Ethos simulator or radio is served')
  }
  return get(target.port, 'getControls')
}

// Waits for the changes (getControls long poll) and notifies them, until the unsubscription
function watchControls() {
  if (controlsWatch) {
    return
  }
  controlsWatch = { active: true }
  const watch = controlsWatch
  ;(async () => {
    let version
    while (watch.active) {
      try {
        const target = await findTarget()
        if (!target) {
          await sleep(2000)
          continue
        }
        const result = await get(target.port, 'getControls', { since: version, timeout: 30000 }, 40000)
        if (!result.ok) {
          await sleep(2000)
          continue
        }
        if (version !== undefined && result.changed && watch.active) {
          send({ jsonrpc: '2.0', method: 'notifications/resources/updated', params: { uri: CONTROLS_URI } })
        }
        version = result.version
      } catch {
        await sleep(2000)
      }
    }
  })()
}

/* ---------- MCP over stdio: one JSON-RPC message per line ---------- */

function send(message) {
  process.stdout.write(JSON.stringify(message) + '\n')
}

async function handle(message) {
  const { id, method, params } = message
  const reply = (result) => id !== undefined && send({ jsonrpc: '2.0', id, result })
  const fail = (code, error) => id !== undefined && send({ jsonrpc: '2.0', id, error: { code, message: error } })
  switch (method) {
    case 'initialize':
      return reply({
        protocolVersion: (params && params.protocolVersion) || DEFAULT_PROTOCOL_VERSION,
        capabilities: { tools: {}, resources: { subscribe: true } },
        serverInfo: SERVER_INFO,
        instructions: INSTRUCTIONS,
      })
    case 'ping':
      return reply({})
    case 'tools/list':
      return reply({
        tools: TOOLS.map((tool) => ({
          name: tool.name,
          description: tool.description,
          inputSchema: { type: 'object', properties: tool.properties, ...(tool.required ? { required: tool.required } : {}) },
        })),
      })
    case 'resources/list':
      return reply({ resources: [CONTROLS_RESOURCE] })
    case 'resources/read':
      if (!params || params.uri !== CONTROLS_URI) {
        return fail(-32002, `Resource not found: ${ params && params.uri }`)
      }
      try {
        const controls = await readControls()
        return reply({ contents: [{ uri: CONTROLS_URI, mimeType: 'application/json', text: JSON.stringify(controls) }] })
      } catch (err) {
        return fail(-32603, err.message)
      }
    case 'resources/subscribe':
      if (!params || params.uri !== CONTROLS_URI) {
        return fail(-32002, `Resource not found: ${ params && params.uri }`)
      }
      watchControls()
      return reply({})
    case 'resources/unsubscribe':
      if (controlsWatch) {
        controlsWatch.active = false
        controlsWatch = undefined
      }
      return reply({})
    case 'tools/call':
      try {
        return reply(await callTool(params.name, params.arguments))
      } catch (err) {
        return reply({ content: [text(`${ params.name }() failed: ${ err && err.message || err }`)], isError: true })
      }
    default:
      if (method && method.startsWith('notifications/')) {
        return undefined // nothing to answer
      }
      return fail(-32601, `Method not found: ${ method }`)
  }
}

// The simulator started by startSimulator() lives as long as the session
function stopChild() {
  if (simulatorProcess) {
    simulatorProcess.kill()
    simulatorProcess = undefined
  }
}
process.on('exit', stopChild)
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => process.exit(0))
}

readline.createInterface({ input: process.stdin }).on('close', stopChild).on('line', (line) => {
  if (!line.trim()) {
    return
  }
  let message
  try {
    message = JSON.parse(line)
  } catch {
    send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } })
    return
  }
  handle(message).catch((err) => process.stderr.write(`[ethos mcp] ${ err && err.stack || err }\n`))
})
