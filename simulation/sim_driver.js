/*
 * Copyright (C) FrSky RC, Inc - All Rights Reserved
 *
 * Unauthorized copying of this file, via any medium is strictly prohibited
 * Proprietary and confidential
 *
 */

'use strict'

// Interactive driver for the Ethos WASM simulator.
//
//   node sim_driver.js serve <simulator.js> [--root-directory DIR] [--port N]
//   node sim_driver.js <command> [args...] [--port N]
//
// "serve" boots the simulator and keeps it running behind a small HTTP server
// on 127.0.0.1. Every other invocation is a client that sends one command to
// that server and prints the JSON result.

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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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

/* ---------- Server ---------- */

class Simulator {
  constructor() {
    this.Module = null
    this.frame = null // { width, height, pixels: Uint16Array, seq }
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

  async boot(jsPath, rootDirectory) {
    const factory = require(jsPath)
    const Module = await factory({
      print: (text) => { process.stdout.write(text + '\n'); this.pushLog(text) },
      printErr: (text) => { process.stderr.write(text + '\n'); this.pushLog(text) },
    })
    this.Module = Module

    Module.updateCanvas = (width, height, ptr) => {
      // Copy out: the heap may be shared with the simulator threads
      const pixels = new Uint16Array(Module.HEAPU16.buffer, ptr, width * height).slice()
      this.frame = { width, height, pixels, seq: ++this.frameSeq }
    }
    Module.setSwitchesConfig = (config) => { this.switchesConfig = config }
    Module.setSwitchesPosition = (ptr, count) => {
      this.switchesPosition = Array.from(new Int8Array(Module.HEAP8.buffer, ptr, count))
    }
    Module.setTrimsValue = (ptr, count) => {
      this.trimsValue = Array.from(new Int16Array(Module.HEAP16.buffer, ptr, count), (v) => Math.round(v * 100 / 256))
    }

    const mountPoint = Module.ccall('getCurrentModel', 'string')
    Module.FS.mkdir('/persist')
    Module.FS.mkdir(mountPoint)
    if (rootDirectory) {
      if (!fs.existsSync(rootDirectory)) throw new Error(`Directory ${ rootDirectory } doesn't exist`)
      Module.FS.mount(Module.NODEFS, { root: rootDirectory }, mountPoint)
    } else {
      console.log('No workspace, will use MEMFS')
      Module.FS.mount(Module.MEMFS, {}, mountPoint)
    }
    Module.ccall('start', null, [], [])
    this.mountPoint = mountPoint
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

  async runMacro(macroPath, timeoutMs = 60000) {
    const Module = this.Module
    const wait = (name) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => { Module[name] = undefined; reject(new Error(`Timed out waiting for ${ name }`)) }, timeoutMs)
      Module[name] = (...args) => { clearTimeout(timer); Module[name] = undefined; resolve(args) }
    })
    this.macroRunning = true
    try {
      const paused = wait('onDebugEvent')
      Module.ccall('startMacro', 'void', ['string'], [macroPath])
      await paused
      const ended = wait('onMacroEnd')
      Module.ccall('resumeMacro', 'void', [], [])
      await ended
    } finally {
      this.macroRunning = false
    }
  }
}

