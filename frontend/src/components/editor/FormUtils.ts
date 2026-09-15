export function fieldId(path: string): string {
  return `editor-field-${path.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
}
