"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, getAccessToken } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import type { Company, ImportJob } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

interface ResultSummary {
  created?: number;
  skipped?: number;
  needs_review?: number;
  errors?: number;
  items_processed?: number;
  log_messages?: string[];
  error?: string;
  traceback?: string;
  validation_errors?: string[];
}

interface ProgressData {
  phase: string;
  current: number;
  total: number;
  message: string;
  status?: string;
  result?: ResultSummary;
}

const PHASE_LABELS: Record<string, string> = {
  waiting: "Waiting...",
  starting: "Initialising...",
  validating: "Validating file...",
  parsing: "Parsing file...",
  connecting: "Connecting to Odoo...",
  building: "Building moves...",
  executing: "Creating moves in Odoo...",
  posting: "Posting moves...",
  reconciling: "Reconciling...",
  done: "Complete",
  failed: "Failed",
};

export default function ImportDetailPage() {
  const params = useParams();
  const router = useRouter();
  const jobId = params.id as string;

  const [job, setJob] = useState<ImportJob | null>(null);
  const [companyName, setCompanyName] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [progress, setProgress] = useState<ProgressData | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchJob = useCallback(async () => {
    try {
      const data = await apiFetch<{ job: ImportJob }>(`/api/imports/${jobId}`);
      setJob(data.job);
      // Fetch company name
      try {
        const companies = await apiFetch<{ companies: Company[] }>("/api/config/companies");
        const match = companies.companies.find((c) => c.company_id === data.job.company_id);
        if (match) setCompanyName(match.name);
      } catch {
        // Company name lookup is non-critical
      }
    } catch {
      // Job not found
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    fetchJob();
  }, [fetchJob]);

  const jobStatus = job?.status;

  // SSE: connect when job is running
  useEffect(() => {
    if (jobStatus !== "running") return;

    const token = getAccessToken();
    const url = `${API_URL}/api/imports/${jobId}/progress?token=${token}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data: ProgressData = JSON.parse(event.data);
        setProgress(data);

        if (data.phase === "done" || data.phase === "failed") {
          es.close();
          eventSourceRef.current = null;
          // Reload job to get final state from DB
          fetchJob();
        }
      } catch {
        // Ignore parse errors
      }
    };

    es.onerror = () => {
      // SSE connection lost — fallback to polling
      es.close();
      eventSourceRef.current = null;
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [jobStatus, jobId, fetchJob]);

  // Fallback polling when job is running and SSE is not connected
  useEffect(() => {
    if (jobStatus !== "running") {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      return;
    }

    pollIntervalRef.current = setInterval(async () => {
      // Only poll if SSE is not connected
      if (eventSourceRef.current) return;
      try {
        const data = await apiFetch<{ job: ImportJob }>(`/api/imports/${jobId}`);
        setJob(data.job);
        if (data.job.status !== "running") {
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        }
      } catch {
        // Ignore polling errors
      }
    }, 5000);

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [jobStatus, jobId]);

  const result: ResultSummary | null = job?.result_summary
    ? (() => {
        try {
          return JSON.parse(job.result_summary);
        } catch {
          return null;
        }
      })()
    : null;

  const formatDate = (iso: string | null) => {
    if (!iso) return "\u2014";
    return new Date(iso).toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  const handleDownloadSkipReport = async () => {
    try {
      const token = getAccessToken();
      const res = await fetch(`${API_URL}/api/imports/${jobId}/skip-report`, {
        headers: { Authorization: `Bearer ${token}` },
        credentials: "include",
      });
      if (!res.ok) throw new Error("Download failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job?.file_name ?? "skip"}_skip_report.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      alert("Failed to download skip report.");
    }
  };

  // Progress bar percentage
  const progressPct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.current / progress.total) * 100))
      : 0;

  const phaseLabel = progress
    ? PHASE_LABELS[progress.phase] || progress.message || progress.phase
    : "";

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!job) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">Import job not found.</p>
        <button
          onClick={() => router.push("/imports")}
          className="text-sm font-medium text-horizon-blue hover:underline"
        >
          Back to imports
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.push("/imports")}
          className="text-sm text-muted-foreground hover:text-night-blue"
        >
          &larr; Back
        </button>
        <div>
          <h1 className="text-2xl font-semibold text-night-blue">
            Import #{job.id}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {job.file_name}
          </p>
        </div>
      </div>

      {/* Progress bar (visible while running) */}
      {job.status === "running" && (
        <div className="rounded-lg border border-horizon-blue/30 bg-horizon-blue/5 p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-horizon-blue border-t-transparent" />
              <p className="text-sm font-medium text-night-blue">{phaseLabel}</p>
            </div>
            {progress && progress.total > 0 && (
              <p className="text-sm text-muted-foreground">
                {progress.current} / {progress.total}
              </p>
            )}
          </div>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-sand/50">
            <div
              className="h-full rounded-full bg-horizon-blue transition-all duration-500 ease-out"
              style={{ width: `${progress && progress.total > 0 ? progressPct : 100}%` }}
            />
          </div>
          {progress && progress.message && progress.message !== phaseLabel && (
            <p className="mt-2 text-xs text-muted-foreground">{progress.message}</p>
          )}
        </div>
      )}

      {/* Job info card */}
      <div className="rounded-lg border border-border bg-white p-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Type
            </p>
            <p className="mt-1 text-sm font-medium capitalize text-night-blue">
              {job.plugin_name}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Company
            </p>
            <p className="mt-1 text-sm font-medium text-night-blue">
              {companyName ?? `Company ${job.company_id}`}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Status
            </p>
            <div className="mt-1 flex items-center gap-2">
              <StatusBadge status={job.status} />
              {job.dry_run && (
                <span className="text-xs text-muted-foreground">(dry run)</span>
              )}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              File
            </p>
            <p className="mt-1 truncate text-sm text-night-blue" title={job.file_name}>
              {job.file_name}
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Created
            </p>
            <p className="mt-1 text-sm text-night-blue">
              {formatDate(job.created_at)}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Started
            </p>
            <p className="mt-1 text-sm text-night-blue">
              {formatDate(job.started_at)}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Completed
            </p>
            <p className="mt-1 text-sm text-night-blue">
              {formatDate(job.completed_at)}
            </p>
          </div>
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              User
            </p>
            <p className="mt-1 text-sm text-night-blue">
              {job.creator_name ?? "\u2014"}
            </p>
          </div>
        </div>

        {job.skip_report_path && (
          <div className="mt-4">
            <button
              onClick={handleDownloadSkipReport}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50"
            >
              Download Import Report
            </button>
          </div>
        )}
      </div>

      {/* Result summary */}
      {result && !result.error && (
        <div className="rounded-lg border border-border bg-white p-6">
          <h2 className="text-sm font-semibold text-night-blue">Results</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-3 lg:grid-cols-5">
            <div className="rounded-md bg-emerald-50 p-4 text-center">
              <p className="text-2xl font-semibold text-emerald-700">
                {result.created ?? 0}
              </p>
              <p className="mt-1 text-xs text-emerald-600">Created</p>
            </div>
            <div className="rounded-md bg-sand/50 p-4 text-center">
              <p className="text-2xl font-semibold text-ground">
                {result.skipped ?? 0}
              </p>
              <p className="mt-1 text-xs text-ground">Skipped</p>
            </div>
            <div className="rounded-md bg-amber-50 p-4 text-center">
              <p className="text-2xl font-semibold text-amber-700">
                {result.needs_review ?? 0}
              </p>
              <p className="mt-1 text-xs text-amber-600">Needs Review</p>
            </div>
            <div className="rounded-md bg-red-50 p-4 text-center">
              <p className="text-2xl font-semibold text-red-700">
                {result.errors ?? 0}
              </p>
              <p className="mt-1 text-xs text-red-600">Errors</p>
            </div>
            <div className="rounded-md bg-horizon-blue/10 p-4 text-center">
              <p className="text-2xl font-semibold text-horizon-blue">
                {result.items_processed ?? 0}
              </p>
              <p className="mt-1 text-xs text-horizon-blue">Processed</p>
            </div>
          </div>
        </div>
      )}

      {/* Error display */}
      {result?.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-6">
          <h2 className="text-sm font-semibold text-red-700">Error</h2>
          <p className="mt-2 text-sm text-red-700">{result.error}</p>
          {result.validation_errors && result.validation_errors.length > 0 && (
            <ul className="mt-2 list-inside list-disc text-sm text-red-600">
              {result.validation_errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Log messages */}
      {result && !result.error && job.status === "completed" && (() => {
        const msgs = result.log_messages ?? [];
        const hasDetail = msgs.some(
          (m) => m.startsWith("Skipped") || m.startsWith("Create error") || m.startsWith("[dry-run]") || m.startsWith("Warning:")
        );
        const isClean = !hasDetail && result.errors === 0 && result.skipped === 0;
        return (
          <div className={`rounded-lg border p-6 ${isClean ? "border-emerald-200 bg-emerald-50" : "border-border bg-white"}`}>
            <h2 className={`text-sm font-semibold ${isClean ? "text-emerald-700" : "text-night-blue"}`}>Log</h2>
            {msgs.length === 0 ? (
              result.skipped! > 0 || result.errors! > 0 ? (
                <p className="mt-2 text-sm text-amber-600">
                  {[
                    result.skipped ? `${result.skipped} skipped` : "",
                    result.errors ? `${result.errors} error(s)` : "",
                  ].filter(Boolean).join(", ") + ". Re-run the import to see skip details."}
                </p>
              ) : (
                <p className="mt-2 text-sm text-emerald-600">All lines processed successfully.</p>
              )
            ) : (
              <div className="mt-3 max-h-64 overflow-y-auto rounded-md bg-card p-4">
                {msgs.map((msg, i) => {
                  const isSkip = msg.startsWith("Skipped");
                  const isError = msg.startsWith("Create error");
                  const isDry = msg.startsWith("[dry-run]");
                  const isWarning = msg.startsWith("Warning:");
                  const color = isError
                    ? "text-red-600"
                    : isSkip || isWarning
                    ? "text-amber-600"
                    : isDry
                    ? "text-horizon-blue"
                    : isClean
                    ? "text-emerald-700"
                    : "text-night-blue/80";
                  return (
                    <p key={i} className={`font-mono text-xs ${color}`}>
                      {msg}
                    </p>
                  );
                })}
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
