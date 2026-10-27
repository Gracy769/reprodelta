(() => {
  const root = document.body;
  const base = root.dataset.authBase?.replace(/\/$/, "");
  const mode = root.dataset.mode;
  const form = document.getElementById("account-form");
  const message = document.getElementById("auth-message");
  const submit = document.getElementById("submit-account");
  const firebaseConfig = document.getElementById("firebase-config");
  const firebaseAuth = () => {
    if (!firebaseConfig || !window.firebase?.auth) throw new Error("Firebase sign-in could not load. Refresh and try again.");
    if (!firebase.apps.length) firebase.initializeApp(JSON.parse(firebaseConfig.textContent));
    return firebase.auth();
  };
  async function exchangeFirebase(user) {
    const idToken = await user.getIdToken(true);
    const response = await fetch("/firebase/session", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: idToken }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || "Sign-in could not be completed.");
    }
  }
  function authError(error) {
    const messages = {
      "auth/popup-blocked": "Allow the Google sign-in popup and try again.",
      "auth/unauthorized-domain": "Add this site in Firebase Authentication → Settings → Authorized domains.",
      "auth/operation-not-allowed": "Enable this sign-in method in Firebase Authentication → Sign-in method.",
      "auth/invalid-api-key": "Check that this site's Firebase Web app settings come from the same project.",
      "auth/email-already-in-use": "This email already has an account. Sign in instead.",
      "auth/invalid-email": "Enter a valid email address.",
      "auth/weak-password": "Choose a stronger password.",
      "auth/invalid-credential": "The email or password is incorrect.",
      "auth/too-many-requests": "Too many attempts. Wait a little and try again.",
    };
    return messages[error.code] || error.message || "Sign-in could not be completed.";
  }
  function notice(text, good = false) {
    message.textContent = text;
    message.classList.toggle("success", good);
    message.hidden = false;
  }
  if (new URLSearchParams(window.location.search).get("auth_error") === "oauth") {
    notice("Social sign-in could not be completed. Please try again or use email sign-in.");
    window.history.replaceState(null, "", window.location.pathname);
  }
  async function auth(path, data) {
    if (!base) throw new Error("Sign-in is being configured. Please try again shortly.");
    const response = await fetch("/auth" + path, {
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
      if (firebaseConfig) {
        const authClient = firebaseAuth();
        if (mode === "signup") {
          const result = await authClient.createUserWithEmailAndPassword(email, password);
          await result.user.updateProfile({ displayName: name });
          await result.user.sendEmailVerification({ url: window.location.origin + "/signin" });
          await authClient.signOut();
          notice("Account created. Check your email, verify your address, then sign in.", true);
          submit.disabled = false;
          return;
        }
        const result = await authClient.signInWithEmailAndPassword(email, password);
        await result.user.reload();
        if (!authClient.currentUser.emailVerified) {
          await authClient.currentUser.sendEmailVerification({ url: window.location.origin + "/signin" });
          await authClient.signOut();
          throw new Error("Verify your email first. We sent a new verification link.");
        }
        await exchangeFirebase(authClient.currentUser);
        await authClient.signOut().catch(() => {});
        window.location.assign("/workspace");
        return;
      }
      const data = mode === "signup" ? { name, email, password } : { email, password };
      await auth(mode === "signup" ? "/sign-up/email" : "/sign-in/email", data);
      window.location.assign("/workspace");
    } catch (error) {
      notice(authError(error));
      submit.disabled = false;
    }
  });
  document.querySelectorAll("[data-provider]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        if (["github", "gitlab"].includes(button.dataset.provider)) {
          window.location.assign(`/oauth/${button.dataset.provider}/start?next=/workspace`);
          return;
        }
        if (button.dataset.provider === "google" && firebaseConfig) {
          const authClient = firebaseAuth();
          const result = await authClient.signInWithPopup(new firebase.auth.GoogleAuthProvider());
          await exchangeFirebase(result.user);
          await authClient.signOut().catch(() => {});
          window.location.assign("/workspace");
          return;
        }
        const result = await auth("/sign-in/social", {
          provider: button.dataset.provider,
          callbackURL: window.location.origin + "/workspace",
        });
        if (!result.url) throw new Error("Provider did not return a sign-in link.");
        window.location.assign(result.url);
      } catch (error) {
        const provider = button.dataset.provider;
        const providerName = { github: "GitHub", gitlab: "GitLab", google: "Google" }[provider] || provider;
        notice(error.message?.includes("PROVIDER_NOT_SUPPORTED") || error.message?.includes("not supported")
          ? `${providerName} sign-in needs provider activation. Choose another sign-in method for now.`
          : authError(error));
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
