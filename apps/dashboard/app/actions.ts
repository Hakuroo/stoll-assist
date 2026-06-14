"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { apiPost, apiPostAndCopyCookies, loginRequest } from "./lib/api";

function operatorName(): string {
  return process.env.DASHBOARD_OPERATOR_NAME ?? "Operador local";
}

export async function loginAction(formData: FormData): Promise<void> {
  const result = await loginRequest({
    email: String(formData.get("email") ?? ""),
    password: String(formData.get("password") ?? ""),
    tenant_slug: process.env.DASHBOARD_TENANT_SLUG ?? undefined
  });
  if (!result.ok) {
    redirect("/login?error=1");
  }
  redirect("/conversaciones");
}

export async function logoutAction(): Promise<void> {
  await apiPostAndCopyCookies("/auth/logout", {});
  redirect("/login");
}

export async function logoutAllAction(): Promise<void> {
  await apiPostAndCopyCookies("/auth/logout-all", {});
  redirect("/login");
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
