import { type FormEvent, useState } from "react";

import { productionAuthConfigured, sendMagicLink, setToken } from "../auth/session";

export function Login() {
  const productionAuth = productionAuthConfigured();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const input = value.trim();
    if (!input) return;
    if (!productionAuth) {
      setToken(input);
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await sendMagicLink(input);
      setMessage("Check your email for a secure sign-in link.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-layout">
      <section className="login-story" aria-labelledby="login-story-h">
        <p className="eyebrow">Patent reading, with receipts</p>
        <h1 id="login-story-h">Find the exact line.<br />Keep the source in view.</h1>
        <p>
          X-Ray Spec connects specification text, printed references, and drawing callouts back to
          the original patent PDF.
        </p>
        <ul className="feature-list">
          <li><span aria-hidden="true">01</span> Evidence-backed column and line references</li>
          <li><span aria-hidden="true">02</span> Synchronized specification and source PDF</li>
          <li><span aria-hidden="true">03</span> Portable bookmarks, notes, and citations</li>
        </ul>
      </section>
      <section className="panel login-panel" aria-labelledby="login-h">
        <div className="login-lock" aria-hidden="true">⌁</div>
        <p className="eyebrow">Private workspace</p>
        <h2 id="login-h">Sign in to continue</h2>
        <p className="muted">
          {productionAuth
            ? "Enter an approved email address. We’ll send you a secure sign-in link."
            : "Local development mode: paste a bearer token to continue."}
        </p>
        <form className="form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="credential">
              {productionAuth ? "Email address" : "Development token"}
            </label>
            {productionAuth ? (
              <input
                id="credential"
                type="email"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                autoComplete="email"
                required
              />
            ) : (
              <textarea
                id="credential"
                rows={4}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="eyJhbGciOi…"
                spellCheck={false}
                autoComplete="off"
              />
            )}
          </div>
          <button type="submit" disabled={busy || !value.trim()}>
            {busy ? "Sending…" : productionAuth ? "Email sign-in link" : "Enter workspace"}
            {!busy && <span aria-hidden="true"> →</span>}
          </button>
          {message && <p className="status" role="status">{message}</p>}
        </form>
        <p className="privacy-note"><span aria-hidden="true">●</span> Private, allowlisted access</p>
      </section>
    </div>
  );
}
