/*
 * Copyright (C) FrSky RC, Inc - All Rights Reserved
 *
 * Unauthorized copying of this file, via any medium is strictly prohibited
 * Proprietary and confidential
 *
 */

'use strict'

// Runs the Ethos WASM simulator from the command line.
//
//   node run_wasm.js <simulator.js> [--root-directory DIR] [--macro PATH] [--macro-timeout MS]
//       Starts the simulator, optionally runs a Lua macro, then exits.
//
//   node run_wasm.js <simulator.js> --serve [--port N] [--shots-dir DIR] [...]
//       Keeps the simulator running behind a small HTTP server on 127.0.0.1.
//
//   node run_wasm.js <command> [args...] [--port N]
//       Client: sends one command (screenshot, press, touch...) to a serving simulator.
//
// The commands have the names of the firmware's remote control functions, as
// the simulator Lua API, the USB protocol of the radio (radio_remote.py) and
// the ethos-simulator MCP server.

const path = require('path')
const fs = require('fs')
const http = require('http')
const zlib = require('zlib')

const DEFAULT_PORT = 8765
const REMOTE_TIMEOUT_MS = 30000
const MODEL_JSON_TIMEOUT_MS = 2000
// Largest file readFile returns inline (text or base64)
const MAX_READ_FILE_SIZE = 512 * 1024
// From this duration a touch is a long press (TOUCH_LONG_PRESS_DELAY in the firmware)
const LONG_TOUCH_MS = 1000

// Same values as RemoteControl::Status
const STATUS = {
  0: 'success', [-1]: 'unsupported', [-2]: 'refused', [-3]: 'invalid argument',
  [-4]: 'I/O error', [-5]: 'busy (a macro is running)', [-6]: 'timeout',
  [-7]: 'woke up (the screen was off: the input only turned it back on, send it again)',
}

const DISPLAYS = ['MAIN', 'TOP', 'GLASSES']

// Client commands and the names of their positional arguments
const CLIENT_COMMANDS = {
  status: [],
  screenshot: ['out', 'display'],
  press: ['name', 'ms'],
  touch: ['x', 'y', 'ms'],
  swipe: ['x1', 'y1', 'x2', 'y2'],
  turnRotaryEncoder: ['steps'],
  enterText: ['text'],
  loadModel: ['path'],
  resetBacklightTimer: [],
  setSwitch: ['index', 'value'],
  setAnalog: ['index', 'value'],
  startMacro: ['path', 'timeout'],
  getInformation: [],
  getLog: ['since'],
  listFiles: ['path'],
  readFile: ['path', 'out'],
  writeFile: ['path', 'src'],
  createDirectory: ['path'],
  deleteFile: ['path'],
  reload: ['what'],
  getChannelsOutputs: [],
  getTrims: [],
  getControls: ['since', 'timeout'],
  resetFunctionSwitches: [],
  getModelJson: ['out'],
  applyModelJson: ['src'],
  wait: ['ms'],
  quit: [],
}

const FS_ERRORS = {
  ENOENT: 'No such file or folder (a written file\'s folder must exist)',
  ENOTEMPTY: 'The folder is not empty',
  EISDIR: 'This is a folder',
  EEXIST: 'It already exists',
  ENOTDIR: 'This is not a folder',
}

// Arguments which are local files, sent as absolute paths (the server may run elsewhere)
const LOCAL_FILE_ARGUMENTS = ['out', 'src']

// Numbered lines of the system log (Lua print() and errors): getLog returns
// next, to pass back as since to only get the lines added meanwhile
class LogBuffer {
  constructor(capacity = 1000) {
    this.capacity = capacity
    this.lines = []
    this.first = 0 // number of lines[0]
    this.partial = ''
  }

