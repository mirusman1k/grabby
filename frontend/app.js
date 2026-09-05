const $ = (id) => document.getElementById(id);
const jobEls = new Map();   // job_id -> <li>
let poller = null;
let current = null;         // last fetched video or playlist
let downloadDir = "";       // used to show paths relative to it

// Playlist entries are fetched "flat" for speed, so we don't know each video's
// real resolutions. Offer the standard ladder and let yt-dlp fall back.
const QUALITY_LADDER = [2160, 1440, 1080, 720, 480, 360];

// Instagram, X and LinkedIn are H.264/AAC only and top out around 1080p, so
// the 4K rungs and the VP9/AV1 "max quality" escape hatch are both noise there.
const MUXED_PLATFORMS = new Set(["instagram", "twitter", "linkedin"]);
const SOCIAL_LADDER = [1080, 720, 480, 360];
const PLATFORM_LABEL = { instagram: "Instagram", twitter: "X", linkedin: "LinkedIn" };

const chosenBrowser = () => $("browser").value || "none";

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

async function api(path, body, method) {
  const res = await fetch(path, {
    method: method || (body ? "POST" : "GET"),
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
    current = await api("/api/info", { url: $("url").value, browser: chosenBrowser() });
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

// LinkedIn reports no resolution for its videos, only a bitrate, so there are
// no "1080p" rungs to list -- just a bigger file and a smaller one. Labelling
// those by kbps is meaningless to most people, so they are ranked instead and
// the number kept as a hint.
const BITRATE_NAMES = ["Higher quality", "Lower quality"];

function fillQuality(heights, bitrates = []) {
  const sel = $("quality");
  sel.innerHTML = "";
  sel.append(new Option("Best available", "best"));
  for (const h of heights) sel.append(new Option(`${h}p`, String(h)));
  bitrates.forEach((b, i) => {
    const name = BITRATE_NAMES[i] || `Option ${i + 1}`;
    sel.append(new Option(`${name} (~${b} kbps)`, `tbr:${b}`));
  });
}

// The "max quality" toggle only means something where VP9/AV1 exist. Showing
// it on an Instagram reel invites a choice that changes nothing.
function applyPlatform(platform) {
  const muxed = MUXED_PLATFORMS.has(platform);
  $("maxQualityLabel").hidden = muxed || $("audioOnly").checked;
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

  fillQuality(info.available_heights || [], info.available_bitrates || []);
  applyPlatform(info.platform);

  // A photo-only post still renders, but there is no video to queue.
  const hasVideo = !(info.images || []).length
    || (info.available_heights || []).length || (info.available_bitrates || []).length
    || info.duration;
  // Hide the whole row, not each control: leaving the empty container
  // visible renders as a stray blank card under the post.
  document.querySelector(".controls").hidden = !hasVideo;

  showImagesButton($("imagesBtn"), info);

  const av = $("avatarBtn");
  av.hidden = !(info.platform === "instagram" && info.uploader_id);
  av.disabled = false;
  av.textContent = `Save @${info.channel || "user"}'s profile picture`;

  $("downloadBtn").textContent = "Download";
}

function renderPlaylist(pl) {
  $("videoCard").hidden = true;
  $("playlistCard").hidden = false;

  $("plTitle").textContent = pl.title;
  const noun = pl.profile ? "posts" : MUXED_PLATFORMS.has(pl.platform) ? "items" : "videos";
  const photos = pl.skipped_photos
    ? `${pl.skipped_photos} photo${pl.skipped_photos > 1 ? "s" : ""} skipped`
    : null;
  // Instagram reports "Instagram" as the uploader on its own posts, which
  // rendered as "Instagram · Instagram · 3 items".
  const site = PLATFORM_LABEL[pl.platform];
  const who = pl.uploader && pl.uploader !== site ? pl.uploader : null;
  $("plMeta").textContent = [site, who, `${pl.count} ${noun}`, photos,
    pl.profile ? "saved to one folder" : null].filter(Boolean).join(" · ");

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

  showImagesButton($("imagesBtnPl"), pl);
  fillQuality(MUXED_PLATFORMS.has(pl.platform) ? SOCIAL_LADDER : QUALITY_LADDER);
  applyPlatform(pl.platform);
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
    ? selectedEntries().map((e) => ({
        url: e.url, title: e.title, playlist_index: e.playlist_index,
      }))
    : [{ url: current.webpage_url, title: current.title }];

  if (!items.length) return showError("Nothing selected.");

  try {
    await api("/api/download", {
      items,
      quality: $("quality").value,
      audio_only: $("audioOnly").checked,
      // Unchecked means "give me something QuickTime can actually open".
      compatible: !$("maxQuality").checked,
      browser: chosenBrowser(),
      // Profile posts are unrelated videos that belong together only because
      // of who posted them, so they get a folder named for the account.
      folder: current.profile ? `${current.profile} [instagram]` : null,
    });
    $("jobsWrap").hidden = false;
    startPolling();
  } catch (err) {
    showError(err.message);
  }
});

// ---------- removing a download ----------
// Deleting the file is the point: someone who grabbed the wrong image wants it
// gone from the folder, not just hidden from this list.
async function removeJob(id) {
  const li = jobEls.get(id);
  if (li) {
    li.classList.add("removing");
    li.querySelectorAll("button").forEach((b) => (b.disabled = true));
  }
  try {
    await api(`/api/jobs/${id}`, null, "DELETE");
  } catch (err) {
    if (li) li.classList.remove("removing");
    return showError(err.message);
  }
  setTimeout(() => {
    li?.remove();
    jobEls.delete(id);
    syncJobsChrome();
  }, 200);
}

function syncJobsChrome() {
  const rows = [...jobEls.values()];
  $("jobsWrap").hidden = rows.length === 0;
  const finished = rows.filter((li) =>
    li.querySelector(".job-state").classList.contains("done")).length;
  $("clearDone").hidden = finished < 2;
}

$("clearDone").addEventListener("click", () => {
  for (const [id, li] of [...jobEls.entries()]) {
    if (li.querySelector(".job-state").classList.contains("done")) {
      // Keep the files; this only tidies the list.
      api(`/api/jobs/${id}?keep_file=true`, null, "DELETE").catch(() => {});
      li.remove();
      jobEls.delete(id);
    }
  }
  syncJobsChrome();
});

// ---------- progress polling ----------
function startPolling() {
  if (poller) return;
  const tick = async () => {
    let jobs;
    try {
      const r = await api("/api/jobs");
      jobs = r.jobs;
      downloadDir = r.download_dir || downloadDir;
    } catch { return; }

    jobs.forEach(renderJob);
    syncJobsChrome();

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
        <button type="button" class="job-act job-show" title="Show in Finder" hidden>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.75 4.5A1.75 1.75 0 0 1 3.5 2.75h2.4c.4 0 .78.16 1.06.44l.85.85h4.69c.97 0 1.75.78 1.75 1.75v6A1.75 1.75 0 0 1 12.5 13.5h-9A1.75 1.75 0 0 1 1.75 11.75z"/></svg>
        </button>
        <button type="button" class="job-act job-remove" title="Remove and delete the file">
          <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M1.7 1.7a.75.75 0 0 1 1.06 0L6 4.94l3.24-3.24a.75.75 0 1 1 1.06 1.06L7.06 6l3.24 3.24a.75.75 0 0 1-1.06 1.06L6 7.06 2.76 10.3a.75.75 0 0 1-1.06-1.06L4.94 6 1.7 2.76a.75.75 0 0 1 0-1.06"/></svg>
        </button>
      </div>
      <div class="bar"><i></i></div>
      <p class="job-err" hidden></p>
      <p class="job-path" hidden></p>`;
    li.querySelector(".job-remove").addEventListener("click", () => removeJob(job.id));
    li.querySelector(".job-show").addEventListener("click", () =>
      api("/api/reveal", { job_id: job.id }).catch((e) => showError(e.message)));
    $("jobs").prepend(li);
    jobEls.set(job.id, li);
  }

  li.querySelector(".job-title").textContent = job.title;
  li.classList.toggle("settled", ["done", "error"].includes(job.state));
  li.querySelector(".job-show").hidden = !job.filepath;

  const state = li.querySelector(".job-state");
  state.className = `job-state ${job.state}`;
  state.textContent = describe(job);

  // A finished row does not need a full progress bar -- it says "done"
  // already, and a row of 100% bars is just noise down the list.
  const settled = ["done", "error"].includes(job.state);
  const barWrap = li.querySelector(".bar");
  barWrap.hidden = settled;
  if (!settled) {
    const bar = li.querySelector(".bar > i");
    const indeterminate = ["queued", "starting", "processing"].includes(job.state);
    bar.className = indeterminate ? "indeterminate" : "";
    bar.style.width = indeterminate ? "" : `${job.percent || 0}%`;
  }

  const err = li.querySelector(".job-err");
  err.hidden = !job.error;
  err.textContent = job.error || "";

  // Show where it landed, not the whole absolute path. The download folder is
  // the one thing the reader already knows.
  const path = li.querySelector(".job-path");
  path.hidden = !job.filepath;
  path.textContent = job.filepath ? whereItLanded(job.filepath) : "";
}

function whereItLanded(filepath) {
  const rel = downloadDir && filepath.startsWith(downloadDir)
    ? filepath.slice(downloadDir.length).replace(/^\/+/, "")
    : filepath;
  const cut = rel.lastIndexOf("/");
  return cut === -1 ? "Downloads folder" : rel.slice(0, cut);
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
  applyPlatform(current?.platform);
});

// ---------- images ----------
// Images are fetched straight from the post rather than through yt-dlp, so
// they are a separate action from the video queue -- one click, no job rows.
function showImagesButton(btn, info) {
  const n = (info.images || []).length;
  btn.hidden = n === 0;
  btn.disabled = false;
  btn.textContent = n === 1 ? "Save image" : `Save ${n} images`;
}

async function saveImages(btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Saving…";
  showError("");
  try {
    const r = await api("/api/images", {
      url: current.webpage_url,
      title: current.title,
      browser: chosenBrowser(),
      folder: current.profile ? `${current.profile} [instagram]` : null,
    });
    btn.textContent = r.saved === 1 ? "Saved" : `Saved ${r.saved} images`;
    $("jobsWrap").hidden = false;
    startPolling();
  } catch (err) {
    btn.textContent = original;
    btn.disabled = false;
    showError(err.message);
  }
}

$("imagesBtn").addEventListener("click", () => saveImages($("imagesBtn")));
$("imagesBtnPl").addEventListener("click", () => saveImages($("imagesBtnPl")));

// ---------- login picker ----------
// Browser names come from the server so config.BROWSERS stays the one source.
api("/api/options").then((opt) => {
  const sel = $("browser");
  sel.append(new Option("None", "none"));
  for (const b of opt.browsers) {
    sel.append(new Option(b[0].toUpperCase() + b.slice(1), b));
  }
  if (opt.cookies_from_browser) sel.value = opt.cookies_from_browser;
  $("authHint").textContent = opt.cookies_file && !opt.cookies_from_browser
    ? "Falls back to your cookies.txt when set to None."
    : "Only for private posts or if you get rate-limited.";
}).catch(() => {});

$("avatarBtn").addEventListener("click", async () => {
  const btn = $("avatarBtn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Saving…";
  showError("");
  try {
    const r = await api("/api/avatar",
      { user_id: current.uploader_id, browser: chosenBrowser() });
    btn.textContent = `Saved (${r.width || 150}px)`;
    $("jobsWrap").hidden = false;
    startPolling();
    // Instagram hands a logged-out client the 150px thumbnail and nothing
    // else, so say why the file is small rather than let it look broken.
    if (!r.hd && chosenBrowser() === "none") {
      showError("Instagram only gives the 150px thumbnail to logged-out "
        + "visitors. Set Login to your browser and click again for full size.");
    }
  } catch (err) {
    btn.textContent = original;
    btn.disabled = false;
    showError(err.message);
  }
});

$("reveal").addEventListener("click", () => api("/api/reveal", {}).catch(() => {}));

// Pick up any downloads still running from a previous page load.
api("/api/jobs").then(({ jobs }) => {
  if (!jobs.length) return;
  $("jobsWrap").hidden = false;
  jobs.forEach(renderJob);
  syncJobsChrome();
  startPolling();
}).catch(() => {});
