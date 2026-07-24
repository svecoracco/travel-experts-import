import { cn } from "@/lib/utils";

const statusStyles: Record<string, string> = {
  pending: "bg-sand text-ground",
  running: "bg-horizon-blue/15 text-horizon-blue",
  completed: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize",
        statusStyles[status] || "bg-sand text-ground"
      )}
    >
      {status}
    </span>
  );
}
