const state = {
  model: "live",
  voice: "p326",
  size: "9:16",
  music: "random",
  length: "youtube",
  seconds: 180,
  jobId: null,
  poll: null,
  options: null,
  previewId: null,
  previewObjectUrl: null,
  pendingDelete: null,
  playingUrl: null,
  epidemicKind: "music",
  epidemicLoaded: false,
  epidemicReq: 0,
  hls: null,
  generateHintShown: false,
  imageModel: "comic",
  imageSize: "9:16",
  imageIndex: 0,
  currentBoard: null,
  tab: "video",
  storyEngine: "ollama",
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

let voicePreviewAbort = null;

function stopPreview() {
  if (voicePreviewAbort) {
    voicePreviewAbort.abort();
    voicePreviewAbort = null;
  }
  if (state.hls) {
    state.hls.destroy();
    state.hls = null;
  }
  if (state.previewObjectUrl) {
    URL.revokeObjectURL(state.previewObjectUrl);
    state.previewObjectUrl = null;
  }
  const a = $("preview");
  a.pause();
  a.removeAttribute("src");
  a.load();
  state.previewId = null;
  document.querySelectorAll(".tune.is-playing, .epidemic-row.is-playing, #voices .card.is-playing, #voices .card.is-loading").forEach((n) => {
    n.classList.remove("is-playing");
    n.classList.remove("is-loading");
  });
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

async function playVoiceSample(voice, button) {
  const previewId = "voice-" + voice.id;
  const a = $("preview");
  if (state.previewId === previewId && !a.paused) {
    stopPreview();
    const note = $("voiceNote");
    if (note) note.textContent = "Click a voice to hear a sample.";
    return;
  }
  if (!voice.preview) return;
  stopPreview();
  voicePreviewAbort = new AbortController();
  const ac = voicePreviewAbort;
  state.previewId = previewId;
  if (button) {
    button.classList.add("is-playing", "is-loading");
  }
  const note = $("voiceNote");
  if (note) note.textContent = "Loading sample… first time is slow.";
  $("player").pause();
  try {
    const res = await api(voice.preview, { signal: ac.signal });
    if (!res.ok) throw new Error("preview");
    const blob = await res.blob();
    if (ac.signal.aborted) return;
    const url = URL.createObjectURL(blob);
    state.previewObjectUrl = url;
    a.src = url;
    await a.play().catch(() => {});
    if (button) button.classList.remove("is-loading");
    if (note) note.textContent = "Click a voice to hear a sample.";
  } catch (err) {
    if (err && (err.name === "AbortError" || String(err.message) === "auth")) return;
    if (button) button.classList.remove("is-playing", "is-loading");
    if (state.previewId === previewId) state.previewId = null;
    if (note) note.textContent = "Could not play that sample. Try again.";
  }
}

function setFrame(size, frameId, monitorId) {
  const [w, h] = size.split(":").map(Number);
  const frame = $(frameId || "frame");
  const monitor = $(monitorId || "monitor");
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

function syncActiveFrame() {
  if (state.tab === "images") setFrame(state.imageSize, "imageFrame", "imageMonitor");
  else setFrame(state.size);
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

  const voices = $("voices");
  if (voices) {
    voices.innerHTML = "";
    (data.voices || []).forEach((v) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "card" + (v.id === state.voice ? " is-on" : "");
      b.innerHTML = `<strong>${v.label}</strong><span>${v.hint}</span>`;
      b.addEventListener("click", () => {
        state.voice = v.id;
        voices.querySelectorAll(".card").forEach((n) => n.classList.remove("is-on"));
        b.classList.add("is-on");
        playVoiceSample(v, b);
      });
      voices.appendChild(b);
    });
    if ((data.voices || []).length && !(data.voices || []).some((v) => v.id === state.voice)) {
      state.voice = data.voices[0].id;
    }
  }

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

function renderImageOptions(data) {
  const images = data.images || {};
  const models = $("imageModels");
  if (models) {
    models.innerHTML = "";
    (images.models || []).forEach((m) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "card" + (m.id === state.imageModel ? " is-on" : "");
      b.innerHTML = `<strong>${m.label}</strong><span>${m.hint}</span>`;
      b.addEventListener("click", () => {
        state.imageModel = m.id;
        models.querySelectorAll(".card").forEach((n) => n.classList.remove("is-on"));
        b.classList.add("is-on");
      });
      models.appendChild(b);
    });
  }

  const sizes = $("imageSizes");
  if (sizes) {
    sizes.innerHTML = "";
    (data.sizes || []).forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "size" + (s.id === state.imageSize ? " is-on" : "");
      const rw = 22;
      const rh = Math.max(10, Math.round((s.h / s.w) * rw));
      b.innerHTML = `<i class="ratio" style="width:${rw}px;height:${rh}px"></i><strong>${s.label}</strong><span>${s.hint}</span>`;
      b.addEventListener("click", () => {
        state.imageSize = s.id;
        setFrame(s.id, "imageFrame", "imageMonitor");
        sizes.querySelectorAll(".size").forEach((n) => n.classList.remove("is-on"));
        b.classList.add("is-on");
      });
      sizes.appendChild(b);
    });
  }

  const lib = $("imageLibrary");
  if (lib) {
    lib.innerHTML = "";
    (images.library || []).forEach((item) => {
      const li = document.createElement("li");
      li.className = "lib-item";
      const open = document.createElement("button");
      open.type = "button";
      open.className = "lib-open";
      open.textContent = item.name + " · " + item.count;
      open.addEventListener("click", () => showBoard(item));
      const x = document.createElement("button");
      x.type = "button";
      x.className = "lib-x";
      x.setAttribute("aria-label", "Delete " + item.name);
      x.innerHTML = '<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3 3l6 6M9 3L3 9" stroke="currentColor" stroke-width="1.7" fill="none"/></svg>';
      x.addEventListener("click", (e) => {
        e.stopPropagation();
        openDelete(item, "images");
      });
      li.appendChild(open);
      li.appendChild(x);
      lib.appendChild(li);
    });
  }

  const fine = $("imageFine");
  if (fine && images.hint) fine.textContent = images.hint;
}

