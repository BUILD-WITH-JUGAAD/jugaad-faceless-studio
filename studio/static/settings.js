const FIELDS = ["pexels", "pollinations", "epidemic", "openai"];
const clear = {};

async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  return res;
}

function showKeys(keys) {
  FIELDS.forEach((name) => {
    const info = (keys && keys[name]) || {};
    const hint = document.getElementById(name + "Hint");
    const input = document.getElementById(name);
    hint.textContent = info.set ? "· " + info.hint : "· not set";
    input.value = "";
    input.placeholder = info.set ? "Saved — paste a new key to replace" : input.getAttribute("data-empty") || input.placeholder;
  });
}

document.getElementById("logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
});

document.querySelectorAll(".clear-key").forEach((btn) => {
  btn.addEventListener("click", () => {
    const name = btn.getAttribute("data-key");
    clear[name] = true;
    document.getElementById(name).value = "";
    document.getElementById(name + "Hint").textContent = "· will remove on save";
  });
});

document.getElementById("keysForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = document.getElementById("keysError");
  const note = document.getElementById("keysNote");
  error.hidden = true;
  note.hidden = true;
  const body = {};
  FIELDS.forEach((name) => {
    const value = document.getElementById(name).value.trim();
    if (clear[name] && !value) body[name] = null;
    else if (value) body[name] = value;
  });
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

(async () => {
  const me = await (await api("/api/me")).json();
  document.getElementById("who").textContent = me.email || "";
  showKeys(me.keys);
})().catch(() => {});
