import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  buildEntityNeighborhood,
  buildScopeOverview,
  buildScopeTopology,
  findScopeForNode,
  type EntityNeighborhoodProjection,
  type ScopeOverviewProjection,
  type ScopeTopologyProjection,
  type TopologyEntity,
  type TopologyPortal,
  type TopologyRelation,
} from "../topology-projection";
import {
  calculateTopologyFit,
  createNeighborhoodTopologyLayout,
  createRegionNetworkLayout,
  createScopeTemplateLayout,
  topologyLabelPoint,
  topologyNodeBoundaryPoints,
  topologyPointsToPath,
  type TopologyLayoutEdge,
  type TopologyLayoutNode,
  type TopologyLayoutNodePosition,
  type TopologyLayoutResult,
} from "../topology-layout";

export type TopologyContext =
  | { kind: "overview" }
  | { kind: "scope"; scopeKey: string }
  | { kind: "entity"; entityKey: string; scopeKey: string | null };

export type TopologySelection =
  | { kind: "scope"; key: string }
  | { kind: "node"; key: string }
  | { kind: "portal"; key: string; scopeKey: string; neighborScopeKey: string }
  | { kind: "relation"; key: string }
  | null;

type Props = {
  document: Record<string, unknown>;
  context: TopologyContext;
  selection: TopologySelection;
  focusNodeKey?: string | null;
  onContextChange: (context: TopologyContext) => void;
  onSelectionChange: (selection: TopologySelection) => void;
  onOpenEditor: (nodeKey: string) => void;
  onFocusNodeConsumed?: () => void;
};

const SCOPE_CARD = { width: 136, height: 56 };
const ENTITY_CARD = { width: 156, height: 58 };
const FOCUSED_ENTITY_CARD = { width: 164, height: 62 };
const NODE_NAME_LINE_HEIGHT = 14;
const NODE_META_HEIGHT = 10;
const NODE_TEXT_GAP = 4;