function storyEngineMeta() {
  const story = (state.options && state.options.story) || {};
  const list = story.providers || [];
  return list.find((p) => p.id === state.storyEngine) || list[0] || { id: "ollama" };
}

function renderStoryEngines(data) {
  const story = (data && data.story) || {};
  const providers = story.providers || [
    { id: "ollama", label: "Ollama", hint: "FREE LOCAL", online: false },
    { id: "openai", label: "OpenAI", hint: "API", online: false },
  ];
  if (!providers.some((p) => p.id === state.storyEngine)) {
    state.storyEngine = "ollama";
  }
  const ollama = providers.find((p) => p.id === "ollama") || {};
  const note = ollama.online
    ? (ollama.model_ready ? "" : (ollama.model ? "Model " + ollama.model + " is not installed. Run: ollama pull " + ollama.model : ""))
    : "Ollama is offline. Start it on this Mac, or pick OpenAI — API.";
  ["storyEngines", "imageStoryEngines"].forEach((id) => {
    const wrap = $(id);
    if (!wrap) return;
    wrap.innerHTML = "";
    providers.forEach((p) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "card" + (p.id === state.storyEngine ? " is-on" : "") + (p.online === false ? " is-off" : "");
      const hint = p.id === "ollama" ? (p.online ? "FREE LOCAL" : "offline") : (p.hint || "API");
      b.innerHTML = "<strong>" + p.label + "</strong><span>" + hint + "</span>";
      b.addEventListener("click", () => {
        state.storyEngine = p.id;
        renderStoryEngines(state.options || data);
      });
      wrap.appendChild(b);
    });
  });
  ["storyEngineNote", "imageStoryEngineNote"].forEach((id) => {
    const el = $(id);
    if (el) el.textContent = note;
  });
}

