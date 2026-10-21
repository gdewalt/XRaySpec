import { type FormEvent, useEffect, useState } from "react";

import { api } from "../api/client";
import type { AccountRead } from "../api/types";
import { signOut, updatePassword } from "../auth/session";
import { Icon } from "./Icon";

export function AccountMenu() {
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState<AccountRead | null>(null);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || account) return;
    void api.getAccount().then(setAccount, (reason) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  }, [account, open]);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (password.length < 8) {
      setError("Use at least 8 characters for the new password.");
      return;
    }
    if (password !== confirmation) {
      setError("The password confirmation does not match.");
      return;
    }
    setBusy(true);
    try {
      await updatePassword(password);
      setPassword("");
      setConfirmation("");
      setMessage("Password updated.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveApiKey(event: FormEvent) {
    event.preventDefault();
    if (!apiKey.trim()) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const updated = await api.setUsptoApiKey(apiKey);
      setAccount(updated);
      setApiKey("");
      setMessage("USPTO API key saved securely.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function removeApiKey() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.deleteUsptoApiKey();
      setAccount((current) => current && { ...current, uspto_api_key_configured: false });
      setApiKey("");
      setMessage("USPTO API key removed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="account-menu"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <button
        type="button"
        className="quiet-button account-menu-button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="settings" size={17} />
        <span>Account</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <section className="account-popover" role="dialog" aria-label="Account settings">
          <header>
            <strong>Account</strong>
            {account?.email && <span>{account.email}</span>}
          </header>
          <form className="account-form" onSubmit={changePassword}>
            <h3>Change password</h3>
            <label>
              New password
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={8}
              />
            </label>
            <label>
              Confirm password
              <input
                type="password"
                autoComplete="new-password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                minLength={8}
              />
            </label>
            <button type="submit" disabled={busy || !password || !confirmation}>
              Update password
            </button>
          </form>
          <form className="account-form" onSubmit={saveApiKey}>
            <h3>USPTO API key</h3>
            <p>
              {account?.uspto_api_key_configured
                ? "A key is stored. Its value cannot be viewed."
                : "Add a personal key for authenticated USPTO access."}
            </p>
            <label>
              {account?.uspto_api_key_configured ? "Replace key" : "API key"}
              <input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Paste API key"
              />
            </label>
            <div className="account-form-actions">
              <button type="submit" disabled={busy || apiKey.trim().length < 8}>
                {account?.uspto_api_key_configured ? "Replace key" : "Save key"}
              </button>
              {account?.uspto_api_key_configured && (
                <button type="button" className="secondary" disabled={busy} onClick={removeApiKey}>
                  Remove
                </button>
              )}
            </div>
          </form>
          {message && <p className="status ok" role="status">{message}</p>}
          {error && <p className="status err" role="alert">{error}</p>}
          <button type="button" className="account-sign-out" onClick={() => void signOut()}>
            <Icon name="log-out" size={16} /> Sign out
          </button>
        </section>
      )}
    </div>
  );
}
