import { describe, expect, it } from "vitest";

import en from "./en.json";
import { DEFAULT_LOCALE } from "./index";
import ptBR from "./pt-BR.json";

const flatten = (obj: unknown, prefix = ""): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, key));
    else out[key] = String(v);
  }
  return out;
};

describe("i18n", () => {
  it("pt-BR and en have identical key sets", () => {
    expect(Object.keys(flatten(ptBR)).sort()).toEqual(Object.keys(flatten(en)).sort());
  });

  it("defaults to pt-BR", () => {
    expect(DEFAULT_LOCALE).toBe("pt-BR");
  });

  it("has no empty values in either locale", () => {
    for (const [locale, dict] of [
      ["pt-BR", ptBR],
      ["en", en],
    ] as const)
      for (const [k, v] of Object.entries(flatten(dict)))
        expect(v.trim(), `${locale}:${k}`).not.toBe("");
  });
});
