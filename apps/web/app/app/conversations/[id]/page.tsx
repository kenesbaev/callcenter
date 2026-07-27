import { ConversationDetail } from "@/components/conversation-detail";

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ConversationDetail callId={id} />;
}
