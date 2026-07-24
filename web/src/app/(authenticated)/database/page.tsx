"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

interface TableStatus {
  table_name: string;
  display_name: string;
  records: number;
  last_updated: string | null;
  last_modified: string | null;
}

interface DatabaseStatus {
  tables: TableStatus[];
  fetched_at: string;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRecords(n: number): string {
  return n.toLocaleString("nl-BE");
}

type Freshness = "fresh" | "aging" | "stale" | "unknown";

function getFreshness(iso: string | null): Freshness {
  if (!iso) return "unknown";
  const diffDays = (Date.now() - new Date(iso).getTime()) / (1000 * 60 * 60 * 24);
  if (diffDays <= 2) return "fresh";
  if (diffDays <= 5) return "aging";
  return "stale";
}

const freshnessConfig: Record<Freshness, { dot: string; badge: string; label: string }> = {
  fresh:   { dot: "bg-green-500",  badge: "bg-green-50 text-green-700 border-green-200",  label: "Up to date" },
  aging:   { dot: "bg-amber-400",  badge: "bg-amber-50 text-amber-700 border-amber-200",  label: "Aging" },
  stale:   { dot: "bg-red-400",    badge: "bg-red-50 text-red-700 border-red-200",        label: "Stale" },
  unknown: { dot: "bg-gray-300",   badge: "bg-gray-50 text-gray-500 border-gray-200",     label: "No data" },
};

function FreshnessBadge({ iso }: { iso: string | null }) {
  const f = getFreshness(iso);
  const cfg = freshnessConfig[f];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${cfg.badge}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}

function SkeletonRow() {
  return (
    <tr className="border-b border-border">
      {[1, 2, 3, 4, 5].map((i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-4 rounded bg-sand animate-pulse" style={{ width: `${60 + i * 8}%` }} />
        </td>
      ))}
    </tr>
  );
}

export default function DatabasePage() {
  const [status, setStatus] = useState<DatabaseStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [dryRunDate, setDryRunDate] = useState("");
  const [dryRunConfirm, setDryRunConfirm] = useState(false);
  const [dryRunDeleting, setDryRunDeleting] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<number | null>(null);
  const [dryRunError, setDryRunError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<DatabaseStatus>("/api/database/status")
      .then((data) => setStatus(data))
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load database status"))
      .finally(() => setLoading(false));
  }, []);

  const handleDryRunDelete = useCallback(async () => {
    setDryRunDeleting(true);
    setDryRunError(null);
    setDryRunResult(null);
    try {
      const data = await apiFetch<{ deleted: number }>("/api/database/dry-runs", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ before_date: dryRunDate }),
      });
      setDryRunResult(data.deleted);
      setDryRunConfirm(false);
    } catch (err) {
      setDryRunError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDryRunDeleting(false);
    }
  }, [dryRunDate]);

  return (
    <div className="p-6 max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-night-blue">Database Status</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Record counts and freshness of the BTS SQL Server lookup tables.
        </p>
      </div>

      {error && (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="font-medium">Connection error:</span> {error}
        </div>
      )}

      <div className="rounded-lg border border-border bg-white overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-sand/30">
              <th className="px-4 py-3 text-left font-medium text-night-blue">Table</th>
              <th className="px-4 py-3 text-right font-medium text-night-blue">Records</th>
              <th className="px-4 py-3 text-left font-medium text-night-blue">Last Record Update</th>
              <th className="px-4 py-3 text-left font-medium text-night-blue">Status</th>
              <th className="px-4 py-3 text-left font-medium text-night-blue">Table Modified</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <>
                <SkeletonRow />
                <SkeletonRow />
                <SkeletonRow />
                <SkeletonRow />
              </>
            ) : status ? (
              status.tables.map((t) => (
                <tr key={t.table_name} className="border-b border-border last:border-0 hover:bg-sand/10 transition-colors">
                  <td className="px-4 py-3">
                    <span className="font-medium text-night-blue">{t.display_name}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{t.table_name}</span>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-sm tabular-nums">
                    {formatRecords(t.records)}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDate(t.last_updated)}
                  </td>
                  <td className="px-4 py-3">
                    <FreshnessBadge iso={t.last_updated} />
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {formatDate(t.last_modified)}
                  </td>
                </tr>
              ))
            ) : null}
          </tbody>
        </table>
      </div>

      {status && (
        <p className="mt-3 text-xs text-muted-foreground">
          Fetched at {formatDate(status.fetched_at)}. Reload the page to refresh.
        </p>
      )}
        <p className="mt-3 text-xs text-muted-foreground">
          Every night at 03:00 the tables are synced with the Travelmind data.
        </p>

      {/* ── Clean Up Dry Runs ── */}
      <div className="mt-10">
        <h2 className="text-lg font-semibold text-night-blue">Clean Up Dry Runs</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Delete all dry-run import jobs created up to and including the selected date.
        </p>

        <div className="mt-4 flex items-end gap-3">
          <div>
            <label htmlFor="dry-run-date" className="mb-1 block text-sm font-medium text-night-blue">
              Delete up to
            </label>
            <input
              id="dry-run-date"
              type="date"
              value={dryRunDate}
              onChange={(e) => { setDryRunDate(e.target.value); setDryRunResult(null); setDryRunError(null); }}
              className="w-48 rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
            />
          </div>
          <button
            disabled={!dryRunDate || dryRunDeleting}
            onClick={() => setDryRunConfirm(true)}
            className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            Delete dry runs
          </button>
        </div>

        {dryRunResult !== null && (
          <p className="mt-3 text-sm text-green-700">
            {dryRunResult === 0
              ? "No dry-run imports found for the selected period."
              : `Successfully deleted ${dryRunResult} dry-run import${dryRunResult === 1 ? "" : "s"}.`}
          </p>
        )}
        {dryRunError && (
          <p className="mt-3 text-sm text-red-700">{dryRunError}</p>
        )}
      </div>

      {/* Confirmation modal */}
      {dryRunConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-lg border border-border bg-white p-6 shadow-lg">
            <h3 className="text-base font-semibold text-night-blue">Confirm deletion</h3>
            <p className="mt-2 text-sm text-muted-foreground">
              This will permanently delete all dry-run imports created on or before{" "}
              <span className="font-medium text-night-blue">
                {new Date(dryRunDate + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })}
              </span>
              . This action cannot be undone.
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setDryRunConfirm(false)}
                disabled={dryRunDeleting}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/30 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDryRunDelete}
                disabled={dryRunDeleting}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {dryRunDeleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
