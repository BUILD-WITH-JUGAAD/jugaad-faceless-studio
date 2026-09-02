const state = {
  model: "live",
  size: "9:16",
  music: "random",
  length: "youtube",
  seconds: 180,
  jobId: null,
  poll: null,
  options: null,
  previewId: null,
  pendingDelete: null,
  playingUrl: null,
  epidemicKind: "music",
  epidemicLoaded: false,
  epidemicReq: 0,
  hls: null,
};

const $ = (id) => document.getElementById(id);

async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  return res;
}

function mediaSrc(path) {
  if (!path) return "";
  const q = path.indexOf("?");
  const base = q === -1 ? path : path.slice(0, q);
  const query = q === -1 ? "" : path.slice(q);
  const parts = base.split("/");
  parts[parts.length - 1] = encodeURIComponent(parts[parts.length - 1]);
  return parts.join("/") + query;
}

function stopPreview() {
  if (state.hls) {
    state.hls.destroy();
    state.hls = null;
  }
  const a = $("preview");
  a.pause();
  a.removeAttribute("src");
  a.load();
  state.previewId = null;
  document.querySelectorAll(".tune.is-playing, .epidemic-row.is-playing").forEach((n) => n.classList.remove("is-playing"));
}

function playPreview(track, button) {
  const a = $("preview");
  if (!track.preview) {
    stopPreview();
    return;
  }
  $("player").pause();
  if (state.previewId === track.id && !a.paused) {
    a.pause();
    if (button) button.classList.remove("is-playing");
    return;
  }
  stopPreview();
  state.previewId = track.id;
  const src = mediaSrc(track.preview);
  const epidemicPreview = src.indexOf("/api/epidemic/preview/") !== -1;
  const useHls = epidemicPreview && track.hls !== false && window.Hls && Hls.isSupported();
  if (useHls) {
    state.hls = new Hls({ enableWorker: true });
    state.hls.loadSource(src);
    state.hls.attachMedia(a);
    state.hls.on(Hls.Events.MANIFEST_PARSED, () => {
      a.play().catch(() => {});
    });
  } else {
    a.src = src;
    a.play().catch(() => {});
  }
  if (button) button.classList.add("is-playing");
}

function setFrame(size) {
  const [w, h] = size.split(":").map(Number);
  const frame = $("frame");
  const monitor = $("monitor");
  if (!frame || !monitor || !w || !h) return;
  frame.style.aspectRatio = `${w} / ${h}`;
  const box = monitor.getBoundingClientRect();
  const pad = 36;
  const maxW = Math.min(280, Math.max(140, box.width - pad));
  const maxH = Math.min(520, Math.max(180, box.height - pad));
  let width = maxW;
  let height = width * (h / w);
  if (height > maxH) {
    height = maxH;
    width = height * (w / h);
  }
  frame.style.width = `${Math.round(width)}px`;
  frame.style.height = `${Math.round(height)}px`;
}

function pill(text, hot) {
  const el = $("busyPill");
  el.textContent = text;
  el.classList.toggle("hot", Boolean(hot));
}

function markLength(id) {
  document.querySelectorAll(".length").forEach((n) => {
    n.classList.toggle("is-on", n.dataset.id === id);
  });
}

function pickLength(item) {
  state.length = item.id;
  if (item.seconds === null || item.seconds === undefined) {
    $("seconds").focus();
    $("seconds").select();
  } else {
    state.seconds = item.seconds;
  }
  markLength(item.id);
  syncSecondsField();
}

function lengthFromSeconds(seconds) {
  const list = (state.options && state.options.lengths) || [];
  const exact = list.find((item) => item.id !== "custom" && item.seconds === seconds);
  if (exact) return exact.id;
  return "custom";
}

function currentLengthMeta() {
  const list = (state.options && state.options.lengths) || [];
  return list.find((item) => item.id === state.length) || list[0];
}

function syncSecondsField() {
  const input = $("seconds");
  const note = $("lengthNote");
  const meta = currentLengthMeta();
  if (state.seconds > 0) {
    input.value = String(state.seconds);
  } else {
    input.value = "";
    input.placeholder = "full story";
  }
  if (note) {
    if (state.length === "custom" && state.seconds > 0) {
      note.textContent = "Custom cap: " + state.seconds + "s. Narration longer than this is trimmed.";
    } else if (meta && meta.note) {
      note.textContent = meta.note;
    } else {
      note.textContent = "";
    }
  }
}

