import { AuthoringActionButton, FieldActionRow } from "./AuthoringActionButton";

export type ReferenceEvidence = {
  type: string;
  name: string;
  path: string;
  fieldLabel?: string;
  href: string | null;
  consumerKey?: string;
  stableKey?: string;
};

type ReferenceEvidenceGroup = {
  key: string;
  type: string;
  name: string;
  stableKey?: string;
  href: string | null;
  fieldLabels: string[];
  paths: string[];
};

function groupReferenceEvidence(references: ReferenceEvidence[]): ReferenceEvidenceGroup[] {
  const groups = new Map<string, ReferenceEvidenceGroup>();
  for (const reference of references) {
    const consumerKey = reference.consumerKey ?? reference.href ?? `${reference.type}:${reference.name}`;
    const key = `${reference.type}:${consumerKey}`;
    const group = groups.get(key) ?? {
      key,
      type: reference.type,
      name: reference.name,
      stableKey: reference.stableKey,
      href: reference.href,
      fieldLabels: [],
      paths: [],
    };
    if (reference.fieldLabel && !group.fieldLabels.includes(reference.fieldLabel)) group.fieldLabels.push(reference.fieldLabel);
    if (reference.path && reference.path !== "(object)" && !group.paths.includes(reference.path)) group.paths.push(reference.path);
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => a.type.localeCompare(b.type) || a.name.localeCompare(b.name));
}

export function ReferenceEvidenceList({ references, onNavigate }: { references: ReferenceEvidence[]; onNavigate?: () => void }) {
  const groups = groupReferenceEvidence(references);
  const byType = new Map<string, ReferenceEvidenceGroup[]>();
  for (const group of groups) {
    const current = byType.get(group.type) ?? [];
    current.push(group);
    byType.set(group.type, current);
  }
  return <div className="reference-evidence-list">
    {[...byType].map(([type, items]) => <section className="reference-evidence-group" key={type}>
      <h5>{type}<span>{items.length} 个对象</span></h5>
      <ul>{items.map((item) => <li key={item.key}>
        <div className="reference-evidence-copy"><strong>{item.name}</strong>{item.stableKey && <code>{item.stableKey}</code>}{item.fieldLabels.length > 0 && <small>{item.fieldLabels.join("、")}</small>}</div>
        {item.href && <FieldActionRow><AuthoringActionButton intent="navigate" to={item.href} onPress={onNavigate}>{`前往${item.name}`}</AuthoringActionButton></FieldActionRow>}
      </li>)}</ul>
    </section>)}
  </div>;
}