async function handle(sim, cmd, q, opts) {
  const num = (k, d) => (q[k] === undefined ? d : Number(q[k]))
  const before = sim.frameSeq
  let result = { ok: true }
  switch (cmd) {
    case 'status':
      return {
        ok: true, started: !!sim.Module, mountPoint: sim.mountPoint, macroRunning: sim.macroRunning,
        frame: sim.frame && { width: sim.frame.width, height: sim.frame.height, seq: sim.frame.seq },
        switchesConfig: sim.switchesConfig, switchesPosition: sim.switchesPosition, trims: sim.trimsValue,
      }
    case 'screenshot': {
      await sim.waitSettled(-1, num('quiet', 150), num('timeout', 3000))
      const out = path.resolve(q.out || path.join(opts.shotsDir, `shot-${ Date.now() }.png`))
      return { ok: true, ...sim.screenshot(out) }
    }
    case 'tap': await sim.tap(num('x'), num('y'), num('hold', 80)); break
    case 'longpress': await sim.longPress(num('x'), num('y')); break
    case 'swipe': await sim.swipe(num('x1'), num('y1'), num('x2'), num('y2'), num('ms', 300)); break
    case 'wheel': await sim.wheel(num('steps', 1)); break
    case 'press': result.used = await sim.press(q.key, num('hold', 100)); break
    case 'keydown': result.used = sim.key(q.key, true); break
    case 'keyup': result.used = sim.key(q.key, false); break
    case 'switch': sim.Module.ccall('setSwitchPosition', 'void', ['number', 'number'], [num('index'), num('value')]); break
    case 'fswitch': sim.Module.ccall('setFunctionSwitchPressed', 'void', ['number', 'number'], [num('index'), num('value')]); break
    case 'analog': sim.Module.ccall('setAnalogPosition', 'void', ['number', 'number'], [num('index'), num('value')]); break
    case 'trim': sim.Module.ccall('setTrimPressed', 'void', ['number', 'number'], [num('index'), num('value')]); break
    case 'macro': await sim.runMacro(q.path, num('timeout', 60000)); break
    case 'wait': await sleep(num('ms', 500)); return result
    case 'log': return { ok: true, lines: sim.log.slice(-num('n', 50)) }
    case 'quit':
      setTimeout(() => process.exit(0), 50)
      return result
    default:
      return { ok: false, error: `Unknown command ${ cmd }` }
  }
  // Let the UI react before returning, so a following screenshot shows the result
  await sim.waitSettled(before, 150, num('settle', 1500))
  return result
}

async function serve(args) {
  const opts = { port: DEFAULT_PORT, rootDirectory: null, shotsDir: path.resolve('screenshots') }
  const positional = []
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a === '--root-directory') opts.rootDirectory = args[++i]
    else if (a === '--port') opts.port = parseInt(args[++i], 10)
    else if (a === '--shots-dir') opts.shotsDir = path.resolve(args[++i])
    else positional.push(a)
  }
  if (!positional[0]) {
    printUsage()
    process.exit(2)
  }

  const sim = new Simulator()
  await sim.boot(path.resolve(positional[0]), opts.rootDirectory)

  let queue = Promise.resolve()
  http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost')
    const cmd = url.pathname.slice(1)
    const q = Object.fromEntries(url.searchParams)
    // Commands run one at a time, in arrival order
    queue = queue.then(() => handle(sim, cmd, q, opts)).catch((err) => ({ ok: false, error: String(err && err.message || err) }))
      .then((result) => {
        res.writeHead(result.ok ? 200 : 400, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(result))
      })
  }).listen(opts.port, '127.0.0.1', () => {
    console.log(`[sim_driver] listening on http://127.0.0.1:${ opts.port }`)
  })
}

/* ---------- Client ---------- */

const CLIENT_ARGS = {
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
}

async function client(cmd, args) {
  let port = DEFAULT_PORT
  const names = CLIENT_ARGS[cmd] || []
  const params = new URLSearchParams()
  let n = 0
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--port') port = parseInt(args[++i], 10)
    else if (names[n]) params.set(names[n++], args[i])
  }
  const res = await fetch(`http://127.0.0.1:${ port }/${ cmd }?${ params }`)
  const body = await res.json()
  process.stdout.write(JSON.stringify(body) + '\n')
  process.exit(body.ok ? 0 : 1)
}

function printUsage() {
  process.stderr.write([
    'Usage:',
    '  node sim_driver.js serve <simulator.js> [--root-directory DIR] [--port N] [--shots-dir DIR]',
    '  node sim_driver.js <command> [args] [--port N]',
    '',
    'Commands:',
    '  status | screenshot [out.png] | tap X Y | longpress X Y | swipe X1 Y1 X2 Y2 [ms]',
    '  wheel STEPS | press KEY [holdMs] | keydown KEY | keyup KEY',
    '  switch I V | fswitch I V | analog I V | trim I V | macro PATH [timeoutMs]',
    '  wait MS | log [n] | quit',
    '',
    'KEY: SYS MDL DISP RTN PAGE ENTER, or a browser key name (Escape, PageDown, a, ...)',
    '',
  ].join('\n'))
}

const [cmd, ...rest] = process.argv.slice(2)
if (!cmd || cmd === '-h' || cmd === '--help') {
  printUsage()
  process.exit(cmd ? 0 : 2)
}
;(cmd === 'serve' ? serve(rest) : client(cmd, rest)).catch((err) => {
  process.stderr.write(String(err && err.stack || err) + '\n')
  process.exit(1)
})
