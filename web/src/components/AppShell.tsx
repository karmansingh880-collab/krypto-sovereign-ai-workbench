import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/** Signed-in layout: top bar, history sidebar (a drawer on small screens), and the page. */
export function AppShell() {
  const [drawer, setDrawer] = useState(false);
  const location = useLocation();

  useEffect(() => setDrawer(false), [location.pathname]);

  return (
    <div className="shell">
      <Topbar onMenu={() => setDrawer(true)} />
      <div className="shell-body">
        <Sidebar open={drawer} onClose={() => setDrawer(false)} />
        <main className="content" id="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
