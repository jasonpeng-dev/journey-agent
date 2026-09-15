import type { JsonObject } from "../editor";

type Props = { document: Record<string, unknown> };
type Point = { x: number; y: number };

const NODE_WIDTH = 172;
const NODE_HEIGHT = 68;
const COLUMN_GAP = 54;
const ROW_GAP = 74;
const PADDING = 52;

function displayName(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function shortText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function nodeLines(value: string): string[] {
  if (value.length <= 14) return [value];
  return [value.slice(0, 14), shortText(value.slice(14), 14)];
}

function relationLabel(value: string): string {
  return shortText(value.replaceAll("_", " "), 22);
}

export function WorldGraph({ document }: Props) {
  const world = (document.world ?? {}) as JsonObject;
  const nodes = Array.isArray(world.nodes) ? world.nodes.filter((node): node is JsonObject => Boolean(node) && typeof node === "object" && !Array.isArray(node)) : [];
  const relations = Array.isArray(world.relations) ? world.relations.filter((relation): relation is JsonObject => Boolean(relation) && typeof relation === "object" && !Array.isArray(relation)) : [];
  const columns = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(Math.max(nodes.length, 1)))));
  const rows = Math.max(1, Math.ceil(nodes.length / columns));
  const width = PADDING * 2 + columns * NODE_WIDTH + (columns - 1) * COLUMN_GAP;
  const height = PADDING * 2 + rows * NODE_HEIGHT + (rows - 1) * ROW_GAP;
  const positions = new Map(nodes.map((node, index): [string, Point] => [String(node.key ?? index), {
    x: PADDING + NODE_WIDTH / 2 + (index % columns) * (NODE_WIDTH + COLUMN_GAP),
    y: PADDING + NODE_HEIGHT / 2 + Math.floor(index / columns) * (NODE_HEIGHT + ROW_GAP),
  }]));

  return <section className="world-graph-panel" aria-label="世界关系图"><header className="world-graph-heading"><div><p className="panel-kicker">TOPOLOGY</p><h4>世界关系图</h4><p className="muted">{nodes.length} 个节点 · {relations.length} 条关系</p></div><div className="world-graph-legend"><span><i className="legend-node" />节点</span><span><i className="legend-relation" />关系</span></div></header><div className="world-graph-viewport">{nodes.length === 0 ? <p className="muted graph-empty">当前还没有可绘制的节点。</p> : <svg className="world-graph" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="世界节点与关系图">{relations.map((relation, index) => { const source = positions.get(String(relation.source_node_key)); const target = positions.get(String(relation.target_node_key)); if (!source || !target) return null; const label = relationLabel(String(relation.relation_type_key ?? "relation")); const labelWidth = Math.max(68, Math.min(170, label.length * 7 + 22)); const middleX = (source.x + target.x) / 2; const middleY = (source.y + target.y) / 2; return <g className="world-graph-relation" key={`${String(relation.key ?? index)}`}><line x1={source.x} y1={source.y} x2={target.x} y2={target.y} /><g transform={`translate(${middleX} ${middleY - 3})`}><rect x={-labelWidth / 2} y={-11} width={labelWidth} height={22} rx={11} /><text x="0" y="0">{label}</text></g></g>; })}{nodes.map((node, index) => { const position = positions.get(String(node.key ?? index))!; const lines = nodeLines(displayName(node.name, String(node.key ?? "未命名节点"))); return <g className="world-graph-node" key={String(node.key ?? index)}><title>{displayName(node.name, String(node.key ?? "未命名节点"))}</title><rect x={position.x - NODE_WIDTH / 2} y={position.y - NODE_HEIGHT / 2} width={NODE_WIDTH} height={NODE_HEIGHT} rx={14} /><text x={position.x} y={position.y - (lines.length - 1) * 8}>{lines.map((line, lineIndex) => <tspan x={position.x} dy={lineIndex === 0 ? 0 : 16} key={line}>{line}</tspan>)}</text></g>; })}</svg>}</div></section>;
}
