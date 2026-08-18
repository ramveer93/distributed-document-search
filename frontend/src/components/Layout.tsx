import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { identity, logout } = useAuth();
  return (
    <div className="shell">
      <header>
        <span className="brand">Deeprunner</span>
        <nav>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/documents">Documents</NavLink>
          <NavLink to="/upload">Add document</NavLink>
        </nav>
        <div className="who">
          <span className="tenant">{identity?.tenant}</span>
          <span className="muted">{identity?.email}</span>
          <button className="link" onClick={logout}>sign out</button>
        </div>
      </header>
      <main><Outlet /></main>
    </div>
  );
}