  push(text) {
    // eslint-disable-next-line no-control-regex
    const parts = (this.partial + text.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')).split('\n')
    this.partial = parts.pop()
    this.lines.push(...parts.map((line) => line.replace(/\r$/, '')))
    if (this.lines.length > this.capacity) {
      const dropped = this.lines.length - this.capacity
      this.lines.splice(0, dropped)
      this.first += dropped
    }
  }

  get next() {
    return this.first + this.lines.length
  }

  // Without since (or with a number beyond next), the last 50 lines; at most 200 lines
  get(since) {
    const next = this.next
    let start = since === undefined || since > next ? next - 50 : since
    let truncated = since !== undefined && start < this.first
    start = Math.max(start, this.first)
    if (next - start > 200) {
      start = next - 200
      truncated = since !== undefined
    }
    return { lines: this.lines.slice(start - this.first), next, truncated }
  }
}

// Content of a file: text when it is UTF-8, else base64
function fileContent(data) {
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(data) }
  } catch {
    return { base64: Buffer.from(data).toString('base64') }
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function printUsage() {
  process.stderr.write([
    'Usage:',
    '  node run_wasm.js <simulator.js> [--root-directory DIR] [--macro path[.lua]] [--macro-timeout MS]',
    '  node run_wasm.js <simulator.js> --serve [--port N] [--shots-dir DIR] [--root-directory DIR] [--macro ...]',
    '  node run_wasm.js --radio RADIO [--release TAG] ... (instead of <simulator.js>)',
    '      The simulator of RADIO (X20S, X20S-FLEX, X18-EU...) of a release on GitHub (TAG, the latest',
    '      by default), downloaded once in a cache folder (ETHOS_CACHE_DIR, see simulators.js)',
    '  node run_wasm.js --list-simulators [--radio PATTERN] [--release TAG]',
    '      The release and its radios (PATTERN: X20*, X20S-*..., FCC by default), as JSON',
    '  node run_wasm.js <command> [args] [--port N]',
    '',
    'Commands (to a --serve instance):',
    '  status | screenshot [out.png] [MAIN|TOP|GLASSES]',
    '  press NAME [ms] | touch X Y [ms] | swipe X1 Y1 X2 Y2 | turnRotaryEncoder STEPS',
    '  enterText TEXT | loadModel PATH | resetBacklightTimer',
    '  setSwitch I V | setAnalog I V | startMacro PATH [timeoutMs]',
    '  getInformation | getLog [since] | getChannelsOutputs | getTrims | getControls [since] [timeoutMs] | resetFunctionSwitches',
    '  reload [SCRIPTS|ALL]',
    '  listFiles [PATH] | readFile PATH [out] | writeFile PATH SRC | createDirectory PATH | deleteFile PATH',
    '  getModelJson [out.json] | applyModelJson SRC.json',
    '  wait MS | quit',
    '',
    'PATH: radio path, from its root (/scripts/main.lua...)',
    '',
    'NAME: ENTER RTN SYS MDL DISP PAGE UP DOWN LEFT RIGHT (those the radio has), trims T1- T1+ ... T6+,',
    '      function switches FS1 FS2 ...',
    'ms:   press duration, 20 by default; from 540 ms a long key press, from 1000 ms a long touch',
    '',
  ].join('\n'))
}

function parseArgs(argv) {
  const opts = {
    macro: null, macroTimeout: null,
    rootDirectory: null,
    serve: false, port: DEFAULT_PORT, shotsDir: path.resolve('screenshots'),
  }
  const positional = []
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--help' || a === '-h') { printUsage(); process.exit(0) }
    else if (a === '--macro' || a === '--exec') opts.macro = argv[++i]
    else if (a === '--macro-timeout') opts.macroTimeout = parseInt(argv[++i], 10)
    else if (a === '--root-directory') opts.rootDirectory = argv[++i]
    else if (a === '--serve') opts.serve = true
    else if (a === '--port') opts.port = parseInt(argv[++i], 10)
    else if (a === '--shots-dir') opts.shotsDir = path.resolve(argv[++i])
    else if (a === '--radio') opts.radio = argv[++i]
    else if (a === '--release') opts.release = argv[++i]
    else if (a === '--list-simulators') opts.listSimulators = true
    else positional.push(a)
  }
  if (positional.length < 1 && !opts.radio && !opts.listSimulators) {
    printUsage()
    process.exit(2)
  }
  opts.positional = positional
  return opts
}

