async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  return res;
}

const $ = (id) => document.getElementById(id);

let youtubeConnected = false;
let pendingYouTube = null;

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

function formatYouTubeDuration(seconds) {
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(n / 60);
  const s = n % 60;
  return m + ":" + String(s).padStart(2, "0");
}

function renderYouTubeList(videos) {
  const list = $("youtubeList");
  const note = $("youtubeListNote");
  if (!list) return;
  list.innerHTML = "";
  const rows = videos || [];
  if (!youtubeConnected) {
    if (note) note.textContent = "Connect YouTube in Settings to list your shorts here.";
    return;
  }
  if (!rows.length) {
    if (note) note.textContent = "No uploads on this channel yet.";
    return;
  }
  if (note) {
    note.textContent = rows.length + " on your channel. Edit title, description, or visibility — or delete from YouTube.";
  }
  rows.forEach((video) => {
    const li = document.createElement("li");
    li.className = "yt-row";
    const img = document.createElement("img");
    img.alt = "";
    img.src = video.thumb || "";
    const meta = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = video.title || video.id;
    const sub = document.createElement("em");
    sub.textContent = [
      video.shorts ? "Short" : "Video",
      video.privacy || "",
      formatYouTubeDuration(video.seconds),
    ].filter(Boolean).join(" · ");
    meta.appendChild(title);
    meta.appendChild(sub);
    const actions = document.createElement("div");
    actions.className = "yt-row-actions";
    const open = document.createElement("a");
    open.href = video.url;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.textContent = "Open";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => openYouTubeEdit(video));
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "Delete";
    del.addEventListener("click", () => deleteYouTubeVideo(video));
    actions.appendChild(open);
    actions.appendChild(edit);
    actions.appendChild(del);
    li.appendChild(img);
    li.appendChild(meta);
    li.appendChild(actions);
    list.appendChild(li);
  });
}

function openYouTubeEdit(video) {
  pendingYouTube = video;
  $("youtubeName").textContent = video.id || "";
  $("youtubeVideoTitle").value = video.title || "";
  $("youtubeDescription").value = video.description || "";
  $("youtubePrivacy").value = video.privacy || "unlisted";
  $("youtubeCopy").textContent = "Changes apply on YouTube immediately.";
  $("youtubeConfirm").disabled = false;
  $("youtubeConfirm").textContent = "Save";
  $("youtubeDialog").hidden = false;
  $("youtubeVideoTitle").focus();
}

function closeYouTube() {
  pendingYouTube = null;
  $("youtubeDialog").hidden = true;
  $("youtubeConfirm").disabled = false;
  $("youtubeConfirm").textContent = "Save";
}

async function saveYouTubeEdit() {
  const video = pendingYouTube;
  if (!video) return;
  const btn = $("youtubeConfirm");
  btn.disabled = true;
  btn.textContent = "Saving…";
  const res = await api("/api/youtube/videos/" + encodeURIComponent(video.id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: $("youtubeVideoTitle").value.trim(),
      description: $("youtubeDescription").value,
      privacy: $("youtubePrivacy").value,
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    $("youtubeCopy").textContent = body.detail || "Could not save. Reconnect YouTube in Settings if this channel was connected before manage access.";
    btn.disabled = false;
    btn.textContent = "Save";
    return;
  }
  closeYouTube();
  loadYouTubeVideos();
}

async function deleteYouTubeVideo(video) {
  if (!video || !video.id) return;
  if (!window.confirm("Delete “" + (video.title || video.id) + "” from YouTube? This cannot be undone.")) {
    return;
  }
  const res = await api("/api/youtube/videos/" + encodeURIComponent(video.id), { method: "DELETE" });
  const body = await res.json().catch(() => ({}));
  const note = $("youtubeListNote");
  if (!res.ok) {
    if (note) note.textContent = body.detail || "Could not delete that YouTube video.";
    return;
  }
  loadYouTubeVideos();
}

async function loadYouTubeVideos() {
  const note = $("youtubeListNote");
  if (!youtubeConnected) {
    renderYouTubeList([]);
    return;
  }
  if (note) note.textContent = "Loading your YouTube uploads…";
  try {
    const res = await api("/api/youtube/videos");
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (note) {
        note.textContent = body.detail || "Could not list YouTube videos. Reconnect in Settings to allow manage access.";
      }
      return;
    }
    renderYouTubeList(body.videos || []);
  } catch (err) {
    if (String(err.message) === "auth") return;
    if (note) note.textContent = "Could not list YouTube videos.";
  }
}

