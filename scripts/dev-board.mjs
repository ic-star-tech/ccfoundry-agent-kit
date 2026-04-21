#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import net from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(__dirname, "..");
const packageManifest = JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf-8"));
const webDir = join(packageRoot, "apps", "agent-dev-board-web");
const rawArgs = process.argv.slice(2);
const explicitCommand = rawArgs[0] && !rawArgs[0].startsWith("-") ? rawArgs[0] : "";
const command = explicitCommand || "dev-board";
const args = explicitCommand ? rawArgs.slice(1) : rawArgs;
const validCommands = new Set(["dev-board", "agent-dev-board"]);

function readFlag(name, fallback) {
  const prefixed = `--${name}=`;
  const match = args.find((entry) => entry.startsWith(prefixed));
  if (match) {
    return match.slice(prefixed.length);
  }
  const index = args.findIndex((entry) => entry === `--${name}`);
  if (index >= 0 && args[index + 1]) {
    return args[index + 1];
  }
  return fallback;
}

function hasFlag(...names) {
  return args.some((entry) => names.includes(entry));
}

function printHelp() {
  console.log(`CCFoundry Agent Kit ${packageManifest.version}

Usage:
  ccfoundry [command] [options]
  npm run dev-board -- [options]

Commands:
  dev-board               Start Agent Dev Board (default)
  agent-dev-board         Alias for dev-board

Options:
  --host <host>            Bind host for both services (default: 127.0.0.1)
  --api-port <port>        Preferred API port (default: 8090)
  --web-port <port>        Preferred web port (default: 3000)
  --workspace <path>       Working directory for .venv and .dev-board (default: current directory)
  -h, --help               Show help
  -v, --version            Print version
`);
}

if (hasFlag("-v", "--version")) {
  console.log(packageManifest.version);
  process.exit(0);
}

if (hasFlag("-h", "--help")) {
  printHelp();
  process.exit(0);
}

if (!validCommands.has(command)) {
  fail(`Unknown command: ${command}. Run "ccfoundry --help" for usage.`);
}

const workspaceRoot = resolve(readFlag("workspace", process.env.CCFOUNDRY_WORKSPACE || process.cwd()));
const venvDir = join(workspaceRoot, ".venv");
const venvPython = join(venvDir, "bin", "python");
const venvStamp = join(venvDir, ".ccfoundry-dev-board.stamp");
const webStamp = join(webDir, "node_modules", ".ccfoundry-dev-board.stamp");
const runtimeDir = join(workspaceRoot, ".dev-board");
const runtimeAgentsFile = join(runtimeDir, "agents.generated.yaml");
const cliHint = process.env.npm_lifecycle_event
  ? `npm run ${process.env.npm_lifecycle_event}`
  : `ccfoundry${explicitCommand ? ` ${command}` : ""}`;

const host = readFlag("host", process.env.CCFOUNDRY_DEV_HOST || "127.0.0.1");
const requestedApiPort = Number.parseInt(readFlag("api-port", process.env.CCFOUNDRY_API_PORT || "8090"), 10);
const requestedWebPort = Number.parseInt(readFlag("web-port", process.env.CCFOUNDRY_WEB_PORT || "3000"), 10);

function log(message) {
  console.log(`[dev-board] ${message}`);
}

function fail(message) {
  console.error(`[dev-board] ${message}`);
  process.exit(1);
}

function runCapture(command, commandArgs, options = {}) {
  return spawnSync(command, commandArgs, {
    cwd: workspaceRoot,
    encoding: "utf-8",
    ...options,
  });
}

