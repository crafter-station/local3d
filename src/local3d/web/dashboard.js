"use strict";

const byId = (id) => document.getElementById(id);
const providers = {sf3d: "Stable Fast 3D", trellis2: "TRELLIS.2", demo: "Blender demo"};
const labels = {queued: "Queued", running: "Running", succeeded: "Complete", failed: "Failed", online: "Online", offline: "Offline", unconfigured: "Unconfigured"};
const phases = [["preparing", "Prepare"], ["reference", "Reference"], ["geometry", "3D inference"], ["blender", "Export"]];
let tableSignature = "";
let activeJob = null;
let viewerReady = false;
let viewerImport;
let previewRequest = 0;

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function badge(status) {
  const item = node("span", labels[status] || status, "badge");
  item.dataset.status = status;
  return item;
}

function size(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const unit = bytes >= 2 ** 30 ? ["GiB", 2 ** 30] : bytes >= 2 ** 20 ? ["MiB", 2 ** 20] : ["KiB", 2 ** 10];
  return new Intl.NumberFormat(undefined, {maximumFractionDigits: 1}).format(bytes / unit[1]) + " " + unit[0];
}

function activityTime() {
  const label = byId("phase-time");
  if (!label || !activeJob?.phase_started_at) return;
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(activeJob.phase_started_at)) / 1000));
  const duration = seconds < 60 ? seconds + "s" : Math.floor(seconds / 60) + "m " + seconds % 60 + "s";
  const phase = phases.find(([id]) => id === activeJob.phase)?.[1] || "Starting";
  label.textContent = phase + " · " + duration;
}

function renderActivity(jobs) {
  activeJob = jobs.find((job) => job.status === "running") || null;
  const pending = jobs.some((job) => job.status === "queued");
  const target = byId("activity");
  const state = byId("current-state");
  state.textContent = activeJob ? "Running" : pending ? "Queued" : "Idle";
  state.dataset.status = activeJob ? "running" : pending ? "queued" : "idle";
  if (!activeJob) {
    target.replaceChildren(node("p", pending ? "Waiting for the local worker." : "Ready for the next generation.", "empty"));
    return;
  }
  const phase = phases.findIndex(([id]) => id === activeJob.phase);
  const stages = node("ol", undefined, "steps");
  stages.setAttribute("aria-label", "Generation stages");
  for (const [index, [, name]] of phases.entries()) {
    const step = node("li", name);
    if (index === phase) {
      step.dataset.state = "active";
      step.setAttribute("aria-current", "step");
    } else if (index < phase && (activeJob.provider !== "demo" || index === 0)) {
      step.dataset.state = "done";
    }
    stages.append(step);
  }
  const time = node("span", undefined, "secondary stage-time");
  time.id = "phase-time";
  target.replaceChildren(node("p", activeJob.title, "job-title"), time, stages);
  activityTime();
}

function renderPorts(ports) {
  const target = byId("ports");
  const items = ports.map((port) => {
    const row = node("li");
    const description = node("div");
    description.append(node("span", port.service, "port-name"));
    if (port.port) description.append(node("div", "127.0.0.1:" + port.port, "port-value"));
    row.append(description, badge(port.status));
    return row;
  });
  target.replaceChildren(...items);
}

async function showAsset(job) {
  const token = ++previewRequest;
  const dialog = byId("asset-dialog");
  const reference = byId("reference-preview");
  const preview = byId("model-preview");
  const base = "/v1/jobs/" + encodeURIComponent(job.id);
  const photo = node("img");
  photo.alt = "Reference image for " + job.title;
  photo.addEventListener("error", () => {
    if (token === previewRequest) reference.replaceChildren(node("p", "No reference image saved.", "empty"));
  }, {once: true});
  byId("asset-dialog-title").textContent = job.title;
  reference.replaceChildren(photo);
  photo.src = base + "/reference";
  preview.replaceChildren(node("p", "Loading model…", "empty"));
  dialog.showModal();

  if (!job.asset || !viewerReady) {
    const message = job.asset ? "The local GLB viewer is unavailable." : "No 3D model is available yet.";
    preview.replaceChildren(node("p", message, "empty"));
    return;
  }
  try {
    viewerImport ??= import("/v1/dashboard/viewer-module");
    await viewerImport;
    if (token !== previewRequest || !dialog.open) return;
    const model = node("model-viewer");
    model.setAttribute("alt", "Interactive 3D preview for " + job.title);
    model.setAttribute("camera-controls", "");
    model.setAttribute("touch-action", "pan-y");
    model.setAttribute("shadow-intensity", ".65");
    model.addEventListener("error", () => {
      if (token === previewRequest) {
        preview.replaceChildren(node("p", "3D preview unavailable. Use the downloaded file to open this asset.", "empty"));
      }
    }, {once: true});
    model.setAttribute("src", base + "/artifact?preview=true");
    preview.replaceChildren(model);
  } catch {
    viewerImport = undefined;
    if (token === previewRequest) preview.replaceChildren(node("p", "Could not load the local viewer.", "empty"));
  }
}

