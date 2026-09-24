/// <reference types="vite/client" />

interface Window {
  __XRAY_CONFIG__?: {
    supabaseUrl?: string;
    supabaseAnonKey?: string;
    environment?: string;
  };
}
