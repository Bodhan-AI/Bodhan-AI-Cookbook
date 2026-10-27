import { createApiClient } from "../api";

export const api = createApiClient({
  baseUrl: import.meta.env.VITE_API_BASE_URL ?? "",
  appKey: import.meta.env.VITE_APP_KEY || undefined,
});
