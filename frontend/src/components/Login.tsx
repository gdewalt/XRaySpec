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
    <section className="panel" aria-labelledby="login-h">
      <h2 id="login-h">Sign in</h2>
      <p className="muted">
        Production uses Supabase sign-in (magic link or Google). For local development, paste a
        bearer token to continue — it is stored only in this browser.
      </p>
      <form className="form" onSubmit={submit}>
        <label htmlFor="token">Bearer token</label>
        <textarea
          id="token"
          rows={3}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="eyJhbGciOi…"
          spellCheck={false}
        />
        <button type="submit" disabled={!value.trim()}>
          Continue
        </button>
      </form>
    </section>
  );
}