document.getElementById("logout").addEventListener("click", logout);
if ($("youtubeCancel")) $("youtubeCancel").addEventListener("click", closeYouTube);
if ($("youtubeConfirm")) $("youtubeConfirm").addEventListener("click", saveYouTubeEdit);
if ($("youtubeDialog")) {
  $("youtubeDialog").addEventListener("click", (e) => {
    if (e.target === $("youtubeDialog")) closeYouTube();
  });
}
if ($("youtubeRefresh")) $("youtubeRefresh").addEventListener("click", loadYouTubeVideos);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("youtubeDialog") && !$("youtubeDialog").hidden) closeYouTube();
});

(async () => {
  const meRes = await api("/api/me");
  const me = await meRes.json();
  document.getElementById("who").textContent = me.email || "";
  document.getElementById("hello").textContent = "Welcome back, " + (me.name || me.email.split("@")[0]) + ".";

  const keys = me.keys || {};
  const cards = [
    { id: "pexels", label: "Pexels", hint: "Live b-roll stock video", href: "https://www.pexels.com/api/" },
    { id: "pollinations", label: "Pollinations", hint: "Comic / cartoon / anime stills (optional paid video)", href: "https://enter.pollinations.ai/keys" },
    { id: "epidemic", label: "Epidemic Sound", hint: "Music and sound effects", href: "https://partner-content-api.epidemicsound.com" },
    { id: "openai", label: "OpenAI", hint: "Write the story from a short prompt", href: "https://platform.openai.com/api-keys" },
    { id: "youtube", label: "YouTube", hint: "List, edit, and delete shorts from this dashboard", href: "/settings" },
  ];
  const wrap = document.getElementById("keyCards");
  cards.forEach((item) => {
    const info = keys[item.id] || {};
    const el = document.createElement("article");
    el.className = "deck key-card" + (info.set ? " is-on" : "");
    el.innerHTML =
      "<p class='dialog-kicker'>" + item.label + "</p>" +
      "<h2>" + (info.set ? "Ready" : "Missing") + "</h2>" +
      "<p>" + (info.set ? info.hint : item.hint) + "</p>" +
      "<a href='/settings'>" + (info.set ? "Update key" : "Add key") + "</a>";
    wrap.appendChild(el);
  });

  const optRes = await api("/api/options");
  const opt = await optRes.json();
  const gen = opt.generate || {};
  if (gen.enabled === false) {
    const copy = document.querySelector(".dash-hero .dialog-copy");
    if (copy) {
      copy.textContent = gen.hint
        || "This host keeps accounts and keys. Open Studio on your computer to generate (`python studio.py`).";
    }
  }
  youtubeConnected = Boolean(opt.youtube && opt.youtube.enabled && opt.youtube.connected);

  const lib = document.getElementById("library");
  const empty = document.getElementById("libEmpty");
  const items = opt.library || [];
  if (!items.length) {
    empty.hidden = false;
  } else {
    empty.hidden = true;
    items.forEach((item) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = "/studio";
      a.textContent = item.name;
      li.appendChild(a);
      if (item.youtube && item.youtube.url) {
        const yt = document.createElement("a");
        yt.href = item.youtube.url;
        yt.target = "_blank";
        yt.rel = "noopener noreferrer";
        yt.textContent = "YouTube";
        li.appendChild(yt);
      }
      lib.appendChild(li);
    });
  }

  if (youtubeConnected) loadYouTubeVideos();
  else renderYouTubeList([]);
})().catch(() => {});
