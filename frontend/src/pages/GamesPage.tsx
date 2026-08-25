import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useCheckpointGame } from "../hooks/useCheckpointGame";
import { useForkGame } from "../hooks/useForkGame";
import type { GameSummary } from "../types";
import { errorText, uiLabel } from "../ui";

export function GameCards({ games, archived }: { games: GameSummary[] | undefined; archived: boolean }) {
  const queryClient = useQueryClient();
  const checkpoint = useCheckpointGame();
  const fork = useForkGame();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["games"] });
  const archive = useMutation({
    mutationFn: ({ id, revision }: { id: string; revision: number }) =>
      api.archiveGame(id, revision),
    onSuccess: () => void refresh(),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.deleteGame(id), onSuccess: () => void refresh() });
  const permanentlyDelete = (id: string) => {
    if (window.confirm("永久删除该游戏及其任务、计划和执行历史？\n\n此操作无法恢复。")) remove.mutate(id);
  };
  if (!games?.length) return <p className="muted">这里还没有游戏。</p>;
  return <>
    {(archive.error || checkpoint.error || remove.error || fork.error) && <p className="error-text">{errorText(archive.error ?? checkpoint.error ?? remove.error ?? fork.error)}</p>}
    <div className="card-grid">{games.map((game) => <article className={`scenario-card game-card${game.is_checkpoint ? " checkpoint-card" : ""}`} key={game.id}>
      <Link className="game-card-main" to={`/games/${game.id}`}>
        <span className={`status ${game.status.toLowerCase()}`}>{uiLabel(game.status)}</span>
        <h2>游戏 {game.id.slice(0, 8)}</h2><p>场景版本 {game.scenario_version_number}</p>
        <code>{game.scenario_content_hash.slice(0, 12)}</code>
        <p className="game-card-lineage">{game.is_checkpoint ? <>存档 · {game.checkpointed_from_game_instance_id ? `来源 ${game.checkpointed_from_game_instance_id.slice(0, 8)}` : "来源已删除"}</> : archived ? "普通归档" : "进行中"}</p>
      </Link>
      <div className="game-card-actions"><Link className="secondary-button" to={`/games/${game.id}`}>{archived ? "查看记录" : "继续游戏"}</Link>{archived && <button disabled={fork.isPending || remove.isPending} onClick={() => fork.fork(game.id)}>以归档状态新开一局</button>}{!archived && <><button disabled={checkpoint.isPending || archive.isPending || remove.isPending || Boolean(game.active_task_id)} title={game.active_task_id ? "当前有活动任务，完成或放弃后才能存档" : undefined} onClick={() => checkpoint.checkpoint(game.id, game.runtime_revision)}>存档</button><button disabled={archive.isPending || remove.isPending || checkpoint.isPending || Boolean(game.active_task_id)} title={game.active_task_id ? "当前有活动任务，完成或放弃后才能归档" : undefined} onClick={() => archive.mutate({ id: game.id, revision: game.runtime_revision })}>结束并归档</button></>}<button className="danger-button" disabled={archive.isPending || remove.isPending || fork.isPending || checkpoint.isPending} onClick={() => permanentlyDelete(game.id)}>永久删除</button></div>
    </article>)}</div>
  </>;
}

export function GamesPage() {
  const active = useQuery({ queryKey: ["games", false], queryFn: () => api.games(false) });
  const archived = useQuery({ queryKey: ["games", true], queryFn: () => api.games(true) });
  return <main className="page">
    <div className="page-heading"><div><p className="eyebrow">游玩</p><h1>游戏</h1></div><Link className="primary-button" to="/games/new">新游戏</Link></div>
    <h2 className="section-title">进行中的游戏</h2><GameCards games={active.data} archived={false} />
    <h2 className="section-title">历史与已归档游戏</h2><GameCards games={archived.data} archived />
  </main>;
}
