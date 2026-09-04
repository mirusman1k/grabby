const $ = (id) => document.getElementById(id);
const jobEls = new Map();   // job_id -> <li>
let poller = null;
let current = null;         // last fetched video or playlist

// Playlist entries are fetched "flat" for speed, so we don't know each video's
// real resolutions. Offer the standard ladder and let yt-dlp fall back.
const QUALITY_LADDER = [2160, 1440, 1080, 720, 480, 360];

// ---------- helpers ----------
const fmtDuration = (s) => {
  if (!s) return "";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
};

const fmtBytes = (b) => {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
};

const showError = (msg) => {
  const el = $("error");
  el.textContent = msg;
  el.hidden = !msg;
};

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

// ---------- lookup ----------
$("lookup").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  const btn = $("lookupBtn");
  btn.disabled = true;
  btn.textContent = "Fetching…";
  try {
    current = await api("/api/info", { url: $("url").value });
    current.type === "playlist" ? renderPlaylist(current) : renderVideo(current);
    $("result").hidden = false;
  } catch (err) {
    $("result").hidden = true;
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Fetch";
  }
});

function fillQuality(heights) {
  const sel = $("quality");
  sel.innerHTML = "";
  sel.append(new Option("Best available", "best"));
  for (const h of heights) sel.append(new Option(`${h}p`, String(h)));
}

function renderVideo(info) {
  $("playlistCard").hidden = true;
  $("videoCard").hidden = false;

  $("thumb").src = info.thumbnail || "";
  $("title").textContent = info.title || "Untitled";
  $("submeta").textContent = [
    info.uploader,
    fmtDuration(info.duration),
    info.view_count ? `${info.view_count.toLocaleString()} views` : null,
  ].filter(Boolean).join(" · ");

  fillQuality(info.available_heights || []);
  $("downloadBtn").textContent = "Download";
}

function renderPlaylist(pl) {
  $("videoCard").hidden = true;
  $("playlistCard").hidden = false;

  $("plTitle").textContent = pl.title;
  $("plMeta").textContent = [pl.uploader, `${pl.count} videos`].filter(Boolean).join(" · ");

  const ul = $("entries");
  ul.innerHTML = "";
  pl.entries.forEach((e, i) => {
    const li = document.createElement("li");
    li.className = "entry";
    li.innerHTML = `
      <input type="checkbox" checked data-i="${i}">
      <img src="${e.thumbnail || ""}" alt="">
      <span class="name"></span>
      <span class="dur"></span>`;
    li.querySelector(".name").textContent = `${i + 1}. ${e.title}`;
    li.querySelector(".dur").textContent = fmtDuration(e.duration);
    // Clicking anywhere on the row toggles it, not just the 8px checkbox.
    li.addEventListener("click", (ev) => {
      if (ev.target.tagName !== "INPUT") {
        li.querySelector("input").checked = !li.querySelector("input").checked;
      }
      syncSelection();
    });
    ul.append(li);
  });

  fillQuality(QUALITY_LADDER);
  syncSelection();
}

function selectedEntries() {
  if (!current || current.type !== "playlist") return [];
  return [...$("entries").querySelectorAll("input:checked")]
    .map((cb) => current.entries[+cb.dataset.i]);
}

function syncSelection() {
  const n = selectedEntries().length;
  $("selCount").textContent = `${n} of ${current.count} selected`;
  $("downloadBtn").textContent = n ? `Download ${n}` : "Download";
  $("downloadBtn").disabled = n === 0;
  for (const li of $("entries").children) {
    li.classList.toggle("off", !li.querySelector("input").checked);
  }
}

const setAll = (checked) => {
  for (const cb of $("entries").querySelectorAll("input")) cb.checked = checked;
  syncSelection();
};
$("selAll").addEventListener("click", () => setAll(true));
$("selNone").addEventListener("click", () => setAll(false));

// ---------- download ----------
$("downloadBtn").addEventListener("click", async () => {
  if (!current) return;
  showError("");

  const items = current.type === "playlist"
    ? selectedEntries().map((e) => ({ url: e.url, title: e.title }))
    : [{ url: current.webpage_url, title: current.title }];

  if (!items.length) return showError("Nothing selected.");

  try {
    await api("/api/download", {
      items,
      quality: $("quality").value,
      audio_only: $("audioOnly").checked,
      // Unchecked means "give me something QuickTime can actually open".
      compatible: !$("maxQuality").checked,
    });
    $("jobsWrap").hidden = false;
    startPolling();
  } catch (err) {
    showError(err.message);
  }
});

// ---------- progress polling ----------
function startPolling() {
  if (poller) return;
  const tick = async () => {
    let jobs;
    try {
      ({ jobs } = await api("/api/jobs"));
    } catch { return; }

    jobs.forEach(renderJob);

    // Stop polling once nothing is moving; saves a request every second.
    const busy = jobs.some((j) => !["done", "error"].includes(j.state));
    if (!busy) { clearInterval(poller); poller = null; }
  };
  tick();
  poller = setInterval(tick, 1000);
}

function renderJob(job) {
  let li = jobEls.get(job.id);
  if (!li) {
    li = document.createElement("li");
    li.className = "job";
    li.innerHTML = `
      <div class="job-head">
        <span class="job-title"></span>
        <span class="job-state"></span>
      </div>
      <div class="bar"><i></i></div>
      <p class="job-err" hidden></p>
      <p class="job-path" hidden></p>`;
    $("jobs").prepend(li);
    jobEls.set(job.id, li);
  }

  li.querySelector(".job-title").textContent = job.title;

  const state = li.querySelector(".job-state");
  state.className = `job-state ${job.state}`;
  state.textContent = describe(job);

  const bar = li.querySelector(".bar > i");
  const indeterminate = ["queued", "starting", "processing"].includes(job.state);
  bar.className = job.state === "done" ? "done" : indeterminate ? "indeterminate" : "";
  bar.style.width = indeterminate ? "" : `${job.percent || 0}%`;

  const err = li.querySelector(".job-err");
  err.hidden = !job.error;
  err.textContent = job.error || "";

  const path = li.querySelector(".job-path");
  path.hidden = !job.filepath;
  path.textContent = job.filepath || "";
}

function describe(job) {
  switch (job.state) {
    case "queued":     return "queued";
    case "starting":   return "starting…";
    case "processing": return job.stage ? `${job.stage}…` : "converting…";
    case "done":       return "done";
    case "error":      return "failed";
    case "downloading": {
      const parts = [`${job.percent ?? 0}%`];
      if (job.total) parts.push(`${fmtBytes(job.downloaded)} / ${fmtBytes(job.total)}`);
      if (job.speed) parts.push(`${fmtBytes(job.speed)}/s`);
      if (job.eta) parts.push(`${fmtDuration(job.eta)} left`);
      return parts.join(" · ");
    }
    default: return job.state;
  }
}

$("audioOnly").addEventListener("change", () => {
  $("maxQualityLabel").hidden = $("audioOnly").checked;
});

$("reveal").addEventListener("click", () => api("/api/reveal", {}).catch(() => {}));

// Pick up any downloads still running from a previous page load.
api("/api/jobs").then(({ jobs }) => {
  if (jobs.length) { $("jobsWrap").hidden = false; jobs.forEach(renderJob); startPolling(); }
}).catch(() => {});