function onSecondsInput() {
  const raw = $("seconds").value.trim();
  if (raw === "") {
    state.seconds = 0;
    state.length = "facebook";
  } else {
    const n = parseInt(raw, 10);
    if (Number.isNaN(n) || n < 0) return;
    const min = (state.options && state.options.length_min) || 3;
    const max = (state.options && state.options.length_max) || 3600;
    state.seconds = n === 0 ? 0 : Math.min(max, Math.max(min, n));
    state.length = lengthFromSeconds(state.seconds);
  }
  document.querySelectorAll(".length").forEach((n) => n.classList.remove("is-on"));
  markLength(state.length);
  syncSecondsField();
}

function renderOptions(data) {
  const models = $("models");
  models.innerHTML = "";
  data.models.forEach((m) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "card" + (m.id === state.model ? " is-on" : "");
    b.innerHTML = `<strong>${m.label}</strong><span>${m.hint}</span>`;
    b.addEventListener("click", () => {
      state.model = m.id;
      models.querySelectorAll(".card").forEach((n) => n.classList.remove("is-on"));
      b.classList.add("is-on");
    });
    models.appendChild(b);
  });

  const sizes = $("sizes");
  sizes.innerHTML = "";
  data.sizes.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "size" + (s.id === state.size ? " is-on" : "");
    const rw = 22;
    const rh = Math.max(10, Math.round((s.h / s.w) * rw));
    b.innerHTML = `<i class="ratio" style="width:${rw}px;height:${rh}px"></i><strong>${s.label}</strong><span>${s.hint}</span>`;
    b.addEventListener("click", () => {
      state.size = s.id;
      setFrame(s.id);
      sizes.querySelectorAll(".size").forEach((n) => n.classList.remove("is-on"));
      b.classList.add("is-on");
    });
    sizes.appendChild(b);
  });

  const lengths = $("lengths");
  lengths.innerHTML = "";
  const chips = {
    youtube: { lines: ["YouTube", "Shorts"], hint: "3 min max" },
    tiktok: { lines: ["TikTok"], hint: "10 min" },
    instagram: { lines: ["IG Reels"], hint: "20 min" },
    facebook: { lines: ["FB Reels"], hint: "No cap" },
    custom: { lines: ["Custom"], hint: "Seconds" },
  };
  (data.lengths || []).forEach((item) => {
    const chip = chips[item.id] || { lines: [item.label], hint: item.hint };
    const b = document.createElement("button");
    b.type = "button";
    b.className = "length" + (item.id === state.length ? " is-on" : "");
    b.dataset.id = item.id;
    (chip.lines || [chip.label || item.label]).forEach((line) => {
      const strong = document.createElement("strong");
      strong.textContent = line;
      b.appendChild(strong);
    });
    const hint = document.createElement("span");
    hint.textContent = chip.hint || item.hint || "";
    b.appendChild(hint);
    b.addEventListener("click", () => pickLength(item));
    lengths.appendChild(b);
  });
  syncSecondsField();

  const music = $("music");
  music.innerHTML = "";
  data.music.forEach((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tune" + (t.id === state.music ? " is-on" : "");
    if (state.previewId === t.id) b.classList.add("is-playing");
    const label = document.createElement("strong");
    label.textContent = t.name;
    label.title = t.name;
    b.appendChild(label);
    b.addEventListener("click", () => {
      state.music = t.id;
      music.querySelectorAll(".tune").forEach((n) => n.classList.remove("is-on"));
      b.classList.add("is-on");
      playPreview(t, b);
    });
    music.appendChild(b);
  });

  const lib = $("library");
  lib.innerHTML = "";
  (data.library || []).forEach((item) => {
    const li = document.createElement("li");
    li.className = "lib-item";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "lib-open";
    open.textContent = item.name;
    open.addEventListener("click", () => showVideo(item.url, item.name));
    const x = document.createElement("button");
    x.type = "button";
    x.className = "lib-x";
    x.setAttribute("aria-label", "Delete " + item.name);
    x.innerHTML = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3 3l6 6M9 3L3 9" stroke="currentColor" stroke-width="1.7" fill="none"/></svg>';
    x.addEventListener("click", (e) => {
      e.stopPropagation();
      openDelete(item);
    });
    li.appendChild(open);
    li.appendChild(x);
    lib.appendChild(li);
  });

  pill(data.busy ? "rendering" : "idle", data.busy);
}

