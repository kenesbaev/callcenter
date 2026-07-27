import { ComingLater } from "@/components/coming-later";

export default async function ReservedWorkspacePage({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = await params;
  return <ComingLater path={path.join("/")} />;
}
