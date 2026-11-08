// Dependency bootstrap for the packaged Dev Board. Configuration output may
// contain credentials: capture it only for hashing, never print or persist it.
import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const sha = value => createHash("sha256").update(value).digest("hex");
const fileHash = path => existsSync(path) && statSync(path).isFile() ? sha(readFileSync(path)) : null;
const sorted = value => Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));

function flag(args, name, fallback = "") {
  for (let index = 0; index < args.length; index += 1) {
    if (args[index].startsWith(`--${name}=`)) {
      const value = args[index].slice(name.length + 3);
      if (!value) throw new Error(`--${name} requires a value`);
      return value;
    }
    if (args[index] === `--${name}`) {
      const value = args[index + 1];
      if (!value || value.startsWith("-")) throw new Error(`--${name} requires a value`);
      return value;
    }
  }
  return fallback;
}

export function dependencyOptions(args, env = process.env, cwd = process.cwd()) {
  const rawOffline = String(env.CCFOUNDRY_OFFLINE || "false").toLowerCase();
  if (!["true", "false", "1", "0"].includes(rawOffline)) throw new Error("CCFOUNDRY_OFFLINE must be true, false, 1 or 0");
  if (args.includes("--offline") && args.includes("--online")) throw new Error("Choose either --offline or --online");
  if (args.some(a => a.startsWith("--offline=") || a.startsWith("--online="))) throw new Error("Use --offline or --online without a value");
  const offline = args.includes("--offline") || (!args.includes("--online") && ["true", "1"].includes(rawOffline));
  const wheelValue = flag(args, "wheelhouse", env.CCFOUNDRY_WHEELHOUSE);
  const cacheValue = flag(args, "npm-cache", env.CCFOUNDRY_NPM_CACHE);
  const wheelhouse = wheelValue ? resolve(cwd, wheelValue) : "";
  const npmCache = cacheValue ? resolve(cwd, cacheValue) : "";
  if (offline && !wheelhouse) throw new Error("Offline setup requires --wheelhouse or CCFOUNDRY_WHEELHOUSE");
  if (wheelhouse && (!existsSync(wheelhouse) || !statSync(wheelhouse).isDirectory())) throw new Error("The selected wheelhouse must be an existing directory");
  if (offline && !readdirSync(wheelhouse).some(name => name.endsWith(".whl") && statSync(join(wheelhouse, name)).isFile())) throw new Error("Offline setup requires dependency wheels in the selected wheelhouse");
  return { offline, wheelhouse, npmCache, setupOnly: args.includes("--setup-only") };
}

export function dependencyEnvironments(options, inherited = process.env) {
  const pip = { ...inherited, PIP_DISABLE_PIP_VERSION_CHECK: "1" };
  const npm = { ...inherited };
  if (options.wheelhouse) pip.PIP_FIND_LINKS = pathToFileURL(options.wheelhouse).href;
  if (options.offline) {
    pip.PIP_NO_INDEX = "1";
    npm.npm_config_offline = "true";
  }
  if (options.npmCache) npm.npm_config_cache = options.npmCache;
  return { pip, npm };
}

function capture(command, args, cwd, env, label) {
  const result = spawnSync(command, args, { cwd, env, encoding: "utf-8", timeout: 60000, maxBuffer: 4 * 1024 * 1024 });
  if (result.status !== 0 || result.error) throw new Error(`${label} failed; check the selected tool and its configuration`);
  return result.stdout;
}

function install(command, args, cwd, env, label) {
  const result = spawnSync(command, args, { cwd, env, stdio: "inherit" });
  if (result.status !== 0 || result.error) throw new Error(`${label} failed; dependencies are not marked ready. No alternate registry or online retry was attempted.`);
}

