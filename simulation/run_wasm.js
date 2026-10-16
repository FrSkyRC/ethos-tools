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
//       Client: sends one command (screenshot, tap, press...) to a serving simulator.

const path = require('path')
const fs = require('fs')
const http = require('http')
const zlib = require('zlib')

const DEFAULT_PORT = 8765

// Radio buttons -> browser keys, as sent by the VS Code extension's controls panel
const BUTTONS = {
  SYS: 'ArrowLeft',
  MDL: 'ArrowUp',
  DISP: 'ArrowRight',
  RTN: 'ArrowDown',
  PAGE: 'PageUp',
  ENTER: 'Enter',
}

const KEY_CODES = {
  Enter: 13, Escape: 27, Tab: 9, Backspace: 8, PageUp: 33, PageDown: 34,
  End: 35, Home: 36, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
}

// Client commands and the names of their positional arguments
const CLIENT_COMMANDS = {
  status: [],
  screenshot: ['out'],
  tap: ['x', 'y'],
  longpress: ['x', 'y'],
  swipe: ['x1', 'y1', 'x2', 'y2', 'ms'],
  wheel: ['steps'],
  press: ['key', 'hold'],
  keydown: ['key'],
  keyup: ['key'],
  switch: ['index', 'value'],
  fswitch: ['index', 'value'],
  analog: ['index', 'value'],
  trim: ['index', 'value'],
  macro: ['path', 'timeout'],
  wait: ['ms'],
  log: ['n'],
  quit: [],
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function printUsage() {
  process.stderr.write([
    'Usage:',
    '  node run_wasm.js <simulator.js> [--root-directory DIR] [--macro path[.lua]] [--macro-timeout MS]',
    '  node run_wasm.js <simulator.js> --serve [--port N] [--shots-dir DIR] [--root-directory DIR] [--macro ...]',
    '  node run_wasm.js <command> [args] [--port N]',
    '',
    'Commands (to a --serve instance):',
    '  status | screenshot [out.png] | tap X Y | longpress X Y | swipe X1 Y1 X2 Y2 [ms]',
    '  wheel STEPS | press KEY [holdMs] | keydown KEY | keyup KEY',
    '  switch I V | fswitch I V | analog I V | trim I V | macro PATH [timeoutMs]',
    '  wait MS | log [n] | quit',
    '',
    'KEY: SYS MDL DISP RTN PAGE ENTER, or a browser key name (Escape, PageDown, a, ...)',
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
    else positional.push(a)
  }
  if (positional.length < 1) {
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

async function runMacro(Module, path, timeoutMs) {
    const pausedPromise = waitOnce((h) => { Module.onDebugEvent = h }, timeoutMs, 'Timed out waiting for the macro to pause at its first line')
    Module.ccall('startMacro', 'void', ['string'], [path])
    const [source, line] = await pausedPromise
    const endPromise = waitOnce((h) => { Module.onMacroEnd = h }, timeoutMs, 'Timed out waiting for the macro to finish')
    Module.ccall('resumeMacro', 'void', [], [])
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
    this.frame = null // { width, height, pixels: Uint16Array }
    this.frameSeq = 0
    this.log = []
    this.switchesConfig = 0
    this.switchesPosition = []
    this.trimsValue = []
    this.macroRunning = false
  }

  pushLog(text) {
    this.log.push(text)
    if (this.log.length > 500) this.log.shift()
  }

  // Callbacks the simulator calls back into, to be set before it starts
  moduleCallbacks() {
    return {
      updateCanvas: (width, height, ptr) => {
        // Copy out: the heap may be shared with the simulator threads
        const pixels = new Uint16Array(this.Module.HEAPU16.buffer, ptr, width * height).slice()
        this.frame = { width, height, pixels }
        this.frameSeq++
      },
      setSwitchesConfig: (config) => { this.switchesConfig = config },
      setSwitchesPosition: (ptr, count) => {
        this.switchesPosition = Array.from(new Int8Array(this.Module.HEAP8.buffer, ptr, count))
      },
      setTrimsValue: (ptr, count) => {
        this.trimsValue = Array.from(new Int16Array(this.Module.HEAP16.buffer, ptr, count), (v) => Math.round(v * 100 / 256))
      },
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

  screenshot(out) {
    if (!this.frame) throw new Error('No frame received yet')
    const { width, height, pixels } = this.frame
    fs.mkdirSync(path.dirname(out), { recursive: true })
    fs.writeFileSync(out, encodePng(width, height, pixels))
    return { file: out, width, height }
  }

  key(name, down) {
    const key = BUTTONS[name.toUpperCase()] || name
    const keyCode = KEY_CODES[key] || (key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0)
    return this.Module.ccall('onKeyEvent', 'boolean', ['number', 'string', 'number', 'number'], [keyCode, key, 0, down ? 1 : 0])
  }

  async press(name, holdMs = 100) {
    const used = this.key(name, true)
    await sleep(holdMs)
    this.key(name, false)
    return used
  }

  async tap(x, y, holdMs = 80) {
    this.Module.ccall('onMouseDown', 'void', ['number', 'number'], [x, y])
    await sleep(holdMs)
    this.Module.ccall('onMouseUp', 'void', ['number', 'number'], [x, y])
  }

  async longPress(x, y) {
    this.Module.ccall('onMouseDown', 'void', ['number', 'number'], [x, y])
    await sleep(1000)
    this.Module.ccall('onMouseLongPress', 'void', [], [])
    await sleep(100)
    this.Module.ccall('onMouseUp', 'void', ['number', 'number'], [x, y])
  }

  async swipe(x1, y1, x2, y2, durationMs = 300) {
    const steps = Math.max(5, Math.round(durationMs / 20))
    this.Module.ccall('onMouseDown', 'void', ['number', 'number'], [x1, y1])
    for (let i = 1; i <= steps; i++) {
      await sleep(durationMs / steps)
      const x = Math.round(x1 + (x2 - x1) * i / steps)
      const y = Math.round(y1 + (y2 - y1) * i / steps)
      this.Module.ccall('onMouseMove', 'boolean', ['number', 'number'], [x, y])
    }
    this.Module.ccall('onMouseUp', 'void', ['number', 'number'], [x2, y2])
  }

  async wheel(steps) {
    const dir = Math.sign(steps)
    for (let i = 0; i < Math.abs(steps); i++) {
      this.Module.ccall('onMouseWheel', 'boolean', ['number'], [dir])
      await sleep(40)
    }
  }

  async runMacro(macroPath, timeoutMs) {
    this.macroRunning = true
    try {
      await runMacro(this.Module, macroPath, timeoutMs)
    } finally {
      this.macroRunning = false
    }
  }

  async handle(cmd, q, opts) {
    const num = (k, d) => (q[k] === undefined ? d : Number(q[k]))
    const call = (name, index, value) => this.Module.ccall(name, 'void', ['number', 'number'], [index, value])
    const before = this.frameSeq
    const result = { ok: true }
    switch (cmd) {
      case 'status':
        return {
          ok: true, mountPoint: this.mountPoint, macroRunning: this.macroRunning,
          frame: this.frame && { width: this.frame.width, height: this.frame.height },
          switchesConfig: this.switchesConfig, switchesPosition: this.switchesPosition, trims: this.trimsValue,
        }
      case 'screenshot': {
        await this.waitSettled(-1, num('quiet', 150), num('timeout', 3000))
        const out = path.resolve(q.out || path.join(opts.shotsDir, `shot-${ Date.now() }.png`))
        return { ok: true, ...this.screenshot(out) }
      }
      case 'tap': await this.tap(num('x'), num('y'), num('hold', 80)); break
      case 'longpress': await this.longPress(num('x'), num('y')); break
      case 'swipe': await this.swipe(num('x1'), num('y1'), num('x2'), num('y2'), num('ms', 300)); break
      case 'wheel': await this.wheel(num('steps', 1)); break
      case 'press': result.used = await this.press(q.key, num('hold', 100)); break
      case 'keydown': result.used = this.key(q.key, true); break
      case 'keyup': result.used = this.key(q.key, false); break
      case 'switch': call('setSwitchPosition', num('index'), num('value')); break
      case 'fswitch': call('setFunctionSwitchPressed', num('index'), num('value')); break
      case 'analog': call('setAnalogPosition', num('index'), num('value')); break
      case 'trim': call('setTrimPressed', num('index'), num('value')); break
      case 'macro': await this.runMacro(q.path, num('timeout', 60000)); break
      case 'wait': await sleep(num('ms', 500)); return result
      case 'log': return { ok: true, lines: this.log.slice(-num('n', 50)) }
      case 'quit':
        setTimeout(() => process.exit(0), 50)
        return result
      default:
        return { ok: false, error: `Unknown command ${ cmd }` }
    }
    // Let the UI react before returning, so a following screenshot shows the result
    await this.waitSettled(before, 150, num('settle', 1500))
    return result
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
  }))

  if (!session) {
    if (opts.macro) {
      await runMacro(Module, opts.macro, opts.macroTimeout)
    }
    process.exit(0)
  }

  if (opts.macro) {
    await session.runMacro(opts.macro, opts.macroTimeout)
  }

  let queue = Promise.resolve()
  http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost')
    const cmd = url.pathname.slice(1)
    const q = Object.fromEntries(url.searchParams)
    // Commands run one at a time, in arrival order
    queue = queue.then(() => session.handle(cmd, q, opts))
      .catch((err) => ({ ok: false, error: String(err && err.message || err) }))
      .then((result) => {
        res.writeHead(result.ok ? 200 : 400, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(result))
      })
  }).listen(opts.port, '127.0.0.1', () => {
    console.log(`[run_wasm] serving on http://127.0.0.1:${ opts.port }`)
  })
}

async function client(cmd, args, port) {
  const params = new URLSearchParams()
  CLIENT_COMMANDS[cmd].forEach((name, i) => {
    if (args[i] !== undefined) params.set(name, args[i])
  })
  const res = await fetch(`http://127.0.0.1:${ port }/${ cmd }?${ params }`)
  const body = await res.json()
  process.stdout.write(JSON.stringify(body) + '\n')
  process.exit(body.ok ? 0 : 1)
}

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  const [first, ...rest] = opts.positional
  if (Object.prototype.hasOwnProperty.call(CLIENT_COMMANDS, first)) {
    await client(first, rest, opts.port)
  } else {
    opts.jsPath = path.resolve(first)
    await run(opts)
  }
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + '\n')
  process.exit(1)
})
