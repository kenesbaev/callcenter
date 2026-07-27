// Ajv 6 exposes a CommonJS constructor; this import is intentionally isolated here.
// eslint-disable-next-line @typescript-eslint/no-require-imports
import Ajv = require("ajv");
import type { ToolName } from "@teamora/contracts";
import { toolNames } from "@teamora/contracts";
import { toolRegistry } from "./tool-registry.js";

const ajv = new Ajv({ allErrors: true });
const validators = new Map(
  toolNames.map((name) => [name, ajv.compile(toolRegistry[name].inputSchema)]),
);

export type ToolContext = {
  tenantId: string;
  callId: string;
  allowedTools: ToolName[];
};
export type ToolDispatcher = (request: {
  context: ToolContext;
  name: ToolName;
  arguments: unknown;
  idempotencyKey: string;
  signal: AbortSignal;
}) => Promise<unknown>;

export class ToolExecutionError extends Error {
  constructor(
    public readonly code:
      "tool_not_allowed" | "invalid_arguments" | "timeout" | "execution_failed",
    message: string,
  ) {
    super(message);
  }
}

export class ToolExecutor {
  constructor(private readonly dispatch: ToolDispatcher) {}

  async execute(
    context: ToolContext,
    name: string,
    args: unknown,
    idempotencyKey: string,
  ): Promise<unknown> {
    if (
      !toolNames.includes(name as ToolName) ||
      !context.allowedTools.includes(name as ToolName)
    )
      throw new ToolExecutionError(
        "tool_not_allowed",
        "The tool is not authorized for this operator",
      );
    const toolName = name as ToolName;
    const validate = validators.get(toolName);
    if (!validate?.(args))
      throw new ToolExecutionError(
        "invalid_arguments",
        "Tool arguments do not match the registered JSON Schema",
      );
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      toolRegistry[toolName].timeoutMs,
    );
    try {
      return await this.dispatch({
        context,
        name: toolName,
        arguments: args,
        idempotencyKey,
        signal: controller.signal,
      });
    } catch (error) {
      if (controller.signal.aborted)
        throw new ToolExecutionError("timeout", "The tool timed out safely");
      throw new ToolExecutionError(
        "execution_failed",
        error instanceof Error ? error.message : "The tool failed safely",
      );
    } finally {
      clearTimeout(timer);
    }
  }
}
