import { useEffect, useRef, useState } from "react";

type Source = {
  name: string;
  status?: string;
  runtime_kind?: string;
  project_path?: string;
  app_module?: string;
  project_build_id?: string;
};
type Receipt = {
  image_id?: string;
  platform?: string;
  manifest_path?: string;
  log_path?: string;
  archive_path?: string;
  sha256?: string;
  repo_digest?: string;
};

export function ProjectContainerPanel({ source, apiBase, onChanged }: {
  source: Source;
  apiBase: string;
  onChanged: () => Promise<void>;
}) {
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const [imageRef, setImageRef] = useState("");
  const [platform, setPlatform] = useState("");
  const [offline, setOffline] = useState("project");
  const [registryRef, setRegistryRef] = useState("");
  const [receipt, setReceipt] = useState<Receipt>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const endpoint = `${apiBase}/api/local-agents/${encodeURIComponent(source.name)}/container`;

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${endpoint}/build`, { signal: controller.signal }).then(async (response) => {
      if (response.ok) setReceipt(await response.json());
    }).catch((reason) => {
      if (reason.name !== "AbortError") setError("Could not read the last image build. Refresh to retry.");
    });
    return () => controller.abort();
  }, [endpoint, source.project_build_id]);

  async function operate(action: string, body: Record<string, unknown> = {}) {
    setBusy(action); setError(""); setNotice("");
    try {
      const response = await fetch(`${endpoint}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!active.current) return;
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Container operation failed");
      if (action !== "start") setReceipt((previous) => action === "build" ? result : { ...previous, ...result });
      setNotice(action === "start" ? `Container ${result.status}. Open the local playground after it becomes healthy.` : `${action} completed.`);
      await onChanged();
    } catch (reason) {
      if (active.current) setError(reason instanceof Error ? reason.message : "Container operation failed. Check Docker state before retrying.");
    } finally { if (active.current) setBusy(""); }
  }

  return <section className="project-container-panel" aria-label="Customer project container">
    <h4>Customer project</h4>
    <p><code>{source.project_path}</code> · <code>{source.app_module}</code></p>
    <p className="muted">Build your registered source as an AgentService image. The local container uses this source's workspace and loopback debug port.</p>
    <div className="cloud-run-form-grid">
      <label>Build image tag<input value={imageRef} onChange={(e) => setImageRef(e.target.value)} placeholder="local/my-agent:0.1.0" disabled={Boolean(busy)} /></label>
      <label>Image architecture<select value={platform} onChange={(e) => setPlatform(e.target.value)} disabled={Boolean(busy)}>
        <option value="">Build host architecture</option><option value="linux/amd64">Linux amd64</option><option value="linux/arm64">Linux arm64</option>
      </select></label>
      <label>Build dependencies<select value={offline} onChange={(e) => setOffline(e.target.value)} disabled={Boolean(busy)}>
        <option value="project">Use project configuration</option><option value="offline">Offline wheelhouse</option><option value="online">Configured pip index</option>
      </select></label>
    </div>
    <div className="actions compact-actions">
      <button disabled={Boolean(busy)} onClick={() => void operate("build", { image_ref: imageRef.trim(), platform, offline: offline === "project" ? null : offline === "offline" })}>Build customer image</button>
      <button className="secondary" disabled={Boolean(busy) || !receipt.image_id || source.status === "running"} onClick={() => void operate("start")}>Start local container</button>
      <button className="secondary" disabled={Boolean(busy) || !receipt.image_id} onClick={() => void operate("export")}>Export image archive</button>
    </div>
    <label>Private registry destination<input value={registryRef} onChange={(e) => setRegistryRef(e.target.value)} placeholder="registry.example.internal/team/my-agent:0.1.0" disabled={Boolean(busy)} /></label>
    <p className="muted">Use Docker login on this host first. Push sends the built image to the destination above. Export saves an archive and local-package manifest on this host for transfer to your appliance.</p>
    <button className="secondary" disabled={Boolean(busy) || !receipt.image_id || !registryRef.trim()} onClick={() => void operate("push", { target_ref: registryRef.trim() })}>Push to private registry</button>
    {busy && <p role="status">{busy} in progress; image operations can take several minutes.</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <div className="error" role="alert">{error}</div>}
    {receipt.image_id && <dl className="project-build-receipt">
      {Object.entries(receipt).filter(([key, value]) => value && ["image_id", "platform", "manifest_path", "log_path", "archive_path", "sha256", "repo_digest"].includes(key)).map(([key, value]) => <div key={key}><dt>{key.replace(/_/g, " ")}</dt><dd><code>{value}</code></dd></div>)}
    </dl>}
  </section>;
}