function waitOnce(setHandler, timeoutMs, timeoutMessage) {
  return new Promise((resolve, reject) => {
    let timer = null
    if (timeoutMs != null) {
      timer = setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs)
    }
    setHandler((...args) => {
      if (timer) {
        clearTimeout(timer)
      }
      resolve(args)
    })
  })
}

async function startMacro(Module, path, timeoutMs) {
  const endPromise = waitOnce((h) => { Module.onMacroEnd = h }, timeoutMs, 'Timed out waiting for the macro to finish')
  Module.ccall('startMacro', 'void', ['string', 'boolean'], [path, false])
  await endPromise
}

/* ---------- PNG encoding ---------- */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c
  }
  return t
})()

function crc32(buf) {
  let crc = 0xffffffff
  for (let i = 0; i < buf.length; i++) crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buf[i]) & 0xff]
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([len, body, crc])
}

function encodePng(width, height, rgb565) {
  const scanlines = Buffer.alloc(height * (1 + width * 3))
  let o = 0
  // The frame buffer is stored bottom row first
  for (let y = height - 1; y >= 0; y--) {
    scanlines[o++] = 0
    for (let x = 0; x < width; x++) {
      const p = rgb565[y * width + x]
      scanlines[o++] = ((p >> 11) & 0x1f) * 255 / 31 | 0
      scanlines[o++] = ((p >> 5) & 0x3f) * 255 / 63 | 0
      scanlines[o++] = (p & 0x1f) * 255 / 31 | 0
    }
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8 // bit depth
  ihdr[9] = 2 // RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', zlib.deflateSync(scanlines)),
    pngChunk('IEND', Buffer.alloc(0)),
  ])
}

/* ---------- Simulator ---------- */

async function startSimulator(opts, moduleArg, beforeStart) {
  // eslint-disable-next-line import/no-dynamic-require, global-require -- path comes from CLI args, can't be a literal
  const factory = require(opts.jsPath)
  const Module = await factory(moduleArg)

  const mountPoint = Module.ccall('getCurrentModel', 'string')
  Module.FS.mkdir('/persist')
  Module.FS.mkdir(mountPoint)

  if (opts.rootDirectory) {
    if (!fs.existsSync(opts.rootDirectory)) {
      console.error(`Directory ${ opts.rootDirectory } doesn't exist`)
      process.exit(-1)
    }
    // console.log(`Mount ${ opts.rootDirectory } as ${ mountPoint }`)
    Module.FS.mount(Module.NODEFS, { root: opts.rootDirectory }, mountPoint)
  } else {
    console.log('No workspace, will use MEMFS')
    Module.FS.mount(Module.MEMFS, {}, mountPoint)
  }

  if (beforeStart) beforeStart(Module, mountPoint)
  Module.ccall('start', null, [], [])

  await sleep(500)

  return Module
}

// Interactive state around a running simulator (--serve)
class Session {
  constructor() {
    this.Module = null
    this.frames = {} // by display: { width, height, pixels: Uint16Array }
    this.frameSeq = 0
    this.logBuffer = new LogBuffer()
    this.firmware = {} // from the boot trace: board, version, gitHash
    this.rfProtocol = undefined // from the build name (X20S_FCC)
    this.modelJson = undefined
    this.modelJsonVersion = 0 // incremented on each JSON sent by the firmware
    this.controlsVersion = 0 // incremented when the switches, trims or function switches change
    this.controlsWaiters = [] // getControls() waiting for a change
    this.switchesConfig = 0
    this.switchesPosition = []
    this.trimsValue = []
    this.macroRunning = false
    this.remoteId = 1
    this.remotePending = new Map()
  }

  pushLog(text) {
    this.logBuffer.push(text + '\n')
    // "X20S ETHOS 26.2.0 @ abcdef started!", traced by start()
    const started = / ?(\S+) \S+ (\S+) @ (\S+) started!/.exec(text)
    if (started) {
      this.firmware = { board: started[1], version: started[2], gitHash: started[3] }
    }
  }

