import type {
  PresentationActorField,
  PresentationProfileDocument,
  PresentationTemplate,
} from "../types";
import "../presentationSettings.css";
import {
  PRESENTATION_OPTIONS,
  resolvePresentationProfilePreview,
} from "../presentationPolicy";

type EditableSection =
  | "global_display"
  | "world_entities"
  | "actor_team"
  | "goal_execution";

type PresentationSettingsPanelProps = {
  profile: PresentationProfileDocument;
  onChange: (profile: PresentationProfileDocument) => void;
  disabled?: boolean;
  compact?: boolean;
};

const labels: Record<string, string> = {
  compact: "紧凑",
  standard: "标准",
  detailed: "详尽",
  COMPACT: "紧凑",
  STANDARD: "标准",
  DETAILED: "详尽",
  COLLAPSED: "折叠",
  FULL: "完整",
  HEADER: "标题区",
  BODY: "正文区",
  BOTH: "标题区 + 正文区",
  SUMMARY: "摘要",
  DETAIL: "详情",
  CAUSALITY: "因果链",
  A: "已确认事实",
  "A+B": "事实 + 关系",
  "A+B+C": "事实 + 关系 + 因果",
  NAME: "名称",
  NODE_TYPE: "类型",
  VISIBILITY: "可见性",
  ACCESS: "访问状态",
  STATUS: "状态",
  FACTS: "事实",
  RESOURCES: "资源",
  RELATIONS: "关系",
  AMOUNT: "数量",
  UNIT: "单位",
  TYPE: "关系类型",
  TARGET: "目标",
  ROLE: "角色",
  LOCATION: "位置",
  TASK: "当前任务",
  COMMAND_REACHABILITY: "指挥可达性",
};

function displayLabel(value: string): string {
  return labels[value] ?? value;
}

function updateSection(
  profile: PresentationProfileDocument,
  section: EditableSection,
  field: string,
  value: unknown | null,
): PresentationProfileDocument {
  const next = JSON.parse(JSON.stringify(profile)) as PresentationProfileDocument;
  const sections = next as unknown as Record<string, Record<string, unknown> | undefined>;
  const current = { ...(sections[section] ?? {}) };
  if (value === null || value === "") delete current[field];
  else current[field] = value;
  if (Object.keys(current).length === 0) delete sections[section];
  else sections[section] = current;
  return next;
}

function SelectField({
  label,
  testId,
  value,
  values,
  onChange,
  disabled,
}: {
  label: string;
  testId: string;
  value: string | undefined;
  values: string[];
  onChange: (value: string | null) => void;
  disabled?: boolean;
}) {
  return (
    <label className="presentation-field">
      <span>{label}</span>
      <select
        data-testid={testId}
        disabled={disabled}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">跟随模板（当前：{value ? displayLabel(value) : "模板默认"}）</option>
        {values.map((option) => <option key={option} value={option}>{displayLabel(option)}</option>)}
      </select>
    </label>
  );
}

