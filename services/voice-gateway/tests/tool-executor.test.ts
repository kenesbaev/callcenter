import { describe, expect, it, vi } from "vitest";
import { ToolExecutor } from "../src/tool-executor.js";

const context = {
  tenantId: "tenant-a",
  callId: "call-a",
  allowedTools: ["search_knowledge"] as const,
};

describe("ToolExecutor", () => {
  it("rejects tools outside the operator allowlist", async () => {
    const executor = new ToolExecutor(vi.fn());
    await expect(
      executor.execute(
        { ...context, allowedTools: [...context.allowedTools] },
        "transfer_call",
        { queue: "support", reason: "requested" },
        "idem-1",
      ),
    ).rejects.toMatchObject({ code: "tool_not_allowed" });
  });

  it("validates arguments before dispatch", async () => {
    const dispatch = vi.fn();
    const executor = new ToolExecutor(dispatch);
    await expect(
      executor.execute(
        { ...context, allowedTools: [...context.allowedTools] },
        "search_knowledge",
        { query: "" },
        "idem-2",
      ),
    ).rejects.toMatchObject({ code: "invalid_arguments" });
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("passes tenant, call and idempotency context to the dispatcher", async () => {
    const dispatch = vi.fn().mockResolvedValue({ answer: "verified" });
    const executor = new ToolExecutor(dispatch);
    await expect(
      executor.execute(
        { ...context, allowedTools: [...context.allowedTools] },
        "search_knowledge",
        { query: "opening hours" },
        "idem-3",
      ),
    ).resolves.toEqual({ answer: "verified" });
    expect(dispatch).toHaveBeenCalledWith(
      expect.objectContaining({
        context: expect.objectContaining({
          tenantId: "tenant-a",
          callId: "call-a",
        }),
        idempotencyKey: "idem-3",
      }),
    );
  });

  it("fails closed on timeout", async () => {
    vi.useFakeTimers();
    const executor = new ToolExecutor(
      ({ signal }) =>
        new Promise((_resolve, reject) =>
          signal.addEventListener("abort", () => reject(new Error("aborted"))),
        ),
    );
    const pending = executor.execute(
      { ...context, allowedTools: [...context.allowedTools] },
      "search_knowledge",
      { query: "opening hours" },
      "idem-4",
    );
    const assertion = expect(pending).rejects.toMatchObject({
      code: "timeout",
    });
    await vi.advanceTimersByTimeAsync(3001);
    await assertion;
    vi.useRealTimers();
  });
});