// Public pip CLI, rather than pip._internal imports. Values use Python repr in
// `pip config list`; ast.literal_eval decodes them without executing content.
const PIP_METADATA = `
import ast, hashlib, json, os, pathlib, subprocess, sys
result = subprocess.run([sys.executable, '-m', 'pip', 'config', 'list'], capture_output=True, text=True)
if result.returncode: raise SystemExit(1)
paths = set()
configuration = {}
for line in result.stdout.splitlines():
    key, sep, raw = line.partition('=')
    if sep:
        try: value = ast.literal_eval(raw)
        except (ValueError, SyntaxError): raise SystemExit(1)
        configuration[key] = value
        if key.rsplit('.', 1)[-1] in {'cert', 'client-cert', 'constraint', 'requirement', 'config-file'} and isinstance(value, str) and value: paths.add(value)
for key in ['PIP_CONFIG_FILE', 'PIP_CERT', 'PIP_CLIENT_CERT', 'PIP_CONSTRAINT', 'PIP_REQUIREMENT', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE']:
    if os.environ.get(key): paths.add(os.environ[key])
files = {}
for value in sorted(paths):
    path = pathlib.Path(value).expanduser()
    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
print(json.dumps({'configuration': hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest(), 'files': files,
                  'python': sys.version, 'executable': sys.executable, 'prefix': sys.prefix}))
`;

export function dependencyFingerprint({ kind, packageRoot, watchedFiles, metadata, options, environment }) {
  const files = watchedFiles.map(path => [path, fileHash(path)]);
  const variables = sorted(Object.fromEntries(Object.entries(environment).filter(([key]) =>
    kind === "python" ? /^(PIP_|PYTHON|VIRTUAL_ENV$|REQUESTS_CA_BUNDLE$|CURL_CA_BUNDLE$|SSL_CERT_FILE$|HTTPS?_PROXY$|ALL_PROXY$|NO_PROXY$)/i.test(key)
      : /^(npm_config_|NODE_|HTTPS?_PROXY$|ALL_PROXY$|NO_PROXY$)/i.test(key))));
  const wheels = kind === "python" && options.wheelhouse ? readdirSync(options.wheelhouse).filter(n => n.endsWith(".whl")).sort().map(n => {
    const info = statSync(join(options.wheelhouse, n));
    return [n, info.size, info.mtimeMs];
  }) : [];
  return sha(JSON.stringify({ schema: 1, kind, packageRoot: realpathSync(packageRoot), files, metadata,
    options: kind === "python" ? { offline: options.offline, wheelhouse: options.wheelhouse } : { offline: options.offline, npmCache: options.npmCache },
    variables, wheels, platform: process.platform, arch: process.arch, node: kind === "web" ? process.version : undefined }));
}

export function currentStamp(path, fingerprint) {
  try {
    const value = JSON.parse(readFileSync(path, "utf-8"));
    return value.schema === 1 && value.fingerprint === fingerprint;
  } catch { return false; }
}

