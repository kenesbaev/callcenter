import { CallResultsEditor } from "@/components/call-results-editor";

export default async function ProjectCallResultsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <CallResultsEditor projectId={id} />;
}
