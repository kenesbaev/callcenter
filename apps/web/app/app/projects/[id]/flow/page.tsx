import { CallFlowEditor } from "@/components/call-flow-editor";

export default async function ProjectCallFlowPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <CallFlowEditor projectId={id} />;
}
