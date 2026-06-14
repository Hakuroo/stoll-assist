"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "./lib/api";

function operatorName(): string {
  return process.env.DASHBOARD_OPERATOR_NAME ?? "Operador local";
}

export async function takeConversation(conversationId: string): Promise<void> {
  await apiPost(`/operator/conversations/${conversationId}/take`, {
    operator_name: operatorName()
  });
  revalidatePath("/conversaciones");
  revalidatePath(`/conversaciones/${conversationId}`);
}

export async function returnConversation(conversationId: string): Promise<void> {
  await apiPost(`/operator/conversations/${conversationId}/return-to-automation`, {
    operator_name: operatorName()
  });
  revalidatePath("/conversaciones");
  revalidatePath(`/conversaciones/${conversationId}`);
}

export async function closeConversation(conversationId: string): Promise<void> {
  await apiPost(`/operator/conversations/${conversationId}/close`, {
    operator_name: operatorName()
  });
  revalidatePath("/conversaciones");
  revalidatePath(`/conversaciones/${conversationId}`);
}

export async function approveOutbound(outboundId: string): Promise<void> {
  await apiPost(`/operator/outbox/${outboundId}/approve`, {
    operator_name: operatorName()
  });
  revalidatePath("/respuestas");
}

export async function rejectOutbound(outboundId: string, formData: FormData): Promise<void> {
  const reason = String(formData.get("reason") ?? "").trim();
  await apiPost(`/operator/outbox/${outboundId}/reject`, {
    operator_name: operatorName(),
    reason: reason || "Rechazado desde el panel local"
  });
  revalidatePath("/respuestas");
}
