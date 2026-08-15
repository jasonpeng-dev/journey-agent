import { Link, Navigate, Route, Routes } from "react-router-dom";

import { EditorPage } from "./pages/EditorPage";
import { GamePage } from "./pages/GamePage";
import { GamesPage } from "./pages/GamesPage";
import { NewGamePage } from "./pages/NewGamePage";
import { NewScenarioPage } from "./pages/NewScenarioPage";
import { ScenarioDetailPage } from "./pages/ScenarioDetailPage";
import { ScenarioLibraryPage } from "./pages/ScenarioLibraryPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/scenarios">Journey Agent · 征途智能体</Link>
        <nav><Link to="/scenarios">场景库</Link><Link to="/games">游戏</Link></nav>
      </header>
      <Routes>
        <Route path="/" element={<Navigate replace to="/scenarios" />} />
        <Route path="/scenarios" element={<ScenarioLibraryPage />} />
        <Route path="/scenarios/new" element={<NewScenarioPage />} />
        <Route path="/scenarios/:scenarioId" element={<ScenarioDetailPage />} />
        <Route path="/scenarios/:scenarioId/edit/:section" element={<EditorPage />} />
        <Route path="/scenarios/:scenarioId/edit/:section/:objectKey" element={<EditorPage />} />
        <Route path="/games" element={<GamesPage />} />
        <Route path="/games/new" element={<NewGamePage />} />
        <Route path="/games/:gameId" element={<GamePage />} />
      </Routes>
    </div>
  );
}