function showVideo(url, label) {
  stopPreview();
  const player = $("player");
  $("frame").classList.add("has-video");
  state.playingUrl = url;
  player.src = mediaSrc(url) + "?t=" + Date.now();
  player.play().catch(() => {});
  $("jobMeta").textContent = label || "";
}

function clearPlayerIf(file) {
  if (!file) return;
  const player = $("player");
  const src = player.src || "";
  const playing = state.playingUrl || "";
  if (
    src.includes(encodeURIComponent(file)) ||
    src.includes(file) ||
    playing.includes(file)
  ) {
    player.pause();
    player.removeAttribute("src");
    player.load();
    $("frame").classList.remove("has-video");
    $("jobMeta").textContent = "";
    state.playingUrl = null;
  }
}

function openDelete(item) {
  state.pendingDelete = item;
  $("dialogName").textContent = item.name;
  $("dialog").hidden = false;
  $("dialogConfirm").focus();
}

function closeDelete() {
  state.pendingDelete = null;
  $("dialog").hidden = true;
}

async function confirmDelete() {
  const item = state.pendingDelete;
  if (!item) return;
  const name = item.file || item.name;
  const res = await api("/api/library/" + encodeURIComponent(name), { method: "DELETE" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("jobMeta").textContent = body.detail || "Could not delete.";
    closeDelete();
    return;
  }
  clearPlayerIf(name);
  closeDelete();
  loadOptions();
}

function fmtDuration(seconds) {
  const n = Math.max(0, Number(seconds) || 0);
  const m = Math.floor(n / 60);
  const s = n % 60;
  return m + ":" + String(s).padStart(2, "0");
}

function epidemicEnabled() {
  return Boolean(state.options && state.options.epidemic && state.options.epidemic.enabled);
}

function openEpidemic() {
  $("epidemic").hidden = false;
  $("epidemicQuery").focus();
  if (!epidemicEnabled()) {
    $("epidemicStatus").textContent = "Add an Epidemic Sound key in Settings, or put EPIDEMIC_API_KEY in .env and restart studio.";
    $("epidemicList").innerHTML = "";
    return;
  }
  if (!state.epidemicLoaded) loadEpidemic();
}

function closeEpidemic() {
  $("epidemic").hidden = true;
  stopPreview();
}

function setEpidemicKind(kind) {
  state.epidemicKind = kind === "sfx" ? "sfx" : "music";
  document.querySelectorAll(".epidemic-kinds button").forEach((n) => {
    n.classList.toggle("is-on", n.getAttribute("data-kind") === state.epidemicKind);
  });
  $("epidemicQuery").placeholder =
    state.epidemicKind === "sfx"
      ? "Search thunder, whoosh, crowd…"
      : "Search horror, cinematic, dark…";
  state.epidemicLoaded = false;
  loadEpidemic();
}

async function loadEpidemic() {
  const status = $("epidemicStatus");
  const list = $("epidemicList");
  const req = ++state.epidemicReq;
  const kind = state.epidemicKind;
  if (!epidemicEnabled()) {
    status.textContent = "Add an Epidemic Sound key in Settings, or put EPIDEMIC_API_KEY in .env and restart studio.";
    list.innerHTML = "";
    return;
  }
  const q = ($("epidemicQuery").value || "").trim();
  status.textContent = q ? "Searching…" : "Loading catalogue…";
  list.innerHTML = "";
  const params = new URLSearchParams({
    q,
    kind,
    limit: "24",
  });
  let res;
  try {
    res = await api("/api/epidemic/tracks?" + params.toString());
  } catch (err) {
    if (req !== state.epidemicReq) return;
    status.textContent = "Could not reach Epidemic Sound.";
    return;
  }
  const body = await res.json().catch(() => ({}));
  if (req !== state.epidemicReq) return;
  const detail = typeof body.detail === "string" ? body.detail : "";
  if (!res.ok) {
    status.textContent = detail || "Could not reach Epidemic Sound.";
    return;
  }
  const tracks = body.tracks || [];
  state.epidemicLoaded = true;
  if (!tracks.length) {
    status.textContent = q ? "No matches for that search." : "No sounds in the catalogue yet.";
    return;
  }
  status.textContent = q
    ? tracks.length + " match" + (tracks.length === 1 ? "" : "es")
    : "From your Epidemic collections — search to browse the full catalogue.";
  tracks.forEach((track) => list.appendChild(epidemicRow(track)));
}

function epidemicRow(track) {
  const li = document.createElement("li");
  li.className = "epidemic-row";
  if (track.cover) {
    const img = document.createElement("img");
    img.src = track.cover;
    img.alt = "";
    li.appendChild(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "cover-fallback";
    li.appendChild(ph);
  }
  const meta = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = track.title;
  const sub = document.createElement("span");
  const bits = [];
  if (track.artists) bits.push(track.artists);
  if (track.length) bits.push(fmtDuration(track.length));
  sub.textContent = bits.join(" · ");
  meta.appendChild(title);
  meta.appendChild(sub);
  li.appendChild(meta);
  const actions = document.createElement("div");
  actions.className = "row-actions";
  if (track.preview) {
    const play = document.createElement("button");
    play.type = "button";
    play.className = "play";
    play.textContent = "Play";
    play.addEventListener("click", () => {
      playPreview(track, li);
    });
    actions.appendChild(play);
  }
  const use = document.createElement("button");
  use.type = "button";
  use.className = "use";
  use.textContent = "Use this";
  use.addEventListener("click", () => importEpidemic(track, use));
  actions.appendChild(use);
  li.appendChild(actions);
  return li;
}

async function importEpidemic(track, button) {
  if (button) button.disabled = true;
  $("epidemicStatus").textContent = "Saving " + track.title + " into Music…";
  const res = await api("/api/epidemic/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: track.id,
      title: track.title,
      kind: track.kind || state.epidemicKind,
    }),
  });
  const body = await res.json().catch(() => ({}));
  const detail = typeof body.detail === "string" ? body.detail : "";
  if (button) button.disabled = false;
  if (!res.ok) {
    $("epidemicStatus").textContent = detail || "Could not download that sound.";
    return;
  }
  state.music = body.id;
  closeEpidemic();
  $("jobMeta").textContent = "Added " + body.name;
  await loadOptions();
  playPreview(body, document.querySelector(".tune.is-on"));
}