function showBoard(board, index) {
  state.currentBoard = board;
  const panels = (board && board.panels) || [];
  state.imageIndex = Math.max(0, Math.min(index == null ? 0 : index, Math.max(panels.length - 1, 0)));
  if (board && board.size) {
    state.imageSize = board.size;
    setFrame(board.size, "imageFrame", "imageMonitor");
  }
  const img = $("imagePreview");
  const frame = $("imageFrame");
  const strip = $("imageBoard");
  const use = $("useBoard");
  strip.innerHTML = "";
  if (!panels.length) {
    frame.classList.remove("has-image");
    img.hidden = true;
    img.removeAttribute("src");
    img.removeAttribute("data-file");
    strip.hidden = true;
    if (use) use.hidden = true;
    $("imageMeta").textContent = "";
    return;
  }
  const current = panels[state.imageIndex];
  img.hidden = false;
  img.alt = current.beat || board.name || "panel";
  if (img.getAttribute("data-file") !== current.file) {
    img.src = mediaSrc(current.url) + "?t=" + Date.now();
    img.setAttribute("data-file", current.file || "");
  }
  frame.classList.add("has-image");
  strip.hidden = false;
  panels.forEach((panel, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = i === state.imageIndex ? "is-on" : "";
    const thumb = document.createElement("img");
    thumb.alt = "";
    thumb.src = mediaSrc(panel.url);
    b.appendChild(thumb);
    b.addEventListener("click", () => showBoard(board, i));
    strip.appendChild(b);
  });
  $("imageMeta").textContent = (board.name || "") + (current.beat ? " · " + current.beat : "");
  $("imageMeta").classList.remove("bad");
  if (use) use.hidden = false;
}

function showVideo(url, label) {
  hideProgress("video");
  stopPreview();
  const player = $("player");
  $("frame").classList.add("has-video");
  $("frame").classList.remove("has-progress");
  state.playingUrl = url;
  player.src = mediaSrc(url) + "?t=" + Date.now();
  player.play().catch(() => {});
  $("jobMeta").textContent = label || "";
}

function clearPlayer() {
  const player = $("player");
  player.pause();
  player.removeAttribute("src");
  player.load();
  $("frame").classList.remove("has-video");
  state.playingUrl = null;
}

function showProgress(kind, pct, label) {
  const images = kind === "images";
  const frame = images ? $("imageFrame") : $("frame");
  const wrap = images ? $("imageProgress") : $("progress");
  const bar = images ? $("imageProgressBar") : $("progressBar");
  const pctEl = images ? $("imageProgressPct") : $("progressPct");
  const lab = images ? $("imageProgressLabel") : $("progressLabel");
  const n = Math.max(0, Math.min(99, Math.round(Number(pct) || 0)));
  frame.classList.add("has-progress");
  wrap.hidden = false;
  bar.style.width = n + "%";
  pctEl.textContent = n + "%";
  if (label) lab.textContent = label;
}

function hideProgress(kind) {
  const images = kind === "images";
  const frame = images ? $("imageFrame") : $("frame");
  const wrap = images ? $("imageProgress") : $("progress");
  if (frame) frame.classList.remove("has-progress");
  if (wrap) wrap.hidden = true;
}

function jobProgress(job) {
  const raw = job.log || "";
  const log = raw.toLowerCase();
  if (job.kind === "images") {
    const panels = job.panels || [];
    const total = Number(job.panel_count) || Math.max(panels.length, 8);
    if (job.status === "queued") return { pct: 4, label: "Queued…" };
    if (panels.length) {
      return {
        pct: Math.min(96, Math.round((panels.length / total) * 88) + 8),
        label: "Panel " + panels.length + " / " + total,
      };
    }
    return { pct: 10, label: "Drawing…" };
  }
  if (job.status === "queued") return { pct: 3, label: "Queued…" };
  if (/\[assemble\]/.test(log) || /moviepy - done/.test(log)) return { pct: 94, label: "Assembling…" };
  const movie = [...raw.matchAll(/t:\s+(\d+)%/g)];
  if (movie.length) {
    const last = Number(movie[movie.length - 1][1]);
    return { pct: Math.min(93, 78 + Math.round(last * 0.15)), label: "Assembling…" };
  }
  const panels = [...raw.matchAll(/\[image\] panel (\d+)\/(\d+)/g)];
  if (panels.length) {
    const last = panels[panels.length - 1];
    const i = Number(last[1]);
    const n = Number(last[2]) || 1;
    return { pct: 48 + Math.round((i / n) * 28), label: "Stills " + i + "/" + n };
  }
  if (/\[broll\] downloaded|\[broll\] beat/.test(log)) return { pct: 68, label: "B-roll…" };
  if (/\[broll\]/.test(log)) return { pct: 58, label: "B-roll…" };
  if (/\[captions\] transcribed/.test(log)) return { pct: 44, label: "Captions…" };
  if (/\[captions\]/.test(log)) return { pct: 34, label: "Captions…" };
  if (/\[tts\] saved/.test(log)) return { pct: 24, label: "Voice…" };
  if (/\[tts\]/.test(log)) return { pct: 14, label: "Voice…" };
  return { pct: 8, label: "Starting…" };
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
    clearPlayer();
    $("jobMeta").textContent = "";
  }
}

