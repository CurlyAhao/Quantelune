"use client";

import { create } from "zustand";

import { authApi } from "@/lib/api-endpoints";
import type { AuthResponse, User } from "@/lib/types";
import { isTauri, reportDesktopFault } from "@/lib/desktop-startup";
import {
  advanceActorEpoch,
  assertActorEpoch,
  getActorEpoch,
} from "@/lib/auth-session";

interface AuthState {
  user: User | null;
  /** Invalidates actor-owned cache and page state on explicit identity transitions. */
  actorEpoch: number;
  /** True while an auth operation (session check, login…) is in flight. */
  isLoading: boolean;
  /** True once the initial session check has completed at least once. */
  isInitialized: boolean;

  /** GET /auth/me — resolves to the user or null; never throws. */
  fetchUser: () => Promise<User | null>;
  /** POST /auth/login — throws ApiError on failure. */
  login: (email: string, password: string) => Promise<User>;
  /** POST /auth/register — throws ApiError on failure. */
  register: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<User>;
  /** POST /auth/logout — always clears local state. */
  logout: () => Promise<void>;
  /** POST /auth/refresh — resolves to the user or null; never throws. */
  refreshAuth: () => Promise<User | null>;
}

/** A deduped read is scoped to one identity revision. */
let fetchUserInFlight: {
  epoch: number;
  id: symbol;
  promise: Promise<User | null>;
} | null = null;
let probeController: AbortController | null = null;
let explicitTransition: number | null = null;

export const useAuthStore = create<AuthState>((set, get) => {
  function beginTransition(): number {
    const epoch = advanceActorEpoch();
    explicitTransition = epoch;
    probeController?.abort();
    probeController = null;
    fetchUserInFlight = null;
    set({
      user: null,
      actorEpoch: epoch,
      isLoading: true,
      isInitialized: true,
    });
    return epoch;
  }

  function commitUser(user: User | null): void {
    const epoch =
      get().user?.id !== user?.id ? advanceActorEpoch() : getActorEpoch();
    set({ user, actorEpoch: epoch, isLoading: false, isInitialized: true });
  }

  async function authenticate(
    operation: () => Promise<AuthResponse>,
  ): Promise<User> {
    const epoch = beginTransition();
    try {
      const { user } = await operation();
      assertActorEpoch(epoch);
      explicitTransition = null;
      commitUser(user);
      return user;
    } finally {
      if (getActorEpoch() === epoch) {
        explicitTransition = null;
        set({ isLoading: false });
      }
    }
  }

  return {
    user: null,
    actorEpoch: getActorEpoch(),
    isLoading: false,
    isInitialized: false,

    fetchUser: () => {
      if (explicitTransition !== null) return Promise.resolve(get().user);
      const epoch = getActorEpoch();
      if (fetchUserInFlight?.epoch === epoch) return fetchUserInFlight.promise;
      const id = Symbol("session-probe");
      const controller = new AbortController();
      probeController = controller;
      set({ isLoading: true });
      const promise = (async () => {
        try {
          const user = await Promise.resolve().then(() =>
            authApi.me(controller.signal, { silent: true }),
          );
          assertActorEpoch(epoch);
          commitUser(user);
          return user;
        } catch {
          if (isTauri() && getActorEpoch() === epoch) {
            reportDesktopFault("LOCAL_SESSION_UNAVAILABLE");
            set({ isLoading: false });
            return get().user;
          }
          if (getActorEpoch() === epoch) commitUser(null);
          return get().user;
        } finally {
          if (fetchUserInFlight?.id === id) fetchUserInFlight = null;
          if (probeController === controller) probeController = null;
        }
      })();
      fetchUserInFlight = { epoch, id, promise };
      return promise;
    },

    login: (email, password) =>
      authenticate(() => authApi.login(email, password)),
    register: (email, password, displayName) =>
      authenticate(() => authApi.register(email, password, displayName)),

    logout: async () => {
      const epoch = beginTransition();
      try {
        await authApi.logout();
      } catch {
        // Local identity is already cleared, including on network failure.
      } finally {
        if (getActorEpoch() === epoch) {
          explicitTransition = null;
          set({ isLoading: false });
        }
      }
    },

    refreshAuth: async () => {
      if (explicitTransition !== null) return get().user;
      const epoch = getActorEpoch();
      set({ isLoading: true });
      try {
        const { user } = await authApi.refresh();
        assertActorEpoch(epoch);
        commitUser(user);
        return user;
      } catch {
        if (getActorEpoch() === epoch) commitUser(null);
        return get().user;
      }
    },
  };
});
