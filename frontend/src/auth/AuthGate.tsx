"use client";

// Wraps every signed-in screen (docs/phases/phase-1-step-3.md §9.2): asks the API who is
// signed in (GET /auth/me) once, provides `Me` to the screens, and sends the browser to
// /login on a 401, then and on every later 401 (§6.4). The API is the only authority on
// sessions; the session cookie is HttpOnly and never read here.
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ok, setUnauthorizedHandler } from "@/api/client";
import { ApiError } from "@/api/errors";
import type { Me } from "@/api/schemas";
import { ErrorNotice } from "@/lib/ErrorNotice";

interface AuthValue {
  me: Me;
  /** End this session (POST /auth/logout); `everywhere` ends all of the user's sessions. */
  signOut: (everywhere?: boolean) => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/** The signed-in user, workspace and session. Only inside the AuthGate. */
export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside <AuthGate>");
  return value;
}

type State = { status: "loading" } | { status: "ready"; me: Me } | { status: "failed"; error: unknown };

export function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setUnauthorizedHandler(() => router.replace("/login"));
    return () => setUnauthorizedHandler(null);
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    ok(api.GET("/api/auth/me")).then(
      (me) => {
        if (!cancelled) setState({ status: "ready", me });
      },
      (error: unknown) => {
        if (cancelled) return;
        // A 401 has already been routed to /login by the handler above.
        if (!(error instanceof ApiError && error.status === 401)) setState({ status: "failed", error });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const signOut = useCallback(
    async (everywhere = false) => {
      await ok(api.POST(everywhere ? "/api/auth/logout-all" : "/api/auth/logout"));
      router.replace("/login");
    },
    [router],
  );

  const value = useMemo(
    () => (state.status === "ready" ? { me: state.me, signOut } : null),
    [state, signOut],
  );

  if (state.status === "failed") {
    return (
      <main className="content">
        <ErrorNotice error={state.error} />
        <button
          type="button"
          onClick={() => {
            setState({ status: "loading" });
            setAttempt((n) => n + 1);
          }}
        >
          Erneut versuchen
        </button>
      </main>
    );
  }
  if (!value) {
    return (
      <main className="content">
        <p className="muted">Lädt …</p>
      </main>
    );
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