function openDelete(item, kind) {
  state.pendingDelete = Object.assign({}, item, { kind: kind || "video" });
  $("dialogTitle").textContent = kind === "images" ? "Delete this board?" : "Delete this cut?";
  $("dialogCopy").textContent = kind === "images"
    ? "This permanently deletes the stills. You cannot undo it."
    : "This permanently deletes the video and every associated file (audio, captions, b-roll, stills). You cannot undo it.";
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
  const kind = item.kind || "video";
  const url = kind === "images"
    ? "/api/images/" + encodeURIComponent(name)
    : "/api/library/" + encodeURIComponent(name);
  const res = await api(url, { method: "DELETE" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const meta = kind === "images" ? $("imageMeta") : $("jobMeta");
    meta.textContent = body.detail || "Could not delete.";
    closeDelete();
    return;
  }
  if (kind === "images") {
    if (state.currentBoard && (state.currentBoard.stem === item.stem || state.currentBoard.file === name)) {
      showBoard({ panels: [] });
      state.currentBoard = null;
    }
  } else {
    clearPlayerIf(name);
  }
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
  try {
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
    if (!res.ok) {
      $("epidemicStatus").textContent = detail || "Could not download that sound.";
      return;
    }
    state.music = body.id;
    closeEpidemic();
    $("jobMeta").textContent = "Added " + body.name;
    await loadOptions();
  } catch (err) {
    $("epidemicStatus").textContent = "Could not download that sound.";
  } finally {
    if (button) button.disabled = false;
  }
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
}

async function loadOptions() {
  const data = await (await api("/api/options")).json();
  state.options = data;
  renderOptions(data);
  renderImageOptions(data);
  renderStoryEngines(data);
  syncActiveFrame();
  const keys = data.keys || {};
  const gen = data.generate || {};
  if (gen.enabled === false) {
    $("generate").disabled = true;
    if (!state.generateHintShown) {
      state.generateHintShown = true;
      $("jobMeta").textContent = gen.hint || "Generate is off on this host.";
      const fine = document.querySelector(".fine");
      if (fine) fine.textContent = gen.hint;
    }
  } else {
    $("generate").disabled = false;
    if (state.model === "live" && keys.pexels && !keys.pexels.set) {
      $("jobMeta").textContent = "Add a Pexels key in Settings before generating live b-roll.";
    }
  }
}

function openaiEnabled() {
  return Boolean(state.options && state.options.openai && state.options.openai.enabled);
}

async function syncStoryMeta(kind) {
  const images = kind === "images";
  const area = images ? $("imagePrompt") : $("prompt");
  const title = images ? $("imageTitle") : $("title");
  const setting = images ? $("imageSetting") : $("setting");
  const idea = area.value.trim();
  if (idea.split(/\s+/).length < 8) return;
  try {
    const res = await api("/api/story/derive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idea,
        title: title.value.trim(),
        setting: setting.value.trim(),
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return;
    if (body.title) title.value = body.title;
    if (body.setting) setting.value = body.setting;
  } catch (err) {
    if (String(err.message) === "auth") return;
  }
}

async function expandStory(kind) {
  const images = kind === "images";
  const area = images ? $("imagePrompt") : $("prompt");
  const title = images ? $("imageTitle") : $("title");
  const setting = images ? $("imageSetting") : $("setting");
  const meta = images ? $("imageMeta") : $("jobMeta");
  const button = images ? $("expandImageStory") : $("expandStory");
  const idea = area.value.trim();
  const engine = storyEngineMeta();
  if (engine.id === "openai" && !openaiEnabled()) {
    meta.textContent = "Add an OpenAI key in Settings, or put OPENAI_API_KEY in .env and restart studio.";
    return;
  }
  if (engine.id === "ollama" && engine.online === false) {
    meta.textContent = "Ollama isn't running. Start Ollama and try again.";
    return;
  }
  if (engine.id === "ollama" && engine.model_ready === false && engine.model) {
    meta.textContent = "Model " + engine.model + " is not installed. Run: ollama pull " + engine.model;
    return;
  }
  if (!idea) {
    meta.textContent = "Write a short prompt in the story box first.";
    return;
  }
  if (!images) onSecondsInput();
  button.disabled = true;
  meta.classList.remove("bad");
  meta.textContent = "Writing the story…";
  try {
    const res = await api("/api/story/expand", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idea,
        seconds: images ? 180 : (Number.isFinite(state.seconds) ? state.seconds : 180),
        provider: state.storyEngine || "ollama",
      }),
    });
    const body = await res.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "";
    if (!res.ok) {
      meta.classList.add("bad");
      meta.textContent = detail || "Could not write that story.";
      return;
    }
    area.value = body.text || "";
    if (body.title) title.value = body.title;
    if (body.setting) setting.value = body.setting;
    meta.classList.remove("bad");
    meta.textContent = "Story drafted. Title and setting filled from it — edit if you want, then generate.";
  } catch (err) {
    if (String(err.message) === "auth") return;
    meta.classList.add("bad");
    meta.textContent = "Could not write that story.";
  } finally {
    button.disabled = false;
  }
}

