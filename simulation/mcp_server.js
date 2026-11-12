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
const zlib = require('zlib')
const { spawn } = require('child_process')

const SERVER_INFO = { name: 'ethos', version: '0.3.0' }
const DEFAULT_PROTOCOL_VERSION = '2025-06-18'
const RADIO_PORT = Number(process.env.ETHOS_RADIO_PORT || 8766)
const SIMULATOR_PORT = Number(process.env.ETHOS_SIMULATOR_PORT || 8765)
const STATUS_TIMEOUT_MS = 1000
const COMMAND_TIMEOUT_MS = 120000
const BOOT_TIMEOUT_MS = 60000
const DOWNLOAD_TIMEOUT_MS = 300000

const RELEASES_REPOSITORY = 'FrSkyRC/ETHOS-Feedback-Community'
// The first releases whose simulators have the remote control exports (press, touch...)
const MIN_VERSION = [26, 2]
const SIMULATOR_ASSET = /^(.+)-WebSimulator\.zip$/

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
  'listFiles(), readFile(), writeFile() and deleteFile() work on the radio storage (e.g. /scripts):',
  'call reload() after changing a Lua script. getChannelsOutputs() returns the mixer outputs.',
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
    description: 'Press a key of the radio, as the user would: SYS, MDL, DISP, PAGE, RTN, ENTER, and UP, DOWN, LEFT, RIGHT on the radios which have them; a trim: T1-, T1+ ... T6+ (the number written on the radio); or a function switch: FS1, FS2... A name the radio doesn\'t have fails. duration is the press duration in ms (default 20, a short press); the radio detects a long press from 540 ms, so use e.g. 1000 for a long press. Use screenshot() afterwards to check the result.',
    properties: { name: string('Key, trim or function switch name'), duration: { type: 'integer', default: 20 } },
    required: ['name'],
    command: 'press',
    args: ({ name, duration }) => ({ name: String(name).toUpperCase(), ms: duration ?? 20 }),
  },
  {
    name: 'touch',
    description: 'Touch the screen at (x, y), in screen pixels - the same coordinates as in screenshot() images, (0, 0) being the top-left corner. duration is the touch duration in ms (default 20, a tap); from 1000 ms it is a long press (context menus). Use screenshot() afterwards to check the result.',
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

/* ---------- The simulators of the Ethos releases ---------- */

function cacheDir() {
  if (process.env.ETHOS_CACHE_DIR) {
    return process.env.ETHOS_CACHE_DIR
  }
  const home = os.homedir()
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'ethos-tools')
  }
  if (process.platform === 'darwin') {
    return path.join(home, 'Library', 'Caches', 'ethos-tools')
  }
  return path.join(process.env.XDG_CACHE_HOME || path.join(home, '.cache'), 'ethos-tools')
}

async function github(route) {
  const headers = { Accept: 'application/vnd.github+json', 'User-Agent': 'ethos-tools' }
  if (process.env.GITHUB_TOKEN) {
    headers.Authorization = `Bearer ${ process.env.GITHUB_TOKEN }`
  }
  const res = await fetch(`https://api.github.com/repos/${ RELEASES_REPOSITORY }/${ route }`, { headers, signal: AbortSignal.timeout(30000) })
  if (!res.ok) {
    const limited = res.status === 403 || res.status === 429 ? ' (GitHub API rate limit? Set GITHUB_TOKEN)' : ''
    throw Object.assign(new Error(`GitHub API ${ route }: ${ res.status } ${ res.statusText }${ limited }`), { status: res.status })
  }
  return res.json()
}

function isRecent(tag) {
  const match = /^(\d+)\.(\d+)\./.exec(tag)
  if (!match) {
    return false // e.g. suite2.0.2, the Ethos Suite releases
  }
  const [major, minor] = [Number(match[1]), Number(match[2])]
  return major > MIN_VERSION[0] || (major === MIN_VERSION[0] && minor >= MIN_VERSION[1])
}

const simulatorAssets = (release) => release.assets.filter((asset) => SIMULATOR_ASSET.test(asset.name))
const radioName = (asset) => SIMULATOR_ASSET.exec(asset.name)[1] // e.g. X20S-FCC

let releasesCache
async function recentReleases() {
  if (!releasesCache || Date.now() - releasesCache.time > 5 * 60000) {
    const releases = await github('releases?per_page=30')
    releasesCache = {
      time: Date.now(),
      releases: releases.filter((release) => !release.draft && isRecent(release.tag_name) && simulatorAssets(release).length),
    }
  }
  return releasesCache.releases
}

async function findRelease(tag) {
  // By default the newest prerelease (release candidate, beta), else the newest release
  if (!tag || tag === 'latest') {
    const releases = await recentReleases()
    const newest = releases.find((release) => release.prerelease) || releases[0]
    if (!newest) {
      throw new Error(`No release ${ MIN_VERSION.join('.') } or newer with simulators in ${ RELEASES_REPOSITORY }`)
    }
    return newest
  }
  if (!isRecent(tag)) {
    throw new Error(`Ethos ${ tag } is too old: the simulator tools need ${ MIN_VERSION.join('.') } or newer (remote control). listSimulators() lists the releases.`)
  }
  const known = releasesCache && releasesCache.releases.find((release) => release.tag_name === tag)
  if (known) {
    return known
  }
  try {
    return await github(`releases/tags/${ encodeURIComponent(tag) }`)
  } catch (err) {
    throw err.status === 404 ? new Error(`No release ${ tag } in ${ RELEASES_REPOSITORY }. listSimulators() lists them.`) : err
  }
}

