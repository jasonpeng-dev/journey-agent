import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";

export function GamePage() {
  const { gameId = "" } = useParams();
  const queryClient = useQueryClient();
  const game = useQuery({ queryKey: ["game", gameId], queryFn: () => api.game(gameId) });
  const history = useQuery({ queryKey: ["game-history", gameId], queryFn: () => api.gameHistory(gameId) });
  const archive = useMutation({ mutationFn: () => api.archiveGame(gameId), onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["game", gameId] }); } });
  if (!game.data) return <main className="page"><p>Loading game…</p></main>;
  return <main className="page"><p className="eyebrow">Exact Scenario Version</p><h1>Game {game.data.id.slice(0, 8)}</h1>
    <section className="detail-card"><span className={`status ${game.data.status.toLowerCase()}`}>{game.data.status}</span><h2>Version {game.data.scenario_version_number}</h2><code>{game.data.scenario_content_hash}</code><p>Runtime state persists until you end this game.</p>
      {game.data.status === "ACTIVE" ? <><Link className="primary-button" to={`/games/${gameId}/play`}>Continue</Link> <button className="danger small" onClick={() => archive.mutate()}>End Game</button></> : <p className="muted">Archived games are read-only.</p>}
    </section>
    <h2 className="section-title">History</h2><div className="detail-card"><p>{history.data?.tasks.length ?? 0} tasks · {history.data?.operations.length ?? 0} operations · {history.data?.decisions.length ?? 0} decisions</p></div>
  </main>;
}
