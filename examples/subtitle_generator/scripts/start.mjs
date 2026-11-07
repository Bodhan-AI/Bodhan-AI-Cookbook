// One command for the whole app: `npm start` from the project root.
// Starts the backend (FastAPI on 127.0.0.1:8000, from backend/venv) and the web app (Vite on localhost:5173)
// in this terminal, each line labelled, then opens the app in the browser (`npm start -- --no-open` doesn't).
// Ctrl+C stops both.
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP_URL = 'http://localhost:5173'
const BACKEND_URL = 'http://127.0.0.1:8000'
const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const python = join(root, 'backend', 'venv', ...(process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']))
const vite = join(root, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js')

const missing = [
  [python, 'backend/venv is missing: see README "Setup" (python -m venv venv, then pip install -r requirements.txt in backend/).'],
  [vite, 'frontend/node_modules is missing: run `npm install` in the project root.'],
].filter(([path]) => !existsSync(path))
if (missing.length) {
  for (const [, message] of missing) console.error(message)
  process.exit(1)
}

const services = [
  // `fastapi run`, not `fastapi dev`: a code change won't restart the backend in the middle of a job
  {
    name: 'backend',
    color: 36,
    command: python,
    args: ['-m', 'fastapi', 'run', 'app/main.py', '--host', '127.0.0.1', '--port', '8000'],
    cwd: join(root, 'backend'),
  },
  { name: 'web', color: 35, command: process.execPath, args: [vite], cwd: join(root, 'frontend') },
]

const children = []
let stopping = false

function stopAll(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) if (child.exitCode === null) child.kill()
  process.exitCode = code
}

for (const { name, color, command, args, cwd } of services) {
  const child = spawn(command, args, { cwd, env: { ...process.env, FORCE_COLOR: '1' } })
  const label = `\x1b[${color}m${name.padEnd(7)}|\x1b[0m `
  for (const stream of [child.stdout, child.stderr]) {
    let rest = ''
    stream.on('data', (chunk) => {
      const lines = (rest + chunk).split(/\r?\n/)
      rest = lines.pop()
      for (const line of lines) process.stdout.write(label + line + '\n')
    })
    stream.on('end', () => {
      if (rest) process.stdout.write(label + rest + '\n') // a last line without a newline, e.g. a crash message
      rest = ''
    })
  }
  child.on('exit', (code) => {
    if (!stopping) {
      console.log(`${label}stopped (exit code ${code}); stopping the other one too.`)
      stopAll(code ?? 1)
    }
  })
  children.push(child)
}

// Open the page once the backend and the web app both answer. Each is asked directly: asking through Vite's /api
// proxy would make Vite log an error for every try before the backend is up
async function openWhenReady() {
  for (let tries = 0; tries < 120 && !stopping; tries++) {
    try {
      if ((await fetch(`${BACKEND_URL}/api/health`)).ok && (await fetch(APP_URL)).ok) {
        const [command, args] =
          process.platform === 'win32'
            ? ['cmd', ['/c', 'start', '""', APP_URL]]
            : [process.platform === 'darwin' ? 'open' : 'xdg-open', [APP_URL]]
        spawn(command, args, { stdio: 'ignore', detached: true })
          .on('error', () => {})
          .unref()
        console.log(`Opened ${APP_URL} in the browser.`)
        return
      }
    } catch {
      // Not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
}

console.log(`Starting the backend (${BACKEND_URL}) and the web app (${APP_URL}). Ctrl+C stops both.`)
process.on('SIGINT', () => stopAll(0))
process.on('SIGTERM', () => stopAll(0))
if (!process.argv.includes('--no-open')) openWhenReady()
