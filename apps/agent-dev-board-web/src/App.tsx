import { useEffect, useMemo, useState } from "react";

type ContextMode = "direct" | "inline";
type BoardView = "guide" | "agent" | "playground" | "earnings" | "skills" | "jobs";
type AgentCardTab = "overview" | "runtimes" | "cloud-run" | "runtime" | "profile";
type GuideRunTarget = "local" | "cloud_run";

type AgentManifest = {
  name: string;
  label: string;
  version: string;
  description: string;
  capabilities: string[];
  loaded_skills?: string[];
  billing?: {
    model?: string;
    fee_note?: string;
  };
  manifest?: {
    llm?: {
      needs_gateway?: boolean;
    };
  };
};

type AgentEntry = {
  name: string;
  label: string;
  base_url: string;
  status?: string;
  error?: string;
  manifest: AgentManifest | null;
};

type TranscriptEntry = {
  user: string;
  assistant: string;
};

type StepEntry = {
  status: string;
  text: string;
};

type HandshakeCheck = {
  level: string;
  message: string;
};

type HandshakeProbeResult = {
  agent: {
    name: string;
    label: string;
    base_url: string;
    bootstrap_state_url: string;
    agent_card_url: string;
    bootstrap_probe: Record<string, unknown>;
    bootstrap_state: Record<string, unknown>;
    agent_card_probe: Record<string, unknown>;
    agent_card: Record<string, unknown>;
  };
  foundry: {
    url: string;
    health_probe: Record<string, unknown>;
    health_payload: Record<string, unknown>;
    routes: Record<string, Record<string, unknown>>;
  };
  checks: HandshakeCheck[];
};

type DevOverrides = {
  model: string;
  base_url: string;
  api_key: string;
};

type RuntimeBadge = {
  label: string;
  tone: "neutral" | "success" | "warn";
  detail: string;
};

type DeveloperContextResult = {
  git: Record<string, unknown>;
  github: Record<string, unknown>;
  foundry: {
    url: string;
    routes: Record<string, Record<string, unknown>>;
  };
  developer_identity: Record<string, unknown>;
};

type DeveloperTicketResult = {
  ok: boolean;
  foundry_url: string;
  ticket_route?: string;
  route_attempts?: Array<Record<string, unknown>>;
  git?: Record<string, unknown>;
  github?: Record<string, unknown>;
  developer_identity?: Record<string, unknown>;
  ticket?: Record<string, unknown>;
  claim_applied?: boolean;
  apply_mode?: string;
  apply_result?: Record<string, unknown> | null;
  env_snippet?: string;
  message?: string;
};

type NotificationPreferences = {
  email?: string;
  bounty_success_email_enabled?: boolean;
  status?: string;
  foundry_url?: string;
  synced_at?: string;
  message?: string;
  upstream?: Record<string, unknown>;
};

type DeveloperForm = {
  developer_token: string;
  github_token: string;
  bootstrap_delivery: string;
  force_rediscover: boolean;
};

type FoundryBootstrapAuthMessage = {
  type: string;
  ok: boolean;
  foundry_url?: string;
  developer_token?: string;
  github_login?: string;
  developer_identity?: Record<string, unknown>;
  expires_in_minutes?: number;
  error?: string;
};

type FoundryBootstrapSession = {
  foundry_url: string;
  github_login: string;
  developer_identity: Record<string, unknown>;
  expires_in_minutes: number;
};

type LocalAgentTemplate = {
  id: string;
  label: string;
  description: string;
};

type LocalAgentRuntime = {
  name: string;
  label: string;
  template_id: string;
  host: string;
  port: number;
  base_url: string;
  instance_dir: string;
  foundry_url?: string;
  created_at?: string;
  started_at?: string;
  stopped_at?: string;
  status?: string;
  pid?: number | null;
  env_path?: string;
  log_path?: string;
};

type CreateAgentForm = {
  template_id: string;
  name: string;
  label: string;
  preferred_port: string;
};

type CloudRunForm = {
  project: string;
  region: string;
  memory: string;
  cpu: string;
  min_instances: string;
  poll_schedule: string;
  skip_scheduler: boolean;
};

type CloudRunStatus = {
  ok?: boolean;
  gcloud?: {
    installed?: boolean;
    path?: string;
    version?: string;
    active_account?: string;
    project?: string;
    region?: string;
    authenticated?: boolean;
  };
  docker?: {
    installed?: boolean;
    path?: string;
    version?: string;
  };
  defaults?: {
    project?: string;
    region?: string;
    artifact_repo?: string;
  };
  commands?: Record<string, string>;
  errors?: string[];
};

type CloudRunDeployment = {
  id: string;
  agent_name: string;
  service_name: string;
  status: string;
  dry_run?: boolean;
  project?: string;
  region?: string;
  command?: string[];
  logs?: string[];
  return_code?: number | null;
  result?: {
    image_tag?: string;
    scheduler_job?: string;
    service_url?: string;
    health_url?: string;
    poll_url?: string;
  };
  error?: string;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
};

type SettlementsMeta = {
  agent_name?: string;
  foundry_agent_name?: string;
  matched_agent_names?: string[];
  total_available?: number;
  foundry_url?: string;
};

type CloudRunAuthSession = {
  id: string;
  status: string;
  auth_url?: string;
  logs?: string[];
  return_code?: number | null;
  error?: string;
  created_at?: string;
  updated_at?: string;
};

type RetireAgentResult = {
  ok: boolean;
  agent_name: string;
  foundry?: {
    ok?: boolean;
    message?: string;
    status?: string;
    upstream?: Record<string, unknown>;
  };
  cloud_run?: {
    ok?: boolean;
    actions?: Array<Record<string, unknown>>;
    targets?: Array<Record<string, unknown>>;
    error?: string;
  };
  local_agent?: LocalAgentRuntime | null;
};

const API_PORT = import.meta.env.VITE_API_PORT || "8090";
const DEFAULT_FOUNDRY_URL = "https://foundry.cochiper.com";
const DEFAULT_CLOUD_RUN_POLL_SCHEDULE = "*/5 * * * *";
const CUSTOM_FOUNDRY_PRESET_ID = "__custom__";
const FOUNDRY_URL_PRESETS = [
  { id: "cochiper-com", label: "CoChiper .com (CN)", url: "https://foundry.cochiper.com" },
  { id: "cochiper-ai", label: "CoChiper .ai (WW)", url: "https://foundry.cochiper.ai" },
] as const;
const CLOUD_RUN_REGION_OPTIONS = [
  { value: "us-central1", label: "US Central (Iowa)" },
  { value: "europe-west2", label: "UK (London)" },
  { value: "asia-east2", label: "Hong Kong" },
  { value: "asia-southeast1", label: "Singapore" },
] as const;

const DEFAULT_API_BASE =
  typeof window === "undefined"
    ? `http://127.0.0.1:${API_PORT}`
    : `${window.location.protocol}//${window.location.hostname}:${API_PORT}`;

const API_BASE = import.meta.env.VITE_API_BASE || DEFAULT_API_BASE;
const CC_LOGO_URL = "/brand/cc-logo.png";
const FOUNDRY_LOGO_URL = "/brand/cochiper-foundry.png";

const NAV_ITEMS: Array<{ id: BoardView; label: string; kicker: string }> = [
  { id: "guide", label: "Guided setup", kicker: "Create, onboard, and test end-to-end" },
  { id: "agent", label: "Agent card", kicker: "Runtime, model, and local agent management" },
  { id: "playground", label: "Local playground", kicker: "Debug a local runtime only" },
  { id: "earnings", label: "Earnings", kicker: "Settlements, payments, and provider references" },
  { id: "skills", label: "Skill Store", kicker: "Browse, install, and manage agent skills" },
  { id: "jobs", label: "Job Board", kicker: "Discover Foundry work and claim tasks" },
];

const AGENT_CARD_TABS: Array<{ id: AgentCardTab; label: string; helper: string }> = [
  { id: "overview", label: "Overview", helper: "Agent, mode, and quick status" },
  { id: "runtimes", label: "Agent sources", helper: "Create sources and local debug runtimes" },
  { id: "cloud-run", label: "Cloud Run", helper: "Deploy a source as a pull worker" },
  { id: "runtime", label: "Runtime LLM", helper: "Gateway source and dev overrides" },
  { id: "profile", label: "Profile", helper: "Manifest, skills, and template info" },
];

const DEFAULT_PLAYGROUND_MESSAGE = "What can you do?";

function textValue(value: unknown, fallback = ""): string {
  const rendered = String(value ?? "").trim();
  return rendered || fallback;
}

function normalizeUrl(rawUrl: string): string {
  const value = String(rawUrl || "").trim().replace(/\/+$/, "");
  if (!value) {
    return "";
  }
  if (value.includes("://")) {
    return value;
  }
  return `http://${value}`;
}

function foundryPresetId(rawUrl: string): string {
  const normalized = normalizeUrl(rawUrl).toLowerCase();
  if (!normalized) {
    return CUSTOM_FOUNDRY_PRESET_ID;
  }
  const preset = FOUNDRY_URL_PRESETS.find((item) => item.url.toLowerCase() === normalized);
  return preset?.id ?? CUSTOM_FOUNDRY_PRESET_ID;
}

function foundryPresetUrl(presetId: string): string {
  const preset = FOUNDRY_URL_PRESETS.find((item) => item.id === presetId);
  return preset?.url ?? "";
}

function displaySafeUrl(rawUrl: string, fallback = "n/a"): string {
  const normalized = normalizeUrl(rawUrl);
  if (!normalized) {
    return fallback;
  }
  try {
    const parsed = new URL(normalized);
    const hostname = parsed.hostname.trim().toLowerCase();
    const isPrivateIpv4 =
      hostname === "localhost" ||
      /^127\./.test(hostname) ||
      /^10\./.test(hostname) ||
      /^192\.168\./.test(hostname) ||
      /^172\.(1[6-9]|2\d|3[0-1])\./.test(hostname);

    if (!isPrivateIpv4) {
      return normalized;
    }

    const pathname = parsed.pathname && parsed.pathname !== "/" ? parsed.pathname.replace(/\/+$/, "") : "";
    const port = parsed.port ? `:${parsed.port}` : "";
    return `${parsed.protocol}//localhost${port}${pathname}`;
  } catch {
    return normalized;
  }
}

function boolValue(value: unknown): boolean {
  return value === true;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function numberFromRecord(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (value === null || value === undefined || value === "") {
      continue;
    }
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function settlementNumber(settlement: Record<string, unknown>, keys: string[], fallback = 0): number {
  const direct = numberFromRecord(settlement, keys);
  if (direct !== null) {
    return direct;
  }
  for (const nestedKey of ["settlement", "settlement_record", "verification_result"]) {
    const nested = objectValue(settlement[nestedKey]);
    const value = numberFromRecord(nested, keys);
    if (value !== null) {
      return value;
    }
  }
  return fallback;
}

function settlementNetAmount(settlement: Record<string, unknown>): number {
  return settlementNumber(settlement, ["net_payout_usd", "net_payout", "amount", "settlement_amount"], 0);
}

function settlementGrossAmount(settlement: Record<string, unknown>): number {
  const net = settlementNetAmount(settlement);
  return settlementNumber(settlement, ["gross_reward_usd", "gross_reward", "task_reward", "amount", "settlement_amount"], net);
}

function settlementResourceCost(settlement: Record<string, unknown>): number {
  return settlementNumber(settlement, ["resource_cost_usd", "total_resource_cost_usd", "resource_cost"], 0);
}

function formatDuration(startedAt: string, finishedAt = ""): string {
  const startMs = Date.parse(startedAt);
  if (!Number.isFinite(startMs)) {
    return "pending";
  }
  const endMs = finishedAt ? Date.parse(finishedAt) : Date.now();
  const elapsedSeconds = Math.max(0, Math.round((endMs - startMs) / 1000));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function FoundryUrlChooser({
  label,
  value,
  onChange,
  placeholder = DEFAULT_FOUNDRY_URL,
  helper = "",
}: {
  label: string;
  value: string;
  onChange: (nextValue: string) => void;
  placeholder?: string;
  helper?: string;
}) {
  return (
    <div className="foundry-url-picker">
      <div className="foundry-url-grid">
        <label>
          Quick target
          <select
            value={foundryPresetId(value)}
            onChange={(event) => {
              const presetUrl = foundryPresetUrl(event.target.value);
              if (presetUrl) {
                onChange(presetUrl);
              }
            }}
          >
            <option value={CUSTOM_FOUNDRY_PRESET_ID}>Custom / other</option>
            {FOUNDRY_URL_PRESETS.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label} ({preset.url})
              </option>
            ))}
          </select>
        </label>
        <label>
          {label}
          <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
        </label>
      </div>
      {helper ? <p className="muted foundry-url-helper">{helper}</p> : null}
    </div>
  );
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object") {
      const detail = (payload as { detail?: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
      if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
        return String((detail as { message: string }).message);
      }
      if (typeof (payload as { message?: unknown }).message === "string") {
        return String((payload as { message: string }).message);
      }
    }
    return JSON.stringify(payload);
  } catch {
    const text = await response.text();
    return text || `Request failed with status ${response.status}`;
  }
}

function expectArray<T>(value: unknown, label: string): T[] {
  if (Array.isArray(value)) {
    return value as T[];
  }
  throw new Error(`${label} returned an unexpected response shape.`);
}

function statusTone(status: string): "neutral" | "success" | "warn" {
  const normalized = status.trim().toUpperCase();
  if (["APPROVED", "REGISTERED", "REDEEMED", "MATCHED", "ONLINE", "TRUE", "ACTIVE", "SUCCEEDED", "SUCCESS"].includes(normalized)) {
    return "success";
  }
  if (["REVIEWING", "DISCOVERED", "ISSUED", "PENDING", "INLINE_REGISTER", "QUEUED", "RUNNING"].includes(normalized)) {
    return "warn";
  }
  return "neutral";
}

// ---------------------------------------------------------------------------
// Skill Store Panel component
// ---------------------------------------------------------------------------

type StoreSkill = {
  id: string;
  name: string;
  category: string;
  tags: string[];
  description: string;
  author: string;
  version: string;
};

type InstalledSkill = {
  id: string;
  name: string;
  description: string;
  has_slash_command: boolean;
  from_store?: boolean;
  store_version?: string;
};