  // Callbacks the simulator calls back into, to be set before it starts
  moduleCallbacks() {
    // Copy out: the heap may be shared with the simulator threads, and the
    // glasses buffer is freed after the call
    const frame = (display) => (width, height, ptr) => {
      const pixels = new Uint16Array(this.Module.HEAPU16.buffer, ptr, width * height).slice()
      this.frames[display] = { width, height, pixels }
      this.frameSeq++
    }
    return {
      updateCanvas: frame('MAIN'),
      updateCanvasTop: frame('TOP'),
      updateCanvasGlasses: frame('GLASSES'),
      setSwitchesConfig: (config) => {
        this.switchesConfig = config
        this.controlsChanged()
      },
      setSwitchesPosition: (ptr, count) => {
        this.switchesPosition = Array.from(new Int8Array(this.Module.HEAP8.buffer, ptr, count))
        this.controlsChanged()
      },
      setTrimsValue: (ptr, count) => {
        this.trimsValue = Array.from(new Int16Array(this.Module.HEAP16.buffer, ptr, count), (v) => Math.round(v * 100 / 256))
        this.controlsChanged()
      },
      setFunctionSwitchesState: (ptr, count) => {
        this.functionSwitchesState = Array.from(new Int8Array(this.Module.HEAP8.buffer, ptr, count), Boolean)
        this.controlsChanged()
      },
      setModelJson: (json) => {
        this.modelJson = json
        this.modelJsonVersion++
      },
      onRemoteResult: (id, status) => {
        const resolve = this.remotePending.get(id)
        if (resolve) {
          this.remotePending.delete(id)
          resolve(status)
        }
      },
    }
  }

  hasExport(name) {
    return typeof this.Module['_' + name] === 'function'
  }

