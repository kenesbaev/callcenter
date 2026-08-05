import { beforeEach, describe, expect, it, vi } from "vitest";
import { createLogicalMutationKeyStore } from "@/lib/logical-mutation-key";

const idempotencyKeyMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  idempotencyKey: idempotencyKeyMock,
}));

beforeEach(() => {
  let sequence = 0;
  idempotencyKeyMock.mockReset();
  idempotencyKeyMock.mockImplementation(
    (prefix: string) => `${prefix}-test-${++sequence}`,
  );
});

describe("logical mutation idempotency keys", () => {
  it("reuses a key for retries until success resets the operation", () => {
    const keys = createLogicalMutationKeyStore();

    expect(keys.get("job-retry", "job-1:3")).toBe("job-retry-test-1");
    expect(keys.get("job-retry", "job-1:3")).toBe("job-retry-test-1");
    expect(idempotencyKeyMock).toHaveBeenCalledTimes(1);

    keys.reset("job-retry");

    expect(keys.get("job-retry", "job-1:3")).toBe("job-retry-test-2");
  });

  it("starts a new logical operation when its request fingerprint changes", () => {
    const keys = createLogicalMutationKeyStore();

    expect(keys.get("retention-cancel", "candidate-1:2")).toBe(
      "retention-cancel-test-1",
    );
    expect(keys.get("retention-cancel", "candidate-2:1")).toBe(
      "retention-cancel-test-2",
    );
  });
});
