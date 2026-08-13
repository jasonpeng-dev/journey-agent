import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import type { GameSummary } from "../types";

function GameCards({ games }: { games: GameSummary[] | undefined }) {
  if (!games?.length) return <p className="muted">No games here yet.</p>;
  return <div className="card-grid">{games.map((game) => (
    <Link className="scenario-card" key={game.id} to={`/games/${game.id}`}>
      <span className={`status ${game.status.toLowerCase()}`}>{game.status}</span>
      <h2>Game {game.id.slice(0, 8)}</h2><p>Scenario version {game.scenario_version_number}</p>
      <code>{game.scenario_content_hash.slice(0, 12)}</code>
    </Link>
  ))}</div>;
}

export function GamesPage() {
  const active = useQuery({ queryKey: ["games", false], queryFn: () => api.games(false) });
  const archived = useQuery({ queryKey: ["games", true], queryFn: () => api.games(true) });
  return <main className="page">
    <div className="page-heading"><div><p className="eyebrow">Play</p><h1>Games</h1></div><Link className="primary-button" to="/games/new">New Game</Link></div>
    <h2 className="section-title">Active Games</h2><GameCards games={active.data} />
    <h2 className="section-title">History / Archived Games</h2><GameCards games={archived.data} />
  </main>;
}