async function uploadTrack(file) {
  const fd = new FormData();
  fd.append("file", file);
  $("jobMeta").textContent = "Uploading " + file.name + "…";
  const res = await api("/api/music/upload", { method: "POST", body: fd });
  const body = await res.json().catch(() => ({}));
  const detail = typeof body.detail === "string" ? body.detail : (body.detail && JSON.stringify(body.detail));
  if (!res.ok) {
    $("jobMeta").textContent = detail || "Could not add that track.";
    return;
  }
  state.music = body.id;
  $("jobMeta").textContent = "Added " + body.name;
  await loadOptions();
  playPreview(body, document.querySelector(".tune.is-on"));
}

async function loadOptions() {
  const data = await (await api("/api/options")).json();
  state.options = data;
  renderOptions(data);
  setFrame(state.size);
  const keys = data.keys || {};
  const gen = data.generate || {};
  if (gen.enabled === false) {
    $("generate").disabled = true;
    $("jobMeta").textContent = gen.hint || "Generate is off on this host.";
    const fine = document.querySelector(".fine");
    if (fine) fine.textContent = gen.hint;
  } else if (state.model === "live" && keys.pexels && !keys.pexels.set) {
    $("jobMeta").textContent = "Add a Pexels key in Settings before generating live b-roll.";
  }
}