async function generate() {
  if (state.tab === "images") {
    generateImages();
    return;
  }
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
  await syncStoryMeta("video");
  stopPreview();
  clearPlayer();
  showProgress("video", 2, "Queued…");
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
      voice: state.voice,
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
    hideProgress("video");
    pill("idle", false);
    return;
  }
  state.jobId = body.id;
  if (body.title) $("title").value = body.title;
  if (body.setting) $("setting").value = body.setting;
  $("jobMeta").classList.remove("bad");
  $("jobMeta").textContent = `Job ${body.id} · ${body.model} · ${body.size} · ${body.seconds || "full"}s`;
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(tickJob, 2000);
  tickJob();
}

function imagePanelCount() {
  const raw = ($("imagePanels").value || "").trim();
  if (!raw) return 0;
  const n = parseInt(raw, 10);
  if (Number.isNaN(n)) return 0;
  return Math.min(16, Math.max(3, n));
}

async function generateImages() {
  const prompt = $("imagePrompt").value.trim();
  if (prompt.split(/\s+/).length < 8) {
    $("imageMeta").textContent = "Write a fuller story before generating.";
    return;
  }
  await syncStoryMeta("images");
  stopPreview();
  hideProgress("images");
  $("imageFrame").classList.remove("has-image");
  showProgress("images", 2, "Queued…");
  $("generateImages").disabled = true;
  $("imageLog").hidden = true;
  $("imageLog").textContent = "";
  $("imageMeta").classList.remove("bad");
  $("imageMeta").textContent = "Queued…";
  pill("drawing", true);
  const res = await api("/api/images/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      model: state.imageModel,
      size: state.imageSize,
      title: $("imageTitle").value.trim(),
      setting: $("imageSetting").value.trim(),
      panels: imagePanelCount(),
    }),
  });
  const body = await res.json().catch(() => ({}));
  const detail = typeof body.detail === "string" ? body.detail : (body.detail && JSON.stringify(body.detail));
  if (!res.ok) {
    $("imageMeta").classList.add("bad");
    $("imageMeta").textContent = detail || "Could not start stills.";
    $("generateImages").disabled = false;
    hideProgress("images");
    pill("idle", false);
    return;
  }
  state.jobId = body.id;
  if (body.title) $("imageTitle").value = body.title;
  if (body.setting) $("imageSetting").value = body.setting;
  $("imageMeta").classList.remove("bad");
  $("imageMeta").textContent = "Job " + body.id + " · " + body.model + " · " + body.size;
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(tickJob, 2000);
  tickJob();
}

