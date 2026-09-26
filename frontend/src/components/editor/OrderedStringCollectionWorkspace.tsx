import type { ReactNode } from "react";

import { TextArea, TextInput } from "./FormPrimitives";

export type OrderedStringCollectionWorkspaceProps = {
  itemLabelPrefix: string;
  items: readonly string[];
  selectedIndex: number | null;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (index: number) => void;
  onAdd: () => void;
  onChange: (index: number, value: string) => void;
  onMove: (index: number, direction: "up" | "down") => void;
  onDelete: (index: number) => void;
  fieldLabel: string;
  multiline?: boolean;
  emptyListLabel?: string;
  emptyDetailTitle?: string;
  emptyDetailDescription?: string;
  placeholder?: string;
  focusPath?: string | null;
  fieldPathPrefix?: string;
  headingAddon?: ReactNode;
  showHeader?: boolean;
};

type OrderedStringMasterProps = Pick<
  OrderedStringCollectionWorkspaceProps,
  "itemLabelPrefix" | "items" | "selectedIndex" | "query" | "onQueryChange" | "onSelect" | "onAdd" | "emptyListLabel"
>;

type OrderedStringDetailProps = Pick<
  OrderedStringCollectionWorkspaceProps,
  "itemLabelPrefix" | "items" | "selectedIndex" | "onChange" | "onMove" | "onDelete" | "fieldLabel" | "multiline" | "emptyDetailTitle" | "emptyDetailDescription" | "focusPath" | "fieldPathPrefix" | "headingAddon" | "showHeader"
>;

function itemLabel(prefix: string, index: number): string {
  return `${prefix} ${index + 1}`;
}

export function OrderedStringCollectionMaster({
  itemLabelPrefix,
  items,
  selectedIndex,
  query,
  onQueryChange,
  onSelect,
  onAdd,
  emptyListLabel = "暂无项目",
}: OrderedStringMasterProps) {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = items
    .map((value, index) => ({ value, index }))
    .filter(({ value, index }) => !normalizedQuery || itemLabel(itemLabelPrefix, index).toLocaleLowerCase().includes(normalizedQuery) || value.toLocaleLowerCase().includes(normalizedQuery));
  return <>
    <div className="object-panel-tools">
      <label className="object-search">搜索<input value={query} placeholder={`${itemLabelPrefix}序号或正文`} onChange={(event) => onQueryChange(event.target.value)} /></label>
      <div className="object-list-actions"><button type="button" className="editor-button editor-button-secondary add-object" onClick={onAdd}>＋ {itemLabelPrefix}</button></div>
    </div>
    <div className="object-list-scroll ordered-string-master-list">
      {visible.map(({ value, index }) => <button type="button" className={`collection-list-item${selectedIndex === index ? " selected" : ""}`} key={`${itemLabelPrefix}-${index}`} onClick={() => onSelect(index)}>
        <strong>{itemLabel(itemLabelPrefix, index)}</strong>
        <span>{value || "尚未填写内容"}</span>
      </button>)}
      {items.length === 0 && <p className="muted collection-list-empty">{emptyListLabel}</p>}
      {items.length > 0 && visible.length === 0 && <p className="muted collection-list-empty">没有匹配的项目</p>}
    </div>
  </>;
}

export function OrderedStringCollectionDetail({
  itemLabelPrefix,
  items,
  selectedIndex,
  onChange,
  onMove,
  onDelete,
  fieldLabel,
  multiline = false,
  emptyDetailTitle = "从左侧选择一个对象",
  emptyDetailDescription = "选择或新建对象后，在这里编辑它的结构化字段。",
  focusPath,
  fieldPathPrefix,
  headingAddon,
  showHeader = true,
}: OrderedStringDetailProps) {
  if (selectedIndex === null || items[selectedIndex] === undefined) {
    return <div className="canvas-empty ordered-string-empty"><strong>{emptyDetailTitle}</strong><p>{emptyDetailDescription}</p></div>;
  }
  const title = itemLabel(itemLabelPrefix, selectedIndex);
  const path = focusPath ?? `${fieldPathPrefix ?? (itemLabelPrefix === "快捷目标" ? "goal_resolution.quick_inputs" : "planning.instructions")}.${selectedIndex}`;
  return <div className="typed-specialized-editor ordered-string-detail">
    {showHeader && <header className="collection-detail-heading ordered-string-detail-heading">
      <div><p className="panel-kicker">{itemLabelPrefix}</p><h3>{title}</h3></div>
      <span className="collection-detail-actions">
        <button type="button" className="small" aria-label="上移" disabled={selectedIndex === 0} onClick={() => onMove(selectedIndex, "up")}>↑</button>
        <button type="button" className="small" aria-label="下移" disabled={selectedIndex >= items.length - 1} onClick={() => onMove(selectedIndex, "down")}>↓</button>
        <button type="button" className="small danger" onClick={() => onDelete(selectedIndex)}>删除</button>
      </span>
    </header>}
    {multiline
      ? <TextArea value={items[selectedIndex]} onChange={(value) => onChange(selectedIndex, value)} path={path} label={fieldLabel} headingAddon={headingAddon} />
      : <TextInput value={items[selectedIndex]} onChange={(value) => onChange(selectedIndex, value)} path={path} label={fieldLabel} headingAddon={headingAddon} />}
  </div>;
}

/**
 * Small standalone composition used by focused component tests and future
 * collection pages. EditorPage places the same master/detail halves into its
 * existing shell so navigation and dirty Working Copy state remain shared.
 */
export function OrderedStringCollectionWorkspace(props: OrderedStringCollectionWorkspaceProps) {
  const {
    itemLabelPrefix,
    items,
    selectedIndex,
    query,
    onQueryChange,
    onSelect,
    onAdd,
    onChange,
    onMove,
    onDelete,
    fieldLabel,
    multiline,
    emptyListLabel,
    emptyDetailTitle,
    emptyDetailDescription,
    focusPath,
    fieldPathPrefix,
    headingAddon,
    showHeader,
  } = props;
  return <div className="ordered-string-workspace">
    <aside className="object-list object-panel"><header className="object-panel-header"><div><p className="panel-kicker">内容导航</p><div className="object-panel-title">{itemLabelPrefix}</div></div><span className="object-count">{items.length}</span></header><OrderedStringCollectionMaster itemLabelPrefix={itemLabelPrefix} items={items} selectedIndex={selectedIndex} query={query} onQueryChange={onQueryChange} onSelect={onSelect} onAdd={onAdd} emptyListLabel={emptyListLabel} /></aside>
    <section className="canvas editor-canvas"><header className="canvas-header"><div><p className="panel-kicker">编辑区</p><h3>{selectedIndex === null ? itemLabelPrefix : itemLabel(itemLabelPrefix, selectedIndex)}</h3></div></header><div className="canvas-body"><OrderedStringCollectionDetail itemLabelPrefix={itemLabelPrefix} items={items} selectedIndex={selectedIndex} onChange={onChange} onMove={onMove} onDelete={onDelete} fieldLabel={fieldLabel} multiline={multiline} emptyDetailTitle={emptyDetailTitle} emptyDetailDescription={emptyDetailDescription} focusPath={focusPath} fieldPathPrefix={fieldPathPrefix} headingAddon={headingAddon} showHeader={showHeader} /></div></section>
  </div>;
}
