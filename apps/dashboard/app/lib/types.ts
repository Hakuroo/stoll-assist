export type ConversationState = "AUTOMATED" | "HUMAN_REQUIRED" | "HUMAN_ACTIVE" | "CLOSED";

export type ConversationSummary = {
  conversation_id: string;
  display_name: string | null;
  whatsapp_user_id: string;
  phone_e164: string | null;
  state: ConversationState;
  assigned_operator: string | null;
  last_state_reason: string | null;
  last_message_at: string | null;
  last_message_body: string | null;
  last_message_direction: string | null;
  last_message_type: string | null;
  active_handoff_status: string | null;
  requires_human: boolean;
  created_at: string;
};

export type ConversationMessage = {
  message_id: string;
  direction: "INBOUND" | "OUTBOUND";
  message_type: string;
  body_text: string | null;
  created_at: string;
};

export type ResponsePlan = {
  plan_id: string;
  message_id: string;
  decision: "ANSWER" | "ASK" | "HANDOFF" | "IGNORE";
  reason_code: string;
  risk_level: string;
  policy_rule_key: string | null;
  knowledge_keys: string[];
  allowed_claims: string[];
  forbidden_claims: string[];
  reply_goal: string;
  draft_reply: string | null;
  planner_version: string;
  created_at: string;
  updated_at: string;
};

export type Verification = {
  verification_id: string;
  plan_id: string;
  message_id: string;
  status: "APPROVED" | "REJECTED" | "SKIPPED";
  reason_code: string;
  checks: Record<string, unknown>;
  unsupported_claims: string[];
  verifier_version: string;
  created_at: string;
  updated_at: string;
};

export type Handoff = {
  handoff_id: string;
  reason_code: string;
  summary: string | null;
  status: "OPEN" | "TAKEN" | "RESOLVED";
  requested_by: string | null;
  taken_by: string | null;
  resolved_by: string | null;
  resolution_note: string | null;
  created_at: string;
  taken_at: string | null;
  resolved_at: string | null;
};

export type ConversationDetail = {
  conversation: ConversationSummary;
  messages: ConversationMessage[];
  response_plans: ResponsePlan[];
  verifications: Verification[];
  handoffs: Handoff[];
};

export type KnowledgeItem = {
  item_id: string;
  external_key: string;
  title: string;
  content: string;
  status: "draft" | "published" | "archived";
  risk_class: string;
  version: number;
  source_path: string | null;
  allowed_claims: string[];
  forbidden_claims: string[];
  approved_by: string | null;
  approved_at: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
};

export type KnowledgeSource = {
  external_key: string;
  title: string;
  version: number;
  status: string;
};

export type OutboxReviewItem = {
  outbound_id: string;
  conversation_id: string;
  in_reply_to_message_id: string;
  plan_id: string;
  verification_id: string;
  recipient: string;
  display_name: string | null;
  body_text: string;
  status: "PENDING_REVIEW" | "APPROVED" | "REJECTED" | "QUEUED" | "SENT" | "FAILED" | "CANCELLED";
  requires_review: boolean;
  provider_message_id: string | null;
  send_attempt_count: number;
  created_at: string;
  updated_at: string;
  customer_message_text: string | null;
  customer_message_type: string;
  plan: ResponsePlan;
  verification: Verification;
  knowledge_sources: KnowledgeSource[];
};

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };
