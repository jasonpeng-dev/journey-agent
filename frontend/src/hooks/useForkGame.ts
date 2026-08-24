import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../api";

export function useForkGame() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: ({ sourceId, creationKey }: { sourceId: string; creationKey: string }) =>
      api.forkGame(sourceId, creationKey),
    onSuccess: async (target) => {
      await queryClient.invalidateQueries({ queryKey: ["games"] });
      navigate(`/games/${target.id}`);
    },
  });

  const fork = (sourceId: string) =>
    mutation.mutate({ sourceId, creationKey: crypto.randomUUID() });

  return { ...mutation, fork };
}
