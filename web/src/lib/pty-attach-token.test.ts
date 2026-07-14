import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ptyAttachStorageKey, ptyAttachToken } from "./pty-attach-token";

const values = new Map<string, string>();

beforeEach(() => {
  values.clear();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("ptyAttachStorageKey", () => {
  it("isolates current and named-profile PTYs", () => {
    const current = ptyAttachStorageKey("");
    const victor = ptyAttachStorageKey("victor");
    const sentinel = ptyAttachStorageKey("sentinel");

    expect(new Set([current, victor, sentinel]).size).toBe(3);
    expect(victor).toContain("victor");
  });
});

describe("ptyAttachToken", () => {
  it("is stable within a profile and never reused across profiles", () => {
    const current = ptyAttachToken("");
    const victor = ptyAttachToken("victor");

    expect(ptyAttachToken("")).toBe(current);
    expect(ptyAttachToken("victor")).toBe(victor);
    expect(victor).not.toBe(current);
  });
});
