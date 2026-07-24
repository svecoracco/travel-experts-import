"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import { UploadDialog } from "@/components/upload-dialog";
import type { Company, ImportJob } from "@/types";

const PAGE_SIZE = 20;

export default function ImportsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [page, setPage] = useState(1);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Column filters
  const [filterId, setFilterId] = useState("");
  const [filterDateFrom, setFilterDateFrom] = useState("");
  const [filterDateTo, setFilterDateTo] = useState("");
  const [filterType, setFilterType] = useState("");
  const [filterCompany, setFilterCompany] = useState("");
  const [filterFile, setFilterFile] = useState("");
  const [filterUser, setFilterUser] = useState("");
  const [filterStatus, setFilterStatus] = useState("");

  const companyMap = useMemo(() => {
    const map: Record<number, string> = {};
    for (const c of companies) map[c.company_id] = c.name;
    return map;
  }, [companies]);

  const fetchJobs = useCallback(async () => {
    try {
      const data = await apiFetch<{ jobs: ImportJob[] }>("/api/imports");
      setJobs(data.jobs);
    } catch {
      // Silently fail on fetch
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
    apiFetch<{ companies: Company[] }>("/api/config/companies").then((data) => {
      setCompanies(data.companies);
    }).catch(() => {});
  }, [fetchJobs]);

  // Auto-refresh while any job is running
  const hasRunning = jobs.some((j) => j.status === "running");
  useEffect(() => {
    if (!hasRunning) return;
    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, [hasRunning, fetchJobs]);

  const handleCloseUpload = useCallback(() => {
    setShowUpload(false);
    fetchJobs();
  }, [fetchJobs]);

  const handleDelete = useCallback(async () => {
    if (confirmDeleteId === null) return;
    setIsDeleting(true);
    try {
      await apiFetch(`/api/imports/${confirmDeleteId}`, { method: "DELETE" });
      setJobs((prev) => prev.filter((j) => j.id !== confirmDeleteId));
      setConfirmDeleteId(null);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setIsDeleting(false);
    }
  }, [confirmDeleteId]);

  const formatDate = useCallback((iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }, []);

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      if (filterId && !String(job.id).includes(filterId)) return false;
      const jobDate = job.created_at.slice(0, 10);
      if (filterDateFrom && jobDate < filterDateFrom) return false;
      if (filterDateTo && jobDate > filterDateTo) return false;
      if (filterType && job.plugin_name !== filterType) return false;
      if (filterCompany && String(job.company_id) !== filterCompany) return false;
      if (filterFile && !job.file_name.toLowerCase().includes(filterFile.toLowerCase())) return false;
      if (filterUser && (job.creator_name ?? "") !== filterUser) return false;
      if (filterStatus && job.status !== filterStatus) return false;
      return true;
    });
  }, [jobs, filterId, filterDateFrom, filterDateTo, filterType, filterCompany, filterFile, filterUser, filterStatus]);

  const uniqueTypes = useMemo(() => [...new Set(jobs.map((j) => j.plugin_name))].sort(), [jobs]);
  const uniqueUsers = useMemo(() => [...new Set(jobs.map((j) => j.creator_name).filter(Boolean) as string[])].sort(), [jobs]);

  const totalPages = Math.max(1, Math.ceil(filteredJobs.length / PAGE_SIZE));
  const paginatedJobs = filteredJobs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // Reset to page 1 when filtered list changes
  useEffect(() => {
    setPage(1);
  }, [filterId, filterDateFrom, filterDateTo, filterType, filterCompany, filterFile, filterUser, filterStatus]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [totalPages, page]);

  const confirmJob = jobs.find((j) => j.id === confirmDeleteId);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-night-blue">Imports</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            View and manage import jobs
          </p>
        </div>
        <button
          onClick={() => setShowUpload(true)}
          className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90"
        >
          New Import
        </button>
      </div>

      <div className="overflow-hidden rounded-lg border border-border/80 bg-white">
        <div className="border-b border-border/70 px-6 py-3">
          <div className="grid grid-cols-[3rem_10rem_7rem_10rem_minmax(0,1fr)_7rem_9rem_5rem] gap-x-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <span>ID</span>
            <span>Date</span>
            <span>Type</span>
            <span>Company</span>
            <span>File</span>
            <span>User</span>
            <span>Status</span>
            <span>Actions</span>
          </div>
          <div className="mt-2 grid grid-cols-[3rem_10rem_7rem_10rem_minmax(0,1fr)_7rem_9rem_5rem] gap-x-4">
            <input
              value={filterId}
              onChange={(e) => setFilterId(e.target.value)}
              placeholder="#"
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue placeholder:text-muted-foreground/50"
            />
            <div className="flex flex-col gap-1">
              <input
                type="date"
                value={filterDateFrom}
                onChange={(e) => setFilterDateFrom(e.target.value)}
                title="From"
                className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
              />
              <input
                type="date"
                value={filterDateTo}
                onChange={(e) => setFilterDateTo(e.target.value)}
                title="To"
                className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
              />
            </div>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
            >
              <option value="">All</option>
              {uniqueTypes.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <select
              value={filterCompany}
              onChange={(e) => setFilterCompany(e.target.value)}
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
            >
              <option value="">All</option>
              {companies.map((c) => (
                <option key={c.company_id} value={String(c.company_id)}>{c.name}</option>
              ))}
            </select>
            <input
              value={filterFile}
              onChange={(e) => setFilterFile(e.target.value)}
              placeholder="Filter..."
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue placeholder:text-muted-foreground/50"
            />
            <select
              value={filterUser}
              onChange={(e) => setFilterUser(e.target.value)}
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
            >
              <option value="">All</option>
              {uniqueUsers.map((u) => (
                <option key={u} value={u}>{u}</option>
              ))}
            </select>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="w-full rounded-md border border-border px-2 py-1 text-xs text-night-blue"
            >
              <option value="">All</option>
              <option value="pending">Pending</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
            </select>
            <span />
          </div>
        </div>

        {isLoading ? (
          <div className="px-6 py-12 text-center text-sm text-muted-foreground">
            Loading...
          </div>
        ) : jobs.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-muted-foreground">
            No imports yet. Click &quot;New Import&quot; to get started.
          </div>
        ) : filteredJobs.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-muted-foreground">
            No imports match the current filters.
          </div>
        ) : (
          <div>
            {paginatedJobs.map((job) => (
              <div
                key={job.id}
                onClick={() => router.push(`/imports/${job.id}`)}
                className="grid cursor-pointer grid-cols-[3rem_10rem_7rem_10rem_minmax(0,1fr)_7rem_9rem_5rem] gap-x-4 items-center border-b border-border/50 px-6 py-3 text-sm last:border-b-0 hover:bg-sand/20"
              >
                <span className="font-mono text-xs text-muted-foreground">
                  #{job.id}
                </span>
                <span className="text-muted-foreground">
                  {formatDate(job.created_at)}
                </span>
                <span className="capitalize text-night-blue">
                  {job.plugin_name}
                </span>
                <span className="text-night-blue">
                  {companyMap[job.company_id] ?? `Company ${job.company_id}`}
                </span>
                <span className="truncate text-night-blue" title={job.file_name}>
                  {job.file_name}
                </span>
                <span className="truncate text-night-blue" title={job.creator_name ?? ""}>
                  {job.creator_name ?? "\u2014"}
                </span>
                <span className="flex items-center gap-1.5">
                  <StatusBadge status={job.status} />
                  {job.dry_run && (
                    <span className="text-xs text-muted-foreground">(dry)</span>
                  )}
                </span>
                <span className="flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => router.push(`/imports/${job.id}`)}
                    className="text-xs font-medium text-horizon-blue hover:underline"
                  >
                    View
                  </button>
                  <button
                    onClick={() => setConfirmDeleteId(job.id)}
                    disabled={job.status === "running"}
                    className="text-xs font-medium text-red-500 hover:underline disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    Delete
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Pagination footer */}
        {filteredJobs.length > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-border/70 px-6 py-3">
            <span className="text-xs text-muted-foreground">
              {filteredJobs.length}{filteredJobs.length !== jobs.length ? ` of ${jobs.length}` : ""} imports — page {page} of {totalPages}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="rounded border border-border px-3 py-1 text-xs text-night-blue hover:bg-sand/50 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="rounded border border-border px-3 py-1 text-xs text-night-blue hover:bg-sand/50 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Delete confirmation modal */}
      {confirmDeleteId !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-night-blue/40"
            onClick={() => !isDeleting && setConfirmDeleteId(null)}
          />
          <div className="relative z-10 w-full max-w-sm rounded-lg border border-border bg-white p-6 shadow-lg">
            <h2 className="text-base font-semibold text-night-blue">Delete import?</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              This will permanently delete import{" "}
              <span className="font-medium text-night-blue">#{confirmDeleteId}</span>
              {confirmJob && (
                <> &mdash; <span className="font-medium">{confirmJob.file_name}</span></>
              )}
              . This action cannot be undone.
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => setConfirmDeleteId(null)}
                disabled={isDeleting}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {isDeleting ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      <UploadDialog open={showUpload} onClose={handleCloseUpload} />
    </div>
  );
}
