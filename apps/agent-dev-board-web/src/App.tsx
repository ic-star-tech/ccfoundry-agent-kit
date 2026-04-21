import { useEffect, useMemo, useState } from "react";

type ContextMode = "direct" | "inline";
type BoardView = "guide" | "agent" | "playground";
type AgentCardTab = "overview" | "runtimes" | "runtime" | "profile";

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
  apply_result?: Record<string, unknown> | null;
  env_snippet?: string;
  message?: string;
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

const API_PORT = import.meta.env.VITE_API_PORT || "8090";
const DEFAULT_FOUNDRY_URL = "https://foundry.cochiper.com";

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
  { id: "playground", label: "Agent playground", kicker: "Chat, transcript, and smoke tests" },
];

const AGENT_CARD_TABS: Array<{ id: AgentCardTab; label: string; helper: string }> = [
  { id: "overview", label: "Overview", helper: "Agent, mode, and quick status" },
  { id: "runtimes", label: "Local runtimes", helper: "Create and manage local agents" },
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

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object" && typeof (payload as { detail?: unknown }).detail === "string") {
      return String((payload as { detail: string }).detail);
    }
    return JSON.stringify(payload);
  } catch {
    const text = await response.text();
    return text || `Request failed with status ${response.status}`;
  }
}

function statusTone(status: string): "neutral" | "success" | "warn" {
  const normalized = status.trim().toUpperCase();
  if (["APPROVED", "REGISTERED", "REDEEMED", "MATCHED", "ONLINE", "TRUE", "ACTIVE"].includes(normalized)) {
    return "success";
  }
  if (["REVIEWING", "DISCOVERED", "ISSUED", "PENDING", "INLINE_REGISTER"].includes(normalized)) {
    return "warn";
  }
  return "neutral";
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
  const [oauthLoading, setOauthLoading] = useState(false);
  const [foundryBootstrapSession, setFoundryBootstrapSession] = useState<FoundryBootstrapSession | null>(null);
  const [localAgentLoading, setLocalAgentLoading] = useState(false);
  const [localAgentError, setLocalAgentError] = useState("");
  const [localAgentNotice, setLocalAgentNotice] = useState("");
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

  useEffect(() => {
    if (!selectedAgent) {
      return;
    }
    resetConversation();
    setHandshakeResult(null);
    setHandshakeError("");
    setDeveloperTicket(null);
    setTicketError("");
    void probeHandshake(selectedAgent, foundryUrl);
    void refreshDeveloperContext(foundryUrl);
  }, [selectedAgent]);

  useEffect(() => {
    void refreshAgentInventory();
    void refreshDeveloperContext(foundryUrl);
  }, []);

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
  const normalizedFoundryUrl = normalizeUrl(foundryUrl || textValue(developerContext?.foundry?.url));
  const foundryPortalUrl =
    normalizeUrl(
      textValue(
        foundryBootstrapSession?.foundry_url,
        textValue(handshakeResult?.foundry.url, normalizedFoundryUrl || DEFAULT_FOUNDRY_URL),
      ),
    ) || DEFAULT_FOUNDRY_URL;
  const displayedDeveloperLogin = textValue(
    foundryBootstrapSession?.github_login,
    textValue(developerGithub.login, "not detected"),
  );
  const developerSessionReady = Boolean(developerForm.developer_token.trim() || foundryBootstrapSession);
  const foundryBrowserSessionReady = Boolean(foundryBootstrapSession);
  const claimInstalled = bootstrapHasClaim || boolValue(developerTicket?.claim_applied) || Boolean(textValue(bootstrapState.last_claimed_at));
  const approvalObserved = Boolean(textValue(bootstrapState.approved_at));
  const registrationStatus = textValue(bootstrapState.registration_status).toUpperCase();
  const discoveryStatus = textValue(bootstrapState.discovery_status).toUpperCase();
  const onboardingApproved = approvalObserved || registrationStatus === "APPROVED" || gatewayReady;
  const onboardingRetired = registrationStatus === "RETIRED";
  const playgroundReady = Boolean(selectedAgentEntry && selectedAgentEntry.status === "online");
  const hasConversation = transcript.length > 0 || reply.trim().length > 0;

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

      const templatesPayload = (await templatesResponse.json()) as LocalAgentTemplate[];
      const localAgentsPayload = (await localAgentsResponse.json()) as LocalAgentRuntime[];
      const agentsPayload = (await agentsResponse.json()) as AgentEntry[];

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
      setLocalAgentNotice(`Created ${payload.label} on ${displaySafeUrl(payload.base_url)}.`);
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

  async function requestBootstrapTicket() {
    if (!selectedAgent) {
      return;
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
        }),
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as DeveloperTicketResult;
      setDeveloperTicket(payload);
      await Promise.all([
        probeHandshake(selectedAgent, foundryUrl),
        refreshDeveloperContext(foundryUrl),
      ]);
    } catch (err) {
      setTicketError(String(err));
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
            href="https://foundry.cochiper.com"
            target="_blank"
            rel="noreferrer"
            aria-label="Open Foundry"
            title="Open Foundry"
          >
            <img className="external-link-logo" src={CC_LOGO_URL} alt="" aria-hidden="true" />
            <span className="sr-only">Open Foundry</span>
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
                This guided setup keeps the happy path in one place: create a local agent, connect your developer identity,
                let Foundry bootstrap it, run a local smoke test, and then verify the linked agent inside Foundry itself.
              </p>
              <div className="guide-progress">
                <span className={`chip ${selectedLocalAgent ? "tone-success" : ""}`}>1. Agent created</span>
                <span className={`chip ${developerSessionReady ? "tone-success" : ""}`}>2. Developer login</span>
                <span className={`chip ${claimInstalled ? "tone-success" : ""}`}>3. Claim installed</span>
                <span className={`chip ${onboardingApproved ? "tone-success" : onboardingRetired ? "tone-warn" : ""}`}>4. Foundry approved</span>
                <span className={`chip ${hasConversation ? "tone-success" : ""}`}>5. Playground test</span>
                <span className={`chip ${foundryPortalOpened ? "tone-success" : ""}`}>6. Foundry test</span>
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">1</span>
                  <div>
                    <h4>Create a local agent</h4>
                    <p className="muted">
                      Start from the template, give it a stable name, and let Dev Board manage its port and runtime.
                    </p>
                  </div>
                </div>
                {selectedLocalAgent ? (
                  <div className="reply">
                    <strong>{selectedLocalAgent.label}</strong>
                    <p>
                      Selected runtime: <code>{selectedLocalAgent.name}</code> at{" "}
                      <code>{displaySafeUrl(selectedLocalAgent.base_url)}</code>
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
                    {localAgentLoading ? "Creating..." : "Create local agent"}
                  </button>
                  <button className="secondary" onClick={() => setActiveView("agent")}>
                    Open agent card
                  </button>
                </div>
                {localAgentNotice ? <div className="reply">{localAgentNotice}</div> : null}
                {localAgentError ? <div className="error">{localAgentError}</div> : null}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">2</span>
                  <div>
                    <h4>Log in as a developer</h4>
                    <p className="muted">
                      Use the popup-based GitHub login so Dev Board gets a short-lived Foundry bootstrap token.
                    </p>
                  </div>
                </div>
                <label>
                  Foundry URL
                  <input
                    value={foundryUrl}
                    onChange={(event) => setFoundryUrl(event.target.value)}
                    placeholder="https://foundry.cochiper.com"
                  />
                </label>
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
                {developerError ? <div className="error">{developerError}</div> : null}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">3</span>
                  <div>
                    <h4>Install the Foundry claim</h4>
                    <p className="muted">
                      This requests a bootstrap ticket, applies the discovery claim to the selected agent, and forces a new
                      discover so Foundry sees the developer-linked runtime.
                    </p>
                  </div>
                </div>
                {claimInstalled ? (
                  <div className="reply">
                    <strong>Claim installed</strong>
                    <p>Last claim time: {textValue(bootstrapState.last_claimed_at, "recorded by the agent")}</p>
                  </div>
                ) : null}
                <label>
                  Target runtime
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
                    <span>Selected agent</span>
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
                </div>
                {ticketError ? <div className="error">{ticketError}</div> : null}
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">4</span>
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
                  <span className="step-index">5</span>
                  <div>
                    <h4>Run a playground smoke test</h4>
                    <p className="muted">
                      Once the agent is online, jump into the playground with a starter prompt and confirm the full loop is
                      working from Foundry bootstrap through live chat.
                    </p>
                  </div>
                </div>
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
              </div>
            </section>

            <section className="panel">
              <div className="guide-step">
                <div className="step-header">
                  <span className="step-index">6</span>
                  <div>
                    <h4>Test the agent in Foundry</h4>
                    <p className="muted">
                      Open <code>{foundryPortalUrl}</code> and validate the same agent inside the live Foundry product after
                      bootstrap is approved.
                    </p>
                  </div>
                </div>
                <div className="kv-list compact-kv">
                  <div>
                    <span>Foundry access</span>
                    <strong>GitHub sign-in only</strong>
                  </div>
                  <div>
                    <span>Browser session</span>
                    <strong>{foundryBrowserSessionReady ? "ready from step 2" : "sign in required"}</strong>
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
                      ? "If you completed the GitHub popup login in step 2 in this browser, Foundry will usually open with that session already available. If it still asks you to sign in, continue with GitHub there."
                      : "Foundry uses GitHub login for this flow. If you have not signed in through the popup earlier on this page, Foundry will ask you to continue with GitHub before you can test the agent."}
                  </p>
                </div>
                <div className="actions split-actions">
                  <button onClick={openFoundryPortal} disabled={!onboardingApproved}>
                    Open Foundry to test agent
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
                    {selectedManifest?.description || "Create or select a local agent to inspect its runtime, model, and bootstrap state."}
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
                    <button className="full-width" onClick={() => setActiveView("playground")} disabled={!selectedAgent}>
                      Open playground
                    </button>
                  </div>
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
                      <span>Base URL</span>
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
              </>
            ) : null}

            {agentCardTab === "runtimes" ? (
              <>
                <section className="panel">
                  <div className="section-heading">
                    <div>
                      <p className="eyebrow">Template launcher</p>
                      <h3>Create local agent</h3>
                    </div>
                    <button className="secondary" onClick={() => refreshAgentInventory(selectedAgent)} disabled={localAgentLoading}>
                      {localAgentLoading ? "Refreshing..." : "Refresh inventory"}
                    </button>
                  </div>
                  <p className="muted">
                    <code>npm run dev-board</code> now starts only the board. Create a local agent from a template, choose a
                    stable name, and avoid collisions with old demo identities on Foundry.
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
                  <label>
                    Foundry URL for bootstrap
                    <input
                      value={foundryUrl}
                      onChange={(event) => setFoundryUrl(event.target.value)}
                      placeholder="https://foundry.cochiper.com"
                    />
                  </label>
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
                      <h3>Local agents</h3>
                    </div>
                    <span className="muted">{localAgents.length} runtime(s)</span>
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
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  )}
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
                  <p className="eyebrow">Live playground</p>
                  <h3>Transcript</h3>
                </div>
                <span className="muted">Current conversation id: {conversationId || "new session"}</span>
              </div>
              <div className="transcript">
                {transcript.length === 0 ? (
                  <div className="empty-state">
                    <h4>No messages yet</h4>
                    <p className="muted">Start with a direct question or test an inline call from this playground.</p>
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
                  <h3>Send message</h3>
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
                  Create a runtime in <strong>Agent card</strong> or choose one here before sending a message.
                </p>
              ) : null}
              <div className="actions">
                <button onClick={() => void sendMessage()} disabled={loading || !selectedAgent}>
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
      </main>
    </div>
  );
}
