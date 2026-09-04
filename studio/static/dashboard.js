async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  return res;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

document.getElementById("logout").addEventListener("click", logout);

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
  const lib = document.getElementById("library");
  const empty = document.getElementById("libEmpty");
  const items = opt.library || [];
  if (!items.length) {
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  items.forEach((item) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "/studio";
    a.textContent = item.name;
    li.appendChild(a);
    lib.appendChild(li);
  });
})().catch(() => {});