// e.g. 'x20s fcc' or X20S_FCC: X20S-FCC, and LBT (the EU protocol in the menus): EU
const normalizeRadio = (radio) => String(radio).trim().toUpperCase().replace(/[\s_]+/g, '-').replace(/-LBT$/, '-EU')

function findAsset(release, radio) {
  const assets = simulatorAssets(release)
  const wanted = normalizeRadio(radio)
  const asset = assets.find((a) => radioName(a) === wanted) || assets.find((a) => radioName(a) === `${ wanted }-FCC`)
  if (asset) {
    return asset
  }
  const board = wanted.split('-')[0]
  const sameBoard = assets.map(radioName).filter((name) => name.split('-')[0] === board)
  const available = sameBoard.length ? sameBoard : assets.map(radioName)
  throw new Error(`No ${ wanted } simulator in ${ release.tag_name }. Available: ${ available.join(', ') }`)
}

// Extracts a zip file (stored or deflated entries, no zip64)
function unzip(buffer, dir) {
  let eocd = buffer.length - 22
  while (eocd >= 0 && buffer.readUInt32LE(eocd) !== 0x06054b50) {
    eocd--
  }
  if (eocd < 0) {
    throw new Error('Not a zip file')
  }
  const root = path.resolve(dir)
  let offset = buffer.readUInt32LE(eocd + 16)
  for (let i = buffer.readUInt16LE(eocd + 10); i > 0; i--) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) {
      throw new Error('Corrupted zip file')
    }
    const method = buffer.readUInt16LE(offset + 10)
    const size = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const local = buffer.readUInt32LE(offset + 42)
    const name = buffer.toString('utf8', offset + 46, offset + 46 + nameLength)
    offset += 46 + nameLength + buffer.readUInt16LE(offset + 30) + buffer.readUInt16LE(offset + 32)
    const dest = path.resolve(root, name)
    if (!dest.startsWith(root + path.sep)) {
      throw new Error(`Unexpected path in the zip file: ${ name }`)
    }
    if (name.endsWith('/')) {
      fs.mkdirSync(dest, { recursive: true })
      continue
    }
    const start = local + 30 + buffer.readUInt16LE(local + 26) + buffer.readUInt16LE(local + 28)
    const data = buffer.subarray(start, start + size)
    if (method !== 0 && method !== 8) {
      throw new Error(`Unsupported compression method ${ method } for ${ name }`)
    }
    fs.mkdirSync(path.dirname(dest), { recursive: true })
    fs.writeFileSync(dest, method === 8 ? zlib.inflateRawSync(data) : data)
  }
}

// The simulator file: a .js next to its .wasm
function findSimulatorJs(dir) {
  if (!fs.existsSync(dir)) {
    return undefined
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name)
    const found = entry.isDirectory()
      ? findSimulatorJs(file)
      : (entry.name.endsWith('.js') && fs.existsSync(file.replace(/\.js$/, '.wasm')) ? file : undefined)
    if (found) {
      return found
    }
  }
  return undefined
}

const simulatorDir = (tag, radio) => path.join(cacheDir(), 'simulators', tag, radio)

// Downloads the simulator, unless the cached one is the same asset (the
// assets of a release candidate can be uploaded again)
async function fetchSimulator(release, asset) {
  const dir = simulatorDir(release.tag_name, radioName(asset))
  const stamp = JSON.stringify({ id: asset.id, updated_at: asset.updated_at })
  const stampFile = path.join(dir, 'asset.json')
  if (fs.existsSync(stampFile) && fs.readFileSync(stampFile, 'utf8') === stamp) {
    const js = findSimulatorJs(dir)
    if (js) {
      return { js, downloaded: false }
    }
  }
  const res = await fetch(asset.browser_download_url, { headers: { 'User-Agent': 'ethos-tools' }, signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS) })
  if (!res.ok) {
    throw new Error(`Download of ${ asset.browser_download_url } failed: ${ res.status } ${ res.statusText }`)
  }
  const buffer = Buffer.from(await res.arrayBuffer())
  const tmp = `${ dir }.tmp-${ process.pid }`
  fs.rmSync(tmp, { recursive: true, force: true })
  try {
    fs.mkdirSync(tmp, { recursive: true })
    unzip(buffer, tmp)
    fs.writeFileSync(path.join(tmp, 'asset.json'), stamp)
    fs.rmSync(dir, { recursive: true, force: true })
    fs.renameSync(tmp, dir)
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
  const js = findSimulatorJs(dir)
  if (!js) {
    throw new Error(`${ asset.name } has no simulator (.js and .wasm files)`)
  }
  return { js, downloaded: true }
}

// Without network, a simulator already downloaded for this release
function cachedSimulator(tag, radio) {
  const wanted = normalizeRadio(radio)
  for (const name of [wanted, `${ wanted }-FCC`]) {
    const js = findSimulatorJs(simulatorDir(tag, name))
    if (js) {
      return { tag, radio: name, js }
    }
  }
  return undefined
}

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
  let build
  try {
    const found = await findRelease(release)
    const asset = findAsset(found, radio)
    const { js, downloaded } = await fetchSimulator(found, asset)
    build = { tag: found.tag_name, prerelease: found.prerelease, radio: radioName(asset), js, downloaded }
  } catch (err) {
    const cached = release && release !== 'latest' && cachedSimulator(release, radio)
    if (!cached) {
      throw err
    }
    build = { ...cached, downloaded: false, warning: `Cached simulator used: ${ err.message }` }
  }

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
        capabilities: { tools: {} },
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
