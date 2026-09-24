import { type FormEvent, useState } from "react";

import { setToken } from "../auth/session";

export function Login() {
  const [value, setValue] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = value.trim();
    if (t) setToken(t);
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
          Production uses secure Supabase sign-in. During local development, use a bearer token.
        </p>
        <form className="form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="token">Development token</label>
            <textarea
              id="token"
              rows={4}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="eyJhbGciOi…"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          <button type="submit" disabled={!value.trim()}>
            Enter workspace <span aria-hidden="true">→</span>
          </button>
        </form>
        <p className="privacy-note"><span aria-hidden="true">●</span> Stored only in this browser</p>
      </section>
    </div>
  );
}