  // Remote control export (see wasm_simulator.md): queued in the firmware,
  // the result comes with Module.onRemoteResult() once the UI handled it
  remote(name, types, args) {
    if (!this.hasExport(name)) {
      return Promise.reject(new Error(`This simulator build has no ${ name }() export, use a newer one`))
    }
    const id = this.remoteId++
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.remotePending.delete(id)
        resolve(-6)
      }, REMOTE_TIMEOUT_MS)
      this.remotePending.set(id, (status) => {
        clearTimeout(timer)
        resolve(status)
      })
      this.Module.ccall(name, null, ['number', ...types], [id, ...args])
    })
  }

  // Keys, trims and function switches by name. The builds older than press()
  // have pressKey() for the keys and trims, pressFunctionSwitch() for FSn
  press(name, ms) {
    if (this.hasExport('press')) {
      return this.remote('press', ['string', 'number'], [name, ms])
    }
    if (/^FS\d+$/.test(name)) {
      return this.remote('pressFunctionSwitch', ['string', 'number'], [name, ms])
    }
    return this.remote('pressKey', ['string', 'number'], [name, ms])
  }

  // The builds older than press() take longPress (boolean) instead of the duration
  touch(x, y, ms) {
    if (this.hasExport('press')) {
      return this.remote('touch', ['number', 'number', 'number'], [x, y, ms])
    }
    return this.remote('touch', ['number', 'number', 'boolean'], [x, y, ms >= LONG_TOUCH_MS])
  }

  /* Files, with radio paths: the root is the radio storage (--root-directory) */

  // "scripts/main.lua" is the same as "/scripts/main.lua" (Git Bash converts the arguments starting with /)
  fsPath(radioPath = '/') {
    return path.posix.join(this.mountPoint, '/', radioPath)
  }

  listFiles(radioPath) {
    const { FS } = this.Module
    const folder = this.fsPath(radioPath)
    const files = FS.readdir(folder).filter((name) => name !== '.' && name !== '..').map((name) => {
      const stats = FS.stat(path.posix.join(folder, name))
      return { name, isDirectory: FS.isDir(stats.mode), size: stats.size }
    })
    return { ok: true, path: radioPath, files }
  }

  readFile(radioPath, out) {
    const data = this.Module.FS.readFile(this.fsPath(radioPath))
    if (out) {
      fs.writeFileSync(out, data)
      return { ok: true, path: radioPath, size: data.length, file: out }
    }
    if (data.length > MAX_READ_FILE_SIZE) {
      return { ok: false, error: `${ radioPath } is too big (${ data.length } bytes, ${ MAX_READ_FILE_SIZE } at most, or give out)` }
    }
    return { ok: true, path: radioPath, size: data.length, ...fileContent(data) }
  }

  // Content from q.text, q.base64 or q.src (a local file)
  writeFile(radioPath, q) {
    let data
    if (typeof q.base64 === 'string') data = Buffer.from(q.base64, 'base64')
    else if (q.src) data = fs.readFileSync(q.src)
    else data = Buffer.from(String(q.text ?? ''), 'utf8')
    this.Module.FS.writeFile(this.fsPath(radioPath), data)
    return { ok: true, path: radioPath, size: data.length }
  }

  // Its parent must exist, as on the radio
  createDirectory(radioPath) {
    this.Module.FS.mkdir(this.fsPath(radioPath))
    return { ok: true, path: radioPath }
  }

  deleteFile(radioPath) {
    const { FS } = this.Module
    const fsPath = this.fsPath(radioPath)
    if (FS.isDir(FS.stat(fsPath).mode)) FS.rmdir(fsPath)
    else FS.unlink(fsPath)
    return { ok: true, path: radioPath }
  }

  /* Model JSON (simulator only) */

  async getModelJson() {
    if (!this.hasExport('setModelJsonNeeded')) {
      throw new Error('This simulator build has no model JSON, use a newer one')
    }
    const version = this.modelJsonVersion
    this.Module.ccall('setModelJsonNeeded', 'void', ['boolean'], [true])
    try {
      const deadline = Date.now() + MODEL_JSON_TIMEOUT_MS
      while (this.modelJsonVersion === version && Date.now() < deadline) {
        await sleep(20)
      }
    } finally {
      this.Module.ccall('setModelJsonNeeded', 'void', ['boolean'], [false])
    }
    if (this.modelJsonVersion === version) {
      return { ok: false, status: STATUS[-6] }
    }
    return { ok: true, model: JSON.parse(this.modelJson) }
  }

  applyModelJson(model) {
    const Module = this.Module
    const json = typeof model === 'string' ? model : JSON.stringify(model)
    let applied
    if (Module.stringToNewUTF8 && Module._free) {
      // A 'string' argument of ccall() is copied on the wasm stack (64 KB), which a model JSON overflows
      const pointer = Module.stringToNewUTF8(json)
      try {
        applied = Module.ccall('applyModelJson', 'boolean', ['number'], [pointer])
      } finally {
        Module._free(pointer)
      }
    } else {
      applied = Module.ccall('applyModelJson', 'boolean', ['string'], [json])
    }
    if (applied) {
      return { ok: true, status: STATUS[0] }
    }
    const error = this.hasExport('modelJsonError') ? Module.ccall('modelJsonError', 'string', [], []) : ''
    return { ok: false, status: STATUS[-3], error: error || 'The model JSON was refused' }
  }

  reload(what) {
    if (what === 'ALL') {
      this.Module.ccall('reload', 'void', ['boolean'], [false])
      return 0
    }
    if (!this.hasExport('reloadScripts')) {
      return -1
    }
    this.Module.ccall('reloadScripts', 'void', [], [])
    return 0
  }

  // Mixer outputs in %, -1024..1024 = -100..+100%
  getChannelsOutputs() {
    if (!this.hasExport('getChannelsOutputs')) {
      return { ok: false, status: STATUS[-1] }
    }
    const pointer = this.Module.ccall('getChannelsOutputs', 'number', [], []) >> 1
    const count = this.Module.HEAP16[pointer]
    const outputs = Array.from(this.Module.HEAP16.subarray(pointer + 1, pointer + 1 + count), (value) => Math.round(value * 1000 / 1024) / 10)
    return { ok: true, outputs }
  }

  // The switches, trims and function switches, sent by the firmware when they change (as the
  // EVENT_CONTROLS of a radio): switches -1 up, 0 middle, 1 down (the ones installed), trims in %
  controls() {
    const switches = {}
    ;(this.switchesPosition || []).forEach((position, i) => {
      if (this.switchesConfig === undefined || ((this.switchesConfig >>> (2 * i)) & 3)) {
        switches[`S${ String.fromCharCode(65 + i) }`] = Math.sign(position)
      }
    })
    const functionSwitches = Object.fromEntries((this.functionSwitchesState || []).map((on, i) => [`FS${ i + 1 }`, on]))
    return { switches, trims: this.trimsValue || [], functionSwitches }
  }

  controlsChanged() {
    this.controlsVersion++
    for (const resolve of this.controlsWaiters.splice(0)) resolve()
  }

  // The controls; with since (a version), once they changed (timeoutMs at most)
  async getControls(since, timeoutMs) {
    if (since !== undefined && since === this.controlsVersion) {
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, timeoutMs)
        this.controlsWaiters.push(() => { clearTimeout(timer); resolve() })
      })
    }
    return { ok: true, ...this.controls(), version: this.controlsVersion, changed: since === undefined || since !== this.controlsVersion }
  }

  getInformation() {
    const displays = Object.fromEntries(Object.entries(this.frames).map(([display, { width, height }]) => [display, { width, height }]))
    return {
      ok: true, target: 'simulator', ...this.firmware, rfProtocol: this.rfProtocol,
      displays, drives: [{ name: 'Local', path: '/' }], remoteControl: this.hasExport('touch'),
      controls: this.controls(),
    }
  }

  // Waits until a frame newer than `afterSeq` arrives and no new one comes for `quietMs`
  async waitSettled(afterSeq, quietMs = 150, timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs
    let lastSeq = this.frameSeq
    let lastChange = Date.now()
    while (Date.now() < deadline) {
      await sleep(25)
      if (this.frameSeq !== lastSeq) {
        lastSeq = this.frameSeq
        lastChange = Date.now()
      } else if (this.frameSeq > afterSeq && Date.now() - lastChange >= quietMs) {
        return
      }
    }
  }

  screenshot(out, display) {
    const frame = this.frames[display]
    if (!frame) throw new Error(`No ${ display } frame received (this radio may have no such display)`)
    const { width, height, pixels } = frame
    fs.mkdirSync(path.dirname(out), { recursive: true })
    fs.writeFileSync(out, encodePng(width, height, pixels))
    return { file: out, display, width, height }
  }

  // A macro file (radio path), or its code, written for the time it runs in
  // /macros (not /scripts: everything there is loaded as a script). Returns
  // what it printed
  async startMacro(macroPath, timeoutMs, code) {
    let written
    if (code !== undefined) {
      this.Module.FS.mkdirTree(this.fsPath('/macros'))
      macroPath = written = `/macros/mcp-${ Date.now() }.lua`
      this.Module.FS.writeFile(this.fsPath(written), String(code))
    }
    const before = this.logBuffer.next
    this.macroRunning = true
    try {
      await startMacro(this.Module, macroPath, timeoutMs)
    } finally {
      this.macroRunning = false
      if (written) {
        try { this.Module.FS.unlink(this.fsPath(written)) } catch { /* already gone */ }
      }
    }
    const output = this.logBuffer.get(before).lines
    // As the ethos-simulator MCP server of the online simulator
    const failed = output.some((line) => /error|attempt to/i.test(line))
    return { ok: !failed, output }
  }

  async handle(cmd, q, opts) {
    const num = (k, d) => (q[k] === undefined ? d : Number(q[k]))
    const call = (name, index, value) => this.Module.ccall(name, 'void', ['number', 'number'], [index, value])
    const before = this.frameSeq
    let status = 0
    switch (cmd) {
      case 'status':
        return {
          ok: true, mountPoint: this.mountPoint, macroRunning: this.macroRunning,
          remoteControl: this.hasExport('touch'),
          displays: Object.fromEntries(Object.entries(this.frames).map(([display, { width, height }]) => [display, { width, height }])),
          switchesConfig: this.switchesConfig, switchesPosition: this.switchesPosition, trims: this.trimsValue,
        }
      case 'screenshot': {
        const display = (q.display || 'MAIN').toUpperCase()
        if (!DISPLAYS.includes(display)) {
          return { ok: false, error: `Unknown display ${ q.display }, expected one of: ${ DISPLAYS.join(', ') }` }
        }
        await this.waitSettled(-1, num('quiet', 150), num('timeout', 3000))
        const out = path.resolve(q.out || path.join(opts.shotsDir, `shot-${ Date.now() }.png`))
        return { ok: true, ...this.screenshot(out, display) }
      }
      case 'press': status = await this.press(q.name, num('ms', 20)); break
      case 'touch': status = await this.touch(num('x'), num('y'), num('ms', 20)); break
      case 'swipe': status = await this.remote('swipe', ['number', 'number', 'number', 'number'], [num('x1'), num('y1'), num('x2'), num('y2')]); break
      case 'turnRotaryEncoder': status = await this.remote('turnRotaryEncoder', ['number'], [num('steps', 1)]); break
      case 'enterText': status = await this.remote('enterText', ['string'], [q.text || '']); break
      case 'loadModel': status = await this.remote('loadModel', ['string'], [q.path]); break
      case 'resetBacklightTimer': status = await this.remote('resetBacklightTimer', [], []); break
      // Simulator only (no such command on a radio), as simulator.setSwitch() / setAnalog() in Lua
      case 'setSwitch': call('setSwitchPosition', num('index'), num('value')); break
      case 'setAnalog': call('setAnalogPosition', num('index'), num('value')); break
      case 'startMacro': return this.startMacro(q.path, num('timeout', 60000), q.code)
      case 'getInformation': return this.getInformation()
      case 'getLog': return { ok: true, ...this.logBuffer.get(q.since === undefined ? undefined : Number(q.since)) }
      case 'listFiles': return this.listFiles(q.path || '/')
      case 'readFile': return this.readFile(q.path, q.out)
      case 'writeFile': return this.writeFile(q.path, q)
      case 'createDirectory': return this.createDirectory(q.path)
      case 'deleteFile': return this.deleteFile(q.path)
      case 'reload': status = this.reload((q.what || 'SCRIPTS').toUpperCase()); break
      case 'getChannelsOutputs': return this.getChannelsOutputs()
      case 'getTrims': return { ok: true, trims: this.controls().trims }
      case 'getControls': return this.getControls(q.since === undefined || q.since === '' ? undefined : Number(q.since), num('timeout', 30000))
      case 'resetFunctionSwitches': status = await this.remote('resetFunctionSwitches', [], []); break
      case 'getModelJson': {
        const result = await this.getModelJson()
        if (result.ok && q.out) {
          fs.writeFileSync(q.out, JSON.stringify(result.model, null, 2))
          return { ok: true, file: q.out }
        }
        return result
      }
      case 'applyModelJson': {
        const result = this.applyModelJson(q.model !== undefined ? q.model : fs.readFileSync(q.src, 'utf8'))
        await this.waitSettled(before, 150, num('settle', 1500))
        return result
      }
      case 'wait': await sleep(num('ms', 500)); return { ok: true }
      case 'quit':
        setTimeout(() => process.exit(0), 50)
        return { ok: true }
      default:
        return { ok: false, error: `Unknown command ${ cmd }` }
    }
    // Let the UI repaint before returning, so a following screenshot shows the result
    await this.waitSettled(before, 150, num('settle', 1500))
    return { ok: status === 0, status: STATUS[status] || status }
  }
}

