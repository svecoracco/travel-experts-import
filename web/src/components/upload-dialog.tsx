"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, getAccessToken } from "@/lib/api";
import { FileDropzone } from "@/components/file-dropzone";
import type { Company, ImportJob, PluginMeta } from "@/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
}

export function UploadDialog({ open, onClose }: UploadDialogProps) {
  const router = useRouter();
  const [plugins, setPlugins] = useState<PluginMeta[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedCompany, setSelectedCompany] = useState("");
  const [selectedPlugin, setSelectedPlugin] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(false);
  const [accountingDate, setAccountingDate] = useState("");
  const [originalEntryRef, setOriginalEntryRef] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    apiFetch<{ plugins: PluginMeta[] }>("/api/imports/plugins").then((data) => {
      setPlugins(data.plugins);
    });
    apiFetch<{ companies: Company[] }>("/api/config/companies").then((data) => {
      setCompanies(data.companies);
      if (data.companies.length === 1)
        setSelectedCompany(String(data.companies[0].company_id));
    });
  }, [open]);

  // Filter plugins to only those configured for the selected company
  const availablePlugins = useMemo(() => {
    if (!selectedCompany) return [];
    const company = companies.find(
      (c) => String(c.company_id) === selectedCompany
    );
    if (!company?.scripts?.length) return plugins;
    return plugins.filter((p) => company.scripts!.includes(p.name));
  }, [selectedCompany, companies, plugins]);

  const currentPlugin = plugins.find((p) => p.name === selectedPlugin);

  // Reset plugin + file when company changes
  const handleCompanyChange = useCallback((value: string) => {
    setSelectedCompany(value);
    setSelectedPlugin("");
    setFile(null);
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!selectedPlugin || !selectedCompany || !file) return;
    setIsSubmitting(true);
    setError("");

    try {
      // 1. Upload file
      const formData = new FormData();
      formData.append("file", file);
      formData.append("plugin", selectedPlugin);
      formData.append("company_id", selectedCompany);
      formData.append("dry_run", String(dryRun));
      if (accountingDate) formData.append("accounting_date", accountingDate);
      if (originalEntryRef) formData.append("original_entry_ref", originalEntryRef);

      const uploadRes = await fetch(`${API_URL}/api/imports/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${getAccessToken()}` },
        credentials: "include",
        body: formData,
      });

      if (!uploadRes.ok) {
        const body = await uploadRes.json().catch(() => ({}));
        throw new Error(body.error || "Upload failed");
      }

      const { job } = (await uploadRes.json()) as { job: ImportJob };

      // 2. Run import
      await apiFetch(`/api/imports/${job.id}/run`, { method: "POST" });

      // 3. Navigate to detail
      onClose();
      router.push(`/imports/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setIsSubmitting(false);
    }
  }, [selectedPlugin, selectedCompany, file, dryRun, accountingDate, originalEntryRef, onClose, router]);

  const handleClose = useCallback(() => {
    if (isSubmitting) return;
    setSelectedPlugin("");
    setSelectedCompany("");
    setFile(null);
    setDryRun(false);
    setAccountingDate("");
    setOriginalEntryRef("");
    setError("");
    onClose();
  }, [isSubmitting, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-night-blue/40"
        onClick={handleClose}
      />

      {/* Dialog */}
      <div className="relative z-10 w-full max-w-lg rounded-lg border border-border bg-white p-6 shadow-lg">
        <h2 className="text-lg font-semibold text-night-blue">New Import</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Select a company, then choose an import type and upload a file.
        </p>

        <div className="mt-6 space-y-4">
          {/* Company selector (first) */}
          <div>
            <label className="mb-1 block text-sm font-medium text-night-blue">
              Company
            </label>
            <select
              value={selectedCompany}
              onChange={(e) => handleCompanyChange(e.target.value)}
              disabled={companies.length === 0}
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none disabled:bg-sand/30"
            >
              <option value="">Select a company...</option>
              {companies.map((c) => (
                <option key={c.company_id} value={String(c.company_id)}>
                  {c.name}
                </option>
              ))}
            </select>
            {companies.length === 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                No companies assigned — contact your administrator.
              </p>
            )}
          </div>

          {/* Plugin selector (second, filtered by company) */}
          {selectedCompany && (
            <div>
              <label className="mb-1 block text-sm font-medium text-night-blue">
                Import type
              </label>
              {availablePlugins.length === 0 ? (
                <p className="rounded-md bg-sand/50 px-3 py-2 text-sm text-muted-foreground">
                  No import types configured for this company. Add
                  configuration in Settings first.
                </p>
              ) : (
                <select
                  value={selectedPlugin}
                  onChange={(e) => {
                    setSelectedPlugin(e.target.value);
                    setFile(null);
                  }}
                  className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                >
                  <option value="">Select an import type...</option>
                  {availablePlugins.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* File dropzone */}
          {selectedPlugin && (
            <div>
              <label className="mb-1 block text-sm font-medium text-night-blue">
                File
              </label>
              <FileDropzone
                accept={currentPlugin?.accepted_extensions}
                onFileSelect={setFile}
                selectedFile={file}
              />
            </div>
          )}

          {/* Original entry reference (Rail & Commission) */}
          {(selectedPlugin === "rail" || selectedPlugin === "commission") && (
            <div>
              <label className="mb-1 block text-sm font-medium text-night-blue">
                Original entry{" "}
                <span className="font-normal text-muted-foreground">(optional)</span>
              </label>
              <input
                type="text"
                value={originalEntryRef}
                onChange={(e) => setOriginalEntryRef(e.target.value)}
                placeholder="e.g. A01/2026/02/0193"
                className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue placeholder:text-muted-foreground focus:border-horizon-blue focus:outline-none"
              />
              <p className="mt-1 text-xs text-muted-foreground">
                Reference of the associated Odoo entry. When provided, the bill
                date, accounting date and bill reference are copied from that
                entry and a link is added to the invoice narration.
              </p>
            </div>
          )}

          {/* Dry run toggle */}
          <label className="flex items-center gap-2 text-sm text-night-blue">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="h-4 w-4 rounded border-border text-horizon-blue focus:ring-horizon-blue"
            />
            Dry run (simulate without creating moves in Odoo)
          </label>

          {/* Accounting date override */}
          <div>
            <label className="mb-1 block text-sm font-medium text-night-blue">
              Accounting date{" "}
              <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <input
              type="date"
              value={accountingDate}
              onChange={(e) => setAccountingDate(e.target.value)}
              className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Override the accounting date for all moves in this import. Leave
              empty to let Odoo determine it from the invoice date.
            </p>
          </div>

          {/* Error */}
          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={handleClose}
            disabled={isSubmitting}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={
              !selectedPlugin || !selectedCompany || !file || isSubmitting
            }
            className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
          >
            {isSubmitting ? "Running..." : "Upload & Run"}
          </button>
        </div>
      </div>
    </div>
  );
}