function runChecked(command, commandArgs, options = {}) {
  const result = spawnSync(command, commandArgs, {
    cwd: workspaceRoot,
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) {
    fail(`Command failed: ${command} ${commandArgs.join(" ")}`);
  }
}

function isPositivePort(value) {
  return Number.isInteger(value) && value > 0 && value < 65536;
}

async function isPortFree(targetHost, port) {
  return await new Promise((resolvePort) => {
    const server = net.createServer();
    server.unref();
    server.on("error", () => resolvePort(false));
    server.listen({ host: targetHost, port }, () => {
      server.close(() => resolvePort(true));
    });
  });
}

async function pickPort(label, targetHost, preferredPort, reservedPorts = new Set()) {
  if (!isPositivePort(preferredPort)) {
    fail(`Invalid ${label} port: ${preferredPort}`);
  }
  let port = preferredPort;
  while (port < 65536) {
    if (reservedPorts.has(port)) {
      port += 1;
      continue;
    }
    const free = await isPortFree(targetHost, port);
    if (free) {
      if (port !== preferredPort) {
        log(`${label} port ${preferredPort} is busy; using ${port} instead`);
      }
      return port;
    }
    port += 1;
  }
  fail(`Could not find an available ${label} port starting from ${preferredPort}`);
}

function versionTuple(raw) {
  return String(raw || "")
    .trim()
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);
}

function versionAtLeast(current, minimum) {
  const a = versionTuple(current);
  const b = versionTuple(minimum);
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const left = a[index] || 0;
    const right = b[index] || 0;
    if (left > right) {
      return true;
    }
    if (left < right) {
      return false;
    }
  }
  return true;
}

function findPython() {
  const candidates = [];
  if (process.env.PYTHON) {
    candidates.push(process.env.PYTHON);
  }
  candidates.push("python3", "python");

  for (const candidate of candidates) {
    const result = runCapture(candidate, ["-c", "import sys; print('.'.join(str(v) for v in sys.version_info[:3]))"]);
    if (result.status !== 0) {
      continue;
    }
    const version = String(result.stdout || "").trim();
    if (versionAtLeast(version, "3.10.0")) {
      return { command: candidate, version };
    }
  }
  return null;
}

function ensureVenv(pythonCommand) {
  mkdirSync(workspaceRoot, { recursive: true });
  if (!existsSync(venvPython)) {
    log(`Creating Python virtual environment in ${venvDir}`);
    runChecked(pythonCommand, ["-m", "venv", venvDir]);
  }
}

function newestMtime(paths) {
  let latest = 0;
  for (const target of paths) {
    const value = statSync(target).mtimeMs;
    if (value > latest) {
      latest = value;
    }
  }
  return latest;
}

function needsRefresh(stampFile, watchedFiles) {
  if (!existsSync(stampFile)) {
    return true;
  }
  return statSync(stampFile).mtimeMs < newestMtime(watchedFiles);
}

function touch(target, payload) {
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, payload, "utf-8");
}

async function waitForHttp(url, timeoutMs = 30000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // Service is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function ensurePythonDeps() {
  const watched = [
    join(packageRoot, "packages", "python-sdk", "pyproject.toml"),
    join(packageRoot, "examples", "me_agent", "pyproject.toml"),
    join(packageRoot, "apps", "agent-dev-board-api", "pyproject.toml"),
  ];
  if (!needsRefresh(venvStamp, watched)) {
    return;
  }
  log(`Installing Python packages into ${venvDir}`);
  runChecked(venvPython, ["-m", "pip", "install", "-U", "pip"]);
  runChecked(venvPython, [
    "-m",
    "pip",
    "install",
    "-e",
    join(packageRoot, "packages", "python-sdk"),
    "-e",
    join(packageRoot, "examples", "me_agent"),
    "-e",
    join(packageRoot, "apps", "agent-dev-board-api"),
  ]);
  touch(venvStamp, `python deps refreshed ${new Date().toISOString()}\n`);
}

function ensureWebDeps() {
  const watched = [
    join(webDir, "package.json"),
    join(webDir, "package-lock.json"),
  ].filter((target) => existsSync(target));
  const hasNodeModules = existsSync(join(webDir, "node_modules"));
  if (hasNodeModules && !needsRefresh(webStamp, watched)) {
    return;
  }
  log("Installing web dependencies for Agent Dev Board");
  runChecked("npm", ["install", "--no-fund", "--no-audit"], { cwd: webDir });
  touch(webStamp, `web deps refreshed ${new Date().toISOString()}\n`);
}

