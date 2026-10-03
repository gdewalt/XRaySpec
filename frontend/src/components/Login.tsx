import { type FormEvent, useState } from "react";

import { productionAuthConfigured, sendMagicLink, setToken } from "../auth/session";
import { Icon } from "./Icon";

export function Login() {
  const productionAuth = productionAuthConfigured();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
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
        <div className="login-kicker"><Icon name="scan" size={17} /> Evidence-first patent review</div>
        <h1 id="login-story-h">Read the specification.<br />Verify the source.</h1>
        <p>
          Move between extracted text, precise citations, and the original patent drawings
          without losing context.
        </p>
        <ul className="feature-list">
          <li>
            <span className="feature-icon" aria-hidden="true"><Icon name="text" /></span>
            <span><strong>Precise references</strong><small>Trace findings to printed columns and lines.</small></span>
          </li>
          <li>
            <span className="feature-icon" aria-hidden="true"><Icon name="columns" /></span>
            <span><strong>Synchronized review</strong><small>Compare readable text with the source PDF.</small></span>
          </li>
          <li>
            <span className="feature-icon" aria-hidden="true"><Icon name="bookmark" /></span>
            <span><strong>Reusable evidence</strong><small>Save notes, citations, and portable exports.</small></span>
          </li>
        </ul>
      </section>
      <section className="panel login-panel" aria-labelledby="login-h">
        <div className="login-lock" aria-hidden="true"><Icon name="lock" size={20} /></div>
        <p className="eyebrow">Secure workspace</p>
        <h2 id="login-h">Sign in</h2>
        <p className="muted">
          {productionAuth
            ? "Enter your approved email address. We’ll send a secure sign-in link."
            : "Local development mode: enter a bearer token to continue."}
        </p>
        <form className="form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="credential">{productionAuth ? "Email address" : "Development token"}</label>
            {productionAuth ? (
              <input
                id="credential"
                type="email"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                autoComplete="email"
                placeholder="you@company.com"
                required
              />
            ) : (
              <textarea
                id="credential"
                rows={4}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder="eyJhbGciOi…"
                spellCheck={false}
                autoComplete="off"
              />
            )}
          </div>
          <button type="submit" disabled={busy || !value.trim()}>
            {busy ? "Sending…" : productionAuth ? "Send sign-in link" : "Enter workspace"}
            {!busy && <Icon name="arrow-up-right" size={17} />}
          </button>
          {message && <p className="status ok" role="status">{message}</p>}
        </form>
        <p className="privacy-note"><Icon name="lock" size={14} /> Private, allowlisted access</p>
      </section>
    </div>
  );
}
