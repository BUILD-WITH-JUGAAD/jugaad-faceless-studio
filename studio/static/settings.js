const FIELDS = [
  "pexels",
  "pixabay",
  "unsplash",
  "pixazo",
  "pollinations",
  "epidemic",
  "openai",
  "google_client_id",
  "google_client_secret",
];
const clear = {};
const focused = {};

async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    let detail = "";
    try {
      detail = String(((await res.clone().json()) || {}).detail || "");
    } catch (_) {}
    if (/sign in first/i.test(detail)) {
      window.location.href = "/login";
      throw new Error("auth");
    }
  }
  return res;
}

function wipeKeyInputs() {
  FIELDS.forEach((name) => {
    const input = document.getElementById(name);
    if (input) input.value = "";
  });
  document.querySelectorAll(".autofill-trap input").forEach((el) => {
    el.value = "";
  });
}

function showKeys(keys) {
  FIELDS.forEach((name) => {
    const info = (keys && keys[name]) || {};
    const hint = document.getElementById(name + "Hint");
    const input = document.getElementById(name);
    const remove = document.querySelector('.clear-key[data-key="' + name + '"]');
    if (!hint || !input) return;
    const isSet = Boolean(info.set);
    hint.textContent = isSet ? "· " + (info.hint || "saved") : "· not set";
    input.value = "";
    if (!input.getAttribute("data-empty")) {
      input.setAttribute("data-empty", input.placeholder || "");
    }
    input.placeholder = isSet
      ? "Saved — paste a new value to replace"
      : (input.getAttribute("data-empty") || input.placeholder);
    if (remove) remove.hidden = !isSet && !clear[name];
    delete focused[name];
  });
}

function showYouTube(info, youtube) {
  const box = document.getElementById("youtubeBox");
  const badge = document.getElementById("youtubeBadge");
  const status = document.getElementById("youtubeStatus");
  const setup = document.getElementById("youtubeSetup");
  const connect = document.getElementById("youtubeConnect");
  const disconnect = document.getElementById("youtubeDisconnect");
  const redirect = document.getElementById("youtubeRedirect");
  const yt = youtube || {};
  const keys = info || {};
  if (redirect && yt.redirect) redirect.textContent = yt.redirect;
  const connected = Boolean(keys.set || yt.connected);
  const channel = keys.channel || yt.channel || "";
  if (box) box.classList.toggle("is-on", connected);
  if (badge) badge.textContent = connected ? "Connected" : "Not connected";
  if (status) {
    status.hidden = false;
    if (connected && channel) {
      status.textContent = "Signed in as " + channel + ". Studio can list, edit, and delete your uploads.";
    } else if (connected) {
      status.textContent = "Signed in to YouTube. Studio can list, edit, and delete your uploads.";
    } else {
      status.textContent = "Save the Google client, then Connect YouTube.";
    }
  }
  if (setup) setup.hidden = connected;
  if (connect) {
    connect.hidden = false;
    connect.textContent = connected ? "Reconnect" : "Connect YouTube";
  }
  if (disconnect) disconnect.hidden = !connected;
}

document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
});

FIELDS.forEach((name) => {
  const input = document.getElementById(name);
  if (!input) return;
  input.addEventListener("focus", () => {
    focused[name] = true;
  });
});

document.querySelectorAll(".clear-key").forEach((btn) => {
  if (btn.id === "youtubeDisconnect") return;
  btn.addEventListener("click", () => {
    const name = btn.getAttribute("data-key");
    clear[name] = true;
    focused[name] = true;
    document.getElementById(name).value = "";
    document.getElementById(name + "Hint").textContent = "· will remove on save";
    btn.hidden = false;
  });
});

document.getElementById("keysForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = document.getElementById("keysError");
  const note = document.getElementById("keysNote");
  error.hidden = true;
  note.hidden = true;
  const body = {};
  let changing = 0;
  FIELDS.forEach((name) => {
    const value = document.getElementById(name).value.trim();
    // Ignore Chrome autofill: only save fields the user focused (or marked Remove).
    if (clear[name] && !value) {
      body[name] = null;
      changing += 1;
    } else if (value && focused[name]) {
      body[name] = value;
      changing += 1;
    }
  });
  if (!changing) {
    note.hidden = false;
    note.textContent = "Nothing to save. Click a field, paste the key, then Save.";
    return;
  }
  const res = await api("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  const detail = typeof data.detail === "string" ? data.detail : "";
  if (!res.ok) {
    error.hidden = false;
    error.textContent = detail || "Could not save keys.";
    return;
  }
  Object.keys(clear).forEach((k) => { delete clear[k]; });
  showKeys(data.keys || data);
  note.hidden = false;
  note.textContent = "Saved. Studio will use these keys.";
});

document.getElementById("youtubeDisconnect").addEventListener("click", async () => {
  const res = await api("/api/youtube/disconnect", { method: "POST" });
  const data = await res.json().catch(() => ({}));
  showYouTube((data.keys || {}).youtube, null);
});

(async () => {
  const me = await (await api("/api/me")).json();
  document.getElementById("who").textContent = me.email || "";
  showKeys(me.keys);
  // Chrome often fills after paint — wipe again so passwords never stick.
  wipeKeyInputs();
  setTimeout(wipeKeyInputs, 50);
  setTimeout(wipeKeyInputs, 300);
  setTimeout(wipeKeyInputs, 1000);
  showYouTube((me.keys || {}).youtube, me.youtube);
  const flag = new URLSearchParams(window.location.search).get("youtube");
  const note = document.getElementById("youtubeStatus");
  if (flag === "ok") showYouTube((me.keys || {}).youtube, me.youtube);
  if (flag === "denied" && note) note.textContent = "Google denied access. Try Connect again.";
  if (flag === "state" && note) note.textContent = "That sign-in expired. Click Connect YouTube again.";
  if (flag === "error" && note) note.textContent = "Google did not return tokens. Check the client id, secret, and redirect URI.";
  if (flag === "needclient" && note) note.textContent = "Save a Google client id and secret, then Connect YouTube.";
  if (flag) window.history.replaceState({}, "", "/settings");
})().catch(() => {});
