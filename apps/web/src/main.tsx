import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, Link } from "react-router-dom";
import "./styles.css";
import { ReferenceContext, useAsync, useReferenceLoader } from "./hooks";
import { api } from "./api";
import { IncidentsPage } from "./pages/Incidents";
import { IncidentPage } from "./pages/Incident";
import { ExperimentsPage } from "./pages/Experiments";
import { ExperimentPage } from "./pages/Experiment";
import { RunPage } from "./pages/Run";
import { ReportPage } from "./pages/Report";
import { BenchmarkPage } from "./pages/Benchmark";
import { IntegrationPage } from "./pages/Integration";

function Shell() {
  const reference = useReferenceLoader();
  const health = useAsync(() => api.health(), []);
  const configured = health.data?.model_integration.configured;
  return (
    <ReferenceContext.Provider value={reference}>
      <div className="shell">
        <header className="topbar">
          <Link to="/" className="brand" aria-label="NearMiss home">
            <svg className="brand-mark" viewBox="0 0 22 22" aria-hidden>
              <rect x="2" y="9" width="11" height="5" fill="#e4e4df" />
              <rect x="13" y="9.6" width="4" height="1" fill="#e4e4df" />
              <rect x="13" y="12.4" width="4" height="1" fill="#e4e4df" />
              <circle cx="18" cy="5" r="2.6" fill="#f07f13" />
            </svg>
            NearMiss
          </Link>
          <nav className="topnav" aria-label="Main">
            <NavLink to="/" end>Incidents</NavLink>
            <NavLink to="/experiments">Experiments</NavLink>
            <NavLink to="/benchmark">Benchmark</NavLink>
          </nav>
          <span className="spacer" />
          <Link to="/integration" className="model-status" title="Nebius Token Factory integration">
            <span className={`dot ${health.error ? "" : configured ? "on" : "off"}`} />
            <span className="txt">
              {health.error ? "API unreachable" : configured ? "Nemotron via Nebius: key set" : "Nemotron via Nebius: no key"}
            </span>
          </Link>
        </header>
        <Routes>
          <Route path="/" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentPage />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="/experiments/:id" element={<ExperimentPage />} />
          <Route path="/experiments/:id/runs/:arm/:strategy/:idx" element={<RunPage />} />
          <Route path="/experiments/:id/report" element={<ReportPage />} />
          <Route path="/benchmark" element={<BenchmarkPage />} />
          <Route path="/integration" element={<IntegrationPage />} />
          <Route path="*" element={<main className="page"><h1>Page not found</h1><p><Link to="/">Back to incidents</Link></p></main>} />
        </Routes>
      </div>
    </ReferenceContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  </StrictMode>,
);
