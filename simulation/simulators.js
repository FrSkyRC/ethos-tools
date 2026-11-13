/*
 * Copyright (C) FrSky RC, Inc - All Rights Reserved
 *
 * Unauthorized copying of this file, via any medium is strictly prohibited
 * Proprietary and confidential
 *
 */

'use strict'

// The WASM simulators of the Ethos releases: the <RADIO>-WebSimulator.zip assets
// of the FrSkyRC/ETHOS-Feedback-Community releases, downloaded once in a cache
// folder (ETHOS_CACHE_DIR, else the user cache folder). Used by mcp_server.js
// (listSimulators, startSimulator) and run_wasm.js (--radio, --list-simulators).
// GITHUB_TOKEN (optional) raises the GitHub API rate limit.

const fs = require('fs')
const os = require('os')
const path = require('path')
const zlib = require('zlib')

const DOWNLOAD_TIMEOUT_MS = 300000

const RELEASES_REPOSITORY = 'FrSkyRC/ETHOS-Feedback-Community'
// The first releases whose simulators have the remote control exports (press, touch...)
const MIN_VERSION = [26, 2]
const SIMULATOR_ASSET = /^(.+)-WebSimulator\.zip$/

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
// onDownload(release, asset): called before a download (the MCP server must not write to stdout)
async function fetchSimulator(release, asset, onDownload) {
  const dir = simulatorDir(release.tag_name, radioName(asset))
  const stamp = JSON.stringify({ id: asset.id, updated_at: asset.updated_at })
  const stampFile = path.join(dir, 'asset.json')
  if (fs.existsSync(stampFile) && fs.readFileSync(stampFile, 'utf8') === stamp) {
    const js = findSimulatorJs(dir)
    if (js) {
      return { js, downloaded: false }
    }
  }
  if (onDownload) {
    onDownload(release, asset)
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


// The radios of the release matching pattern: X20S (its FCC simulator), X20S-FLEX or X20S_FLEX,
// X18-LBT (EU), X20* (the FCC ones), X20S-* (all its countries), * ...
function matchRadios(release, pattern) {
  let wanted = normalizeRadio(pattern)
  if (!wanted.includes('-')) {
    wanted += '-FCC'
  }
  const regex = new RegExp(`^${ wanted.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') }$`)
  const radios = simulatorAssets(release).map(radioName).filter((name) => regex.test(name)).sort()
  if (!radios.length) {
    const boards = [...new Set(simulatorAssets(release).map((asset) => radioName(asset).split('-')[0]))].sort()
    throw new Error(`No ${ wanted } simulator in ${ release.tag_name }. The radios: ${ boards.join(', ') }`)
  }
  return radios
}

// The simulator of radio in release (latest by default), downloaded unless it is
// in the cache: { tag, prerelease, radio, js, downloaded[, warning] }. Without
// network, a simulator already downloaded for an explicit release is used.
async function resolveSimulator(radio, release, onDownload) {
  try {
    const found = await findRelease(release)
    const asset = findAsset(found, radio)
    const { js, downloaded } = await fetchSimulator(found, asset, onDownload)
    return { tag: found.tag_name, prerelease: found.prerelease, radio: radioName(asset), js, downloaded }
  } catch (err) {
    const cached = release && release !== 'latest' && cachedSimulator(release, radio)
    if (!cached) {
      throw err
    }
    return { ...cached, downloaded: false, warning: `Cached simulator used: ${ err.message }` }
  }
}

module.exports = {
  MIN_VERSION,
  RELEASES_REPOSITORY,
  cacheDir,
  recentReleases,
  findRelease,
  simulatorAssets,
  radioName,
  matchRadios,
  resolveSimulator,
}
