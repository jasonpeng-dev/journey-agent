import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";

export function useCheckpointGame() {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ sourceId, revision, creationKey }: { sourceId: string; revision: number; creationKey: string }) =>
      api.checkpointGame(sourceId, revision, creationKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["games"] }),
  });

  const checkpoint = (sourceId: string, revision: number) =>
    mutation.mutate({ sourceId, revision, creationKey: crypto.randomUUID() });

  return { ...mutation, checkpoint };
}
