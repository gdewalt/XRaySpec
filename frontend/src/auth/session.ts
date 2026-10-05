import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const KEY = "xray_token";

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

let token: string | null = read();
let client: SupabaseClient | null = null;
let initialized = false;
const listeners = new Set<() => void>();

export function getToken(): string | null {
  return token;
}

export function setToken(next: string | null): void {
  token = next;
  try {
    if (next) localStorage.setItem(KEY, next);
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: keep the in-memory token only */
  }
  listeners.forEach((listener) => listener());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function productionAuthConfigured(): boolean {
  const config = window.__XRAY_CONFIG__;
  return Boolean(config?.supabaseUrl && config?.supabaseAnonKey);
}

function getClient(): SupabaseClient {
  if (client) return client;
  const config = window.__XRAY_CONFIG__;
  if (!config?.supabaseUrl || !config.supabaseAnonKey) {
    throw new Error("Supabase authentication is not configured.");
  }
  client = createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  return client;
}

export async function initializeAuth(): Promise<void> {
  if (initialized || !productionAuthConfigured()) return;
  initialized = true;
  const auth = getClient().auth;
  const { data } = await auth.getSession();
  setToken(data.session?.access_token ?? null);
  auth.onAuthStateChange((_event, session) => setToken(session?.access_token ?? null));
}

export async function signInWithPassword(email: string, password: string): Promise<void> {
  const { data, error } = await getClient().auth.signInWithPassword({ email, password });
  if (error) throw error;
  if (!data.session) throw new Error("Supabase did not return a sign-in session.");
  setToken(data.session.access_token);
}

export async function signUpWithPassword(
  email: string,
  password: string,
): Promise<{ confirmationRequired: boolean }> {
  const { data, error } = await getClient().auth.signUp({
    email,
    password,
    options: { emailRedirectTo: window.location.origin },
  });
  if (error) throw error;
  if (data.session) setToken(data.session.access_token);
  return { confirmationRequired: !data.session };
}

export async function signOut(): Promise<void> {
  if (client) await client.auth.signOut();
  setToken(null);
}