function useBoardInReel() {
  const board = state.currentBoard;
  if (!board) return;
  $("prompt").value = board.prompt || $("imagePrompt").value;
  $("title").value = board.name && board.name !== board.stem ? board.name : ($("imageTitle").value || board.stem || "");
  $("setting").value = board.setting || $("imageSetting").value;
  if (board.model) state.model = board.model;
  if (board.size) {
    state.size = board.size;
    setFrame(board.size);
  }
  const videoTab = document.querySelector('.work-tabs .tab[data-tab="video"]');
  if (videoTab) videoTab.click();
  loadOptions();
}

async function tickJob() {
  if (!state.jobId) return;
  const job = await (await api("/api/jobs/" + state.jobId)).json();
  const log = (job.log || "").trim();
  const isImages = job.kind === "images";
  const logEl = isImages ? $("imageLog") : $("log");
  const meta = isImages ? $("imageMeta") : $("jobMeta");
  const go = isImages ? $("generateImages") : $("generate");
  if (log) {
    logEl.hidden = false;
    logEl.textContent = log;
    logEl.scrollTop = logEl.scrollHeight;
  } else if (job.status === "running" || job.status === "queued") {
    logEl.hidden = false;
    logEl.textContent = "Working…";
  }
  if (job.status === "running" || job.status === "queued") {
    const prog = jobProgress(job);
    if (!(isImages && (job.panels || []).length)) {
      showProgress(isImages ? "images" : "video", prog.pct, prog.label);
    }
  }
  if (isImages && (job.panels || []).length) {
    hideProgress("images");
    showBoard({
      name: job.title || job.stem,
      stem: job.stem,
      prompt: job.prompt,
      setting: job.setting,
      model: job.model,
      size: job.size,
      panels: job.panels,
    }, Math.max((job.panels || []).length - 1, 0));
  }
  if (job.status === "done" && (job.video || isImages)) {
    clearInterval(state.poll);
    meta.classList.remove("bad");
    if (isImages) {
      hideProgress("images");
      showBoard({
        name: job.title || job.stem,
        stem: job.stem,
        prompt: job.prompt,
        setting: job.setting,
        model: job.model,
        size: job.size,
        panels: job.panels || [],
      }, 0);
      meta.textContent = (job.title || job.stem) + " · " + ((job.panels || []).length) + " panels";
    } else {
      showVideo(job.video, job.title || job.stem);
    }
    go.disabled = false;
    pill("idle", false);
    logEl.hidden = true;
    loadOptions();
  } else if (job.status === "error") {
    clearInterval(state.poll);
    meta.classList.add("bad");
    meta.textContent = job.error || "Render failed";
    go.disabled = false;
    hideProgress(isImages ? "images" : "video");
    pill("idle", false);
    if (!log) logEl.hidden = true;
  }
}

document.querySelectorAll(".work-tabs .tab[data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".work-tabs .tab[data-tab]").forEach((t) => t.classList.remove("is-on"));
    tab.classList.add("is-on");
    const name = tab.getAttribute("data-tab");
    state.tab = name;
    $("view-video").hidden = name !== "video";
    $("view-images").hidden = name !== "images";
    syncActiveFrame();
  });
});

$("generate").addEventListener("click", generate);
$("expandStory").addEventListener("click", () => expandStory("video"));
$("expandImageStory").addEventListener("click", () => expandStory("images"));
$("generateImages").addEventListener("click", generateImages);
$("prompt").addEventListener("blur", () => syncStoryMeta("video"));
$("imagePrompt").addEventListener("blur", () => syncStoryMeta("images"));
$("useBoard").addEventListener("click", useBoardInReel);
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
  document.querySelectorAll(".tune.is-playing, .epidemic-row.is-playing, #voices .card.is-playing, #voices .card.is-loading").forEach((n) => {
    n.classList.remove("is-playing");
    n.classList.remove("is-loading");
  });
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
window.addEventListener("resize", () => syncActiveFrame());
