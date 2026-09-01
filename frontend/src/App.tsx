import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuthStore } from "./stores/auth";
import Login from "./pages/Login";
import Chat from "./pages/Chat";

function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  if (status === "authenticated") return <>{children}</>;
  return <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Chat />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
