export type MoveDirection = "up" | "down";

/** Swap one existing array item without sorting or changing its identity. */
export function moveItem<T>(items: readonly T[], index: number, direction: MoveDirection): T[] {
  const next = [...items];
  const target = direction === "up" ? index - 1 : index + 1;
  if (index < 0 || index >= next.length || target < 0 || target >= next.length) return next;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
