const form = document.getElementById("authForm");
const toggle = document.getElementById("authToggle");
const nameField = document.getElementById("nameField");
const nameInput = document.getElementById("name");
const password = document.getElementById("password");
const error = document.getElementById("authError");
let mode = "login";

function setMode(next) {
  mode = next;
  const register = mode === "register";
  document.getElementById("authKicker").textContent = register ? "New desk" : "Account";
  document.getElementById("authTitle").textContent = register ? "Create account" : "Sign in";
  document.getElementById("authCopy").textContent = register
    ? "Then add your Pexels, Pollinations, and Epidemic Sound keys in Settings."
    : "Use your desk. Keys stay on this machine, per account.";
  document.getElementById("authSubmit").textContent = register ? "Create account" : "Sign in";
  toggle.textContent = register ? "Already have a desk? Sign in" : "Need an account? Create one";
  nameField.hidden = !register;
  nameInput.disabled = !register;
  nameInput.required = register;
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
  if (mode === "register") body.name = nameInput.value.trim();
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