byId("asset-dialog").addEventListener("close", () => {
  previewRequest += 1;
  byId("reference-preview").replaceChildren();
  byId("model-preview").replaceChildren();
});

function renderJobs(jobs) {
  const signature = JSON.stringify(jobs);
  if (signature === tableSignature) return;
  tableSignature = signature;
  const target = byId("jobs");
  if (!jobs.length) {
    const row = node("tr");
    const cell = node("td", "Waiting for the first job.", "empty");
    cell.colSpan = 4;
    row.append(cell);
    target.replaceChildren(row);
    return;
  }
  const rows = jobs.map((job) => {
    const row = node("tr");
    const name = node("td");
    name.append(node("strong", job.title, "asset-title"));
    if (job.error) name.append(node("span", job.error, "error"));
    const provider = node("td", providers[job.provider] || job.provider, "provider");
    const status = node("td");
    status.append(badge(job.status));
    const output = node("td");
    if (job.asset) {
      const section = node("div", undefined, "output");
      const format = job.asset.path.endsWith(".blend") ? "BLEND" : "GLB";
      const link = node("a", "Download " + format, "download");
      link.href = "/v1/jobs/" + encodeURIComponent(job.id) + "/artifact";
      link.setAttribute("download", "");
      link.setAttribute("aria-label", "Download " + job.title + " as " + format);
      const detail = node("span", size(job.asset.bytes), "secondary");
      detail.title = new Intl.NumberFormat().format(job.asset.triangles) + " triangles";
      const view = node("button", "View", "view-action");
      view.type = "button";
      view.setAttribute("aria-label", "View " + job.title);
      view.addEventListener("click", () => showAsset(job));
      section.append(view, link, detail);
      output.append(section);
    } else if (["geometry", "blender"].includes(job.phase) && job.provider !== "demo") {
      const view = node("button", "View reference", "view-action");
      view.type = "button";
      view.addEventListener("click", () => showAsset(job));
      output.append(view);
    } else output.textContent = "—";
    row.append(name, provider, status, output);
    return row;
  });
  target.replaceChildren(...rows);
}

async function refresh() {
  const connection = byId("connection");
  try {
    const response = await fetch("/v1/dashboard", {cache: "no-store", signal: AbortSignal.timeout(3000)});
    if (!response.ok) throw new Error("Dashboard request failed");
    const data = await response.json();
    viewerReady = data.viewer_ready;
    byId("image-backend").hidden = !data.image_cli_ready;
    connection.textContent = "Live";
    connection.dataset.status = "online";
    byId("updated").textContent = "Updated " + new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    byId("queued").textContent = data.counts.queued || 0;
    byId("completed").textContent = data.counts.succeeded || 0;
    byId("disk").textContent = size(data.disk_free_bytes);
    const lastRun = data.jobs.find((job) => Object.values(job.asset?.stages || {}).some((stage) => stage.peak_footprint_bytes));
    const peaks = Object.values(lastRun?.asset?.stages || {}).map((stage) => stage.peak_footprint_bytes || 0);
    byId("peak").textContent = peaks.length ? size(Math.max(...peaks)) : "—";
    byId("job-count").textContent = data.jobs.length ? data.jobs.length + " shown" : "";
    renderActivity(data.jobs);
    renderPorts(data.ports);
    renderJobs(data.jobs);
  } catch {
    connection.textContent = "Reconnecting";
    connection.dataset.status = "offline";
  } finally {
    window.setTimeout(refresh, 2400);
  }
}

refresh();
window.setInterval(activityTime, 1000);