async function run(opts) {
  const session = opts.serve ? new Session() : null
  const moduleArg = {
    print: (text) => { process.stdout.write(text + '\n'); session && session.pushLog(text) },
    printErr: (text) => { process.stderr.write(text + '\n'); session && session.pushLog(text) },
    ...(session && session.moduleCallbacks()),
  }

  const Module = await startSimulator(opts, moduleArg, session && ((M, mountPoint) => {
    session.Module = M
    session.mountPoint = mountPoint
    session.rfProtocol = path.basename(opts.jsPath, '.js').split('_')[1]
  }))

  if (!session) {
    if (opts.macro) {
      await startMacro(Module, opts.macro, opts.macroTimeout)
    }
    process.exit(0)
  }

  if (opts.macro) {
    await session.startMacro(opts.macro, opts.macroTimeout)
  }

  let queue = Promise.resolve()
  http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost')
    const cmd = url.pathname.slice(1)
    const q = Object.fromEntries(url.searchParams)
    // POST: the arguments too big for the URL (file content, macro code, model JSON), as a JSON object
    if (req.method === 'POST') {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      try {
        Object.assign(q, JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'))
      } catch (err) {
        res.writeHead(400, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ ok: false, error: `Invalid JSON body: ${ err.message }` }))
        return
      }
    }
    // Commands run one at a time, in arrival order. getControls only reads the state the
    // firmware sent: it can wait for a change without blocking the other commands
    const run = (cmd === 'getControls' ? Promise.resolve() : queue).then(() => session.handle(cmd, q, opts))
      // The errors of the Emscripten file system have an errno, a code (ENOENT...) but not always a message
      .catch((err) => ({ ok: false, error: FS_ERRORS[err && err.code] || String(err && (err.code || err.message || (err.errno !== undefined && `errno ${ err.errno }`)) || err) }))
      .then((result) => {
        res.writeHead(result.ok ? 200 : 400, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(result))
      })
    if (cmd !== 'getControls') {
      queue = run // never rejected: a command which fails doesn't stop the next ones
    }
  }).listen(opts.port, '127.0.0.1', () => {
    console.log(`[run_wasm] serving on http://127.0.0.1:${ opts.port }`)
  })
}