function SkillStorePanel({
  apiBase,
  localAgents,
  selectedAgentName,
}: {
  apiBase: string;
  localAgents: Array<{ name: string; label: string; status?: string }>;
  selectedAgentName: string;
}) {
  const [storeSkills, setStoreSkills] = useState<StoreSkill[]>([]);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [targetAgent, setTargetAgent] = useState(selectedAgentName || "");
  const [installing, setInstalling] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const categories = useMemo(() => {
    const cats = new Set<string>();
    storeSkills.forEach((s) => { if (s.category) cats.add(s.category); });
    return ["all", ...Array.from(cats).sort()];
  }, [storeSkills]);

  const filteredSkills = useMemo(() => {
    if (selectedCategory === "all") return storeSkills;
    return storeSkills.filter((s) => s.category === selectedCategory);
  }, [storeSkills, selectedCategory]);

  const installedIds = useMemo(
    () => new Set(installedSkills.map((s) => s.id)),
    [installedSkills],
  );

  useEffect(() => {
    void loadStore();
  }, []);

  useEffect(() => {
    if (targetAgent) void loadAgentSkills(targetAgent);
  }, [targetAgent]);

  useEffect(() => {
    if (selectedAgentName && !targetAgent) setTargetAgent(selectedAgentName);
  }, [selectedAgentName]);

  async function loadStore() {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${apiBase}/api/skill-store`);
      if (!resp.ok) throw new Error(`Store fetch failed: ${resp.status}`);
      const data = await resp.json();
      setStoreSkills(Array.isArray(data) ? data : []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load store");
    } finally {
      setLoading(false);
    }
  }

  async function loadAgentSkills(agentName: string) {
    if (!agentName) { setInstalledSkills([]); return; }
    try {
      const resp = await fetch(`${apiBase}/api/local-agents/${encodeURIComponent(agentName)}/skills`);
      if (resp.ok) {
        const data = await resp.json();
        setInstalledSkills(Array.isArray(data) ? data : []);
      }
    } catch { /* ignore */ }
  }

  async function installSkill(skillId: string) {
    if (!targetAgent) { setMessage("⚠️ Select an agent first"); return; }
    setInstalling(skillId);
    setMessage("");
    try {
      const resp = await fetch(`${apiBase}/api/local-agents/${encodeURIComponent(targetAgent)}/skills/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_id: skillId }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Install failed" }));
        throw new Error(err.detail || "Install failed");
      }
      setMessage(`✅ Installed "${skillId}" on ${targetAgent}`);
      void loadAgentSkills(targetAgent);
    } catch (e: unknown) {
      setMessage(`❌ ${e instanceof Error ? e.message : "Install failed"}`);
    } finally {
      setInstalling(null);
    }
  }

  async function uninstallSkill(skillId: string) {
    if (!targetAgent) return;
    setInstalling(skillId);
    setMessage("");
    try {
      const resp = await fetch(
        `${apiBase}/api/local-agents/${encodeURIComponent(targetAgent)}/skills/${encodeURIComponent(skillId)}`,
        { method: "DELETE" },
      );
      if (!resp.ok) throw new Error("Uninstall failed");
      setMessage(`🗑️ Removed "${skillId}" from ${targetAgent}`);
      void loadAgentSkills(targetAgent);
    } catch (e: unknown) {
      setMessage(`❌ ${e instanceof Error ? e.message : "Uninstall failed"}`);
    } finally {
      setInstalling(null);
    }
  }

  const categoryIcons: Record<string, string> = {
    hardware: "🔧",
    infrastructure: "🏗️",
    payment: "💳",
    core: "🧠",
  };

  return (
    <>
      <div className="card-header">
        <div>
          <p className="eyebrow">Skills</p>
          <h2>Skill Store</h2>
        </div>
      </div>

      {/* Agent selector + status */}
      <div style={{ display: "flex", gap: "1rem", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap" }}>
        <label style={{ display: "flex", alignItems: "center", gap: ".5rem" }}>
          <span style={{ fontSize: ".85rem", opacity: .7 }}>Install to:</span>
          <select
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
            style={{ padding: ".3rem .5rem", borderRadius: "6px", background: "var(--surface)", border: "1px solid var(--border)", color: "inherit" }}
          >
            <option value="">— select agent —</option>
            {localAgents.map((a) => (
              <option key={a.name} value={a.name}>{a.label || a.name} ({a.status})</option>
            ))}
          </select>
        </label>

        {/* Category filter */}
        <div style={{ display: "flex", gap: ".3rem", flexWrap: "wrap" }}>
          {categories.map((cat) => (
            <button
              key={cat}
              className={`chip ${selectedCategory === cat ? "active" : ""}`}
              onClick={() => setSelectedCategory(cat)}
              style={{
                cursor: "pointer",
                background: selectedCategory === cat ? "var(--accent)" : "var(--surface)",
                color: selectedCategory === cat ? "#fff" : "inherit",
                border: "1px solid var(--border)",
                borderRadius: "999px",
                padding: ".2rem .6rem",
                fontSize: ".8rem",
                transition: "all .2s",
              }}
            >
              {categoryIcons[cat] || "📦"} {cat}
            </button>
          ))}
        </div>
      </div>

      {message ? (
        <div style={{ padding: ".5rem .75rem", borderRadius: "8px", background: "var(--surface)", marginBottom: "1rem", fontSize: ".85rem" }}>
          {message}
        </div>
      ) : null}

      {error ? (
        <div style={{ color: "var(--danger)", marginBottom: "1rem" }}>{error}</div>
      ) : null}

      {loading ? <p className="muted">Loading skill store…</p> : null}

      {/* Installed skills summary */}
      {targetAgent && installedSkills.length > 0 ? (
        <div style={{ marginBottom: "1.5rem" }}>
          <h3 style={{ fontSize: ".9rem", opacity: .7, marginBottom: ".5rem" }}>
            Installed on <strong>{targetAgent}</strong> ({installedSkills.length})
          </h3>
          <div style={{ display: "flex", gap: ".4rem", flexWrap: "wrap" }}>
            {installedSkills.map((s) => (
              <span key={s.id} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
                {s.name || s.id}
                <button
                  onClick={() => uninstallSkill(s.id)}
                  disabled={installing === s.id}
                  style={{
                    background: "none", border: "none", color: "var(--danger)", cursor: "pointer",
                    fontSize: ".75rem", padding: "0 2px", opacity: installing === s.id ? .3 : .7,
                  }}
                  title={`Uninstall ${s.id}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {/* No agent banner */}
      {!targetAgent && storeSkills.length > 0 ? (
        <div style={{
          padding: ".75rem 1rem",
          borderRadius: "10px",
          background: "linear-gradient(135deg, rgba(255,180,0,.12), rgba(255,120,0,.08))",
          border: "1px solid rgba(255,180,0,.25)",
          marginBottom: "1rem",
          fontSize: ".85rem",
          display: "flex",
          alignItems: "center",
          gap: ".5rem",
        }}>
          <span style={{ fontSize: "1.2rem" }}>☝️</span>
          <span>Select an agent above to install skills. Or <strong>create one</strong> from the Agent card tab first.</span>
        </div>
      ) : null}

      {/* Store skills grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
        {filteredSkills.map((skill) => {
          const isInstalled = installedIds.has(skill.id);
          return (
            <article
              key={skill.id}
              style={{
                border: isInstalled ? "1px solid var(--accent)" : "1px solid var(--border)",
                borderRadius: "12px",
                padding: "1.25rem",
                background: isInstalled ? "rgba(46, 196, 182, 0.08)" : "var(--surface)",
                transition: "transform .15s, box-shadow .15s",
                position: "relative",
                display: "flex",
                flexDirection: "column",
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.transform = "translateY(-2px)"; (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 16px rgba(0,0,0,.2)"; }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.transform = ""; (e.currentTarget as HTMLElement).style.boxShadow = ""; }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: ".5rem" }}>
                <div>
                  <h4 style={{ margin: 0, fontSize: "1.05rem" }}>
                    {categoryIcons[skill.category] || "📦"} {skill.name}
                  </h4>
                  <span style={{ fontSize: ".75rem", opacity: .5 }}>v{skill.version} · {skill.author}</span>
                </div>
                {isInstalled ? (
                  <span style={{
                    fontSize: ".7rem", padding: ".2rem .5rem", borderRadius: "6px",
                    background: "var(--accent)", color: "#fff", fontWeight: 600,
                  }}>
                    ✓ installed
                  </span>
                ) : null}
              </div>
              <p style={{ fontSize: ".85rem", margin: ".5rem 0", opacity: .8, lineHeight: 1.5, flex: 1 }}>
                {skill.description}
              </p>
              <div style={{ display: "flex", gap: ".3rem", flexWrap: "wrap", marginBottom: ".75rem" }}>
                {skill.tags.map((tag) => (
                  <span
                    key={tag}
                    style={{
                      fontSize: ".7rem",
                      padding: ".1rem .45rem",
                      borderRadius: "999px",
                      background: "rgba(255,255,255,.06)",
                      border: "1px solid rgba(255,255,255,.1)",
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <button
                onClick={() => {
                  if (!targetAgent) {
                    setMessage("☝️ Please select an agent from the dropdown above first!");
                    return;
                  }
                  isInstalled ? uninstallSkill(skill.id) : installSkill(skill.id);
                }}
                disabled={installing === skill.id}
                style={{
                  width: "100%",
                  padding: ".55rem .75rem",
                  borderRadius: "8px",
                  border: isInstalled ? "1px solid var(--danger)" : "none",
                  background: isInstalled
                    ? "transparent"
                    : "linear-gradient(135deg, var(--accent), #1a9b8f)",
                  color: isInstalled ? "var(--danger)" : "#fff",
                  cursor: installing === skill.id ? "not-allowed" : "pointer",
                  fontSize: ".9rem",
                  fontWeight: 600,
                  opacity: installing === skill.id ? .5 : 1,
                  transition: "all .2s",
                  letterSpacing: ".02em",
                }}
              >
                {installing === skill.id
                  ? "⏳ Working…"
                  : isInstalled
                    ? "🗑 Uninstall"
                    : "⬇ Install"
                }
              </button>
            </article>
          );
        })}
      </div>

      {!loading && filteredSkills.length === 0 ? (
        <p className="muted">No skills found in this category.</p>
      ) : null}
    </>
  );
}


// ─── Job Board Panel ─────────────────────────────────────────────
type FoundryJob = {
  id: string;
  name?: string;
  description?: string;
  label?: string;
  budget?: { max_cost_per_call_usd?: number; ceiling_usd?: number; amount?: number };
  tags?: string[];
  status?: string;
  created_at?: string;
  requirements?: Record<string, unknown>;
  execution_contract?: Record<string, unknown>;
  resource_requests?: Record<string, unknown>;
  payment_criteria?: string;
  payment_terms?: Record<string, unknown>;
  [key: string]: unknown;
};

function JobBoardPanel({
  apiBase,
  localAgents,
  selectedAgentName,
  foundryUrl: initialFoundryUrl,
}: {
  apiBase: string;
  localAgents: Array<{ name: string; label: string; status?: string; base_url?: string; port?: number }>;
  selectedAgentName: string;
  foundryUrl: string;
}) {
  const [foundryUrl, setFoundryUrl] = useState(initialFoundryUrl || "https://foundry.cochiper.com");
  const [foundryToken, setFoundryToken] = useState("");
  const [jobs, setJobs] = useState<FoundryJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [claiming, setClaiming] = useState<string | null>(null);
  const [claimMessage, setClaimMessage] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [targetAgent, setTargetAgent] = useState(selectedAgentName);
  const [showAuthSection, setShowAuthSection] = useState(false);
  const [executing, setExecuting] = useState<string | null>(null);
  const [execResult, setExecResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => { setTargetAgent(selectedAgentName); }, [selectedAgentName]);

  const fetchJobs = async () => {
    if (!foundryUrl.trim()) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/foundry/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ foundry_url: foundryUrl, foundry_token: foundryToken }),
      });
      const data = await res.json();
      if (data.error) setError(data.error);
      setJobs(Array.isArray(data.jobs) ? data.jobs : []);
      setLastRefreshed(new Date());
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void fetchJobs(); }, [foundryUrl]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(fetchJobs, 15000);
    return () => clearInterval(interval);
  }, [autoRefresh, foundryUrl]);

  const claimJob = async (jobId: string) => {
    if (!targetAgent) {
      setClaimMessage("☝️ Please select an agent first!");
      return;
    }
    setClaiming(jobId);
    setClaimMessage("");
    try {
      const res = await fetch(`${apiBase}/api/foundry/jobs/${jobId}/claim`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          foundry_url: foundryUrl,
          agent_name: targetAgent,
          foundry_token: foundryToken,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setClaimMessage(`✅ ${targetAgent} claimed job successfully!`);
      } else {
        setClaimMessage(`❌ Claim failed: ${data.error || "Unknown error"}`);
      }
    } catch (err) {
      setClaimMessage(`❌ ${String(err)}`);
    } finally {
      setClaiming(null);
    }
  };

  const autoExecuteJob = async (job: FoundryJob) => {
    const agent = localAgents.find(a => a.name === targetAgent) || localAgents[0];
    if (!agent) { setClaimMessage("No agent selected."); return; }
    setExecuting(job.id);
    setExecResult(null);
    setExpandedJob(job.id);
    try {
      const agentUrl = agent.base_url || `http://127.0.0.1:${agent.port || 8088}`;
      const resp = await fetch(`${API_BASE}/api/bounty/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          foundry_url: foundryUrl,
          agent_url: agentUrl,
          job_id: job.id,
          job_name: getJobTitle(job),
          dry_run: false,
        }),
      });
      const data = await resp.json();
      setExecResult(data);
      if (data.ok) {
        setClaimMessage(`✅ ${agent.name} completed: ${(data.deliverable?.status || "done")}`);
      } else {
        setClaimMessage(`❌ Execution failed: ${data.error || "unknown"}`);
      }
    } catch (e: unknown) {
      setClaimMessage(`❌ Execute error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExecuting(null);
    }
  };

  const getBudgetDisplay = (job: FoundryJob): string => {
    const b = job.budget;
    // 1. Check structured budget fields
    const budgetAmt = b ? (b.ceiling_usd ?? b.max_cost_per_call_usd ?? b.amount) : undefined;
    if (budgetAmt !== undefined && budgetAmt !== null) return `$${Number(budgetAmt).toFixed(2)}`;
    // 2. Check payment_terms.hard_ceiling_per_call
    const pt = job.payment_terms as Record<string, unknown> | undefined;
    const ceiling = pt?.hard_ceiling_per_call;
    if (ceiling !== undefined && ceiling !== null) return `$${Number(ceiling).toFixed(2)}`;
    // 3. Parse dollar amounts from payment_criteria text (e.g. "$1 hard ceiling")
    const pc = job.payment_criteria || "";
    const match = pc.match(/\$(\d+(?:\.\d+)?)/);
    if (match) return `$${Number(match[1]).toFixed(2)}`;
    return "—";
  };

  const getJobTitle = (job: FoundryJob): string =>
    job.name || job.label || job.id?.slice(0, 12) || "Untitled";

  const getJobDescription = (job: FoundryJob): string =>
    job.description
      || job.payment_criteria
      || (typeof (job as Record<string, unknown>)["brief"] === "string"
        ? String((job as Record<string, unknown>)["brief"])
        : "No description provided.");

  const statusColors: Record<string, string> = {
    active: "#2ec4b6",
    open: "#2ec4b6",
    pending: "#ffb400",
    closed: "#e63946",
    fulfilled: "#888",
  };

  return (
    <>
      <p className="eyebrow">Marketplace</p>
      <h2>🔍 Job Board</h2>
      <p className="muted" style={{ marginBottom: "1.25rem" }}>
        Discover open work from a Foundry instance. Your agent can claim tasks and inspect settlement outcomes.
      </p>

      {/* Foundry URL + Controls */}
      <div style={{ display: "flex", gap: ".5rem", marginBottom: "1rem", flexWrap: "wrap", alignItems: "center" }}>
        <input
          type="text"
          value={foundryUrl}
          onChange={(e) => setFoundryUrl(e.target.value)}
          placeholder="https://foundry.cochiper.com"
          style={{
            flex: "1 1 300px", padding: ".5rem .75rem", borderRadius: "8px",
            border: "1px solid var(--border)", background: "var(--surface)",
            color: "inherit", fontSize: ".9rem",
          }}
        />
        <button
          onClick={fetchJobs}
          disabled={loading}
          style={{
            padding: ".5rem 1rem", borderRadius: "8px", border: "none",
            background: "linear-gradient(135deg, var(--accent), #1a9b8f)",
            color: "#fff", cursor: loading ? "not-allowed" : "pointer",
            fontSize: ".85rem", fontWeight: 600, opacity: loading ? .6 : 1,
          }}
        >
          {loading ? "⏳ Loading…" : "🔄 Refresh"}
        </button>
        <label style={{ display: "flex", alignItems: "center", gap: ".3rem", fontSize: ".8rem", opacity: .8 }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            style={{ accentColor: "var(--accent)" }}
          />
          Auto (15s)
        </label>
      </div>

      {/* Auth section (collapsible) */}
      <div style={{ marginBottom: "1rem" }}>
        <button
          onClick={() => setShowAuthSection(!showAuthSection)}
          style={{
            background: "none", border: "none", color: "var(--accent)",
            cursor: "pointer", fontSize: ".8rem", padding: "0",
            display: "flex", alignItems: "center", gap: ".3rem",
          }}
        >
          {showAuthSection ? "▼" : "▶"} 🔑 Foundry Authentication
          {foundryToken ? <span style={{ color: "#2ec4b6" }}> ✓ token set</span> : null}
        </button>
        {showAuthSection ? (
          <div style={{
            marginTop: ".5rem", padding: ".75rem", borderRadius: "8px",
            background: "rgba(255,255,255,.03)", border: "1px solid var(--border)",
          }}>
            <label style={{ fontSize: ".8rem", opacity: .7, display: "block", marginBottom: ".3rem" }}>
              Foundry access token
            </label>
            <input
              type="password"
              value={foundryToken}
              onChange={(e) => setFoundryToken(e.target.value)}
              placeholder="eyJhbGciOiJIUzI1NiIs..."
              style={{
                width: "100%", padding: ".4rem .6rem", borderRadius: "8px",
                border: "1px solid var(--border)", background: "var(--surface)",
                color: "inherit", fontSize: ".8rem", fontFamily: "monospace",
              }}
            />
            <p style={{ fontSize: ".7rem", opacity: .5, margin: ".4rem 0 0" }}>
              Paste a token from your Foundry login. Some hosts require it for non-public work listings.
              You can get this from the Foundry UI after login, then API Token.
            </p>
          </div>
        ) : null}
      </div>

      {/* Agent selector */}
      <div style={{ display: "flex", gap: ".5rem", marginBottom: "1rem", alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: ".85rem", opacity: .7 }}>Claim as:</span>
        <select
          value={targetAgent}
          onChange={(e) => setTargetAgent(e.target.value)}
          style={{
            padding: ".4rem .6rem", borderRadius: "8px", border: "1px solid var(--border)",
            background: "var(--surface)", color: "inherit", fontSize: ".85rem",
          }}
        >
          <option value="">— select agent —</option>
          {localAgents.map((a) => (
            <option key={a.name} value={a.name}>
              {a.name} {a.status === "running" ? "🟢" : "⚪"}
            </option>
          ))}
        </select>
        {lastRefreshed ? (
          <span style={{ fontSize: ".75rem", opacity: .5, marginLeft: "auto" }}>
            Last: {lastRefreshed.toLocaleTimeString()}
            {autoRefresh ? " · auto-refreshing" : ""}
          </span>
        ) : null}
      </div>

      {/* Messages */}
      {error ? (
        <div style={{
          padding: ".75rem 1rem", borderRadius: "10px", marginBottom: "1rem",
          background: "rgba(230, 57, 70, .1)", border: "1px solid rgba(230, 57, 70, .3)",
          fontSize: ".85rem",
        }}>
          ⚠️ {error}
        </div>
      ) : null}

      {claimMessage ? (
        <div style={{
          padding: ".75rem 1rem", borderRadius: "10px", marginBottom: "1rem",
          background: claimMessage.startsWith("✅")
            ? "rgba(46, 196, 182, .1)"
            : claimMessage.startsWith("☝")
              ? "rgba(255, 180, 0, .1)"
              : "rgba(230, 57, 70, .1)",
          border: `1px solid ${claimMessage.startsWith("✅") ? "rgba(46, 196, 182, .3)" : claimMessage.startsWith("☝") ? "rgba(255,180,0,.3)" : "rgba(230,57,70,.3)"}`,
          fontSize: ".85rem",
        }}>
          {claimMessage}
        </div>
      ) : null}

      {/* Jobs count */}
      {!loading && jobs.length > 0 ? (
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: "1rem" }}>
          <span style={{
            display: "inline-block", padding: ".2rem .6rem", borderRadius: "999px",
            background: "linear-gradient(135deg, var(--accent), #1a9b8f)", color: "#fff",
            fontSize: ".8rem", fontWeight: 600,
          }}>
            {jobs.length}
          </span>
          <span style={{ fontSize: ".9rem" }}>
            open {jobs.length === 1 ? "work item" : "work items"} available
          </span>
        </div>
      ) : null}

      {/* Jobs grid */}
      <div style={{ display: "flex", flexDirection: "column", gap: ".75rem" }}>
        {jobs.map((job) => {
          const isExpanded = expandedJob === job.id;
          const status = String(job.status || "open").toLowerCase();
          const statusColor = statusColors[status] || "#999";
          return (
            <article
              key={job.id}
              style={{
                border: "1px solid var(--border)",
                borderRadius: "12px",
                padding: "1.25rem",
                background: "var(--surface)",
                transition: "transform .15s, box-shadow .15s, border-color .15s",
                borderLeft: `4px solid ${statusColor}`,
                cursor: "pointer",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.transform = "translateY(-1px)";
                (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 16px rgba(0,0,0,.2)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.transform = "";
                (e.currentTarget as HTMLElement).style.boxShadow = "";
              }}
              onClick={() => setExpandedJob(isExpanded ? null : job.id)}
            >
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: ".5rem" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".25rem" }}>
                    <h4 style={{ margin: 0, fontSize: "1.05rem" }}>
                      💼 {getJobTitle(job)}
                    </h4>
                    <span style={{
                      fontSize: ".65rem", padding: ".15rem .45rem", borderRadius: "6px",
                      background: statusColor, color: "#fff", fontWeight: 600,
                      textTransform: "uppercase", letterSpacing: ".05em",
                    }}>
                      {status}
                    </span>
                  </div>
                  <span style={{ fontSize: ".75rem", opacity: .5 }}>
                    ID: {job.id?.slice(0, 12)}…
                    {job.created_at ? ` · Created: ${new Date(job.created_at).toLocaleDateString()}` : ""}
                  </span>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{
                    fontSize: "1.3rem", fontWeight: 700,
                    background: "linear-gradient(135deg, #2ec4b6, #1a9b8f)",
                    WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent",
                  }}>
                    {getBudgetDisplay(job)}
                  </div>
                  <span style={{ fontSize: ".7rem", opacity: .5 }}>budget ceiling</span>
                </div>
              </div>

              {/* Description */}
              <p style={{
                fontSize: ".85rem", margin: ".5rem 0", opacity: .8, lineHeight: 1.5,
                maxHeight: isExpanded ? "none" : "2.8em",
                overflow: isExpanded ? "visible" : "hidden",
              }}>
                {getJobDescription(job)}
              </p>

              {/* Tags */}
              {Array.isArray(job.tags) && job.tags.length > 0 ? (
                <div style={{ display: "flex", gap: ".3rem", flexWrap: "wrap", marginBottom: ".75rem" }}>
                  {job.tags.map((tag) => (
                    <span key={tag} style={{
                      fontSize: ".7rem", padding: ".1rem .45rem", borderRadius: "999px",
                      background: "rgba(255,255,255,.06)", border: "1px solid rgba(255,255,255,.1)",
                    }}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}

              {/* Payment Criteria (always shown if present) */}
              {job.payment_criteria ? (
                <div style={{
                  margin: ".5rem 0 .75rem", padding: ".6rem .8rem", borderRadius: "8px",
                  background: "rgba(255, 180, 0, .08)", border: "1px solid rgba(255, 180, 0, .2)",
                  fontSize: ".82rem", lineHeight: 1.6,
                }}>
                  <strong style={{ fontSize: ".7rem", textTransform: "uppercase", letterSpacing: ".04em", opacity: .7 }}>
                    📋 Requirements & Criteria
                  </strong>
                  <div style={{ marginTop: ".3rem", whiteSpace: "pre-wrap" }}>
                    {job.payment_criteria}
                  </div>
                </div>
              ) : null}

              {/* Expanded details */}
              {isExpanded ? (
                <div style={{
                  marginTop: ".75rem", padding: ".75rem", borderRadius: "8px",
                  background: "rgba(255,255,255,.03)", border: "1px solid var(--border)",
                  fontSize: ".8rem", lineHeight: 1.6,
                }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: ".5rem" }}>
                    {job.execution_contract ? (
                      <div>
                        <strong style={{ opacity: .6 }}>Execution Contract</strong>
                        <pre style={{ margin: ".25rem 0", fontSize: ".7rem", opacity: .7, whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(job.execution_contract, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                    {job.resource_requests ? (
                      <div>
                        <strong style={{ opacity: .6 }}>Resource Requests</strong>
                        <pre style={{ margin: ".25rem 0", fontSize: ".7rem", opacity: .7, whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(job.resource_requests, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                    {job.requirements && Object.keys(job.requirements).length > 0 ? (
                      <div style={{ gridColumn: "1 / -1" }}>
                        <strong style={{ opacity: .6 }}>Requirements</strong>
                        <pre style={{ margin: ".25rem 0", fontSize: ".7rem", opacity: .7, whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(job.requirements, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                    {job.payment_terms && Object.keys(job.payment_terms).length > 0 ? (
                      <div>
                        <strong style={{ opacity: .6 }}>Payment Terms</strong>
                        <pre style={{ margin: ".25rem 0", fontSize: ".7rem", opacity: .7, whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(job.payment_terms, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                  <div style={{ marginTop: ".5rem", opacity: .5, fontSize: ".7rem" }}>
                    Full ID: {job.id}
                  </div>
                </div>
              ) : null}

              {/* Claim + Execute buttons */}
              <div style={{ display: "flex", gap: ".5rem", marginTop: ".75rem", alignItems: "center", flexWrap: "wrap" }}>
                <button
                  onClick={(e) => { e.stopPropagation(); void claimJob(job.id); }}
                  disabled={claiming === job.id || status === "closed" || status === "fulfilled"}
                  style={{
                    padding: ".5rem 1.25rem", borderRadius: "8px", border: "none",
                    background: status === "closed" || status === "fulfilled"
                      ? "rgba(255,255,255,.08)"
                      : "linear-gradient(135deg, #ffb400, #ff8c00)",
                    color: status === "closed" || status === "fulfilled" ? "#666" : "#fff",
                    cursor: claiming === job.id || status === "closed" ? "not-allowed" : "pointer",
                    fontSize: ".85rem", fontWeight: 600,
                    opacity: claiming === job.id ? .5 : 1,
                    transition: "all .2s",
                  }}
                >
                  {claiming === job.id
                    ? "⏳ Claiming…"
                    : status === "fulfilled"
                      ? "✓ Fulfilled"
                      : status === "closed"
                        ? "🚫 Closed"
                        : "🤚 Claim This Job"
                  }
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); void autoExecuteJob(job); }}
                  disabled={executing === job.id || status === "closed" || status === "fulfilled"}
                  style={{
                    padding: ".5rem 1.25rem", borderRadius: "8px", border: "none",
                    background: executing === job.id
                      ? "linear-gradient(135deg, #9b59b6, #8e44ad)"
                      : "linear-gradient(135deg, #2ec4b6, #1a9b8f)",
                    color: "#fff",
                    cursor: executing === job.id ? "wait" : "pointer",
                    fontSize: ".85rem", fontWeight: 600,
                    opacity: executing === job.id ? .7 : 1,
                    transition: "all .2s",
                    animation: executing === job.id ? "pulse 1.5s ease-in-out infinite" : "none",
                  }}
                >
                  {executing === job.id ? "⚙️ Executing…" : "🤖 Auto Execute"}
                </button>
                <span style={{ fontSize: ".75rem", opacity: .5 }}>
                  {isExpanded ? "▲ click to collapse" : "▼ click for details"}
                </span>
              </div>

              {/* Execution Result */}
              {execResult && expandedJob === job.id ? (() => {
                const er = execResult as Record<string, unknown>;
                const dl = er.deliverable as Record<string, unknown> | undefined;
                const steps = er.steps as Record<string, unknown>[] | undefined;
                const statusColor = dl?.status === "verified" ? "#2ec4b6"
                  : dl?.status === "code_ready" ? "#4ea8de" : "#ffb400";
                const statusEmoji = dl?.status === "verified" ? "✅"
                  : dl?.status === "code_ready" ? "📦" : "⚠️";
                return (
                <div style={{
                  marginTop: ".75rem", padding: ".75rem", borderRadius: "8px",
                  background: er.ok ? "rgba(46, 196, 182, .08)" : "rgba(255, 100, 100, .08)",
                  border: `1px solid ${er.ok ? "rgba(46,196,182,.3)" : "rgba(255,100,100,.3)"}`,
                  fontSize: ".82rem", lineHeight: 1.6,
                }}>
                  <strong style={{ fontSize: ".7rem", textTransform: "uppercase", letterSpacing: ".04em" }}>
                    {er.ok ? "✅ Execution Result" : "❌ Execution Failed"}
                  </strong>
                  {dl ? (
                    <div style={{ marginTop: ".4rem" }}>
                      <div>Module: <strong>{dl.module_name as string}</strong></div>
                      <div>Status: <strong style={{ color: statusColor }}>
                        {statusEmoji} {dl.status as string}
                      </strong></div>
                      <div>Files: {(dl.files_delivered as string[])?.join(", ")}</div>
                      {dl.llm_used ? <div style={{ opacity: .7 }}>🧠 LLM generated code</div> : null}
                    </div>
                  ) : null}
                  {Array.isArray(steps) ? (
                    <div style={{ marginTop: ".5rem" }}>
                      <strong style={{ fontSize: ".7rem", opacity: .6 }}>Steps:</strong>
                      {steps.map((step, i) => {
                        const isSkipped = step.sandbox_skipped as boolean;
                        const icon = step.step === "simulate" && step.tests_passed
                          ? "✅" : isSkipped ? "⚠️" : step.error ? "❌" : "▶️";
                        return (
                        <div key={i} style={{ marginLeft: ".5rem", fontSize: ".75rem", opacity: .8, marginTop: ".2rem" }}>
                          {icon} <strong>{step.step as string}</strong>
                          {step.output
                            ? <pre style={{ margin: ".2rem 0", fontSize: ".7rem", opacity: .7, whiteSpace: "pre-wrap", maxHeight: "120px", overflow: "auto" }}>
                                {String(step.output).slice(0, 500)}
                              </pre>
                            : null
                          }
                          {step.error
                            ? <span style={{ color: isSkipped ? "#ffb400" : "#ff6464" }}> {String(step.error).slice(0, 200)}</span>
                            : null
                          }
                        </div>
                        );
                      })}
                    </div>
                  ) : null}
                  {dl?.code_preview ? (
                    <details style={{ marginTop: ".5rem" }}>
                      <summary style={{ cursor: "pointer", fontSize: ".7rem", fontWeight: 600, opacity: .6 }}>
                        📄 Code Preview ({Object.keys(dl.code_preview as Record<string, string>).join(", ")})
                      </summary>
                      {Object.entries(dl.code_preview as Record<string, string>).map(([fname, code]) => (
                        <pre key={fname} style={{
                          margin: ".3rem 0", padding: ".5rem", borderRadius: "6px",
                          background: "rgba(0,0,0,.15)", fontSize: ".65rem",
                          whiteSpace: "pre-wrap", maxHeight: "200px", overflow: "auto",
                        }}>
                          <strong>// {fname}</strong>{"\n"}{code}
                        </pre>
                      ))}
                    </details>
                  ) : null}
                </div>
                );
              })() : null}
            </article>
          );
        })}
      </div>

      {/* Empty state */}
      {!loading && jobs.length === 0 && !error ? (
        <div style={{
          textAlign: "center", padding: "3rem 1rem", opacity: .6,
        }}>
          <div style={{ fontSize: "3rem", marginBottom: ".5rem" }}>🔍</div>
          <p style={{ fontSize: "1rem" }}>No open work found on this Foundry.</p>
          <p style={{ fontSize: ".85rem", opacity: .6 }}>
            Try a different Foundry URL or check back later.
          </p>
        </div>
      ) : null}

      {loading && jobs.length === 0 ? (
        <div style={{ textAlign: "center", padding: "2rem", opacity: .5, fontSize: "1rem" }}>
          ⏳ Searching for work…
        </div>
      ) : null}
    </>
  );
}


export default function App() {
  const [agents, setAgents] = useState<AgentEntry[]>([]);
  const [localAgents, setLocalAgents] = useState<LocalAgentRuntime[]>([]);
  const [localTemplates, setLocalTemplates] = useState<LocalAgentTemplate[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [mode, setMode] = useState<ContextMode>("direct");
  const [username, setUsername] = useState("local-user");
  const [message, setMessage] = useState(DEFAULT_PLAYGROUND_MESSAGE);
  const [conversationId, setConversationId] = useState("");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [reply, setReply] = useState("");
  const [steps, setSteps] = useState<StepEntry[]>([]);
  const [latestMetadata, setLatestMetadata] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeView, setActiveView] = useState<BoardView>("guide");
  const [agentCardTab, setAgentCardTab] = useState<AgentCardTab>("overview");
  const [devModeEnabled, setDevModeEnabled] = useState(false);
  const [devOverrides, setDevOverrides] = useState<DevOverrides>({
    model: "",
    base_url: "",
    api_key: "",
  });
  const [foundryUrl, setFoundryUrl] = useState(DEFAULT_FOUNDRY_URL);
  const [handshakeLoading, setHandshakeLoading] = useState(false);
  const [handshakeError, setHandshakeError] = useState("");
  const [handshakeResult, setHandshakeResult] = useState<HandshakeProbeResult | null>(null);
  const [developerLoading, setDeveloperLoading] = useState(false);
  const [developerError, setDeveloperError] = useState("");
  const [developerContext, setDeveloperContext] = useState<DeveloperContextResult | null>(null);
  const [ticketLoading, setTicketLoading] = useState(false);
  const [ticketError, setTicketError] = useState("");
  const [developerTicket, setDeveloperTicket] = useState<DeveloperTicketResult | null>(null);
  const [notificationEmail, setNotificationEmail] = useState("");
  const [notificationEnabled, setNotificationEnabled] = useState(true);
  const [notificationLoading, setNotificationLoading] = useState(false);
  const [notificationError, setNotificationError] = useState("");
  const [notificationStatus, setNotificationStatus] = useState("");
  const [oauthLoading, setOauthLoading] = useState(false);
  const [foundryBootstrapSession, setFoundryBootstrapSession] = useState<FoundryBootstrapSession | null>(null);
  const [localAgentLoading, setLocalAgentLoading] = useState(false);
  const [localAgentError, setLocalAgentError] = useState("");
  const [retiringAgent, setRetiringAgent] = useState("");
  const [settlements, setSettlements] = useState<Array<Record<string, unknown>>>([]);
  const [settlementsMeta, setSettlementsMeta] = useState<SettlementsMeta>({});
  const [settlementsLoading, setSettlementsLoading] = useState(false);
  const [settlementsError, setSettlementsError] = useState("");
  const [earningsAgentFilter, setEarningsAgentFilter] = useState("__all__");
  const [localAgentNotice, setLocalAgentNotice] = useState("");
  const [guideRunTarget, setGuideRunTarget] = useState<GuideRunTarget>("local");
  const [foundryPortalOpened, setFoundryPortalOpened] = useState(false);
  const [developerForm, setDeveloperForm] = useState<DeveloperForm>({
    developer_token: "",
    github_token: "",
    bootstrap_delivery: "poll",
    force_rediscover: true,
  });
  const [createAgentForm, setCreateAgentForm] = useState<CreateAgentForm>({
    template_id: "me_agent",
    name: "",
    label: "",
    preferred_port: "",
  });
  const [cloudRunStatus, setCloudRunStatus] = useState<CloudRunStatus | null>(null);
  const [cloudRunStatusLoading, setCloudRunStatusLoading] = useState(false);
  const [cloudRunError, setCloudRunError] = useState("");
  const [cloudRunDeployments, setCloudRunDeployments] = useState<CloudRunDeployment[]>([]);
  const [cloudRunCurrentJob, setCloudRunCurrentJob] = useState<CloudRunDeployment | null>(null);
  const [cloudRunDeploying, setCloudRunDeploying] = useState(false);
  const [cloudRunAuthSession, setCloudRunAuthSession] = useState<CloudRunAuthSession | null>(null);
  const [cloudRunAuthLoading, setCloudRunAuthLoading] = useState(false);
  const [cloudRunAuthCode, setCloudRunAuthCode] = useState("");
  const [cloudRunForm, setCloudRunForm] = useState<CloudRunForm>({
    project: "",
    region: "us-central1",
    memory: "512Mi",
    cpu: "1",
    min_instances: "0",
    poll_schedule: DEFAULT_CLOUD_RUN_POLL_SCHEDULE,
    skip_scheduler: false,
  });

  useEffect(() => {
    if (!selectedAgent) {
      return;
    }
    resetConversation();
    setHandshakeResult(null);
    setHandshakeError("");
    setDeveloperTicket(null);
    setTicketError("");
    setEarningsAgentFilter("__all__");
    void fetchSettlements();
    void probeHandshake(selectedAgent, foundryUrl);
    void refreshDeveloperContext(foundryUrl);
    void loadNotificationPreferences(selectedAgent);
  }, [selectedAgent]);

  useEffect(() => {
    void refreshAgentInventory();
    void refreshDeveloperContext(foundryUrl);
    void fetchSettlements();
    void refreshCloudRunStatus();
    void refreshCloudRunDeployments();
  }, []);

  useEffect(() => {
    if (!cloudRunCurrentJob || !["queued", "running"].includes(textValue(cloudRunCurrentJob.status))) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshCloudRunDeployment(cloudRunCurrentJob.id);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [cloudRunCurrentJob?.id, cloudRunCurrentJob?.status]);

  useEffect(() => {
    if (!cloudRunAuthSession || !["starting", "running"].includes(textValue(cloudRunAuthSession.status))) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshCloudRunAuthSession(cloudRunAuthSession.id);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [cloudRunAuthSession?.id, cloudRunAuthSession?.status]);

  const selectedAgentEntry = useMemo(
    () => agents.find((agent) => agent.name === selectedAgent) ?? null,
    [agents, selectedAgent],
  );
  const selectedLocalAgent = useMemo(
    () => localAgents.find((agent) => agent.name === selectedAgent) ?? null,
    [localAgents, selectedAgent],
  );

  const selectedManifest = selectedAgentEntry?.manifest ?? null;
  const capabilities = Array.isArray(selectedManifest?.capabilities) ? selectedManifest.capabilities : [];
  const loadedSkills = Array.isArray(selectedManifest?.loaded_skills) ? selectedManifest.loaded_skills : [];

  const hasDevOverrides = useMemo(
    () => Object.values(devOverrides).some((value) => value.trim().length > 0),
    [devOverrides],
  );

  const bootstrapState = handshakeResult?.agent.bootstrap_state ?? {};
  const bootstrapEnabled = boolValue(bootstrapState.enabled);
  const bootstrapHasGatewayKey = boolValue(bootstrapState.has_llm_api_key);
  const bootstrapHasGatewayBase = boolValue(bootstrapState.has_llm_api_base);
  const bootstrapHasClaim = boolValue(bootstrapState.has_discovery_claim);
  const bootstrapDelivery = textValue(bootstrapState.bootstrap_delivery, "push");
  const gatewayReady = bootstrapHasGatewayKey && bootstrapHasGatewayBase;
  const latestUsedLlm = boolValue(latestMetadata.used_llm);
  const latestOverrideActive = boolValue(latestMetadata.llm_override_active);
  const latestModel = textValue(latestMetadata.model);
  const latestModelSource = textValue(latestMetadata.model_source);
  const latestLlmError = textValue(latestMetadata.llm_error);
  const manifestNeedsGateway = boolValue(selectedManifest?.manifest?.llm?.needs_gateway);
  const manifestBillingModel = textValue(selectedManifest?.billing?.model);
  const developerGit = developerContext?.git ?? {};
  const developerGithub = developerContext?.github ?? {};
  const developerIdentity = developerContext?.developer_identity ?? {};
  const explicitFoundryUrl = normalizeUrl(foundryUrl);
  const normalizedFoundryUrl = explicitFoundryUrl || normalizeUrl(textValue(developerContext?.foundry?.url));
  const foundryPortalUrl =
    normalizeUrl(
      textValue(
        explicitFoundryUrl,
        textValue(
          foundryBootstrapSession?.foundry_url,
          textValue(handshakeResult?.foundry.url, normalizedFoundryUrl || DEFAULT_FOUNDRY_URL),
        ),
      ),
    ) || DEFAULT_FOUNDRY_URL;
  const foundryPortalDisplayUrl = displaySafeUrl(foundryPortalUrl);
  const displayedDeveloperLogin = textValue(
    foundryBootstrapSession?.github_login,
    textValue(developerGithub.login, "not detected"),
  );
  const developerSessionReady = Boolean(developerForm.developer_token.trim() || foundryBootstrapSession);
  const developerAuthReady = developerSessionReady || boolValue(developerGithub.has_token);
  const foundryBrowserSessionReady = Boolean(foundryBootstrapSession);
  const claimInstalled = bootstrapHasClaim || boolValue(developerTicket?.claim_applied) || Boolean(textValue(bootstrapState.last_claimed_at));
  const approvalObserved = Boolean(textValue(bootstrapState.approved_at));
  const registrationStatus = textValue(bootstrapState.registration_status).toUpperCase();
  const discoveryStatus = textValue(bootstrapState.discovery_status).toUpperCase();
  const onboardingApproved = approvalObserved || registrationStatus === "APPROVED" || gatewayReady;
  const onboardingRetired = registrationStatus === "RETIRED";
  const playgroundReady = Boolean(selectedAgentEntry && selectedAgentEntry.status === "online");
  const hasConversation = transcript.length > 0 || reply.trim().length > 0;
  const selectedCloudRunDeployment = useMemo(
    () => cloudRunDeployments.find((job) => job.agent_name === selectedAgent) ?? null,
    [cloudRunDeployments, selectedAgent],
  );
  const displayedCloudRunDeployment =
    cloudRunCurrentJob?.agent_name === selectedAgent ? cloudRunCurrentJob : selectedCloudRunDeployment;
  const cloudRunDeploymentSucceeded = textValue(displayedCloudRunDeployment?.status) === "succeeded";
  const cloudRunPollObserved = Boolean(textValue(bootstrapState.last_polled_at));
  const deploymentTargetReady = guideRunTarget === "cloud_run" ? cloudRunDeploymentSucceeded : playgroundReady;
  const smokeObserved = guideRunTarget === "cloud_run" ? cloudRunDeploymentSucceeded && cloudRunPollObserved : hasConversation;
  const cloudRunAuthenticated = Boolean(cloudRunStatus?.gcloud?.authenticated);
  const cloudRunAuthSessionStatus = textValue(cloudRunAuthSession?.status);
  const cloudRunAuthInProgress = ["starting", "running"].includes(cloudRunAuthSessionStatus);
  const cloudRunAuthSucceeded = cloudRunAuthSessionStatus === "succeeded";
  const cloudRunDeploymentStatus = textValue(displayedCloudRunDeployment?.status);
  const cloudRunDeploymentActive = ["queued", "running"].includes(cloudRunDeploymentStatus);
  const cloudRunDeploymentStarted = Boolean(displayedCloudRunDeployment && !displayedCloudRunDeployment.dry_run);
  const cloudRunClaimReady = claimInstalled || cloudRunDeploymentStarted;
  const cloudRunNeedsSourceClaim = !cloudRunClaimReady;
  const cloudRunCanInstallSourceClaim = cloudRunNeedsSourceClaim && developerSessionReady;
  const cloudRunDeployBlockReason = !selectedLocalAgent
    ? "Create or select an agent source first."
    : !cloudRunAuthenticated
      ? "Google Cloud login is required before Cloud Run deployment."
      : cloudRunNeedsSourceClaim && !developerSessionReady
        ? "GitHub login is required so Dev Board can install the Foundry source claim before deployment."
        : "";
  const cloudRunDeployButtonLabel = cloudRunDeploying
    ? "Starting..."
    : !selectedLocalAgent
      ? "Deploy after source"
      : !cloudRunAuthenticated
        ? "Deploy after Google login"
        : cloudRunNeedsSourceClaim
          ? "Install claim and deploy"
          : "Deploy to Cloud Run";
  const cloudRunDeploymentLogs = displayedCloudRunDeployment?.logs ?? [];
  const latestCloudRunDeploymentLogs = cloudRunDeploymentLogs.slice(-8);
  const cloudRunDeploymentElapsed = formatDuration(
    textValue(displayedCloudRunDeployment?.started_at, textValue(displayedCloudRunDeployment?.created_at)),
    textValue(displayedCloudRunDeployment?.finished_at),
  );

  useEffect(() => {
    if (foundryUrl.trim()) {
      return;
    }
    const candidate = textValue(selectedLocalAgent?.foundry_url);
    if (candidate) {
      setFoundryUrl(candidate);
    }
  }, [selectedLocalAgent, foundryUrl]);

  useEffect(() => {
    if (agents.length === 0) {
      setActiveView("guide");
    }
  }, [agents.length]);

  useEffect(() => {
    if (!selectedAgent || !bootstrapEnabled || !claimInstalled) {
      return;
    }
    if (onboardingApproved || onboardingRetired) {
      return;
    }
    const timer = window.setInterval(() => {
      void probeHandshake(selectedAgent, foundryUrl);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [
    bootstrapEnabled,
    claimInstalled,
    onboardingApproved,
    onboardingRetired,
    selectedAgent,
    foundryUrl,
  ]);

  useEffect(() => {
    const expectedOrigin = (() => {
      if (!normalizedFoundryUrl) {
        return "";
      }
      try {
        return new URL(normalizedFoundryUrl).origin;
      } catch {
        return "";
      }
    })();

    const handleMessage = (event: MessageEvent) => {
      if (!expectedOrigin || event.origin !== expectedOrigin) {
        return;
      }
      const payload = (event.data || {}) as Partial<FoundryBootstrapAuthMessage>;
      if (payload.type !== "ccfoundry:developer-bootstrap-auth") {
        return;
      }
      setOauthLoading(false);
      if (!payload.ok) {
        setDeveloperError(textValue(payload.error, "GitHub bootstrap login failed."));
        return;
      }
      const token = textValue(payload.developer_token);
      if (!token) {
        setDeveloperError("Foundry did not return a developer bootstrap token.");
        return;
      }
      setDeveloperForm((prev) => ({
        ...prev,
        developer_token: token,
      }));
      setFoundryBootstrapSession({
        foundry_url: textValue(payload.foundry_url, expectedOrigin),
        github_login: textValue(payload.github_login, "github-user"),
        developer_identity: (payload.developer_identity as Record<string, unknown>) || {},
        expires_in_minutes: Number(payload.expires_in_minutes) || 30,
      });
      setDeveloperError("");
      setTicketError("");
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [normalizedFoundryUrl]);

  const runtimeBadge = useMemo<RuntimeBadge>(() => {
    if (devModeEnabled && hasDevOverrides) {
      return {
        label: "Per-request override",
        tone: "warn",
        detail: "The next chat request will use the temporary model, base URL, or API key entered in Dev Board.",
      };
    }
    if (latestModelSource === "foundry_gateway" || gatewayReady) {
      return {
        label: "Foundry gateway",
        tone: "success",
        detail: "This agent has approved Foundry LLM credentials and should run against the Foundry-managed gateway rather than a purely self-hosted model.",
      };
    }
    if (latestModelSource === "local_env") {
      return {
        label: "Local env credentials",
        tone: "success",
        detail: "The agent is currently using locally configured API credentials from its own environment.",
      };
    }
    if (manifestNeedsGateway || manifestBillingModel === "foundry_gateway") {
      return {
        label: "Foundry-managed runtime",
        tone: "warn",
        detail: "The manifest says this agent expects gateway credentials from Foundry, but the current runtime state has not exposed both LLM_API_KEY and LLM_API_BASE yet.",
      };
    }
    return {
      label: "Local / self-host",
      tone: "neutral",
      detail: "Without Foundry credentials or a dev override, the example agent falls back to local env credentials or the built-in demo reply path.",
    };
  }, [
    devModeEnabled,
    hasDevOverrides,
    gatewayReady,
    latestModelSource,
    manifestBillingModel,
    manifestNeedsGateway,
  ]);

  const handshakeStages = useMemo(() => {
    const discoveryStatus = textValue(bootstrapState.discovery_status, bootstrapEnabled ? "waiting" : "disabled");
    const inviteStatus = textValue(bootstrapState.invite_status, bootstrapEnabled ? "waiting" : "disabled");
    const registerStatus = textValue(bootstrapState.registration_status, bootstrapEnabled ? "waiting" : "disabled");
    const approvalMode = textValue(bootstrapState.approval_mode, "pending");
    const approvedAt = textValue(bootstrapState.approved_at);

    return [
      {
        label: "Discover",
        status: discoveryStatus,
        tone: statusTone(discoveryStatus),
        detail: textValue(bootstrapState.last_discovery_at, "Waiting for the first discover or heartbeat call."),
      },
      {
        label: "Invite",
        status: inviteStatus,
        tone: statusTone(inviteStatus),
        detail: textValue(bootstrapState.invite_expected_name, "No invite has been delivered yet."),
      },
      {
        label: "Register",
        status: registerStatus,
        tone: statusTone(registerStatus),
        detail: textValue(bootstrapState.registration_agent_id, "No agent registration has been recorded yet."),
      },
      {
        label: "Approved",
        status: approvedAt ? "callback" : approvalMode,
        tone: approvedAt ? "success" : statusTone(approvalMode),
        detail: approvedAt || "Approval callback has not been observed yet.",
      },
    ];
  }, [bootstrapEnabled, bootstrapState]);

  async function sendMessage(overrideMessage = "") {
    const nextMessage = (overrideMessage || message).trim();
    if (!selectedAgent || !nextMessage) {
      return;
    }
    setLoading(true);
    setError("");
    setReply("");
    setSteps([]);
    setLatestMetadata({});
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: selectedAgent,
          message: nextMessage,
          mode,
          username,
          conversation_id: conversationId,
          dev_overrides: devModeEnabled ? devOverrides : {},
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      if (!response.body) {
        throw new Error("Streaming response is not available.");
      }
      setMessage("");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const handleEvent = (chunk: string) => {
        const lines = chunk.split("\n");
        let eventType = "message";
        const dataLines: string[] = [];
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim() || "message";
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
        if (dataLines.length === 0) {
          return;
        }

        const rawData = dataLines.join("\n");
        let payload: Record<string, unknown> = {};
        try {
          payload = JSON.parse(rawData) as Record<string, unknown>;
        } catch {
          payload = { content: rawData };
        }

        if (eventType === "step") {
          setSteps((prev) => [
            ...prev,
            {
              status: textValue(payload.status, "running"),
              text: textValue(payload.text),
            },
          ]);
          return;
        }

        if (eventType === "token") {
          setReply((prev) => prev + textValue(payload.content));
          return;
        }

        if (eventType === "message") {
          setReply(textValue(payload.reply || payload.content));
          setTranscript((payload.transcript as TranscriptEntry[]) || []);
          setConversationId(textValue(payload.conversation_id, conversationId));
          setLatestMetadata((payload.metadata as Record<string, unknown>) || {});
          return;
        }

        if (eventType === "error") {
          setError(textValue(payload.detail, "Unknown stream error"));
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const eventChunk of events) {
          if (eventChunk.trim()) {
            handleEvent(eventChunk);
          }
        }
        if (done) {
          break;
        }
      }

      if (buffer.trim()) {
        handleEvent(buffer);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  function resetConversation() {
    setConversationId("");
    setTranscript([]);
    setReply("");
    setSteps([]);
    setLatestMetadata({});
    setError("");
  }

  function updateDevOverride(key: keyof DevOverrides, value: string) {
    setDevOverrides((prev) => ({
      ...prev,
      [key]: value,
    }));
  }

  function updateCreateAgentForm(key: keyof CreateAgentForm, value: string) {
    setCreateAgentForm((prev) => ({
      ...prev,
      [key]: value,
    }));
  }

  function updateCloudRunForm<K extends keyof CloudRunForm>(key: K, value: CloudRunForm[K]) {
    setCloudRunForm((prev) => ({
      ...prev,
      [key]: value,
    }));
  }

  async function refreshAgentInventory(preferredSelection = "") {
    setLocalAgentLoading(true);
    setLocalAgentError("");
    try {
      const [templatesResponse, localAgentsResponse, agentsResponse] = await Promise.all([
        fetch(`${API_BASE}/api/local-agent-templates`),
        fetch(`${API_BASE}/api/local-agents`),
        fetch(`${API_BASE}/api/agents`),
      ]);
      if (!templatesResponse.ok) {
        throw new Error(await readErrorMessage(templatesResponse));
      }
      if (!localAgentsResponse.ok) {
        throw new Error(await readErrorMessage(localAgentsResponse));
      }
      if (!agentsResponse.ok) {
        throw new Error(await readErrorMessage(agentsResponse));
      }

      const templatesPayload = expectArray<LocalAgentTemplate>(await templatesResponse.json(), "Local agent templates");
      const localAgentsPayload = expectArray<LocalAgentRuntime>(await localAgentsResponse.json(), "Local agents");
      const agentsPayload = expectArray<AgentEntry>(await agentsResponse.json(), "Agent inventory");

      setLocalTemplates(templatesPayload);
      setLocalAgents(localAgentsPayload);
      setAgents(agentsPayload);
      setSelectedAgent((current) => {
        const requested = preferredSelection || current;
        if (requested && agentsPayload.some((item) => item.name === requested)) {
          return requested;
        }
        return agentsPayload[0]?.name || "";
      });
    } catch (err) {
      setLocalAgentError(String(err));
    } finally {
      setLocalAgentLoading(false);
    }
  }

  async function fetchSettlements() {
    setSettlementsLoading(true);
    setSettlementsError("");
    try {
      const response = await fetch(
        `${API_BASE}/api/settlements`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            foundry_url: normalizedFoundryUrl || foundryUrl,
            agent_name: selectedAgent || "",
            limit: 50,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as {
        settlements: Array<Record<string, unknown>>;
        count: number;
        total_available?: number;
        agent_name?: string;
        foundry_agent_name?: string;
        matched_agent_names?: string[];
        foundry_url?: string;
        error?: string;
      };
      if (payload.error) {
        setSettlementsError(payload.error);
      }
      const items = payload.settlements || [];
      setSettlements(items);
      setSettlementsMeta({
        agent_name: payload.agent_name,
        foundry_agent_name: payload.foundry_agent_name,
        matched_agent_names: payload.matched_agent_names,
        total_available: payload.total_available,
        foundry_url: payload.foundry_url,
      });
    } catch (err) {
      setSettlementsError(String(err));
      setSettlements([]);
      setSettlementsMeta({});
    } finally {
      setSettlementsLoading(false);
    }
  }

  async function createLocalAgent() {
    if (!createAgentForm.name.trim()) {
      setLocalAgentError("Agent name is required.");
      return;
    }

    const preferredPort = Number.parseInt(createAgentForm.preferred_port, 10);
    setLocalAgentLoading(true);
    setLocalAgentError("");
    setLocalAgentNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/local-agents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          template_id: createAgentForm.template_id,
          name: createAgentForm.name.trim(),
          label: createAgentForm.label.trim(),
          preferred_port: Number.isInteger(preferredPort) ? preferredPort : null,
          foundry_url: normalizedFoundryUrl,
        }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as LocalAgentRuntime;
      setLocalAgentNotice(`Created source ${payload.label}. Choose local debug to start it, or deploy the source to Cloud Run.`);
      setCreateAgentForm((prev) => ({
        ...prev,
        name: "",
        label: "",
        preferred_port: "",
      }));
      await refreshAgentInventory(payload.name);
      window.setTimeout(() => {
        void refreshAgentInventory(payload.name);
      }, 1400);
    } catch (err) {
      setLocalAgentError(String(err));
    } finally {
      setLocalAgentLoading(false);
    }
  }

  async function startLocalAgent(agentName: string) {
    setLocalAgentLoading(true);
    setLocalAgentError("");
    setLocalAgentNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/local-agents/${encodeURIComponent(agentName)}/start`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as LocalAgentRuntime;
      setLocalAgentNotice(`Started ${payload.label}.`);
      await refreshAgentInventory(agentName);
      window.setTimeout(() => {
        void refreshAgentInventory(agentName);
      }, 1400);
    } catch (err) {
      setLocalAgentError(String(err));
    } finally {
      setLocalAgentLoading(false);
    }
  }

  async function stopLocalAgent(agentName: string) {
    setLocalAgentLoading(true);
    setLocalAgentError("");
    setLocalAgentNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/local-agents/${encodeURIComponent(agentName)}/stop`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as LocalAgentRuntime;
      setLocalAgentNotice(`Stopped ${payload.label}.`);
      await refreshAgentInventory(agentName);
    } catch (err) {
      setLocalAgentError(String(err));
    } finally {
      setLocalAgentLoading(false);
    }
  }

  async function retireLocalAgent(agentName = selectedAgent) {
    const normalizedAgentName = agentName.trim();
    if (!normalizedAgentName) {
      setLocalAgentError("Select an agent before retiring it.");
      return;
    }
    const localAgent = localAgents.find((agent) => agent.name === normalizedAgentName);
    const label = localAgent?.label || normalizedAgentName;
    const confirmed = window.confirm(
      developerSessionReady
        ? `Retire ${label}? Foundry will soft-retire the agent and Dev Board will remove it from the active runtime list.`
        : `Retire ${label}? Dev Board will remove it from the active runtime list. Foundry remote retire may require GitHub login later.`,
    );
    if (!confirmed) {
      return;
    }

    setRetiringAgent(normalizedAgentName);
    setLocalAgentError("");
    setLocalAgentNotice("");
    try {
      const response = await fetch(`${API_BASE}/api/local-agents/${encodeURIComponent(normalizedAgentName)}/retire`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          foundry_url: normalizedFoundryUrl || foundryUrl,
          developer_token: developerForm.developer_token,
          github_token: developerForm.github_token,
          reason: "dev_board_retire",
          stop_local: true,
        }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as RetireAgentResult;
      const foundryStatus = textValue(payload.foundry?.status, "RETIRED");
      const foundryMessage = textValue(payload.foundry?.message);
      const foundrySuffix =
        payload.foundry?.ok === false
          ? ` Foundry remote retire needs review${foundryMessage ? `: ${foundryMessage}` : "."}`
          : ` Foundry status: ${foundryStatus}.`;
      const cloudRunActions = payload.cloud_run?.actions ?? [];
      const cloudRunSuffix =
        cloudRunActions.length === 0
          ? ""
          : payload.cloud_run?.ok
            ? ` Cloud Run cleanup removed ${cloudRunActions.length} resource action(s).`
            : " Cloud Run cleanup needs review.";
      setLocalAgentNotice(
        `Retired ${label} locally and removed it from active Dev Board runtimes.${foundrySuffix}${cloudRunSuffix}`,
      );
      if (selectedAgent === normalizedAgentName) {
        setHandshakeResult(null);
        setDeveloperTicket(null);
        resetConversation();
      }
      await refreshAgentInventory();
      await refreshCloudRunDeployments();
    } catch (err) {
      if (err instanceof TypeError) {
        setLocalAgentError(`Dev Board API did not respond at ${API_BASE}. Check that port ${API_PORT} is reachable from this browser.`);
      } else {
        setLocalAgentError(String(err));
      }
    } finally {
      setRetiringAgent("");
    }
  }

  async function refreshCloudRunStatus() {
    setCloudRunStatusLoading(true);
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/status`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunStatus;
      setCloudRunStatus(payload);
      const defaults = payload.defaults || {};
      setCloudRunForm((prev) => ({
        ...prev,
        project: prev.project || textValue(defaults.project),
        region: prev.region || textValue(defaults.region, "us-central1"),
      }));
    } catch (err) {
      setCloudRunError(String(err));
    } finally {
      setCloudRunStatusLoading(false);
    }
  }

  async function refreshCloudRunDeployments() {
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/deployments`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = expectArray<CloudRunDeployment>(await response.json(), "Cloud Run deployments");
      setCloudRunDeployments(payload);
      setCloudRunCurrentJob((current) => current || payload[0] || null);
    } catch (err) {
      setCloudRunError(String(err));
    }
  }

  async function refreshCloudRunDeployment(jobId: string) {
    if (!jobId) {
      return;
    }
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/deployments/${encodeURIComponent(jobId)}`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunDeployment;
      setCloudRunCurrentJob(payload);
      setCloudRunDeployments((prev) => {
        const without = prev.filter((item) => item.id !== payload.id);
        return [payload, ...without].slice(0, 20);
      });
    } catch (err) {
      setCloudRunError(String(err));
    }
  }

  async function startCloudRunAuth() {
    setCloudRunAuthLoading(true);
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/auth/start`, { method: "POST" });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunAuthSession;
      setCloudRunAuthSession(payload);
      setCloudRunAuthCode("");
    } catch (err) {
      setCloudRunError(String(err));
    } finally {
      setCloudRunAuthLoading(false);
    }
  }

  async function refreshCloudRunAuthSession(sessionId: string) {
    if (!sessionId) {
      return;
    }
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/auth/${encodeURIComponent(sessionId)}`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunAuthSession;
      setCloudRunAuthSession(payload);
      if (payload.status === "succeeded") {
        void refreshCloudRunStatus();
      }
    } catch (err) {
      setCloudRunError(String(err));
    }
  }

  async function submitCloudRunAuthCode() {
    if (!cloudRunAuthSession || !cloudRunAuthCode.trim()) {
      return;
    }
    setCloudRunAuthLoading(true);
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/auth/${encodeURIComponent(cloudRunAuthSession.id)}/code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: cloudRunAuthCode.trim() }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunAuthSession;
      setCloudRunAuthSession(payload);
      setCloudRunAuthCode("");
      window.setTimeout(() => {
        void refreshCloudRunAuthSession(payload.id);
      }, 1200);
    } catch (err) {
      setCloudRunError(String(err));
    } finally {
      setCloudRunAuthLoading(false);
    }
  }

  async function cancelCloudRunAuth() {
    if (!cloudRunAuthSession) {
      return;
    }
    setCloudRunAuthLoading(true);
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/auth/${encodeURIComponent(cloudRunAuthSession.id)}/cancel`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunAuthSession;
      setCloudRunAuthSession(payload);
    } catch (err) {
      setCloudRunError(String(err));
    } finally {
      setCloudRunAuthLoading(false);
    }
  }

  async function deployCloudRun(dryRun: boolean, agentNameOverride = "") {
    const agentName = agentNameOverride || selectedLocalAgent?.name || selectedAgent;
    if (!agentName) {
      setCloudRunError("Select or create a local agent first.");
      return;
    }
    const minInstances = Number.parseInt(cloudRunForm.min_instances, 10);
    setCloudRunDeploying(true);
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: agentName,
          project: cloudRunForm.project.trim(),
          region: cloudRunForm.region.trim(),
          foundry_url: normalizedFoundryUrl || foundryUrl,
          min_instances: Number.isInteger(minInstances) ? minInstances : 0,
          memory: cloudRunForm.memory.trim() || "512Mi",
          cpu: cloudRunForm.cpu.trim() || "1",
          poll_schedule: cloudRunForm.poll_schedule.trim() || DEFAULT_CLOUD_RUN_POLL_SCHEDULE,
          skip_scheduler: cloudRunForm.skip_scheduler,
          dry_run: dryRun,
        }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunDeployment;
      setCloudRunCurrentJob(payload);
      setCloudRunDeployments((prev) => [payload, ...prev.filter((item) => item.id !== payload.id)].slice(0, 20));
      window.setTimeout(() => {
        void refreshCloudRunDeployment(payload.id);
      }, 1200);
    } catch (err) {
      setCloudRunError(String(err));
    } finally {
      setCloudRunDeploying(false);
    }
  }

  async function deployCloudRunAfterSourceClaim(dryRun: boolean) {
    if (!dryRun && !cloudRunClaimReady) {
      if (!developerSessionReady) {
        setTicketError("GitHub login is required before installing the Foundry source claim.");
        return;
      }
      const claimReady = await requestBootstrapTicket();
      if (!claimReady) {
        return;
      }
    }
    await deployCloudRun(dryRun);
  }

  async function cancelCloudRunDeployment() {
    if (!cloudRunCurrentJob) {
      return;
    }
    setCloudRunError("");
    try {
      const response = await fetch(`${API_BASE}/api/cloud-run/deployments/${encodeURIComponent(cloudRunCurrentJob.id)}/cancel`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as CloudRunDeployment;
      setCloudRunCurrentJob(payload);
    } catch (err) {
      setCloudRunError(String(err));
    }
  }

  async function probeHandshake(agentName = selectedAgent, currentFoundryUrl = foundryUrl) {
    if (!agentName) {
      return;
    }
    setHandshakeLoading(true);
    setHandshakeError("");
    try {
      const response = await fetch(`${API_BASE}/api/handshake/probe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: agentName,
          foundry_url: currentFoundryUrl,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as HandshakeProbeResult;
      setHandshakeResult(payload);
      if (!currentFoundryUrl.trim() && payload.foundry.url) {
        setFoundryUrl(payload.foundry.url);
      }
    } catch (err) {
      setHandshakeError(String(err));
    } finally {
      setHandshakeLoading(false);
    }
  }

  function updateDeveloperForm(key: keyof DeveloperForm, value: string | boolean) {
    setDeveloperForm((prev) => ({
      ...prev,
      [key]: value,
    }));
  }

  async function refreshDeveloperContext(currentFoundryUrl = foundryUrl) {
    setDeveloperLoading(true);
    setDeveloperError("");
    try {
      const response = await fetch(`${API_BASE}/api/developer/context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          foundry_url: currentFoundryUrl,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as DeveloperContextResult;
      setDeveloperContext(payload);
      if (!currentFoundryUrl.trim() && payload.foundry?.url) {
        setFoundryUrl(payload.foundry.url);
      }
    } catch (err) {
      setDeveloperError(String(err));
    } finally {
      setDeveloperLoading(false);
    }
  }

  async function loadNotificationPreferences(agentName = selectedAgent) {
    if (!agentName) {
      setNotificationEmail("");
      setNotificationEnabled(true);
      setNotificationStatus("");
      setNotificationError("");
      return;
    }
    setNotificationError("");
    try {
      const response = await fetch(
        `${API_BASE}/api/local-agents/${encodeURIComponent(agentName)}/notification-preferences`,
      );
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as NotificationPreferences;
      setNotificationEmail(textValue(payload.email));
      setNotificationEnabled(payload.bounty_success_email_enabled !== false);
      setNotificationStatus(textValue(payload.status, "not_configured"));
    } catch (err) {
      setNotificationStatus("");
      setNotificationError(String(err));
    }
  }

  async function syncNotificationPreferences(options: { silent?: boolean } = {}) {
    if (!selectedAgent) {
      return false;
    }
    if (notificationEnabled && !notificationEmail.trim()) {
      if (!options.silent) {
        setNotificationError("Enter an email address first.");
      }
      return false;
    }
    setNotificationLoading(true);
    if (!options.silent) {
      setNotificationError("");
      setNotificationStatus("syncing");
    }
    try {
      const response = await fetch(`${API_BASE}/api/developer/notification-preferences/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: selectedAgent,
          foundry_url: foundryUrl,
          developer_token: developerForm.developer_token,
          github_token: developerForm.github_token,
          email: notificationEmail,
          bounty_success_email_enabled: notificationEnabled,
        }),
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }
      const payload = (await response.json()) as { preferences?: NotificationPreferences };
      const preferences = payload.preferences || {};
      setNotificationEmail(textValue(preferences.email, notificationEmail));
      setNotificationEnabled(preferences.bounty_success_email_enabled !== false);
      setNotificationStatus(textValue(preferences.status, "synced"));
      setNotificationError("");
      return true;
    } catch (err) {
      setNotificationStatus("sync_failed");
      setNotificationError(String(err));
      return false;
    } finally {
      setNotificationLoading(false);
    }
  }

  async function requestBootstrapTicket(): Promise<boolean> {
    if (!selectedAgent) {
      return false;
    }
    setTicketLoading(true);
    setTicketError("");
    try {
      const response = await fetch(`${API_BASE}/api/developer/bootstrap-ticket`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: selectedAgent,
          foundry_url: foundryUrl,
          developer_token: developerForm.developer_token,
          github_token: developerForm.github_token,
          bootstrap_delivery: developerForm.bootstrap_delivery,
          force_rediscover: developerForm.force_rediscover,
          runtime_target: guideRunTarget,
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as DeveloperTicketResult;
      setDeveloperTicket(payload);
      if (notificationEmail.trim() || !notificationEnabled) {
        await syncNotificationPreferences({ silent: true });
      }
      await Promise.all([
        probeHandshake(selectedAgent, foundryUrl),
        refreshDeveloperContext(foundryUrl),
      ]);
      return true;
    } catch (err) {
      if (err instanceof TypeError) {
        setTicketError(`Dev Board API did not respond at ${API_BASE}. Check that port ${API_PORT} is reachable from this browser.`);
      } else {
        setTicketError(String(err));
      }
      return false;
    } finally {
      setTicketLoading(false);
    }
  }

  function loginWithFoundryGithub() {
    if (!normalizedFoundryUrl) {
      setDeveloperError("Foundry URL is required before GitHub login.");
      return;
    }
    const popupUrl =
      `${normalizedFoundryUrl}/api/auth/github/bootstrap/start?return_origin=` +
      encodeURIComponent(window.location.origin);
    const popup = window.open(
      popupUrl,
      "ccfoundry-dev-bootstrap-auth",
      "popup=yes,width=560,height=720,resizable=yes,scrollbars=yes",
    );
    if (!popup) {
      setDeveloperError("Popup was blocked. Allow popups for this page and try again.");
      return;
    }
    popup.focus();
    setOauthLoading(true);
    setDeveloperError("");
  }

  function openPlaygroundWithStarterMessage() {
    if (!message.trim()) {
      setMessage(DEFAULT_PLAYGROUND_MESSAGE);
    }
    setActiveView("playground");
  }

  function openFoundryPortal() {
    setFoundryPortalOpened(true);
    window.open(foundryPortalUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="app-shell">
      <datalist id="cloud-run-region-options">
        {CLOUD_RUN_REGION_OPTIONS.map((region) => (
          <option key={region.value} value={region.value}>
            {region.label}
          </option>
        ))}
      </datalist>
      <aside className="global-sidebar">
        <div className="brand-block">
          <div className="brand-row">
            <img className="brand-logo" src={FOUNDRY_LOGO_URL} alt="CoChiper Foundry logo" />
            <h1>Agent Dev Board</h1>
          </div>
          <p className="brand-tagline">Create, test, connect and use your agent in CoChiper Foundry system.</p>
        </div>

        <nav className="nav-stack">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`nav-button ${activeView === item.id ? "active" : ""} ${item.id === "guide" ? "featured-nav" : ""}`}
              onClick={() => setActiveView(item.id)}
            >
              <span className="nav-label">{item.label}</span>
              <span className="nav-kicker">{item.kicker}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="workspace">
        <div className="workspace-toolbar">
          <a
            className="external-link-chip"
            href="https://github.com/ic-star-tech/ccfoundry-agent-kit"
            target="_blank"
            rel="noreferrer"
            aria-label="Open GitHub repository"
            title="Open GitHub repository"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="currentColor"
                d="M12 2C6.48 2 2 6.58 2 12.23c0 4.52 2.87 8.35 6.84 9.71.5.1.68-.22.68-.49 0-.24-.01-1.04-.01-1.88-2.78.62-3.37-1.21-3.37-1.21-.45-1.18-1.11-1.49-1.11-1.49-.91-.64.07-.63.07-.63 1 .08 1.53 1.06 1.53 1.06.89 1.57 2.34 1.12 2.91.86.09-.66.35-1.12.63-1.38-2.22-.26-4.55-1.14-4.55-5.08 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.31.1-2.73 0 0 .84-.28 2.75 1.05A9.3 9.3 0 0 1 12 6.84c.85 0 1.71.12 2.51.36 1.91-1.33 2.75-1.05 2.75-1.05.55 1.42.2 2.47.1 2.73.64.72 1.03 1.63 1.03 2.75 0 3.95-2.33 4.81-4.56 5.07.36.32.67.95.67 1.92 0 1.39-.01 2.5-.01 2.84 0 .27.18.59.69.49A10.26 10.26 0 0 0 22 12.23C22 6.58 17.52 2 12 2Z"
              />
            </svg>
            <span className="sr-only">GitHub repository</span>
          </a>
          <a
            className="external-link-chip"
            href={foundryPortalUrl}
            target="_blank"
            rel="noreferrer"
            aria-label={`Open Foundry (${foundryPortalDisplayUrl})`}
            title={`Open Foundry (${foundryPortalDisplayUrl})`}
          >
            <img className="external-link-logo" src={CC_LOGO_URL} alt="" aria-hidden="true" />
            <span className="sr-only">Open Foundry ({foundryPortalDisplayUrl})</span>
          </a>
        </div>

        {activeView === "agent" ? (
          <section className="panel tab-shell workspace-subtabs">
            <div className="inline-tabs" role="tablist" aria-label="Agent card sections">
              {AGENT_CARD_TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  className={`inline-tab-button ${agentCardTab === tab.id ? "active" : ""}`}
                  onClick={() => setAgentCardTab(tab.id)}
                >
                  <span>{tab.label}</span>
                  <small>{tab.helper}</small>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {activeView === "guide" ? (
          <div className="guide-grid">
            <section className="panel span-two">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Recommended path</p>
                  <h3>Go from zero to a working Foundry-connected agent</h3>
                </div>
                <div className="actions split-actions compact-actions">
                  <button className="secondary" onClick={() => refreshAgentInventory(selectedAgent)} disabled={localAgentLoading}>
                    {localAgentLoading ? "Refreshing..." : "Refresh runtimes"}
                  </button>
                  <button className="secondary" onClick={() => probeHandshake()} disabled={handshakeLoading || !selectedAgent}>
                    {handshakeLoading ? "Refreshing flow..." : "Refresh flow"}
                  </button>
                </div>
              </div>
              <p className="muted">
                This guided setup keeps the happy path in one place: create an agent, choose where it runs, connect your
                developer identity, let Foundry bootstrap it, and then verify the linked agent inside Foundry itself.
              </p>
              <div className="guide-progress">
                <span className={`chip ${selectedLocalAgent ? "tone-success" : ""}`}>1. Agent source</span>
                <span className={`chip ${deploymentTargetReady ? "tone-success" : ""}`}>
                  2. {guideRunTarget === "cloud_run" ? "Cloud Run" : "Local"} target
                </span>
                <span className={`chip ${developerSessionReady ? "tone-success" : ""}`}>3. GitHub login</span>
                <span className={`chip ${claimInstalled ? "tone-success" : ""}`}>4. Foundry claim</span>
                <span className={`chip ${onboardingApproved ? "tone-success" : onboardingRetired ? "tone-warn" : ""}`}>5. Onboarded</span>
                <span className={`chip ${smokeObserved ? "tone-success" : ""}`}>6. Smoke test</span>
                <span className={`chip ${foundryPortalOpened ? "tone-success" : ""}`}>7. Foundry test</span>
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">1</span>
                  <div>
                    <h4>Create agent source</h4>
                    <p className="muted">
                      Start from the template, give it a stable name, and load any skills the agent should carry.
                    </p>
                  </div>
                </div>
                {selectedLocalAgent ? (
                  <div className="reply">
                    <strong>{selectedLocalAgent.label}</strong>
                    <p>
                      Source agent: <code>{selectedLocalAgent.name}</code>. Local debug runtime is{" "}
                      <code>{textValue(selectedLocalAgent.status, "unknown")}</code> at{" "}
                      <code>{displaySafeUrl(selectedLocalAgent.base_url)}</code>.
                    </p>
                  </div>
                ) : null}
                <label>
                  Template
                  <select
                    value={createAgentForm.template_id}
                    onChange={(event) => updateCreateAgentForm("template_id", event.target.value)}
                  >
                    {localTemplates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Agent name
                  <input
                    value={createAgentForm.name}
                    onChange={(event) => updateCreateAgentForm("name", event.target.value)}
                    placeholder="my_agent_dev"
                  />
                </label>
                <label>
                  Friendly label
                  <input
                    value={createAgentForm.label}
                    onChange={(event) => updateCreateAgentForm("label", event.target.value)}
                    placeholder="Optional display name"
                  />
                </label>
                <label>
                  Preferred port
                  <input
                    value={createAgentForm.preferred_port}
                    onChange={(event) => updateCreateAgentForm("preferred_port", event.target.value)}
                    placeholder="8085"
                  />
                </label>
                <div className="actions split-actions">
                  <button onClick={createLocalAgent} disabled={localAgentLoading}>
                    {localAgentLoading ? "Creating..." : "Create source agent"}
                  </button>
                  <button className="secondary" onClick={() => setActiveView("skills")}>
                    Load skills
                  </button>
                  <button
                    className="secondary"
                    onClick={() => {
                      setActiveView("agent");
                    }}
                  >
                    Open agent card
                  </button>
                </div>
                {localAgentNotice ? <div className="reply">{localAgentNotice}</div> : null}
                {localAgentError ? <div className="error">{localAgentError}</div> : null}
              </div>
            </section>

            <section className="panel span-two">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">2</span>
                  <div>
                    <h4>Deploy target</h4>
                    <p className="muted">
                      The source is the durable agent workspace. The active runtime target is either local debugging or a
                      Cloud Run pull worker.
                    </p>
                  </div>
                </div>
                <div className="run-target-toggle" role="radiogroup" aria-label="Run target">
                  <button
                    type="button"
                    className={`run-target-button ${guideRunTarget === "local" ? "active" : ""}`}
                    onClick={() => setGuideRunTarget("local")}
                  >
                    <strong>Local debug runtime</strong>
                    <span>Run chat playground and fast local smoke tests from this Dev Board host.</span>
                  </button>
                  <button
                    type="button"
                    className={`run-target-button ${guideRunTarget === "cloud_run" ? "active" : ""}`}
                    onClick={() => {
                      setGuideRunTarget("cloud_run");
                      void refreshCloudRunStatus();
                      void refreshCloudRunDeployments();
                    }}
                  >
                    <strong>Cloud Run pull worker</strong>
                    <span>Build the selected source into a scheduled worker for Foundry tasks.</span>
                  </button>
                </div>

                {guideRunTarget === "local" ? (
                  <div className="guide-target-panel">
                    <div className="kv-list compact-kv">
                      <div>
                        <span>Selected source</span>
                        <strong>{textValue(selectedLocalAgent?.label, "create an agent source first")}</strong>
                      </div>
                      <div>
                        <span>Debug URL</span>
                        <strong>{displaySafeUrl(textValue(selectedLocalAgent?.base_url), "not running")}</strong>
                      </div>
                      <div>
                        <span>Runtime status</span>
                        <strong>{textValue(selectedAgentEntry?.status, "offline")}</strong>
                      </div>
                    </div>
                    <div className="actions split-actions compact-actions">
                      {selectedLocalAgent?.status === "running" ? (
                        <button className="secondary" onClick={() => stopLocalAgent(selectedLocalAgent.name)} disabled={localAgentLoading}>
                          Stop local runtime
                        </button>
                      ) : (
                        <button onClick={() => selectedLocalAgent && startLocalAgent(selectedLocalAgent.name)} disabled={localAgentLoading || !selectedLocalAgent}>
                          Start local runtime
                        </button>
                      )}
                      <button className="secondary" onClick={() => setActiveView("playground")} disabled={!playgroundReady}>
                        Open local playground
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="guide-cloud-run-panel">
                    <div className="section-heading compact-heading">
                      <div>
                        <p className="eyebrow">Google Cloud</p>
                        <h4>Cloud Run preflight</h4>
                      </div>
                      <button className="secondary" onClick={refreshCloudRunStatus} disabled={cloudRunStatusLoading}>
                        {cloudRunStatusLoading ? "Checking..." : "Refresh"}
                      </button>
                    </div>
                    <div className="chips">
                      <span className={`chip tone-${cloudRunStatus?.gcloud?.authenticated ? "success" : "warn"}`}>
                        {cloudRunStatus?.gcloud?.authenticated ? "gcloud authenticated" : "gcloud login needed"}
                      </span>
                      <span className={`chip tone-${cloudRunStatus?.docker?.installed ? "success" : "warn"}`}>
                        {cloudRunStatus?.docker?.installed ? "docker available" : "docker missing"}
                      </span>
                      <span className={`chip ${cloudRunClaimReady ? "tone-success" : "tone-warn"}`}>
                        {cloudRunClaimReady ? "claim ready" : cloudRunCanInstallSourceClaim ? "claim installs on deploy" : "claim needed before deploy"}
                      </span>
                    </div>
                    <div className="kv-list compact-kv">
                      <div>
                        <span>Google account</span>
                        <strong>{textValue(cloudRunStatus?.gcloud?.active_account, "not authenticated")}</strong>
                      </div>
                      <div>
                        <span>Cloud Run project</span>
                        <strong>{textValue(cloudRunForm.project, textValue(cloudRunStatus?.defaults?.project, "not set"))}</strong>
                      </div>
                      <div>
                        <span>Deployment</span>
                        <strong>{textValue(displayedCloudRunDeployment?.status, "not deployed")}</strong>
                      </div>
                    </div>
                    {!cloudRunStatus?.gcloud?.authenticated ? (
                      <div className="code-block compact-code-block">
                        <pre>{textValue(cloudRunStatus?.commands?.login_no_browser, "gcloud auth login --no-launch-browser")}</pre>
                      </div>
                    ) : null}
                    <div className="actions split-actions compact-actions">
                      <button className="secondary" onClick={startCloudRunAuth} disabled={cloudRunAuthLoading}>
                        {cloudRunAuthLoading
                          ? "Starting login..."
                          : cloudRunAuthenticated
                            ? "Switch Google account"
                            : "Google Cloud login"}
                      </button>
                      {cloudRunAuthSession && cloudRunAuthInProgress ? (
                        <button className="secondary" onClick={cancelCloudRunAuth} disabled={cloudRunAuthLoading}>
                          Cancel login
                        </button>
                      ) : null}
                    </div>
                    {cloudRunAuthSession ? (
                      <div className="cloud-run-auth-box">
                        <div className="kv-list compact-kv">
                          <div>
                            <span>Login status</span>
                            <strong>{cloudRunAuthSession.status}</strong>
                          </div>
                        </div>
                        {cloudRunAuthSucceeded ? (
                          <p className="muted">Google Cloud login succeeded. The active account above is ready for deploy.</p>
                        ) : cloudRunAuthSession.auth_url ? (
                          <a className="secondary link-button" href={cloudRunAuthSession.auth_url} target="_blank" rel="noreferrer">
                            Open Google login
                          </a>
                        ) : (
                          <div className="code-block compact-code-block">
                            <pre>{(cloudRunAuthSession.logs || []).slice(-4).join("\n") || "Waiting for Google login URL..."}</pre>
                          </div>
                        )}
                        {cloudRunAuthInProgress ? (
                          <div className="cloud-run-auth-code-row">
                            <label>
                              Authorization code
                              <input
                                value={cloudRunAuthCode}
                                onChange={(event) => setCloudRunAuthCode(event.target.value)}
                                placeholder="Paste code from Google"
                              />
                            </label>
                            <button onClick={submitCloudRunAuthCode} disabled={cloudRunAuthLoading || !cloudRunAuthCode.trim()}>
                              Submit code
                            </button>
                          </div>
                        ) : null}
                        {cloudRunAuthSession.error ? <div className="error">{cloudRunAuthSession.error}</div> : null}
                      </div>
                    ) : null}
                    <div className="cloud-run-form-grid guide-cloud-run-grid">
                      <label>
                        GCP project
                        <input
                          value={cloudRunForm.project}
                          onChange={(event) => updateCloudRunForm("project", event.target.value)}
                          placeholder="your-gcp-project"
                        />
                      </label>
                      <label>
                        Region
                        <input
                          list="cloud-run-region-options"
                          value={cloudRunForm.region}
                          onChange={(event) => updateCloudRunForm("region", event.target.value)}
                          placeholder="us-central1"
                        />
                        <span className="field-helper">Quick picks: US, UK London, Hong Kong, Singapore.</span>
                      </label>
                      <label>
                        Memory
                        <input
                          value={cloudRunForm.memory}
                          onChange={(event) => updateCloudRunForm("memory", event.target.value)}
                          placeholder="512Mi"
                        />
                      </label>
                      <label>
                        CPU
                        <input
                          value={cloudRunForm.cpu}
                          onChange={(event) => updateCloudRunForm("cpu", event.target.value)}
                          placeholder="1"
                        />
                      </label>
                      <label>
                        Min instances
                        <input
                          value={cloudRunForm.min_instances}
                          onChange={(event) => updateCloudRunForm("min_instances", event.target.value)}
                          placeholder="0"
                        />
                      </label>
                      <label>
                        Poll schedule
                        <input
                          value={cloudRunForm.poll_schedule}
                          onChange={(event) => updateCloudRunForm("poll_schedule", event.target.value)}
                          placeholder={DEFAULT_CLOUD_RUN_POLL_SCHEDULE}
                        />
                        <span className="field-helper">Cloud Scheduler cron. Default runs every 5 minutes.</span>
                      </label>
                    </div>
                    <label className="inline-toggle cloud-run-toggle">
                      <input
                        type="checkbox"
                        checked={cloudRunForm.skip_scheduler}
                        onChange={(event) => updateCloudRunForm("skip_scheduler", event.target.checked)}
                      />
                      Skip Cloud Scheduler
                    </label>
                    <div className="actions split-actions">
                      <button
                        className="secondary"
                        onClick={() => deployCloudRun(true)}
                        disabled={cloudRunDeploying || !selectedLocalAgent}
                      >
                        Dry run Cloud Run
                      </button>
                      <button
                        onClick={() => deployCloudRunAfterSourceClaim(false)}
                        disabled={cloudRunDeploying || Boolean(cloudRunDeployBlockReason)}
                      >
                        {cloudRunDeployButtonLabel}
                      </button>
                      {cloudRunCurrentJob && ["queued", "running"].includes(textValue(cloudRunCurrentJob.status)) ? (
                        <button className="secondary" onClick={cancelCloudRunDeployment}>
                          Cancel deploy
                        </button>
                      ) : null}
                    </div>
                    {cloudRunDeployBlockReason ? (
                      <div className="guide-blocker">
                        <strong>Deploy is locked</strong>
                        <p>{cloudRunDeployBlockReason}</p>
                      </div>
                    ) : null}
                    {displayedCloudRunDeployment ? (
                      <div className={`cloud-run-progress-panel ${cloudRunDeploymentActive ? "active" : ""}`}>
                        <div className="cloud-run-progress-header">
                          <div>
                            <strong>{cloudRunDeploymentActive ? "Deployment in progress" : "Latest deployment"}</strong>
                            <p>
                              {cloudRunDeploymentActive
                                ? "Build, image push, Cloud Run deploy, and Scheduler setup can take a few minutes."
                                : "Most recent Cloud Run job for the selected agent."}
                            </p>
                          </div>
                          <span className={`status-pill ${statusTone(cloudRunDeploymentStatus)}`}>
                            {textValue(displayedCloudRunDeployment.status, "pending")}
                          </span>
                        </div>
                        <div className="kv-list compact-kv">
                          <div>
                            <span>Service</span>
                            <strong>{displayedCloudRunDeployment.service_name}</strong>
                          </div>
                          <div>
                            <span>Elapsed</span>
                            <strong>{cloudRunDeploymentElapsed}</strong>
                          </div>
                          <div>
                            <span>Region</span>
                            <strong>{textValue(displayedCloudRunDeployment.region, cloudRunForm.region)}</strong>
                          </div>
                          <div>
                            <span>Service URL</span>
                            <strong>{displaySafeUrl(textValue(displayedCloudRunDeployment.result?.service_url), "pending")}</strong>
                          </div>
                        </div>
                        <div className="code-block compact-code-block cloud-run-progress-log">
                          <pre>{latestCloudRunDeploymentLogs.join("\n") || "Waiting for deployment output..."}</pre>
                        </div>
                      </div>
                    ) : null}
                    {cloudRunError ? <div className="error">{cloudRunError}</div> : null}
                    {cloudRunStatus?.errors?.length ? (
                      <div className="error">
                        <strong>Cloud Run preflight</strong>
                        <p>{cloudRunStatus.errors.join(" ")}</p>
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">3</span>
                  <div>
                    <h4>Log in as a developer</h4>
                    <p className="muted">
                      Use the popup-based GitHub login so Dev Board gets a short-lived Foundry bootstrap token.
                    </p>
                  </div>
                </div>
                <FoundryUrlChooser
                  label="Foundry URL"
                  value={foundryUrl}
                  onChange={setFoundryUrl}
                  helper=".com is the CN target, .ai is the WW target. You can still type any other compatible Foundry URL."
                />
                {developerSessionReady ? (
                  <div className="reply">
                    <strong>{displayedDeveloperLogin}</strong>
                    <p>Developer bootstrap session is ready. Token TTL: {foundryBootstrapSession?.expires_in_minutes || 30} min.</p>
                  </div>
                ) : (
                  <p className="muted">
                    Local GitHub CLI detection is optional. The popup login is the recommended path because it returns a
                    Foundry-scoped developer token directly to this board.
                  </p>
                )}
                <div className="actions split-actions">
                  <button className="secondary" onClick={loginWithFoundryGithub} disabled={oauthLoading || !normalizedFoundryUrl}>
                    {oauthLoading ? "Waiting for GitHub..." : "Login with GitHub"}
                  </button>
                </div>
                <div className="notification-box">
                  <label>
                    Completion email
                    <input
                      type="email"
                      value={notificationEmail}
                      onChange={(event) => setNotificationEmail(event.target.value)}
                      placeholder="developer@example.com"
                    />
                  </label>
                  <div className="notification-row">
                    <label className="inline-toggle notification-toggle">
                      <input
                        type="checkbox"
                        checked={notificationEnabled}
                        onChange={(event) => setNotificationEnabled(event.target.checked)}
                      />
                      Bounty success emails
                    </label>
                    <button
                      className="secondary"
                      onClick={() => syncNotificationPreferences()}
                      disabled={
                        notificationLoading ||
                        !selectedAgent ||
                        !developerAuthReady ||
                        (notificationEnabled && !notificationEmail.trim())
                      }
                    >
                      {notificationLoading ? "Syncing..." : "Sync email"}
                    </button>
                  </div>
                  {notificationStatus ? (
                    <span className={`status-pill ${notificationStatus === "synced" ? "success" : "neutral"}`}>
                      {notificationStatus}
                    </span>
                  ) : null}
                  {notificationError ? <div className="error">{notificationError}</div> : null}
                </div>
                {developerError ? <div className="error">{developerError}</div> : null}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">4</span>
                  <div>
                    <h4>Install Foundry source claim</h4>
                    <p className="muted">
                      This requests a bootstrap ticket and installs the discovery claim on the selected source before runtime
                      launch. Local debug applies it immediately; Cloud Run carries it into the worker and uses it on the first poll.
                    </p>
                  </div>
                </div>
                {claimInstalled ? (
                  <div className="reply">
                    <strong>{developerTicket?.apply_mode === "source_state" ? "Claim installed on source" : "Claim installed"}</strong>
                    <p>Last claim time: {textValue(bootstrapState.last_claimed_at, "recorded by the agent")}</p>
                  </div>
                ) : null}
                <label>
                  Agent source
                  <select
                    value={selectedAgent}
                    onChange={(event) => setSelectedAgent(event.target.value)}
                    disabled={localAgents.length === 0}
                  >
                    {localAgents.length === 0 ? <option value="">Create one first</option> : null}
                    {localAgents.map((agent) => (
                      <option key={agent.name} value={agent.name}>
                        {agent.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="kv-list compact-kv">
                  <div>
                    <span>Selected source</span>
                    <strong>{textValue(selectedAgentEntry?.label || selectedLocalAgent?.label, "create one first")}</strong>
                  </div>
                  <div>
                    <span>Delivery</span>
                    <strong>{developerForm.bootstrap_delivery}</strong>
                  </div>
                </div>
                <div className="actions split-actions">
                  <button
                    onClick={requestBootstrapTicket}
                    disabled={ticketLoading || !selectedAgent || !developerSessionReady}
                  >
                    {ticketLoading ? "Requesting..." : "Request bootstrap ticket"}
                  </button>
                  {guideRunTarget === "cloud_run" ? (
                    <button
                      className="secondary"
                      onClick={() => deployCloudRunAfterSourceClaim(false)}
                      disabled={cloudRunDeploying || Boolean(cloudRunDeployBlockReason)}
                    >
                      {cloudRunDeployButtonLabel}
                    </button>
                  ) : null}
                </div>
                {guideRunTarget === "cloud_run" && cloudRunDeployBlockReason ? (
                  <div className="guide-blocker">
                    <strong>Cloud Run deploy needs one more step</strong>
                    <p>{cloudRunDeployBlockReason}</p>
                  </div>
                ) : null}
                {ticketError ? <div className="error">{ticketError}</div> : null}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">5</span>
                  <div>
                    <h4>Wait for Foundry onboarding</h4>
                    <p className="muted">
                      Dev Board now auto-refreshes the flow while claim-based onboarding is in progress.
                    </p>
                  </div>
                </div>
                <div className="timeline-grid single-column">
                  {handshakeStages.map((stage) => (
                    <article key={stage.label} className={`timeline-card tone-${stage.tone}`}>
                      <div className="timeline-top">
                        <h4>{stage.label}</h4>
                        <span className={`status-pill ${stage.tone}`}>{stage.status}</span>
                      </div>
                      <p>{stage.detail}</p>
                    </article>
                  ))}
                </div>
                {onboardingRetired ? (
                  <div className="error">
                    <strong>Name collision or retired identity</strong>
                    <p>
                      Foundry returned a retired registration state. The easiest recovery is to create a new local agent with
                      a different name and request a fresh bootstrap ticket.
                    </p>
                  </div>
                ) : null}
                {onboardingApproved ? (
                  <div className="reply">
                    <strong>Foundry approval observed</strong>
                    <p>
                      The agent now has the credentials it needs to talk through the Foundry-managed gateway.
                    </p>
                  </div>
                ) : (
                  <p className="muted">
                    Current discovery status: <strong>{textValue(discoveryStatus, "n/a")}</strong>. If invite or register is
                    still pending, leave this page open for a moment and use <code>Refresh flow</code> if needed.
                  </p>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">6</span>
                  <div>
                    <h4>{guideRunTarget === "cloud_run" ? "Run Cloud Run smoke test" : "Run playground smoke test"}</h4>
                    <p className="muted">
                      {guideRunTarget === "cloud_run"
                        ? "Cloud Run uses pull transport, so Dev Board checks the deployment, Scheduler, and the latest poll."
                        : "Once the agent is online, jump into the playground with a starter prompt and confirm the chat loop."}
                    </p>
                  </div>
                </div>
                {guideRunTarget === "cloud_run" ? (
                  <>
                    <div className="kv-list compact-kv">
                      <div>
                        <span>Deployment</span>
                        <strong>{textValue(displayedCloudRunDeployment?.status, "not deployed")}</strong>
                      </div>
                      <div>
                        <span>Service URL</span>
                        <strong>{displaySafeUrl(textValue(displayedCloudRunDeployment?.result?.service_url), "pending")}</strong>
                      </div>
                      <div>
                        <span>Scheduler</span>
                        <strong>
                          {textValue(
                            displayedCloudRunDeployment?.result?.scheduler_job,
                            cloudRunForm.skip_scheduler ? "skipped" : "not observed",
                          )}
                        </strong>
                      </div>
                      <div>
                        <span>Last poll</span>
                        <strong>{textValue(bootstrapState.last_polled_at, "not observed")}</strong>
                      </div>
                      <div>
                        <span>Poll endpoint</span>
                        <strong>{displaySafeUrl(textValue(displayedCloudRunDeployment?.result?.poll_url), "pending")}</strong>
                      </div>
                    </div>
                    {smokeObserved ? (
                      <div className="reply compact-reply">
                        <strong>Cloud Run poll observed</strong>
                        <p>Deployment succeeded and the agent has reported a recent poll through the Foundry bootstrap state.</p>
                      </div>
                    ) : (
                      <p className="muted">
                        Waiting for a successful deployment and at least one poll from the Cloud Run runtime.
                      </p>
                    )}
                    <div className="actions split-actions">
                      <button
                        className="secondary"
                        onClick={() => {
                          if (displayedCloudRunDeployment?.id) {
                            void refreshCloudRunDeployment(displayedCloudRunDeployment.id);
                          } else {
                            void refreshCloudRunDeployments();
                          }
                        }}
                      >
                        Refresh Cloud Run job
                      </button>
                      <button className="secondary" onClick={() => probeHandshake()} disabled={handshakeLoading || !selectedAgent}>
                        {handshakeLoading ? "Refreshing flow..." : "Refresh flow"}
                      </button>
                      {displayedCloudRunDeployment?.result?.service_url ? (
                        <a className="link-button" href={displayedCloudRunDeployment.result.service_url} target="_blank" rel="noreferrer">
                          Open service
                        </a>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="kv-list compact-kv">
                      <div>
                        <span>Agent runtime</span>
                        <strong>{textValue(selectedAgentEntry?.status, "offline")}</strong>
                      </div>
                      <div>
                        <span>Gateway credentials</span>
                        <strong>{gatewayReady ? "ready" : "not observed yet"}</strong>
                      </div>
                      <div>
                        <span>Conversation started</span>
                        <strong>{hasConversation ? "yes" : "no"}</strong>
                      </div>
                    </div>
                    <div className="actions split-actions">
                      <button onClick={openPlaygroundWithStarterMessage} disabled={!playgroundReady}>
                        Open playground with starter prompt
                      </button>
                      <button className="secondary" onClick={() => setActiveView("agent")} disabled={!selectedAgent}>
                        Open agent card
                      </button>
                    </div>
                  </>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">7</span>
                  <div>
                    <h4>Test the agent in Foundry</h4>
                    <p className="muted">
                      Use the same Foundry target as the GitHub login, or switch it here before you open <code>{foundryPortalUrl}</code>{" "}
                      to validate the same agent inside the live Foundry product after bootstrap is approved.
                    </p>
                  </div>
                </div>
                <FoundryUrlChooser label="Foundry portal URL" value={foundryUrl} onChange={setFoundryUrl} />
                <div className="kv-list compact-kv">
                  <div>
                    <span>Portal target</span>
                    <strong>{displaySafeUrl(foundryPortalUrl)}</strong>
                  </div>
                  <div>
                    <span>Foundry access</span>
                    <strong>GitHub sign-in only</strong>
                  </div>
                  <div>
                    <span>Browser session</span>
                    <strong>{foundryBrowserSessionReady ? "ready from step 3" : "sign in required"}</strong>
                  </div>
                  <div>
                    <span>Agent approval</span>
                    <strong>{onboardingApproved ? "ready" : "wait for approval first"}</strong>
                  </div>
                </div>
                <div className="reply">
                  <strong>{foundryBrowserSessionReady ? "You can likely go straight in" : "GitHub login is required"}</strong>
                  <p>
                    {foundryBrowserSessionReady
                      ? "If you completed the GitHub popup login in step 3 in this browser, Foundry will usually open with that session already available. If it still asks you to sign in, continue with GitHub there."
                      : "Foundry uses GitHub login for this flow. If you have not signed in through the popup earlier on this page, Foundry will ask you to continue with GitHub before you can test the agent."}
                  </p>
                </div>
                <div className="actions split-actions">
                  <button onClick={openFoundryPortal} disabled={!onboardingApproved}>
                    Open selected Foundry to test agent
                  </button>
                  <button className="secondary" onClick={() => setActiveView("playground")} disabled={!selectedAgent}>
                    Back to playground
                  </button>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {activeView === "agent" ? (
          <div className="view-grid">
            {agentCardTab === "overview" ? (
              <>
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Agent selection</p>
                      <h3>Overview</h3>
                    </div>
                  </div>
                  <p className="muted">
                    {selectedManifest?.description || "Create or select an agent source to inspect local debug status, model, and bootstrap state."}
                  </p>
                  <label>
                    Agent
                    <select
                      value={selectedAgent}
                      onChange={(event) => setSelectedAgent(event.target.value)}
                      disabled={agents.length === 0}
                    >
                      {agents.length === 0 ? <option value="">No local agents yet</option> : null}
                      {agents.map((agent) => (
                        <option key={agent.name} value={agent.name}>
                          {agent.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Mode
                    <select value={mode} onChange={(event) => setMode(event.target.value as ContextMode)}>
                      <option value="direct">direct</option>
                      <option value="inline">inline</option>
                    </select>
                  </label>
                  <label>
                    Username
                    <input value={username} onChange={(event) => setUsername(event.target.value)} />
                  </label>
                  <div className="actions">
                    <button className="full-width" onClick={() => setActiveView("playground")} disabled={!playgroundReady}>
                      Open local playground
                    </button>
                  </div>
                  {!playgroundReady && selectedAgent ? (
                    <p className="muted">
                      Playground is for local runtime debugging. Start the local runtime before using it.
                    </p>
                  ) : null}
                </section>

                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Status</p>
                      <h3>Quick summary</h3>
                    </div>
                  </div>
                  <div className="chips">
                    <span className={`chip tone-${runtimeBadge.tone}`}>{runtimeBadge.label}</span>
                    {capabilities.map((capability) => (
                      <span className="chip" key={capability}>
                        {capability}
                      </span>
                    ))}
                  </div>
                  <div className="kv-list compact-kv">
                    <div>
                      <span>Local debug URL</span>
                      <strong>{displaySafeUrl(textValue(selectedAgentEntry?.base_url), "n/a")}</strong>
                    </div>
                    <div>
                      <span>Foundry URL</span>
                      <strong>{textValue(handshakeResult?.foundry.url, textValue(bootstrapState.foundry_base_url, "n/a"))}</strong>
                    </div>
                    <div>
                      <span>GitHub login</span>
                      <strong>{displayedDeveloperLogin}</strong>
                    </div>
                    <div>
                      <span>Bootstrap delivery</span>
                      <strong>{bootstrapDelivery}</strong>
                    </div>
                    <div>
                      <span>Gateway credentials</span>
                      <strong>{gatewayReady ? "ready" : "not observed yet"}</strong>
                    </div>
                    <div>
                      <span>Latest model</span>
                      <strong>{latestModel || "not observed yet"}</strong>
                    </div>
                  </div>
                </section>

                <section className="panel lifecycle-panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Lifecycle</p>
                      <h3>Retire agent</h3>
                    </div>
                  </div>
                  <p className="muted">
                    Retiring keeps Foundry history and audit data, removes active bindings, and stops this Dev Board runtime.
                  </p>
                  <div className="kv-list compact-kv">
                    <div>
                      <span>Selected source</span>
                      <strong>{textValue(selectedLocalAgent?.label || selectedAgent, "none selected")}</strong>
                    </div>
                    <div>
                      <span>Foundry action</span>
                      <strong>soft retire</strong>
                    </div>
                    <div>
                      <span>Developer login</span>
                      <strong>{developerSessionReady ? displayedDeveloperLogin : "required"}</strong>
                    </div>
                  </div>
                  {!developerSessionReady ? (
                    <div className="guide-blocker">
                      <strong>GitHub login required</strong>
                      <p>Use Guided setup step 3 before retiring a Foundry-linked agent.</p>
                    </div>
                  ) : null}
                  <div className="actions split-actions">
                    <button
                      className="secondary danger"
                      onClick={() => retireLocalAgent(selectedAgent)}
                      disabled={!selectedAgent || !developerSessionReady || Boolean(retiringAgent)}
                    >
                      {retiringAgent && retiringAgent === selectedAgent ? "Retiring..." : "Retire selected agent"}
                    </button>
                  </div>
                  {localAgentNotice ? <div className="reply">{localAgentNotice}</div> : null}
                  {localAgentError ? <div className="error">{localAgentError}</div> : null}
                </section>
              </>
            ) : null}

            {agentCardTab === "runtimes" ? (
              <>
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Template launcher</p>
                      <h3>Create agent source</h3>
                    </div>
                    <button className="secondary" onClick={() => refreshAgentInventory(selectedAgent)} disabled={localAgentLoading}>
                      {localAgentLoading ? "Refreshing..." : "Refresh inventory"}
                    </button>
                  </div>
                  <p className="muted">
                    <code>npm run dev-board</code> starts the board. Create a durable source from a template, then start
                    a local debug runtime or deploy that source to Cloud Run.
                  </p>
                  <label>
                    Template
                    <select
                      value={createAgentForm.template_id}
                      onChange={(event) => updateCreateAgentForm("template_id", event.target.value)}
                    >
                      {localTemplates.map((template) => (
                        <option key={template.id} value={template.id}>
                          {template.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Agent name
                    <input
                      value={createAgentForm.name}
                      onChange={(event) => updateCreateAgentForm("name", event.target.value)}
                      placeholder="my_agent_dev"
                    />
                  </label>
                  <label>
                    Label
                    <input
                      value={createAgentForm.label}
                      onChange={(event) => updateCreateAgentForm("label", event.target.value)}
                      placeholder="Optional friendly label"
                    />
                  </label>
                  <label>
                    Preferred port
                    <input
                      value={createAgentForm.preferred_port}
                      onChange={(event) => updateCreateAgentForm("preferred_port", event.target.value)}
                      placeholder="8085"
                    />
                  </label>
                  <FoundryUrlChooser
                    label="Foundry URL for bootstrap"
                    value={foundryUrl}
                    onChange={setFoundryUrl}
                    helper=".com is the CN target, .ai is the WW target. You can still type any other compatible Foundry URL."
                  />
                  <div className="actions split-actions">
                    <button onClick={createLocalAgent} disabled={localAgentLoading}>
                      {localAgentLoading ? "Creating..." : "Create from template"}
                    </button>
                  </div>
                  {localAgentNotice ? <div className="reply">{localAgentNotice}</div> : null}
                  {localAgentError ? <div className="error">{localAgentError}</div> : null}
                </section>

                <section className="panel span-two">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Runtime inventory</p>
                      <h3>Agent sources</h3>
                    </div>
                    <span className="muted">{localAgents.length} source(s)</span>
                  </div>
                  {localAgents.length === 0 ? (
                    <div className="empty-state">
                      <h4>No local agents yet</h4>
                      <p className="muted">
                        Start by creating a template instance on the left. Each agent gets its own directory, port, logs, and
                        bootstrap state under <code>.dev-board/agents/</code>.
                      </p>
                    </div>
                  ) : (
                    <div className="runtime-grid">
                      {localAgents.map((agent) => {
                        const catalogEntry = agents.find((entry) => entry.name === agent.name);
                        const runtimeStatus = textValue(agent.status, "stopped");
                        const runtimeTone = statusTone(runtimeStatus);
                        return (
                          <article
                            key={agent.name}
                            className={`runtime-card ${selectedAgent === agent.name ? "selected-runtime" : ""}`}
                          >
                            <div className="card-header">
                              <div>
                                <h4>{agent.label}</h4>
                                <p className="muted">{agent.name}</p>
                              </div>
                              <div className="chips">
                                <span className={`status-pill ${runtimeTone}`}>{runtimeStatus}</span>
                                <span className={`status-pill ${statusTone(textValue(catalogEntry?.status, "offline"))}`}>
                                  {textValue(catalogEntry?.status, "offline")}
                                </span>
                              </div>
                            </div>
                            <div className="kv-list compact-kv">
                              <div>
                                <span>Base URL</span>
                                <strong>{displaySafeUrl(agent.base_url)}</strong>
                              </div>
                              <div>
                                <span>Port</span>
                                <strong>{agent.port}</strong>
                              </div>
                              <div>
                                <span>Foundry</span>
                                <strong>{textValue(agent.foundry_url, "not set")}</strong>
                              </div>
                              <div>
                                <span>PID</span>
                                <strong>{textValue(agent.pid, "n/a")}</strong>
                              </div>
                              <div>
                                <span>Instance dir</span>
                                <strong>{agent.instance_dir}</strong>
                              </div>
                              <div>
                                <span>Log file</span>
                                <strong>{textValue(agent.log_path, "n/a")}</strong>
                              </div>
                            </div>
                            <div className="actions split-actions">
                              <button className="secondary" onClick={() => setSelectedAgent(agent.name)}>
                                Select
                              </button>
                              {runtimeStatus === "running" ? (
                                <button className="secondary" onClick={() => stopLocalAgent(agent.name)} disabled={localAgentLoading}>
                                  Stop
                                </button>
                              ) : (
                                <button onClick={() => startLocalAgent(agent.name)} disabled={localAgentLoading}>
                                  Start
                                </button>
                              )}
                              <button
                                className="secondary"
                                onClick={() => {
                                  setSelectedAgent(agent.name);
                                  setActiveView("playground");
                                }}
                              >
                                Open playground
                              </button>
                              <button
                                className="secondary danger"
                                onClick={() => retireLocalAgent(agent.name)}
                                disabled={!developerSessionReady || Boolean(retiringAgent)}
                                title={developerSessionReady ? "Retire this agent in Foundry and Dev Board" : "Login with GitHub before retiring"}
                              >
                                {retiringAgent === agent.name ? "Retiring..." : "Retire"}
                              </button>
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </section>
              </>
            ) : null}

            {agentCardTab === "cloud-run" ? (
              <>
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Google Cloud</p>
                      <h3>gcloud status</h3>
                    </div>
                    <button className="secondary" onClick={refreshCloudRunStatus} disabled={cloudRunStatusLoading}>
                      {cloudRunStatusLoading ? "Checking..." : "Refresh"}
                    </button>
                  </div>
                  <div className="chips">
                    <span className={`chip tone-${cloudRunStatus?.gcloud?.authenticated ? "success" : "warn"}`}>
                      {cloudRunStatus?.gcloud?.authenticated ? "gcloud authenticated" : "gcloud login needed"}
                    </span>
                    <span className={`chip tone-${cloudRunStatus?.docker?.installed ? "success" : "warn"}`}>
                      {cloudRunStatus?.docker?.installed ? "docker available" : "docker missing"}
                    </span>
                  </div>
                  <div className="kv-list compact-kv">
                    <div>
                      <span>Account</span>
                      <strong>{textValue(cloudRunStatus?.gcloud?.active_account, "not authenticated")}</strong>
                    </div>
                    <div>
                      <span>Project</span>
                      <strong>{textValue(cloudRunStatus?.gcloud?.project, textValue(cloudRunStatus?.defaults?.project, "not set"))}</strong>
                    </div>
                    <div>
                      <span>Region</span>
                      <strong>{textValue(cloudRunStatus?.gcloud?.region, textValue(cloudRunStatus?.defaults?.region, "us-central1"))}</strong>
                    </div>
                    <div>
                      <span>gcloud</span>
                      <strong>{textValue(cloudRunStatus?.gcloud?.version, "not found")}</strong>
                    </div>
                    <div>
                      <span>Docker</span>
                      <strong>{textValue(cloudRunStatus?.docker?.version, "not found")}</strong>
                    </div>
                  </div>
                  {!cloudRunStatus?.gcloud?.authenticated ? (
                    <div className="code-block">
                      <pre>{textValue(cloudRunStatus?.commands?.login_no_browser, "gcloud auth login --no-launch-browser")}</pre>
                    </div>
                  ) : null}
                  <div className="actions split-actions">
                    <button className="secondary" onClick={startCloudRunAuth} disabled={cloudRunAuthLoading}>
                      {cloudRunAuthLoading
                        ? "Starting login..."
                        : cloudRunAuthenticated
                          ? "Switch Google account"
                          : "Google Cloud login"}
                    </button>
                    {cloudRunAuthSession && cloudRunAuthInProgress ? (
                      <button className="secondary" onClick={cancelCloudRunAuth} disabled={cloudRunAuthLoading}>
                        Cancel login
                      </button>
                    ) : null}
                  </div>
                  {cloudRunAuthSession ? (
                    <div className="cloud-run-auth-box">
                      <div className="kv-list compact-kv">
                        <div>
                          <span>Login status</span>
                          <strong>{cloudRunAuthSession.status}</strong>
                        </div>
                      </div>
                      {cloudRunAuthSucceeded ? (
                        <p className="muted">Google Cloud login succeeded. The active account above is ready for deploy.</p>
                      ) : cloudRunAuthSession.auth_url ? (
                        <a className="link-button" href={cloudRunAuthSession.auth_url} target="_blank" rel="noreferrer">
                          Open Google login
                        </a>
                      ) : (
                        <div className="code-block compact-code-block">
                          <pre>{(cloudRunAuthSession.logs || []).slice(-4).join("\n") || "Waiting for Google login URL..."}</pre>
                        </div>
                      )}
                      {cloudRunAuthInProgress ? (
                        <div className="cloud-run-auth-code-row">
                          <label>
                            Authorization code
                            <input
                              value={cloudRunAuthCode}
                              onChange={(event) => setCloudRunAuthCode(event.target.value)}
                              placeholder="Paste code from Google"
                            />
                          </label>
                          <button onClick={submitCloudRunAuthCode} disabled={cloudRunAuthLoading || !cloudRunAuthCode.trim()}>
                            Submit code
                          </button>
                        </div>
                      ) : null}
                      {cloudRunAuthSession.error ? <div className="error">{cloudRunAuthSession.error}</div> : null}
                    </div>
                  ) : null}
                  {cloudRunStatus?.errors?.length ? (
                    <div className="error">
                      <strong>Cloud Run preflight</strong>
                      <p>{cloudRunStatus.errors.join(" ")}</p>
                    </div>
                  ) : null}
                </section>

                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Deploy target</p>
                      <h3>Selected agent</h3>
                    </div>
                  </div>
                  <label>
                    Source agent
                    <select
                      value={selectedAgent}
                      onChange={(event) => setSelectedAgent(event.target.value)}
                      disabled={localAgents.length === 0}
                    >
                      {localAgents.length === 0 ? <option value="">Create one first</option> : null}
                      {localAgents.map((agent) => (
                        <option key={agent.name} value={agent.name}>
                          {agent.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <FoundryUrlChooser
                    label="Foundry URL"
                    value={foundryUrl}
                    onChange={setFoundryUrl}
                    helper="The Cloud Run worker registers against this Foundry and uses pull transport."
                  />
                  <div className="kv-list compact-kv">
                    <div>
                      <span>Source path</span>
                      <strong>{textValue(selectedLocalAgent?.instance_dir, "select a source agent")}</strong>
                    </div>
                    <div>
                      <span>Template</span>
                      <strong>{textValue(selectedLocalAgent?.template_id, "n/a")}</strong>
                    </div>
                    <div>
                      <span>Local debug runtime</span>
                      <strong>
                        {textValue(selectedLocalAgent?.status, "unknown")} · {displaySafeUrl(textValue(selectedLocalAgent?.base_url), "n/a")}
                      </strong>
                    </div>
                  </div>
                </section>

                <section className="panel span-two">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Cloud Run deployment</p>
                      <h3>Build, push, deploy, and schedule polling</h3>
                    </div>
                    <button className="secondary" onClick={refreshCloudRunDeployments}>
                      Refresh jobs
                    </button>
                  </div>
                  <div className="cloud-run-form-grid">
                    <label>
                      GCP project
                      <input
                        value={cloudRunForm.project}
                        onChange={(event) => updateCloudRunForm("project", event.target.value)}
                        placeholder="your-gcp-project"
                      />
                    </label>
                    <label>
                      Region
                      <input
                        list="cloud-run-region-options"
                        value={cloudRunForm.region}
                        onChange={(event) => updateCloudRunForm("region", event.target.value)}
                        placeholder="us-central1"
                      />
                      <span className="field-helper">Quick picks: US, UK London, Hong Kong, Singapore.</span>
                    </label>
                    <label>
                      Memory
                      <input
                        value={cloudRunForm.memory}
                        onChange={(event) => updateCloudRunForm("memory", event.target.value)}
                        placeholder="512Mi"
                      />
                    </label>
                    <label>
                      CPU
                      <input
                        value={cloudRunForm.cpu}
                        onChange={(event) => updateCloudRunForm("cpu", event.target.value)}
                        placeholder="1"
                      />
                    </label>
                    <label>
                      Min instances
                      <input
                        value={cloudRunForm.min_instances}
                        onChange={(event) => updateCloudRunForm("min_instances", event.target.value)}
                        placeholder="0"
                      />
                    </label>
                    <label>
                      Poll schedule
                      <input
                        value={cloudRunForm.poll_schedule}
                        onChange={(event) => updateCloudRunForm("poll_schedule", event.target.value)}
                        placeholder={DEFAULT_CLOUD_RUN_POLL_SCHEDULE}
                      />
                      <span className="field-helper">Cloud Scheduler cron. Default runs every 5 minutes.</span>
                    </label>
                  </div>
                  <label className="inline-toggle cloud-run-toggle">
                    <input
                      type="checkbox"
                      checked={cloudRunForm.skip_scheduler}
                      onChange={(event) => updateCloudRunForm("skip_scheduler", event.target.checked)}
                    />
                    Skip Cloud Scheduler
                  </label>
                  <div className="actions split-actions">
                    <button
                      className="secondary"
                      onClick={() => deployCloudRun(true)}
                      disabled={cloudRunDeploying || !selectedLocalAgent}
                    >
                      Dry run
                    </button>
                    <button
                      onClick={() => deployCloudRunAfterSourceClaim(false)}
                      disabled={cloudRunDeploying || Boolean(cloudRunDeployBlockReason)}
                    >
                      {cloudRunDeployButtonLabel}
                    </button>
                    {cloudRunCurrentJob && ["queued", "running"].includes(textValue(cloudRunCurrentJob.status)) ? (
                      <button className="secondary" onClick={cancelCloudRunDeployment}>
                        Cancel
                      </button>
                    ) : null}
                  </div>
                  {cloudRunDeployBlockReason ? (
                    <div className="guide-blocker">
                      <strong>Deploy is locked</strong>
                      <p>{cloudRunDeployBlockReason}</p>
                    </div>
                  ) : null}
                  {cloudRunError ? <div className="error">{cloudRunError}</div> : null}
                </section>

                <section className="panel span-two">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Deployment result</p>
                      <h3>{cloudRunCurrentJob ? cloudRunCurrentJob.service_name : "No Cloud Run job selected"}</h3>
                    </div>
                    {cloudRunCurrentJob ? (
                      <span className={`status-pill ${statusTone(textValue(cloudRunCurrentJob.status))}`}>
                        {cloudRunCurrentJob.status}
                      </span>
                    ) : null}
                  </div>
                  {cloudRunCurrentJob ? (
                    <>
                      <div className="kv-list compact-kv">
                        <div>
                          <span>Job ID</span>
                          <strong>{cloudRunCurrentJob.id}</strong>
                        </div>
                        <div>
                          <span>Image</span>
                          <strong>{textValue(cloudRunCurrentJob.result?.image_tag, "pending")}</strong>
                        </div>
                        <div>
                          <span>Service URL</span>
                          <strong>{displaySafeUrl(textValue(cloudRunCurrentJob.result?.service_url), "pending")}</strong>
                        </div>
                        <div>
                          <span>Scheduler</span>
                          <strong>{textValue(cloudRunCurrentJob.result?.scheduler_job, cloudRunCurrentJob.dry_run ? "dry run" : "skipped")}</strong>
                        </div>
                      </div>
                      {cloudRunCurrentJob.result?.service_url ? (
                        <div className="actions split-actions">
                          <a className="external-link-chip" href={cloudRunCurrentJob.result.service_url} target="_blank" rel="noreferrer">
                            Service
                          </a>
                          <a className="external-link-chip" href={cloudRunCurrentJob.result.health_url} target="_blank" rel="noreferrer">
                            Health
                          </a>
                        </div>
                      ) : null}
                      <div className="code-block cloud-run-log">
                        <pre>{(cloudRunCurrentJob.logs || []).join("\n") || "Waiting for deployment output..."}</pre>
                      </div>
                    </>
                  ) : (
                    <div className="empty-state">
                      <h4>No deployment jobs yet</h4>
                      <p className="muted">Start with a dry run to preview the gcloud and Docker commands for the selected agent.</p>
                    </div>
                  )}
                  {cloudRunDeployments.length > 1 ? (
                    <div className="runtime-grid cloud-run-history">
                      {cloudRunDeployments.slice(0, 4).map((job) => (
                        <article key={job.id} className="runtime-card">
                          <div className="card-header">
                            <div>
                              <h4>{job.service_name}</h4>
                              <p className="muted">{job.id}</p>
                            </div>
                            <span className={`status-pill ${statusTone(textValue(job.status))}`}>{job.status}</span>
                          </div>
                          <button className="secondary" onClick={() => setCloudRunCurrentJob(job)}>
                            View logs
                          </button>
                        </article>
                      ))}
                    </div>
                  ) : null}
                </section>
              </>
            ) : null}

            {agentCardTab === "runtime" ? (
              <>
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Runtime LLM</p>
                      <h3>Model and source</h3>
                    </div>
                    <span className={`status-pill ${runtimeBadge.tone}`}>{runtimeBadge.label}</span>
                  </div>
                  <p>{runtimeBadge.detail}</p>
                  <div className="kv-list">
                    <div>
                      <span>Manifest billing</span>
                      <strong>{manifestBillingModel || "n/a"}</strong>
                    </div>
                    <div>
                      <span>Bootstrap has key</span>
                      <strong>{String(bootstrapHasGatewayKey)}</strong>
                    </div>
                    <div>
                      <span>Bootstrap has base URL</span>
                      <strong>{String(bootstrapHasGatewayBase)}</strong>
                    </div>
                    <div>
                      <span>Latest reply used LLM</span>
                      <strong>{String(latestUsedLlm)}</strong>
                    </div>
                    <div>
                      <span>Latest model</span>
                      <strong>{latestModel || "not observed yet"}</strong>
                    </div>
                    <div>
                      <span>Latest source</span>
                      <strong>{latestModelSource || "not observed yet"}</strong>
                    </div>
                    <div>
                      <span>Foundry URL</span>
                      <strong>{textValue(handshakeResult?.foundry.url, textValue(bootstrapState.foundry_base_url, "n/a"))}</strong>
                    </div>
                    <div>
                      <span>GitHub login</span>
                      <strong>{displayedDeveloperLogin}</strong>
                    </div>
                  </div>
                  {latestLlmError ? (
                    <div className="error">
                      <strong>Latest LLM error</strong>
                      <p>{latestLlmError}</p>
                    </div>
                  ) : null}
                </section>

                <section className="panel span-two">
                  <div className="card-header">
                    <div>
                      <p className="eyebrow">Temporary override</p>
                      <h3>Dev mode</h3>
                    </div>
                    <label className="inline-toggle">
                      <input
                        type="checkbox"
                        checked={devModeEnabled}
                        onChange={(event) => setDevModeEnabled(event.target.checked)}
                      />
                      Enable
                    </label>
                  </div>
                  <p className="muted">
                    Use this only for local testing. Values are sent with the next request and are not persisted by the board.
                  </p>
                  <label>
                    Model
                    <input
                      value={devOverrides.model}
                      onChange={(event) => updateDevOverride("model", event.target.value)}
                      placeholder="ccfoundry-local"
                      disabled={!devModeEnabled}
                    />
                  </label>
                  <label>
                    Base URL
                    <input
                      value={devOverrides.base_url}
                      onChange={(event) => updateDevOverride("base_url", event.target.value)}
                      placeholder="http://localhost:4000"
                      disabled={!devModeEnabled}
                    />
                  </label>
                  <label>
                    API key
                    <input
                      type="password"
                      value={devOverrides.api_key}
                      onChange={(event) => updateDevOverride("api_key", event.target.value)}
                      placeholder="sk-..."
                      disabled={!devModeEnabled}
                    />
                  </label>
                  {devModeEnabled ? (
                    <p className="muted">
                      {hasDevOverrides
                        ? "The next request will carry these overrides."
                        : "Dev mode is on, but blank fields still fall back to the agent runtime config."}
                    </p>
                  ) : null}
                  <p className="muted">
                    Not exactly self-hosted: once Foundry approves this agent and hands out gateway credentials, the runtime is
                    still yours, but the model path becomes Foundry-managed.
                  </p>
                  {latestLlmError ? (
                    <div className="error">
                      <strong>Latest LLM error</strong>
                      <p>{latestLlmError}</p>
                    </div>
                  ) : null}
                </section>
              </>
            ) : null}

            {agentCardTab === "profile" ? (
              <>
                <section className="panel span-two">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Profile</p>
                      <h3>Manifest and skills</h3>
                    </div>
                  </div>
                  <div className="kv-list">
                    <div>
                      <span>Version</span>
                      <strong>{selectedManifest?.version || "n/a"}</strong>
                    </div>
                    <div>
                      <span>Billing model</span>
                      <strong>{manifestBillingModel || "n/a"}</strong>
                    </div>
                    <div>
                      <span>Needs gateway</span>
                      <strong>{String(manifestNeedsGateway)}</strong>
                    </div>
                  </div>
                  <p className="muted">{textValue(selectedManifest?.billing?.fee_note)}</p>
                  <div className="chips">
                    {loadedSkills.length === 0 ? <span className="chip">No skills declared</span> : null}
                    {loadedSkills.map((skill) => (
                      <span className="chip" key={skill}>
                        {skill}
                      </span>
                    ))}
                  </div>
                </section>

                <section className="panel span-two">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Templates</p>
                      <h3>Available starting points</h3>
                    </div>
                  </div>
                  <div className="runtime-grid">
                    {localTemplates.map((template) => (
                      <article key={template.id} className="runtime-card">
                        <div className="card-header">
                          <div>
                            <h4>{template.label}</h4>
                            <p className="muted">{template.id}</p>
                          </div>
                          <span className="status-pill neutral">template</span>
                        </div>
                        <p className="muted">{template.description}</p>
                      </article>
                    ))}
                  </div>
                </section>
              </>
            ) : null}
          </div>
        ) : null}

        {activeView === "playground" ? (
          <div className="view-grid">
            <section className="panel span-two">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Local debug playground</p>
                  <h3>Transcript</h3>
                </div>
                <span className="muted">Current conversation id: {conversationId || "new session"}</span>
              </div>
              <div className="transcript">
                {transcript.length === 0 ? (
                  <div className="empty-state">
                    <h4>No messages yet</h4>
                    <p className="muted">
                      Start a local runtime, then send a direct question to test the agent before deploying it as a worker.
                    </p>
                  </div>
                ) : null}
                {transcript.map((entry, index) => (
                  <div key={index} className="turn">
                    <div className="turn-user">User: {entry.user}</div>
                    <div className="turn-agent">Agent: {entry.assistant}</div>
                  </div>
                ))}
                {reply && transcript.length === 0 ? (
                  <div className="turn draft-turn">
                    <div className="turn-user">Latest reply</div>
                    <div className="turn-agent">{reply}</div>
                  </div>
                ) : null}
              </div>
            </section>

            <section className="panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Input</p>
                  <h3>Send to local runtime</h3>
                </div>
              </div>
              <label>
                Agent
                <select
                  value={selectedAgent}
                  onChange={(event) => setSelectedAgent(event.target.value)}
                  disabled={agents.length === 0}
                >
                  {agents.length === 0 ? <option value="">No local agents yet</option> : null}
                  {agents.map((agent) => (
                    <option key={agent.name} value={agent.name}>
                      {agent.label}
                    </option>
                  ))}
                </select>
              </label>
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                placeholder="Ask the agent something..."
                rows={6}
              />
              {!selectedAgent ? (
                <p className="muted">
                  Create an agent source in <strong>Agent card</strong> before sending a message.
                </p>
              ) : !playgroundReady ? (
                <p className="muted">
                  Playground is only for local runtime debugging. The selected local runtime is{" "}
                  <strong>{textValue(selectedAgentEntry?.status, "offline")}</strong>.
                </p>
              ) : null}
              <div className="actions">
                <button onClick={() => void sendMessage()} disabled={loading || !playgroundReady}>
                  {loading ? "Sending..." : "Send"}
                </button>
              </div>
              {reply ? (
                <div className="reply">
                  <h4>Latest reply</h4>
                  <p>{reply}</p>
                </div>
              ) : null}
              {steps.length > 0 ? (
                <div className="reply">
                  <h4>Steps</h4>
                  {steps.map((step, index) => (
                    <p key={`${step.status}-${index}`}>
                      [{step.status}] {step.text}
                    </p>
                  ))}
                </div>
              ) : null}
              {error ? <div className="error">{error}</div> : null}
            </section>

            <section className="panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Manifest</p>
                  <h3>Agent profile</h3>
                </div>
              </div>
              <div className="kv-list">
                <div>
                  <span>Version</span>
                  <strong>{selectedManifest?.version || "n/a"}</strong>
                </div>
                <div>
                  <span>Billing model</span>
                  <strong>{manifestBillingModel || "n/a"}</strong>
                </div>
                <div>
                  <span>Needs gateway</span>
                  <strong>{String(manifestNeedsGateway)}</strong>
                </div>
              </div>
              <p className="muted">{textValue(selectedManifest?.billing?.fee_note)}</p>
            </section>

            <section className="panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Skills</p>
                  <h3>Loaded skills</h3>
                </div>
              </div>
              <div className="chips">
                {loadedSkills.length === 0 ? <span className="chip">No skills declared</span> : null}
                {loadedSkills.map((skill) => (
                  <span className="chip" key={skill}>
                    {skill}
                  </span>
                ))}
              </div>
            </section>
          </div>
        ) : null}

        {activeView === "earnings" ? (
          <div className="view-grid" style={{ gridTemplateColumns: "1fr" }}>
            <section className="panel">
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Earnings</p>
                  <h3>Settlement dashboard</h3>
                </div>
                <button
                  className="secondary"
                  onClick={() => void fetchSettlements()}
                  disabled={settlementsLoading}
                >
                  {settlementsLoading ? "Loading..." : "Refresh"}
                </button>
              </div>

              {(() => {
                const uniqueAgents = [
                  ...new Set(
                    settlements
                      .map((s) => String(s.agent_name || settlementsMeta.foundry_agent_name || "unknown"))
                      .filter(Boolean),
                  ),
                ];
                const filtered = earningsAgentFilter === "__all__"
                  ? settlements
                  : settlements.filter((s) => String(s.agent_name || "") === earningsAgentFilter);
                const filteredTotal = filtered.reduce((sum, s) => sum + settlementNetAmount(s), 0);
                const matchedNames = settlementsMeta.matched_agent_names || [];
                return (
                <>
                  <div className="dashboard-grid" style={{ marginBottom: "18px" }}>
                    <div className="metric-card">
                      <span className="metric-label">Net earned</span>
                      <strong style={{ fontSize: "2rem", color: "var(--success)" }}>
                        ${filteredTotal.toFixed(2)}
                      </strong>
                      <span className="muted" style={{ fontSize: "0.84rem" }}>
                        {filtered.length} settlement{filtered.length !== 1 ? "s" : ""}
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">Selected source</span>
                      <strong>{textValue(settlementsMeta.agent_name, textValue(selectedLocalAgent?.name, "all sources"))}</strong>
                      <span className="muted" style={{ fontSize: "0.84rem" }}>
                        {textValue(selectedLocalAgent?.status, "source")} local debug runtime
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">Foundry identity</span>
                      <strong>{textValue(settlementsMeta.foundry_agent_name, "not registered yet")}</strong>
                      <span className="muted" style={{ fontSize: "0.84rem" }}>
                        {matchedNames.length ? `matching ${matchedNames.join(", ")}` : "runtime-agnostic earnings"}
                      </span>
                    </div>
                    <div className="metric-card">
                      <span className="metric-label">Agent filter</span>
                      <select
                        value={earningsAgentFilter}
                        onChange={(e) => setEarningsAgentFilter(e.target.value)}
                        style={{
                          fontSize: "1rem",
                          fontWeight: 600,
                          padding: "6px 10px",
                          borderRadius: "8px",
                          border: "1px solid var(--border)",
                          background: "var(--surface)",
                          color: "var(--text)",
                          cursor: "pointer",
                          maxWidth: "100%",
                        }}
                      >
                        <option value="__all__">All settlement names ({uniqueAgents.length})</option>
                        {uniqueAgents.map((name) => (
                          <option key={name} value={name}>{name}</option>
                        ))}
                      </select>
                      <span className="muted" style={{ fontSize: "0.84rem" }}>
                        Foundry settlement agent names
                      </span>
                    </div>
                  </div>

                  {settlementsError ? (
                    <div className="error">
                      <strong>Settlement fetch error</strong>
                      <p>{settlementsError}</p>
                    </div>
                  ) : null}

                  {filtered.length === 0 && !settlementsLoading && !settlementsError ? (
                    <div className="empty-state">
                      <h4>No settlements yet</h4>
                      <p className="muted">
                        Settlements appear here after Foundry verifies your agent's completed tasks and triggers payment.
                        Connect to a Foundry instance with your agent onboarded to see settlement history.
                      </p>
                    </div>
                  ) : null}

                  {filtered.length > 0 ? (
                    <div className="timeline-grid single-column">
                      {filtered.map((s, index) => {
                        const netAmount = settlementNetAmount(s);
                        const grossAmount = settlementGrossAmount(s);
                        const resourceCost = settlementResourceCost(s);
                        const currency = String(s.currency || "USD");
                        const settlementId = String(s.settlement_id || "");
                        const reqName = String(s.requirement_name || s.task_ref || "—");
                        const agentName = String(s.agent_name || "—");
                        const moduleName = String(s.module_name || "");
                        const settledAt = String(s.settled_at || "");
                        const reason = String(s.reason || "");
                        const status = String(s.status || "settled");
                        const nestedSettlement = objectValue(s.settlement);
                        const nestedRecord = objectValue(s.settlement_record);
                        const verification = objectValue(s.verification_result);
                        const paymentObj = objectValue(s.stripe || nestedSettlement.stripe || nestedRecord.stripe || verification.stripe);
                        const paymentStatus = String(paymentObj.status || "");
                        const paymentReference = String(paymentObj.stripe_payment_intent_id || "");
                        const checks = (
                          s.verification_checks ||
                          nestedSettlement.verification_checks ||
                          nestedRecord.verification_checks ||
                          verification.verification_checks ||
                          []
                        ) as Array<Record<string, unknown>>;

                        return (
                          <div className="timeline-card" key={settlementId || `s-${index}`}>
                            <div className="timeline-top">
                              <div>
                                <strong style={{ fontSize: "1.2rem", color: "var(--success)" }}>
                                  +${netAmount.toFixed(2)} {currency}
                                </strong>
                              </div>
                              <span className={`status-pill ${status === "settled" ? "success" : ""}`}>
                                {status}
                              </span>
                            </div>
                            <div className="kv-list" style={{ fontSize: "0.9rem" }}>
                              <div>
                                <span>Task</span>
                                <strong>{reqName}</strong>
                              </div>
                              <div>
                                <span>Agent</span>
                                <strong>{agentName}</strong>
                              </div>
                              <div>
                                <span>Gross reward</span>
                                <strong>${grossAmount.toFixed(2)} {currency}</strong>
                              </div>
                              <div>
                                <span>Resource cost</span>
                                <strong>${resourceCost.toFixed(2)} {currency}</strong>
                              </div>
                              <div>
                                <span>Net payout</span>
                                <strong>${netAmount.toFixed(2)} {currency}</strong>
                              </div>
                              {moduleName ? (
                                <div>
                                  <span>Module</span>
                                  <strong style={{ fontFamily: "monospace" }}>{moduleName}</strong>
                                </div>
                              ) : null}
                              <div>
                                <span>Settlement ID</span>
                                <strong style={{ fontFamily: "monospace", fontSize: "0.82rem" }}>
                                  {settlementId || "—"}
                                </strong>
                              </div>
                              {reason ? (
                                <div>
                                  <span>Reason</span>
                                  <strong>{reason}</strong>
                                </div>
                              ) : null}
                              {settledAt ? (
                                <div>
                                  <span>Settled at</span>
                                  <strong>{new Date(settledAt).toLocaleString()}</strong>
                                </div>
                              ) : null}
                              {checks.length > 0 ? (
                                <div>
                                  <span>Checks</span>
                                  <strong>
                                    {checks.filter((c) => c.passed).length}/{checks.length} passed
                                  </strong>
                                </div>
                              ) : null}
                              {paymentReference ? (
                                <div>
                                  <span>Payment provider</span>
                                  <strong>
                                    {paymentReference.length > 24 ? `${paymentReference.slice(0, 24)}...` : paymentReference}
                                  </strong>
                                </div>
                              ) : paymentStatus ? (
                                <div>
                                  <span>Payment provider</span>
                                  <strong>{paymentStatus}</strong>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </>
                );
              })()}
            </section>
          </div>
        ) : null}

        {activeView === "skills" ? (
          <div className="panel-grid">
            <section className="card">
              <SkillStorePanel
                apiBase={API_BASE}
                localAgents={localAgents}
                selectedAgentName={selectedAgent || ""}
              />
            </section>
          </div>
        ) : null}

        {activeView === "jobs" ? (
          <div className="panel-grid">
            <section className="card">
              <JobBoardPanel
                apiBase={API_BASE}
                localAgents={localAgents}
                selectedAgentName={selectedAgent || ""}
                foundryUrl={foundryUrl}
              />
            </section>
          </div>
        ) : null}
      </main>
    </div>
  );
}
