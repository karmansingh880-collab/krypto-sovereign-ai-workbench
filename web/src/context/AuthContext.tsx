import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { auth, setUnauthorizedHandler } from "../api/client";
import type { User } from "../api/types";

type AuthState = "loading" | "authed" | "anon";

interface AuthValue {
  state: AuthState;
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (name: string, email: string, password: string) => Promise<void>;
  demo: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>("loading");
  const [user, setUser] = useState<User | null>(null);

  const signedIn = useCallback((next: User) => {
    setUser(next);
    setState("authed");
  }, []);

  useEffect(() => {
    let alive = true;
    auth
      .me()
      .then((me) => alive && signedIn(me))
      .catch(() => {
        if (alive) {
          setUser(null);
          setState("anon");
        }
      });
    // A protected request answered 401 (session expired): drop to the sign-in page.
    setUnauthorizedHandler(() => {
      setUser(null);
      setState("anon");
    });
    return () => {
      alive = false;
      setUnauthorizedHandler(null);
    };
  }, [signedIn]);

  const value = useMemo<AuthValue>(
    () => ({
      state,
      user,
      login: async (email, password) => signedIn(await auth.login(email, password)),
      signup: async (name, email, password) => signedIn(await auth.signup(name, email, password)),
      demo: async () => signedIn(await auth.demo()),
      logout: async () => {
        try {
          await auth.logout();
        } finally {
          setUser(null);
          setState("anon");
        }
      },
    }),
    [state, user, signedIn],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
