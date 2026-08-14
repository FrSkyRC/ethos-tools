/*
 * Copyright (C) FrSky RC, Inc - All Rights Reserved
 *
 * Unauthorized copying of this file, via any medium is strictly prohibited
 * Proprietary and confidential
 *
 */

'use strict'

const path = require('path')
const fs = require('fs')

function parseArgs(argv) {
  const opts = {
    macro: null, macroTimeout: null,
    rootDirectory: null
  }
  const positional = []
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--help' || a === '-h') { printUsage(); process.exit(0) }
    else if (a === '--macro' || a === '--exec') opts.macro = argv[++i]
    else if (a === '--macro-timeout') opts.macroTimeout = parseInt(argv[++i], 10)
    else if (a === '--root-directory') opts.rootDirectory = argv[++i]
    else positional.push(a)
  }
  if (positional.length < 1) {
    console.log(positional.length)
    printUsage()
    process.exit(2)
  }
  opts.jsPath = path.resolve(positional[0])
  return opts
}

function printUsage() {
  process.stderr.write('Usage: node run_wasm.js <glue.js> [--root-directory DIR] [--macro path[.lua]] [--macro-timeout MS] [--read-only]\n')
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

async function main() {
  const opts = parseArgs(process.argv.slice(2))

  const moduleArg = {
    print: (text) => { process.stdout.write(text + '\n') },
    printErr: (text) => { process.stderr.write(text + '\n') },
  }

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

  Module.ccall('start', null, [], [])

  if (opts.macro) {
    await runMacro(Module, opts.macro, opts.macroTimeout)
  }

  process.exit(0)
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack || err) + '\n')
  process.exit(1)
})