async function client(cmd, args, port) {
  // Git Bash (MSYS) turns an argument like /scripts/main.lua into C:/Program Files/Git/scripts/main.lua
  const pathIndex = CLIENT_COMMANDS[cmd].indexOf('path')
  if (process.env.MSYSTEM && pathIndex >= 0 && /^[A-Za-z]:[\\/]/.test(args[pathIndex] || '')) {
    process.stderr.write(`Git Bash converted the radio path to ${ args[pathIndex] }: write it without the leading / (scripts/main.lua), or set MSYS_NO_PATHCONV=1\n`)
    process.exit(2)
  }
  const params = new URLSearchParams()
  CLIENT_COMMANDS[cmd].forEach((name, i) => {
    if (args[i] !== undefined) params.set(name, LOCAL_FILE_ARGUMENTS.includes(name) ? path.resolve(args[i]) : args[i])
  })
  const res = await fetch(`http://127.0.0.1:${ port }/${ cmd }?${ params }`)
  const body = await res.json()
  process.stdout.write(JSON.stringify(body) + '\n')
  process.exit(body.ok ? 0 : 1)
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  const [first, ...rest] = opts.positional
  if (opts.listSimulators) {
    const simulators = require('./simulators')
    try {
      const release = await simulators.findRelease(opts.release)
      const radios = opts.radio ? simulators.matchRadios(release, opts.radio) : simulators.simulatorAssets(release).map(simulators.radioName)
      process.stdout.write(JSON.stringify({ ok: true, release: release.tag_name, prerelease: release.prerelease, radios }) + '\n')
    } catch (err) {
      process.stdout.write(JSON.stringify({ ok: false, error: err.message }) + '\n')
      process.exit(1)
    }
  } else if (Object.prototype.hasOwnProperty.call(CLIENT_COMMANDS, first)) {
    await client(first, rest, opts.port)
  } else {
    if (opts.radio) {
      // The simulator of a release, downloaded unless it is in the cache
      const onDownload = (release, asset) => console.log(`[run_wasm] downloading ${ asset.name } of ${ release.tag_name } (${ Math.round(asset.size / 1048576) } MB)...`)
      const build = await require('./simulators').resolveSimulator(opts.radio, opts.release, onDownload).catch((err) => {
        process.stderr.write(`[run_wasm] ${ err.message }\n`)
        process.exit(1)
      })
      console.log(`[run_wasm] ${ build.radio } ${ build.tag }${ build.downloaded ? ' downloaded' : '' }: ${ build.js }${ build.warning ? ` (${ build.warning })` : '' }`)
      opts.jsPath = build.js
    } else {
      opts.jsPath = path.resolve(first)
    }
    await run(opts)
  }
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + '\n')
  process.exit(1)
})