async function generate() {
  if (state.options && state.options.generate && state.options.generate.enabled === false) {
    $("jobMeta").textContent = state.options.generate.hint || "Generate is off on this host.";
    return;
  }
  const prompt = $("prompt").value.trim();
  if (prompt.split(/\s+/).length < 8) {
    $("jobMeta").textContent = "Write a fuller story before generating.";
    return;
  }
  onSecondsInput();
  stopPreview();
  $("generate").disabled = true;
  $("log").hidden = true;
  $("log").textContent = "";
  $("jobMeta").classList.remove("bad");
  $("jobMeta").textContent = "Queued…";
  pill("rendering", true);
  const res = await api("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      model: state.model,
      music: state.music,
      size: state.size,
      length: state.length,
      seconds: Number.isFinite(state.seconds) ? state.seconds : 180,
      title: $("title").value.trim(),
      setting: $("setting").value.trim(),
    }),
  });
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : (body.detail && JSON.stringify(body.detail));
  if (!res.ok) {
    $("jobMeta").classList.add("bad");
    $("jobMeta").textContent = detail || "Could not start render.";
    $("generate").disabled = false;
    pill("idle", false);
    return;
  }
  state.jobId = body.id;
  $("jobMeta").classList.remove("bad");
  $("jobMeta").textContent = `Job ${body.id} · ${body.model} · ${body.size} · ${body.seconds || "full"}s`;
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(tickJob, 2000);
  tickJob();
}

async function tickJob() {
  if (!state.jobId) return;
  const job = await (await api("/api/jobs/" + state.jobId)).json();
  const log = (job.log || "").trim();
  if (log) {
    $("log").hidden = false;
    $("log").textContent = log;
    $("log").scrollTop = $("log").scrollHeight;
  } else if (job.status === "running" || job.status === "queued") {
    $("log").hidden = false;
    $("log").textContent = "Working…";
  }
  if (job.status === "done" && job.video) {
    clearInterval(state.poll);
    $("jobMeta").classList.remove("bad");
    showVideo(job.video, job.title || job.stem);
    $("generate").disabled = false;
    pill("idle", false);
    $("log").hidden = true;
    loadOptions();
  } else if (job.status === "error") {
    clearInterval(state.poll);
    $("jobMeta").classList.add("bad");
    $("jobMeta").textContent = job.error || "Render failed";
    $("generate").disabled = false;
    pill("idle", false);
    if (!log) $("log").hidden = true;
  }
}

document.querySelectorAll(".work-tabs .tab[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".work-tabs .tab[data-tab]").forEach((t) => t.classList.remove("is-on"));
    tab.classList.add("is-on");
    const name = tab.getAttribute("data-tab");
    $("view-video").hidden = name !== "video";
    $("view-images").hidden = name !== "images";
  });
});

$("generate").addEventListener("click", generate);
$("seconds").addEventListener("change", onSecondsInput);
$("seconds").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    onSecondsInput();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("epidemic").hidden) {
    closeEpidemic();
    return;
  }
  if (e.key === "Escape" && !$("dialog").hidden) {
    closeDelete();
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") generate();
});

$("preview").addEventListener("ended", () => {
  document.querySelectorAll(".tune.is-playing, .epidemic-row.is-playing").forEach((n) => n.classList.remove("is-playing"));
  state.previewId = null;
});

$("musicFile").addEventListener("change", (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (file) uploadTrack(file);
});

$("dialogCancel").addEventListener("click", closeDelete);
$("dialogConfirm").addEventListener("click", confirmDelete);
$("dialog").addEventListener("click", (e) => {
  if (e.target === $("dialog")) closeDelete();
});

$("epidemicOpen").addEventListener("click", openEpidemic);
$("epidemicClose").addEventListener("click", closeEpidemic);
$("epidemic").addEventListener("click", (e) => {
  if (e.target === $("epidemic")) closeEpidemic();
});
$("epidemicSearch").addEventListener("submit", (e) => {
  e.preventDefault();
  state.epidemicLoaded = false;
  loadEpidemic();
});
$("epidemicKindMusic").addEventListener("click", () => setEpidemicKind("music"));
$("epidemicKindSfx").addEventListener("click", () => setEpidemicKind("sfx"));

$("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
});

api("/api/me")
  .then((res) => res.json())
  .then((me) => {
    $("who").textContent = me.email || "";
  })
  .catch(() => {});

loadOptions().catch((err) => {
  if (String(err.message) === "auth") return;
  $("jobMeta").textContent = "Could not load studio options: " + err;
});
window.addEventListener("resize", () => setFrame(state.size));
