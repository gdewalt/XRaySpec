import { type FormEvent, useState } from "react";

import {
  productionAuthConfigured,
  setToken,
  signInWithPassword,
  signUpWithPassword,
} from "../auth/session";
import { Icon } from "./Icon";

type AuthMode = "signin" | "signup";
type Message = { kind: "ok" | "err"; text: string } | null;

export function Login() {
  const productionAuth = productionAuthConfigured();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [developmentToken, setDevelopmentToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Message>(null);

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setPassword("");
    setConfirmPassword("");
    setMessage(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!productionAuth) {
      const token = developmentToken.trim();
      if (token) setToken(token);
      return;
    }
    const accountEmail = email.trim().toLowerCase();
    if (!accountEmail || !password) return;
    if (mode === "signup" && password !== confirmPassword) {
      setMessage({ kind: "err", text: "The passwords do not match." });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      if (mode === "signin") {
        await signInWithPassword(accountEmail, password);
      } else {
        const result = await signUpWithPassword(accountEmail, password);
        if (result.confirmationRequired) {
          setMessage({
            kind: "ok",
            text: "Account created. Check your email to confirm it, then sign in.",
          });
          setMode("signin");
          setPassword("");
          setConfirmPassword("");
        }
      }
    } catch (error) {
      setMessage({ kind: "err", text: error instanceof Error ? error.message : String(error) });
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
        <h2 id="login-h">{productionAuth && mode === "signup" ? "Create account" : "Sign in"}</h2>
        <p className="muted">
          {productionAuth
            ? mode === "signup"
              ? "Create a private account. Your email address is your username."
              : "Use your email address and password to open your workspace."
            : "Local development mode: enter a bearer token to continue."}
        </p>
        {productionAuth && (
          <div className="auth-mode-tabs" role="tablist" aria-label="Account action">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "signin"}
              className={mode === "signin" ? "active" : ""}
              onClick={() => changeMode("signin")}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "signup"}
              className={mode === "signup" ? "active" : ""}
              onClick={() => changeMode("signup")}
            >
              Create account
            </button>
          </div>
        )}
        <form className="form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="credential">{productionAuth ? "Email address" : "Development token"}</label>
            {productionAuth ? (
              <input
                id="credential"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@company.com"
                required
              />
            ) : (
              <textarea
                id="credential"
                rows={4}
                value={developmentToken}
                onChange={(event) => setDevelopmentToken(event.target.value)}
                placeholder="eyJhbGciOi…"
                spellCheck={false}
                autoComplete="off"
              />
            )}
          </div>
          {productionAuth && (
            <div className="field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                minLength={8}
                required
              />
              {mode === "signup" && (
                <span className="field-hint">Use at least 8 characters.</span>
              )}
            </div>
          )}
          {productionAuth && mode === "signup" && (
            <div className="field">
              <label htmlFor="confirm-password">Confirm password</label>
              <input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </div>
          )}
          <button
            type="submit"
            disabled={
              busy ||
              (productionAuth
                ? !email.trim() || !password || (mode === "signup" && !confirmPassword)
                : !developmentToken.trim())
            }
          >
            {busy
              ? mode === "signup" ? "Creating account…" : "Signing in…"
              : productionAuth
                ? mode === "signup" ? "Create account" : "Sign in"
                : "Enter workspace"}
            {!busy && <Icon name="arrow-up-right" size={17} />}
          </button>
          {message && (
            <p className={`status ${message.kind}`} role={message.kind === "err" ? "alert" : "status"}>
              {message.text}
            </p>
          )}
        </form>
        <p className="privacy-note">
          <Icon name="lock" size={14} /> Private, Supabase-authenticated workspace
        </p>
      </section>
    </div>
  );
}
