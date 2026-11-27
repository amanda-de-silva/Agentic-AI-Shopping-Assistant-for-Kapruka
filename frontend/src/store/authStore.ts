import { create } from "zustand";

interface AuthStore {
  authenticated: boolean;
  phone: string | null;
  signIn: (phone: string) => void;
  signOut: () => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  authenticated: false,
  phone: null,

  signIn: (phone) =>
    set({
      authenticated: true,
      phone,
    }),

  signOut: () =>
    set({
      authenticated: false,
      phone: null,
    }),
}));