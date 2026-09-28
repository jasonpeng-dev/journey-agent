import { describe, expect, it } from "vitest";

import { platformEnumLabel } from "../../ui";
import { parseTypedScalarInput, typedScalarDisplay, typedScalarToken } from "./typed-values";

describe("typed authored value presentation", () => {
  it("always presents Boolean values as 是/否 while preserving Boolean wire values", () => {
    expect(typedScalarDisplay(true, { value_type: "BOOLEAN" })).toBe("是");
    expect(typedScalarDisplay(false, { value_type: "BOOLEAN" })).toBe("否");
    expect(parseTypedScalarInput("true", "BOOLEAN")).toBe(true);
    expect(parseTypedScalarInput("false", "BOOLEAN")).toBe(false);
  });

  it("uses labels authored for the exact binary enum values and keeps their tokens", () => {
    const domain = {
      value_type: "ENUM",
      allowed_values: ["AVAILABLE", "UNAVAILABLE"],
      value_labels: [
        { value: "AVAILABLE", label: "已供电" },
        { value: "UNAVAILABLE", label: "未供电" },
      ],
    };
    expect(typedScalarDisplay("AVAILABLE", domain)).toBe("已供电");
    expect(typedScalarDisplay("UNAVAILABLE", domain)).toBe("未供电");
    expect(parseTypedScalarInput(typedScalarToken("AVAILABLE"), "ENUM", domain.allowed_values)).toBe("AVAILABLE");
    expect(parseTypedScalarInput(typedScalarToken("UNAVAILABLE"), "ENUM", domain.allowed_values)).toBe("UNAVAILABLE");
  });

  it("uses 是/否 only for the exact unlabeled AVAILABLE/UNAVAILABLE enum contract", () => {
    const domain = { value_type: "ENUM", allowed_values: ["AVAILABLE", "UNAVAILABLE"] };
    expect(typedScalarDisplay("AVAILABLE", domain)).toBe("是");
    expect(typedScalarDisplay("UNAVAILABLE", domain)).toBe("否");
    expect(parseTypedScalarInput(typedScalarToken("AVAILABLE"), "ENUM", domain.allowed_values)).toBe("AVAILABLE");
  });

  it("preserves arbitrary authored enum tokens and platform knowledge semantics", () => {
    const domain = { value_type: "ENUM", allowed_values: ["RED_ALERT", "BLUE_ALERT"] };
    expect(typedScalarDisplay("RED_ALERT", domain)).toBe("RED_ALERT");
    expect(typedScalarDisplay("BLUE_ALERT", domain)).toBe("BLUE_ALERT");
    expect(platformEnumLabel("node_knowledge", "KNOWN")).toBe("已知");
    expect(platformEnumLabel("node_knowledge", "HIDDEN")).toBe("隐藏");
  });

  it("does not infer an authored value domain from token spelling", () => {
    expect(typedScalarDisplay("AVAILABLE", { value_type: "ENUM", allowed_values: ["AVAILABLE", "LOCKED"] })).toBe("AVAILABLE");
    expect(typedScalarDisplay("AVAILABLE", { value_type: "STRING" })).toBe("AVAILABLE");
  });
});
