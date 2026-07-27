import { ShieldAlert } from "lucide-react";
import { StatusBadge } from "@teamora/ui";

export default async function PlatformPage({
  params,
}: {
  params: Promise<{ path?: string[] }>;
}) {
  const { path = [] } = await params;
  return (
    <div className="coming-later">
      <div>
        <StatusBadge tone="warning">Backend authorization pending</StatusBadge>
        <ShieldAlert aria-hidden="true" size={36} />
        <h1>Platform administration</h1>
        <p>
          The reserved route{" "}
          <code>/platform{path.length ? `/${path.join("/")}` : ""}</code> is
          unavailable. Tenant roles cannot access platform operations, and no
          platform action is simulated.
        </p>
      </div>
    </div>
  );
}