function shortText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}…` : value;
}

function textLines(value: string, max = 13): string[] {
  if (value.length <= max) return [value];
  return [value.slice(0, max), shortText(value.slice(max), max)];
}

function relationLabel(value: string): string {
  return shortText(value.replaceAll("_", " "), 18);
}

function stackedNodeTextPosition(layout: TopologyLayoutNodePosition, lineCount: number): { nameY: number; metaY: number } {
  const nameHeight = lineCount * NODE_NAME_LINE_HEIGHT;
  const contentHeight = nameHeight + NODE_TEXT_GAP + NODE_META_HEIGHT;
  const top = layout.y - contentHeight / 2;
  return {
    nameY: top + NODE_NAME_LINE_HEIGHT / 2,
    metaY: top + nameHeight + NODE_TEXT_GAP + NODE_META_HEIGHT / 2,
  };
}

function relationLabelWidth(label: string): number {
  return Math.max(52, Math.min(132, label.length * 6.5 + 18));
}

function useElementSize(elementRef: { current: HTMLDivElement | null }): { width: number; height: number } {
  const [size, setSize] = useState({ width: 960, height: 580 });
  useEffect(() => {
    const element = elementRef.current;
    if (!element) return undefined;
    const update = () => {
      const bounds = element.getBoundingClientRect();
      const width = bounds.width || element.clientWidth;
      const height = bounds.height || element.clientHeight;
      if (width > 0 && height > 0) setSize({ width, height });
    };
    update();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", update);
      return () => window.removeEventListener("resize", update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [elementRef]);
  return size;
}

function relationTypes(relations: TopologyRelation[], portals: TopologyPortal[] = []): string[] {
  return Array.from(new Set([
    ...relations.map((relation) => relation.relationTypeKey),
    ...portals.flatMap((portal) => portal.relationTypeKeys),
  ])).sort((left, right) => left.localeCompare(right));
}

function NodeCard({ node, layout, selected, onClick, onDoubleClick }: { node: TopologyEntity; layout: TopologyLayoutNodePosition; selected: boolean; onClick: () => void; onDoubleClick: () => void }) {
  const nodeLines = textLines(node.name);
  const textPosition = stackedNodeTextPosition(layout, nodeLines.length);
  return <g className={`topology-node topology-node-${selected ? "selected" : "entity"}`} role="button" tabIndex={0} aria-label={node.name} onClick={onClick} onDoubleClick={onDoubleClick} onKeyDown={(event) => { if (event.key === "Enter") onClick(); }}>
    <title>{`${node.name} · ${node.nodeTypeName} · ${node.key}`}</title>
    <rect x={layout.x - layout.width / 2} y={layout.y - layout.height / 2} width={layout.width} height={layout.height} rx={12} />
    <text className="topology-node-name" x={layout.x} y={textPosition.nameY}>{nodeLines.map((line, index) => <tspan x={layout.x} dy={index === 0 ? 0 : NODE_NAME_LINE_HEIGHT} key={`${node.key}:${index}`}>{line}</tspan>)}</text>
    <text className="topology-node-meta" x={layout.x} y={textPosition.metaY}>{node.relationCount} 条关系</text>
  </g>;
}

function TopologyGraphFrame({ layout, ariaLabel, children, fitToViewport = true, layoutForViewport }: { layout: TopologyLayoutResult; ariaLabel: string; children: ReactNode | ((layout: TopologyLayoutResult) => ReactNode); fitToViewport?: boolean; layoutForViewport?: (viewport: { width: number; height: number }) => TopologyLayoutResult }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const viewport = useElementSize(viewportRef);
  const renderedLayout = layoutForViewport ? layoutForViewport(viewport) : layout;
  const fit = calculateTopologyFit(renderedLayout.width, renderedLayout.height, viewport.width, viewport.height);
  const scale = fitToViewport ? fit.scale : 1;
  const offsetX = fitToViewport ? fit.offsetX : 0;
  const offsetY = fitToViewport ? fit.offsetY : 0;
  const renderedChildren = typeof children === "function" ? children(renderedLayout) : children;
  return <div ref={viewportRef} className="topology-fit-viewport"><svg className="topology-canvas" width={fit.viewportWidth} height={fit.viewportHeight} viewBox={`0 0 ${fit.viewportWidth} ${fit.viewportHeight}`} preserveAspectRatio="none" role="img" aria-label={ariaLabel}>
    <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>{renderedChildren}</g>
  </svg></div>;
}

function RelationEdges({ relations, layout, filteredTypes }: { relations: TopologyRelation[]; layout: TopologyLayoutResult; filteredTypes: string[] }) {
  return <>{relations.filter((relation) => filteredTypes.length === 0 || filteredTypes.includes(relation.relationTypeKey)).map((relation) => {
    const source = layout.nodes.get(relation.sourceNodeKey);
    const target = layout.nodes.get(relation.targetNodeKey);
    if (!source || !target) return null;
    const edge = layout.edges.get(relation.key);
    const points = edge?.points.length ? edge.points : topologyNodeBoundaryPoints(source, target);
    const labelPoint = edge?.labelPoint ?? topologyLabelPoint(points);
    const label = relationLabel(relation.relationTypeKey);
    const labelWidth = relationLabelWidth(label);
    return <g className="topology-relation" key={relation.key}>
      <path markerEnd="url(#topology-arrow)" d={topologyPointsToPath(points)} />
      <g className="topology-relation-label" transform={`translate(${labelPoint.x} ${labelPoint.y})`}><rect x={-labelWidth / 2} y={-9} width={labelWidth} height={18} rx={4} /><text x="0" y="0">{label}</text></g>
    </g>;
  })}</>;
}

function PortalRail({ portals, selected, onSelect, onEnter }: { portals: TopologyPortal[]; selected: TopologySelection; onSelect: (portal: TopologyPortal) => void; onEnter: (portal: TopologyPortal) => void }) {
  return <aside className="topology-portal-rail" aria-label="道路与对外连接">
    {portals.length === 0 ? <p className="topology-portal-rail-empty">当前筛选下暂无道路连接</p> : <div className="topology-portal-rail-list">
      {portals.map((portal) => {
        const connectionName = portal.transportNodeNames[0] ?? portal.relationSummaries[0] ?? "外部连接";
        const isSelected = selected?.kind === "portal" && selected.key === portal.key;
        return <button type="button" className={`topology-portal-node${isSelected ? " selected" : ""}`} aria-label={`前往${portal.neighborScopeName}`} key={portal.key} onClick={() => onSelect(portal)} onDoubleClick={() => onEnter(portal)}>
          <span className="topology-portal-arrow" aria-hidden="true">→</span>
          <span className="topology-portal-copy"><strong>{portal.neighborScopeName}</strong><small>{shortText(connectionName, 24)}</small></span>
        </button>;
      })}
    </div>}
  </aside>;
}

function OverviewEdges({ projection, layout }: { projection: ScopeOverviewProjection; layout: TopologyLayoutResult }) {
  return <>{projection.boundaryConnections.map((connection) => {
    const edge = layout.edges.get(connection.key);
    if (!edge) return null;
    return <g className="topology-overview-connection" key={connection.key}><path d={topologyPointsToPath(edge.points)} />{connection.transportNodeKeys.length > 1 && <g transform={`translate(${edge.labelPoint.x} ${edge.labelPoint.y})`}><rect x={-10} y={-8} width={20} height={16} rx={8} /><text x="0" y="0">{connection.transportNodeKeys.length}</text></g>}</g>;
  })}</>;
}

function ScopeOverviewCanvas({ projection, selected, onSelect, onEnter }: { projection: ScopeOverviewProjection; selected: TopologySelection; onSelect: (key: string) => void; onEnter: (key: string) => void }) {
  const layoutNodes = useMemo<TopologyLayoutNode[]>(() => projection.scopes.map((scope) => ({ id: scope.key, ...SCOPE_CARD })), [projection.scopes]);
  const layoutEdges = useMemo<TopologyLayoutEdge[]>(() => projection.boundaryConnections.map((connection) => ({ id: connection.key, source: connection.sourceScopeKey, target: connection.targetScopeKey })), [projection.boundaryConnections]);
  const layout = useMemo(() => createRegionNetworkLayout(layoutNodes, layoutEdges), [layoutEdges, layoutNodes]);
  return <TopologyGraphFrame layout={layout} ariaLabel="范围总览拓扑">
    <OverviewEdges projection={projection} layout={layout} />
    {projection.scopes.map((scope) => {
      const nodeLayout = layout.nodes.get(scope.key);
      if (!nodeLayout) return null;
      const scopeLines = textLines(scope.name, 17);
      const scopeTextPosition = stackedNodeTextPosition(nodeLayout, scopeLines.length);
      const isSelected = selected?.kind === "scope" && selected.key === scope.key;
      return <g className={`topology-scope ${isSelected ? "selected" : ""}`} role="button" tabIndex={0} aria-label={scope.name} key={scope.key} onClick={() => onSelect(scope.key)} onDoubleClick={() => onEnter(scope.key)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(scope.key); }}>
        <title>{`${scope.name} · ${scope.internalNodeCount} 个内部节点 · ${scope.externalConnectionCount} 个外部连接`}</title>
        <rect x={nodeLayout.x - nodeLayout.width / 2} y={nodeLayout.y - nodeLayout.height / 2} width={nodeLayout.width} height={nodeLayout.height} rx={13} />
        <text className="topology-scope-name" x={nodeLayout.x} y={scopeTextPosition.nameY}>{scopeLines.map((line, index) => <tspan x={nodeLayout.x} dy={index === 0 ? 0 : NODE_NAME_LINE_HEIGHT} key={`${scope.key}:${index}`}>{line}</tspan>)}</text>
        <text className="topology-scope-meta" x={nodeLayout.x} y={scopeTextPosition.metaY}>{scope.internalNodeCount} 个实体 · {scope.externalConnectionCount} 个出口</text>
      </g>;
    })}
  </TopologyGraphFrame>;
}

function ScopeTopologyCanvas({ projection, selected, filteredTypes, onSelectNode, onOpenEditor }: { projection: ScopeTopologyProjection; selected: TopologySelection; filteredTypes: string[]; onSelectNode: (key: string) => void; onOpenEditor: (key: string) => void }) {
  const layoutNodes = useMemo<TopologyLayoutNode[]>(() => projection.nodes.map((node) => ({ id: node.key, ...ENTITY_CARD })), [projection.nodes]);
  const layoutEdges = useMemo<TopologyLayoutEdge[]>(() => projection.relations.map((relation) => ({ id: relation.key, source: relation.sourceNodeKey, target: relation.targetNodeKey })), [projection.relations]);
  const layout = useMemo(() => createScopeTemplateLayout(layoutNodes, layoutEdges), [layoutEdges, layoutNodes]);
  const layoutForViewport = useCallback((viewport: { width: number; height: number }) => createScopeTemplateLayout(layoutNodes, layoutEdges, viewport), [layoutEdges, layoutNodes]);
  return <TopologyGraphFrame layout={layout} layoutForViewport={layoutForViewport} fitToViewport={false} ariaLabel="范围内部拓扑">{(renderedLayout) => <>
    <defs><marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
    <RelationEdges relations={projection.relations} layout={renderedLayout} filteredTypes={filteredTypes} />
    {projection.nodes.map((node) => {
      const nodeLayout = renderedLayout.nodes.get(node.key);
      return nodeLayout ? <NodeCard key={node.key} node={node} layout={nodeLayout} selected={selected?.kind === "node" && selected.key === node.key} onClick={() => onSelectNode(node.key)} onDoubleClick={() => onOpenEditor(node.key)} /> : null;
    })}
  </>}</TopologyGraphFrame>;
}

function ScopeContents({ projection, selected, filteredTypes, onSelectNode, onOpenEditor, onPortalSelect, onPortalEnter }: { projection: ScopeTopologyProjection; selected: TopologySelection; filteredTypes: string[]; onSelectNode: (key: string) => void; onOpenEditor: (key: string) => void; onPortalSelect: (portal: TopologyPortal) => void; onPortalEnter: (portal: TopologyPortal) => void }) {
  const portals = projection.portals.filter((portal) => filteredTypes.length === 0 || portal.relationTypeKeys.some((type) => filteredTypes.includes(type)));
  return <div className="topology-scope-contents">
    <div className="topology-scope-main"><ScopeTopologyCanvas projection={projection} selected={selected} filteredTypes={filteredTypes} onSelectNode={onSelectNode} onOpenEditor={onOpenEditor} /></div>
    <PortalRail portals={portals} selected={selected} onSelect={onPortalSelect} onEnter={onPortalEnter} />
  </div>;
}

function EntityNeighborhoodCanvas({ projection, selected, filteredTypes, onSelectNode, onOpenEditor }: { projection: EntityNeighborhoodProjection; selected: TopologySelection; filteredTypes: string[]; onSelectNode: (key: string) => void; onOpenEditor: (key: string) => void }) {
  const layoutNodes = useMemo<TopologyLayoutNode[]>(() => projection.nodes.map((node) => ({ id: node.key, ...(node.key === projection.entity?.key ? FOCUSED_ENTITY_CARD : ENTITY_CARD) })), [projection.entity?.key, projection.nodes]);
  const layoutEdges = useMemo<TopologyLayoutEdge[]>(() => projection.relations.map((relation) => ({ id: relation.key, source: relation.sourceNodeKey, target: relation.targetNodeKey })), [projection.relations]);
  const layout = useMemo(() => createNeighborhoodTopologyLayout(layoutNodes, layoutEdges, projection.entity?.key ?? ""), [layoutEdges, layoutNodes, projection.entity?.key]);
  return <TopologyGraphFrame layout={layout} ariaLabel="实体邻域拓扑">
    <defs><marker id="topology-arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
    <RelationEdges relations={projection.relations} layout={layout} filteredTypes={filteredTypes} />
    {projection.nodes.map((node) => {
      const nodeLayout = layout.nodes.get(node.key);
      return nodeLayout ? <NodeCard key={node.key} node={node} layout={nodeLayout} selected={selected?.kind === "node" && selected.key === node.key} onClick={() => onSelectNode(node.key)} onDoubleClick={() => onOpenEditor(node.key)} /> : null;
    })}
  </TopologyGraphFrame>;
}

function EmptyTopology({ title, detail }: { title: string; detail: string }) {
  return <div className="topology-empty"><strong>{title}</strong><p>{detail}</p></div>;
}

export function WorldGraph({ document, context, selection, focusNodeKey = null, onContextChange, onSelectionChange, onOpenEditor, onFocusNodeConsumed }: Props) {
  const overview = useMemo(() => buildScopeOverview(document), [document]);
  const scopeProjection = useMemo(() => context.kind === "scope" ? buildScopeTopology(document, context.scopeKey) : null, [context, document]);
  const neighborhood = useMemo(() => context.kind === "entity" ? buildEntityNeighborhood(document, context.entityKey) : null, [context, document]);
  const [activeRelationTypes, setActiveRelationTypes] = useState<string[]>([]);
  const contextIdentity = context.kind === "scope" ? context.scopeKey : context.kind === "entity" ? context.entityKey : "overview";

  useEffect(() => {
    setActiveRelationTypes([]);
  }, [contextIdentity]);
  useEffect(() => {
    if (!focusNodeKey) return;
    const scopeKey = findScopeForNode(document, focusNodeKey);
    if (scopeKey) {
      onContextChange({ kind: "scope", scopeKey });
      onSelectionChange({ kind: "node", key: focusNodeKey });
    }
    onFocusNodeConsumed?.();
  }, [document, focusNodeKey, onContextChange, onFocusNodeConsumed, onSelectionChange]);

  const currentRelations = scopeProjection?.relations ?? neighborhood?.relations ?? [];
  const currentPortals = scopeProjection?.portals ?? [];
  const availableRelationTypes = relationTypes(currentRelations, currentPortals);
  const toggleRelationType = (type: string) => setActiveRelationTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type]);
  const contextScopeKey = context.kind === "scope" ? context.scopeKey : context.kind === "entity" ? neighborhood?.entity?.scopeKeys[0] : null;
  const scopeNameByKey = new Map(overview.scopes.map((scope) => [scope.key, scope.name]));
  const headerTitle = context.kind === "overview" ? "世界范围总览" : context.kind === "scope" ? scopeProjection?.scope?.name ?? "范围拓扑" : neighborhood?.entity?.name ?? "实体邻域";
  const headerSubtitle = context.kind === "overview" ? `${overview.scopes.length} 个范围 · ${overview.boundaryConnections.length} 个边界连接` : context.kind === "scope" ? `${scopeProjection?.nodes.length ?? 0} 个内部实体 · ${scopeProjection?.portals.length ?? 0} 个边界出口` : `${neighborhood?.relations.length ?? 0} 条一跳业务关系 · ${neighborhood?.nodes.length ?? 0} 个邻接实体`;

  const goOverview = () => { onContextChange({ kind: "overview" }); onSelectionChange(null); };
  const goScope = (scopeKey: string) => { onContextChange({ kind: "scope", scopeKey }); onSelectionChange({ kind: "scope", key: scopeKey }); };
  const selectPortal = (portal: TopologyPortal) => onSelectionChange({ kind: "portal", key: portal.key, scopeKey: context.kind === "scope" ? context.scopeKey : "", neighborScopeKey: portal.neighborScopeKey });
  const enterPortal = (portal: TopologyPortal) => goScope(portal.neighborScopeKey);

  return <section className="world-graph-panel topology-explorer" aria-label="分层拓扑浏览器">
    <header className="world-graph-heading topology-heading"><div className="topology-heading-copy"><p className="panel-kicker">分层拓扑</p><h4>{headerTitle}</h4><p className="muted">{headerSubtitle}</p></div><div className="topology-heading-actions"><span className="topology-level-badge">{context.kind === "overview" ? "范围总览" : context.kind === "scope" ? "范围内容" : "实体邻域"}</span>{context.kind !== "overview" && <button type="button" className="editor-button editor-button-ghost" onClick={() => context.kind === "entity" && context.scopeKey ? goScope(context.scopeKey) : goOverview()}>← 返回上层</button>}</div></header>
    <div className="topology-breadcrumb" aria-label="拓扑层级导航"><button type="button" className={context.kind === "overview" ? "active" : ""} onClick={goOverview}>世界</button>{context.kind !== "overview" && <><span aria-hidden="true">›</span><button type="button" className={context.kind === "scope" ? "active" : ""} onClick={() => context.kind === "scope" ? goScope(context.scopeKey) : contextScopeKey ? goScope(contextScopeKey) : goOverview()}>{context.kind === "scope" ? scopeProjection?.scope?.name ?? context.scopeKey : contextScopeKey ? scopeNameByKey.get(contextScopeKey) ?? contextScopeKey : "所在范围"}</button></>}{context.kind === "entity" && <><span aria-hidden="true">›</span><button type="button" className="active">{neighborhood?.entity?.name ?? context.entityKey}</button></>}</div>
    {availableRelationTypes.length > 0 && <div className="topology-filter-row"><span>关系筛选</span><button type="button" className={activeRelationTypes.length === 0 ? "selected" : ""} onClick={() => setActiveRelationTypes([])}>全部</button>{availableRelationTypes.map((type) => <button type="button" className={activeRelationTypes.includes(type) ? "selected" : ""} key={type} onClick={() => toggleRelationType(type)}>{relationLabel(type)}</button>)}</div>}
    <div className="world-graph-viewport topology-viewport">
      {!overview.configured && <EmptyTopology title="当前 Scenario 未配置范围拓扑契约" detail="请在已编写的局部性契约中提供范围类型和归属关系；本浏览器不会创建第二套拓扑结构。" />}
      {overview.configured && context.kind === "overview" && (overview.scopes.length === 0 ? <EmptyTopology title="暂无可浏览的范围" detail="当前工作副本没有符合局部性契约的范围节点。" /> : <ScopeOverviewCanvas projection={overview} selected={selection} onSelect={(key) => onSelectionChange({ kind: "scope", key })} onEnter={(key) => goScope(key)} />)}
      {overview.configured && context.kind === "scope" && (scopeProjection?.scope ? (scopeProjection.nodes.length === 0 && scopeProjection.portals.length === 0 ? <EmptyTopology title="此范围暂无内部拓扑" detail="可以返回世界总览或从左侧对象列表定位其它节点。" /> : <ScopeContents projection={scopeProjection} selected={selection} filteredTypes={activeRelationTypes} onSelectNode={(key) => onSelectionChange({ kind: "node", key })} onOpenEditor={onOpenEditor} onPortalSelect={selectPortal} onPortalEnter={enterPortal} />) : <EmptyTopology title="找不到当前范围" detail="该范围可能已在工作副本中被删除。" />)}
      {overview.configured && context.kind === "entity" && (neighborhood?.entity ? <EntityNeighborhoodCanvas projection={neighborhood} selected={selection} filteredTypes={activeRelationTypes} onSelectNode={(key) => onSelectionChange({ kind: "node", key })} onOpenEditor={onOpenEditor} /> : <EmptyTopology title="找不到当前实体" detail="该实体可能已在工作副本中被删除。" />)}
    </div>
    <footer className="topology-hint">单击只选择并查看检查器 · 双击范围进入内部拓扑 · 双击实体打开编辑器 · 双击出口前往邻接范围</footer>
  </section>;
}
