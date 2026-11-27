import { apiFetch } from "./client";

export interface AuthResponse {
  session_id: string;
  phone: string;
}

export interface LogoutResponse {
  ok: boolean;
}

export async function loginWithPhone(
  phone: string
): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({
      phone,
    }),
  });
}

export async function logoutCurrentUser(): Promise<LogoutResponse> {
  return apiFetch<LogoutResponse>("/auth/logout", {
    method: "POST",
  });
}