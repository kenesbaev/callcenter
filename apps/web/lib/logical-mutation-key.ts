import { idempotencyKey } from "@/lib/api";

type StoredKey = {
  fingerprint: string;
  value: string;
};

export type LogicalMutationKeyStore = {
  get: (prefix: string, fingerprint?: string) => string;
  reset: (prefix: string) => void;
  resetAll: () => void;
};

export function createLogicalMutationKeyStore(): LogicalMutationKeyStore {
  const keys = new Map<string, StoredKey>();

  return {
    get(prefix, fingerprint = "") {
      const stored = keys.get(prefix);
      if (stored?.fingerprint === fingerprint) return stored.value;

      const value = idempotencyKey(prefix);
      keys.set(prefix, { fingerprint, value });
      return value;
    },
    reset(prefix) {
      keys.delete(prefix);
    },
    resetAll() {
      keys.clear();
    },
  };
}
