(() => {
  const root = document.body;
  const base = root.dataset.authBase?.replace(/\/$/, "");
  const mode = root.dataset.mode;
  const form = document.getElementById("account-form");
  const message = document.getElementById("auth-message");
  const submit = document.getElementById("submit-account");
  function notice(text, good = false) {
    message.textContent = text;
    message.classList.toggle("success", good);
    message.hidden = false;
  }
  async function auth(path, data) {
    if (!base) throw new Error("Sign-in is being configured. Please try again shortly.");
    const social = path === "/sign-in/social";
    const response = await fetch((social ? base : "/auth") + path, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.message || result.error || "Sign-in could not be completed.");
    return result;
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const email = form.elements.namedItem("email").value.trim();
    const password = form.elements.namedItem("password").value;
    const name = mode === "signup" ? form.elements.namedItem("name").value.trim() : undefined;
    if (mode === "signup" && name.length < 2) { notice("Please enter your name."); return; }
    submit.disabled = true;
    notice("Checking your account…", true);
    try {
      const data = mode === "signup" ? { name, email, password } : { email, password };
      await auth(mode === "signup" ? "/sign-up/email" : "/sign-in/email", data);
      window.location.assign("/workspace");
    } catch (error) {
      notice(error.message);
      submit.disabled = false;
    }
  });
  document.querySelectorAll("[data-provider]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await auth("/sign-in/social", {
          provider: button.dataset.provider,
          callbackURL: window.location.origin + "/workspace",
        });
        if (!result.url) throw new Error("Provider did not return a sign-in link.");
        window.location.assign(result.url);
      } catch (error) {
        const provider = button.dataset.provider;
        const providerName = { github: "GitHub", gitlab: "GitLab", google: "Google" }[provider] || provider;
        notice(error.message.includes("PROVIDER_NOT_SUPPORTED") || error.message.includes("not supported")
          ? `${providerName} sign-in needs provider activation. Use email or Google for now; connect your source host in the workspace.`
          : error.message);
        button.disabled = false;
      }
    });
  });
  document.getElementById("toggle-password").addEventListener("click", (event) => {
    const input = document.getElementById("password");
    input.type = input.type === "password" ? "text" : "password";
    event.currentTarget.textContent = input.type === "password" ? "Show" : "Hide";
    event.currentTarget.setAttribute("aria-label", input.type === "password" ? "Show password" : "Hide password");
  });
})();
