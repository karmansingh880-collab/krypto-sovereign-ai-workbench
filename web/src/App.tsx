import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { AppShell } from "./components/AppShell";
import { useAuth } from "./context/AuthContext";
import { TasksProvider } from "./context/TasksContext";
import { LoginPage } from "./pages/LoginPage";
import { NewTaskPage } from "./pages/NewTaskPage";
import { ProofPage } from "./pages/ProofPage";
import { TaskPage } from "./pages/TaskPage";

function Splash() {
  return (
    <div className="splash" role="status" aria-label="Loading">
      <Loader2 size={28} className="spin" />
    </div>
  );
}

/** Signed-out visitors are sent to the sign-in page and brought back afterwards. */
function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();
  if (state === "loading") return <Splash />;
  if (state === "anon") {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <TasksProvider>
              <AppShell />
            </TasksProvider>
          </RequireAuth>
        }
      >
        <Route index element={<NewTaskPage />} />
        <Route path="task/:id" element={<TaskPage />} />
        <Route path="proof" element={<ProofPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
