type Props = { document: Record<string, unknown> };

export function WorldGraph({ document }: Props) {
  const world = (document.world ?? {}) as Record<string, unknown>;
  const nodes = Array.isArray(world.nodes) ? world.nodes as Array<Record<string, unknown>> : [];
  const relations = Array.isArray(world.relations) ? world.relations as Array<Record<string, unknown>> : [];
  const positions = new Map(nodes.map((node, index) => [String(node.key), { x: 90 + (index % 3) * 180, y: 80 + Math.floor(index / 3) * 140 }]));
  return <svg className="world-graph" viewBox="0 0 540 360" role="img" aria-label="World nodes and relations">
    {relations.map((relation, index) => { const source = positions.get(String(relation.source_node_key)); const target = positions.get(String(relation.target_node_key)); return source && target ? <g key={index}><line x1={source.x} y1={source.y} x2={target.x} y2={target.y} /><text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 5}>{String(relation.relation_type_key)}</text></g> : null; })}
    {nodes.map((node) => { const position = positions.get(String(node.key))!; return <g key={String(node.key)}><circle cx={position.x} cy={position.y} r="35" /><text x={position.x} y={position.y}>{String(node.name ?? node.key)}</text></g>; })}
  </svg>;
}
