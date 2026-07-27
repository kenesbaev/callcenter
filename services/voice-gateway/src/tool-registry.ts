import type { RealtimeToolDefinition, ToolName } from "@teamora/contracts";

const reasonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["reason"],
  properties: { reason: { type: "string", minLength: 3, maxLength: 500 } },
} as const;

export const toolRegistry: Record<ToolName, RealtimeToolDefinition> = {
  search_knowledge: {
    name: "search_knowledge",
    description:
      "Search verified tenant knowledge. Never infer facts not returned.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["query"],
      properties: { query: { type: "string", minLength: 2, maxLength: 1000 } },
    },
    timeoutMs: 3000,
  },
  find_customer: {
    name: "find_customer",
    description:
      "Find a customer using an authorized tenant-scoped identifier.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["phone"],
      properties: {
        phone: { type: "string", pattern: "^\\+[1-9][0-9]{7,14}$" },
      },
    },
    timeoutMs: 3000,
  },
  create_customer: {
    name: "create_customer",
    description: "Create a tenant-scoped customer after server validation.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["name", "phone"],
      properties: {
        name: { type: "string", minLength: 2, maxLength: 160 },
        phone: { type: "string", pattern: "^\\+[1-9][0-9]{7,14}$" },
      },
    },
    timeoutMs: 5000,
  },
  create_lead: {
    name: "create_lead",
    description: "Create a lead through a configured CRM connector.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["subject"],
      properties: {
        subject: { type: "string", minLength: 2, maxLength: 240 },
        note: { type: "string", maxLength: 2000 },
      },
    },
    timeoutMs: 8000,
  },
  create_support_ticket: {
    name: "create_support_ticket",
    description: "Create a support ticket through a configured connector.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["subject", "description"],
      properties: {
        subject: { type: "string", minLength: 2, maxLength: 240 },
        description: { type: "string", minLength: 2, maxLength: 4000 },
      },
    },
    timeoutMs: 8000,
  },
  get_order_status: {
    name: "get_order_status",
    description:
      "Read order status; never report payment without returned evidence.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["order_reference"],
      properties: {
        order_reference: { type: "string", minLength: 3, maxLength: 120 },
      },
    },
    timeoutMs: 5000,
  },
  book_appointment: {
    name: "book_appointment",
    description:
      "Request an appointment after availability is server-validated.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["service", "starts_at"],
      properties: {
        service: { type: "string", minLength: 2, maxLength: 160 },
        starts_at: { type: "string", minLength: 20, maxLength: 40 },
      },
    },
    timeoutMs: 8000,
  },
  reschedule_appointment: {
    name: "reschedule_appointment",
    description: "Reschedule an appointment after server authorization.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["appointment_id", "starts_at"],
      properties: {
        appointment_id: { type: "string", minLength: 1 },
        starts_at: { type: "string", minLength: 20, maxLength: 40 },
      },
    },
    timeoutMs: 8000,
  },
  cancel_appointment: {
    name: "cancel_appointment",
    description: "Cancel an appointment only after server policy checks.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["appointment_id", "reason"],
      properties: {
        appointment_id: { type: "string", minLength: 1 },
        reason: { type: "string", minLength: 2, maxLength: 500 },
      },
    },
    timeoutMs: 8000,
  },
  send_confirmation: {
    name: "send_confirmation",
    description:
      "Send a non-sensitive confirmation through an enabled notification provider.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["template", "recipient"],
      properties: {
        template: { type: "string", minLength: 1, maxLength: 120 },
        recipient: { type: "string", minLength: 3, maxLength: 320 },
      },
    },
    timeoutMs: 8000,
  },
  request_human_operator: {
    name: "request_human_operator",
    description: "Request human assistance and record the reason.",
    inputSchema: reasonSchema,
    timeoutMs: 3000,
  },
  transfer_call: {
    name: "transfer_call",
    description: "Transfer to an authorized queue after server validation.",
    inputSchema: {
      type: "object",
      additionalProperties: false,
      required: ["queue", "reason"],
      properties: {
        queue: { type: "string", minLength: 1, maxLength: 120 },
        reason: { type: "string", minLength: 3, maxLength: 500 },
      },
    },
    timeoutMs: 5000,
  },
  end_call: {
    name: "end_call",
    description: "End the active call with a safe reason.",
    inputSchema: reasonSchema,
    timeoutMs: 3000,
  },
};

export function definitionsFor(names: ToolName[]): RealtimeToolDefinition[] {
  return names.map((name) => toolRegistry[name]);
}
