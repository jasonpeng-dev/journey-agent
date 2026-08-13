import { Link, Navigate, Route, Routes } from "react-router-dom";

import { EditorPage } from "./pages/EditorPage";
import { ScenarioDetailPage } from "./pages/ScenarioDetailPage";
import { ScenarioLibraryPage } from "./pages/ScenarioLibraryPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/scenarios">
          Journey Agent
        </Link>
        <nav><Link to="/scenarios">Scenarios</Link><span>Games · D5</span></nav>
      </header>
      <Routes>
        <Route path="/" element={<Navigate replace to="/scenarios" />} />
        <Route path="/scenarios" element={<ScenarioLibraryPage />} />
        <Route path="/scenarios/:scenarioId" element={<ScenarioDetailPage />} />
        <Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} />
        <Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} />
      </Routes>
    </div>
  );
}