function OrderedOptions({
  label,
  values,
  onChange,
  disabled,
  testId,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  disabled?: boolean;
  testId: string;
}) {
  const move = (index: number, offset: -1 | 1) => {
    const nextIndex = index + offset;
    if (nextIndex < 0 || nextIndex >= values.length) return;
    const next = [...values];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    onChange(next);
  };
  return (
    <fieldset className="presentation-order" data-testid={testId}>
      <legend>{label}</legend>
      <div className="presentation-order-list">
        {values.map((value, index) => (
          <div className="presentation-order-row" key={value}>
            <span>{index + 1}. {displayLabel(value)}</span>
            <span>
              <button type="button" disabled={disabled || index === 0} aria-label={`${displayLabel(value)} 上移`} onClick={() => move(index, -1)}>↑</button>
              <button type="button" disabled={disabled || index === values.length - 1} aria-label={`${displayLabel(value)} 下移`} onClick={() => move(index, 1)}>↓</button>
            </span>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

export function PresentationSettingsPanel({
  profile,
  onChange,
  disabled = false,
  compact = false,
}: PresentationSettingsPanelProps) {
  const effective = resolvePresentationProfilePreview(profile);
  const update = (section: EditableSection, field: string, value: unknown | null) =>
    onChange(updateSection(profile, section, field, value));
  const updateOrder = (section: EditableSection, field: string, values: string[]) =>
    update(section, field, values);
  const actorFields = PRESENTATION_OPTIONS.actorFields;
  const visibleActorFields = profile.actor_team?.visible_fields ?? effective.actor_fields as PresentationActorField[];
  const actorOrder = profile.actor_team?.field_order ?? effective.actor_fields as PresentationActorField[];

  return (
    <div className={`presentation-settings-panel${compact ? " presentation-settings-panel--compact" : ""}`} data-testid="presentation-settings-panel">
      <section className="presentation-settings-card">
        <div className="presentation-card-heading"><div><p className="eyebrow">PresentationProfile</p><h2>全局显示模板</h2></div><span className="presentation-effective">当前：{displayLabel(effective.template)} / {displayLabel(effective.density)}</span></div>
        <div className="presentation-field-grid">
          <label className="presentation-field">
            <span>模板</span>
            <select data-testid="presentation-template" disabled={disabled} value={profile.template} onChange={(event) => onChange({ ...profile, template: event.target.value as PresentationTemplate })}>
              {PRESENTATION_OPTIONS.templates.map((option) => <option key={option} value={option}>{displayLabel(option)}</option>)}
            </select>
          </label>
          <SelectField label="信息密度" testId="presentation-density" value={profile.global_display?.density} values={PRESENTATION_OPTIONS.densities} disabled={disabled} onChange={(value) => update("global_display", "density", value)} />
          <SelectField label="默认展开" testId="presentation-default-open" value={profile.global_display?.default_open} values={PRESENTATION_OPTIONS.defaultOpen} disabled={disabled} onChange={(value) => update("global_display", "default_open", value)} />
          <SelectField label="摘要位置" testId="presentation-summary-slot" value={profile.global_display?.summary_slot} values={PRESENTATION_OPTIONS.summarySlots} disabled={disabled} onChange={(value) => update("global_display", "summary_slot", value)} />
        </div>
        <OrderedOptions label="语义信息顺序" testId="presentation-semantic-order" values={profile.global_display?.semantic_order ?? effective.semantic_order} disabled={disabled} onChange={(values) => updateOrder("global_display", "semantic_order", values)} />
      </section>

      <section className="presentation-settings-card">
        <div className="presentation-card-heading"><div><p className="eyebrow">World entities</p><h2>世界与知识</h2></div><span className="presentation-effective">玩家边界不变</span></div>
        <div className="presentation-field-grid">
          <SelectField label="实体详情" testId="presentation-entity-detail" value={profile.world_entities?.entity_detail} values={PRESENTATION_OPTIONS.entityDetails} disabled={disabled} onChange={(value) => update("world_entities", "entity_detail", value)} />
          <SelectField label="知识层级" testId="presentation-knowledge-level" value={profile.world_entities?.knowledge_level} values={PRESENTATION_OPTIONS.knowledgeLevels} disabled={disabled} onChange={(value) => update("world_entities", "knowledge_level", value)} />
        </div>
        <div className="presentation-order-grid">
          <OrderedOptions label="资源字段顺序" testId="presentation-resource-order" values={profile.world_entities?.resource_order ?? effective.resource_order} disabled={disabled} onChange={(values) => updateOrder("world_entities", "resource_order", values)} />
          <OrderedOptions label="关系字段顺序" testId="presentation-relation-order" values={profile.world_entities?.relation_order ?? effective.relation_order} disabled={disabled} onChange={(values) => updateOrder("world_entities", "relation_order", values)} />
        </div>
      </section>

      <section className="presentation-settings-card">
        <div className="presentation-card-heading"><div><p className="eyebrow">Actor team</p><h2>角色与团队</h2></div><span className="presentation-effective">仅显示安全字段</span></div>
        <div className="presentation-actor-fields">
          <fieldset className="presentation-order"><legend>可见字段</legend><div className="presentation-checkbox-list">
            {actorFields.map((field) => <label key={field}><input type="checkbox" disabled={disabled} checked={visibleActorFields.includes(field)} onChange={(event) => {
              const next = event.target.checked
                ? [...visibleActorFields, field]
                : visibleActorFields.filter((item) => item !== field);
              update("actor_team", "visible_fields", next);
            }} />{displayLabel(field)}</label>)}
          </div></fieldset>
          <OrderedOptions label="字段顺序" testId="presentation-actor-order" values={actorOrder} disabled={disabled} onChange={(values) => updateOrder("actor_team", "field_order", values)} />
        </div>
      </section>

      <section className="presentation-settings-card">
        <div className="presentation-card-heading"><div><p className="eyebrow">Goal & execution</p><h2>目标、计划与时间线</h2></div><span className="presentation-effective">Agent 行为不变</span></div>
        <div className="presentation-field-grid">
          <SelectField label="目标路线图" testId="presentation-roadmap-detail" value={profile.goal_execution?.roadmap_detail} values={PRESENTATION_OPTIONS.entityDetails} disabled={disabled} onChange={(value) => update("goal_execution", "roadmap_detail", value)} />
          <SelectField label="计划默认展开" testId="presentation-plan-default" value={profile.goal_execution?.plan_default} values={PRESENTATION_OPTIONS.planDetails} disabled={disabled} onChange={(value) => update("goal_execution", "plan_default", value)} />
          <SelectField label="时间线密度" testId="presentation-timeline-density" value={profile.goal_execution?.timeline_density} values={PRESENTATION_OPTIONS.timelineDensities} disabled={disabled} onChange={(value) => update("goal_execution", "timeline_density", value)} />
        </div>
      </section>
    </div>
  );
}