function ensureRuntimeAgentsFile() {
  mkdirSync(runtimeDir, { recursive: true });
  if (!existsSync(runtimeAgentsFile)) {
    writeFileSync(runtimeAgentsFile, "agents: []\n", "utf-8");
  }
}

function attachLogs(child, label) {
  const bind = (stream, writer, prefix) => {
    if (!stream) {
      return;
    }
    let pending = "";
    stream.on("data", (chunk) => {
      pending += chunk.toString();
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() ?? "";
      for (const line of lines) {
        writer(`${prefix} ${line}\n`);
      }
    });
    stream.on("end", () => {
      if (pending) {
        writer(`${prefix} ${pending}\n`);
      }
    });
  };

  bind(child.stdout, process.stdout.write.bind(process.stdout), `[${label}]`);
  bind(child.stderr, process.stderr.write.bind(process.stderr), `[${label}]`);
}

function spawnService(label, command, commandArgs, options = {}) {
  const child = spawn(command, commandArgs, {
    cwd: workspaceRoot,
    env: process.env,
    stdio: ["inherit", "pipe", "pipe"],
    ...options,
  });
  attachLogs(child, label);
  child.on("exit", (code, signal) => {
    if (shuttingDown) {
      return;
    }
    if (signal) {
      console.error(`[${label}] exited via signal ${signal}`);
    } else if (code !== 0) {
      console.error(`[${label}] exited with code ${code}`);
    } else {
      console.error(`[${label}] stopped`);
    }
    shutdown(code || 0);
  });
  return child;
}

let shuttingDown = false;
const children = [];

function shutdown(exitCode = 0) {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) {
      child.kill("SIGTERM");
    }
  }
  setTimeout(() => process.exit(exitCode), 200);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

const python = findPython();
if (!python) {
  fail("Python 3.10+ is required. Install python3 and try again.");
}

async function main() {
  log(`Using ${python.command} (${python.version})`);
  log(`Workspace: ${workspaceRoot}`);
  log(`Package:   ${packageRoot}`);
  ensureVenv(python.command);
  ensurePythonDeps();
  ensureWebDeps();
  ensureRuntimeAgentsFile();

  const reserved = new Set();
  const apiPort = await pickPort("API", host, requestedApiPort, reserved);
  reserved.add(apiPort);
  const webPort = await pickPort("Web", host, requestedWebPort, reserved);
  reserved.add(webPort);

  log("Starting Agent Dev Board stack");
  log(`API:   http://${host}:${apiPort}`);
  log(`Web:   http://${host}:${webPort}`);
  if (host === "127.0.0.1") {
    log(`For LAN access, rerun with: ${cliHint} --host 0.0.0.0`);
  }

  children.push(
    spawnService(
      "api",
      venvPython,
      [
        "-m",
        "uvicorn",
        "agent_dev_board_api.app:app",
        "--app-dir",
        join(packageRoot, "apps", "agent-dev-board-api", "src"),
        "--reload",
        "--host",
        host,
        "--port",
        String(apiPort),
      ],
      {
        env: {
          ...process.env,
          CCFOUNDRY_AGENTS_FILE: runtimeAgentsFile,
          CCFOUNDRY_DEV_VENV_PYTHON: venvPython,
        },
      },
    ),
  );

  children.push(
    spawnService(
      "web",
      "npm",
      [
        "run",
        "dev",
        "--",
        "--host",
        host,
        "--port",
        String(webPort),
      ],
      {
        cwd: webDir,
        env: {
          ...process.env,
          VITE_API_PORT: String(apiPort),
        },
      },
    ),
  );

  try {
    await Promise.all([
      waitForHttp(`http://${host}:${apiPort}/api/agents`),
      waitForHttp(`http://${host}:${webPort}/`),
    ]);
    log(`Ready. Open http://${host}:${webPort}/`);
  } catch (error) {
    fail(String(error && error.message ? error.message : error));
  }
}

main().catch((error) => {
  fail(String(error && error.message ? error.message : error));
});