export function writeStamp(path, fingerprint) {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`;
  try {
    writeFileSync(temporary, JSON.stringify({ schema: 1, fingerprint }) + "\n", { mode: 0o600, flag: "wx" });
    chmodSync(temporary, 0o600);
    renameSync(temporary, path);
  } finally { rmSync(temporary, { force: true }); }
}

function healthy(command, args, cwd, env) {
  return spawnSync(command, args, { cwd, env, stdio: "ignore", timeout: 60000 }).status === 0;
}

export function ensureDependencies({ packageRoot, workspaceRoot, venvPython, options, log = () => {} }) {
  const webDir = join(packageRoot, "apps", "agent-dev-board-web");
  const packages = ["packages/python-sdk", "examples/me_agent", "apps/agent-dev-board-api"].map(p => join(packageRoot, p));
  const environments = dependencyEnvironments(options);
  const pythonStamp = join(dirname(venvPython), "..", ".ccfoundry-dev-board.stamp");
  const webStamp = join(webDir, "node_modules", ".ccfoundry-dev-board.stamp");
  const metadata = () => JSON.parse(capture(venvPython, ["-c", PIP_METADATA], workspaceRoot, environments.pip, "Reading pip configuration"));
  const watchedPython = [...packages.map(p => join(p, "pyproject.toml")), join(packageRoot, "packages/python-sdk/src/ccfoundry_agent_kit/_version.py"), join(packageRoot, "scripts/dev-board-deps.mjs")];
  const pythonFingerprint = () => dependencyFingerprint({ kind: "python", packageRoot, watchedFiles: watchedPython, metadata: metadata(), options, environment: environments.pip });
  const checkPython = () => healthy(venvPython, ["-m", "pip", "check"], workspaceRoot, environments.pip)
    && healthy(venvPython, ["-c", "import ccfoundry_agent_kit, me_agent_example, agent_dev_board_api, uvicorn"], workspaceRoot, environments.pip);
  const beforePython = pythonFingerprint();
  if (!currentStamp(pythonStamp, beforePython) || !checkPython()) {
    rmSync(pythonStamp, { force: true });
    log(`Installing Python dependencies (${options.offline ? "offline wheelhouse" : "configured pip sources"})`);
    const flags = ["--disable-pip-version-check", "install"];
    if (options.offline) flags.push("--no-index");
    if (options.wheelhouse) flags.push("--find-links", pathToFileURL(options.wheelhouse).href);
    install(venvPython, ["-m", "pip", ...flags, "--upgrade", "pip"], workspaceRoot, environments.pip, "pip upgrade");
    install(venvPython, ["-m", "pip", ...flags, ...packages.flatMap(p => ["-e", p])], workspaceRoot, environments.pip, "Python dependency installation");
    if (!checkPython()) throw new Error("Python dependencies did not pass the installed-package check");
    const afterPython = pythonFingerprint();
    if (afterPython !== beforePython) throw new Error("Python inputs or configuration changed during setup; rerun with stable inputs");
    writeStamp(pythonStamp, afterPython);
  } else log("Python dependencies are ready (configuration and package checks passed)");

  const npmFlags = ["--global=false", "--include=dev", "--include=optional", "--no-audit", "--no-fund"];
  if (options.offline) npmFlags.push("--offline");
  if (options.npmCache) npmFlags.push("--cache", options.npmCache);
  const npmMetadata = () => {
    const config = JSON.parse(capture("npm", ["config", "list", "--json", ...npmFlags], webDir, environments.npm, "Reading npm configuration"));
    const paths = [config.userconfig, config.globalconfig, config.cafile, join(webDir, ".npmrc"), environments.npm.NODE_EXTRA_CA_CERTS];
    const files = paths.filter(p => typeof p === "string" && p).map(p => [p, fileHash(p)]);
    // npm hides auth values in `config list`. Include environment substitutions
    // used by native rc files, without persisting their names or values.
    const substitutions = {};
    for (const [path, hash] of files) {
      if (!hash || path === config.cafile || path === environments.npm.NODE_EXTRA_CA_CERTS) continue;
      for (const match of readFileSync(path, "utf-8").matchAll(/\$\{([^}]+)\}/g)) {
        substitutions[match[1]] = sha(environments.npm[match[1]] ?? "");
      }
    }
    return { configuration: sha(JSON.stringify(sorted(config))), files, substitutions: sorted(substitutions) };
  };
  const webFingerprint = () => dependencyFingerprint({ kind: "web", packageRoot, watchedFiles: [join(webDir, "package.json"), join(webDir, "package-lock.json"), join(packageRoot, "scripts/dev-board-deps.mjs")], metadata: npmMetadata(), options, environment: environments.npm });
  const webProbe = ["--input-type=module", "-e", "await import('vite'); await import('react'); await import('react-dom'); await import('typescript'); const e = await import('esbuild'); e.transformSync('const ready = true', {loader:'js'});"];
  const checkWeb = () => healthy(process.execPath, webProbe, webDir, environments.npm);
  const beforeWeb = webFingerprint();
  if (!currentStamp(webStamp, beforeWeb) || !checkWeb()) {
    rmSync(webStamp, { force: true });
    if (!existsSync(join(webDir, "package-lock.json"))) throw new Error("Dev Board requires its packaged npm lockfile");
    log(`Installing web dependencies (${options.offline ? "offline npm cache" : "configured npm registry"})`);
    install("npm", ["ci", ...npmFlags], webDir, environments.npm, "npm dependency installation");
    if (!checkWeb()) throw new Error("Web dependencies did not pass the Vite/esbuild check");
    const afterWeb = webFingerprint();
    if (afterWeb !== beforeWeb) throw new Error("npm inputs or configuration changed during setup; rerun with stable inputs");
    writeStamp(webStamp, afterWeb);
  } else log("Web dependencies are ready (configuration and package checks passed)");
}
