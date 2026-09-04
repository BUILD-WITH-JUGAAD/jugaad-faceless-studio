const form = document.getElementById("authForm");
const toggle = document.getElementById("authToggle");
const nameField = document.getElementById("nameField");
const nameInput = document.getElementById("name");
const inviteField = document.getElementById("inviteField");
const inviteInput = document.getElementById("invite");
const password = document.getElementById("password");
const error = document.getElementById("authError");
let mode = "login";
let hosted = false;
let canRegister = true;
let needInvite = false;

fetch("/api/health")
  .then((res) => res.json())
  .then((data) => {
    hosted = Boolean(data.postgres) || data.generate === false;
    canRegister = data.register !== false;
    needInvite = Boolean(data.invite);
    if (!canRegister && mode === "register") mode = "login";
    setMode(mode);
  })
  .catch(() => {});

function setMode(next) {
  if (next === "register" && !canRegister) next = "login";
  mode = next;
  const register = mode === "register";
  document.getElementById("authKicker").textContent = register ? "New desk" : "Account";
  document.getElementById("authTitle").textContent = register ? "Create account" : "Sign in";
  document.getElementById("authCopy").textContent = register
    ? (hosted
      ? "Add your own Pexels, OpenAI, and Epidemic keys in Settings. This host will not share its keys with your account."
      : "Then add your Pexels, Pollinations, and Epidemic Sound keys in Settings.")
    : (hosted
      ? "Sign in to your JUGAAD account. Keys are stored encrypted with the account, not in the browser."
      : "Use your desk. Keys stay on this machine, per account.");
  document.getElementById("authSubmit").textContent = register ? "Create account" : "Sign in";
  toggle.hidden = !canRegister;
  toggle.textContent = register ? "Already have a desk? Sign in" : "Need an account? Create one";
  nameField.hidden = !register;
  nameInput.disabled = !register;
  nameInput.required = register;
  inviteField.hidden = !register || !needInvite;
  inviteInput.required = register && needInvite;
  inviteInput.disabled = !register || !needInvite;
  password.autocomplete = register ? "new-password" : "current-password";
  error.hidden = true;
}

toggle.addEventListener("click", () => setMode(mode === "login" ? "register" : "login"));

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.hidden = true;
  const body = {
    email: document.getElementById("email").value.trim(),
    password: password.value,
  };
  if (mode === "register") {
    body.name = nameInput.value.trim();
    if (needInvite) body.invite = inviteInput.value.trim();
  }
  const res = await fetch(mode === "register" ? "/api/auth/register" : "/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  const detail = typeof data.detail === "string" ? data.detail : "";
  if (!res.ok) {
    error.hidden = false;
    error.textContent = detail || "Could not sign in.";
    return;
  }
  window.location.href = "/";
});
