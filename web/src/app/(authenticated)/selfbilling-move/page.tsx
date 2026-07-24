"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import type { Company } from "@/types";

interface JournalRef {
  id: number;
  name: string;
}

interface Supplier {
  partner_id: number | null;
  partner_name: string;
  invoice_count: number;
  journal_ids: number[];
}

interface SuppliersResponse {
  moveto_journal: JournalRef;
  search_journals: JournalRef[];
  suppliers: Supplier[];
}

interface MoveResult {
  requested: number;
  moved: number;
  cancelled: number;
  errors: { invoice_id: number; error: string }[];
}

type ConfigErrorKey = "sbmov_search_journals" | "sbmov_moveto_journal";

function parseConfigError(msg: string): ConfigErrorKey | null {
  if (msg.includes("sbmov.sbmov_search_journals")) return "sbmov_search_journals";
  if (msg.includes("sbmov.sbmov_moveto_journal")) return "sbmov_moveto_journal";
  return null;
}

export default function SelfbillingMovePage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<number | "">("");

  const [data, setData] = useState<SuppliersResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missingConfig, setMissingConfig] = useState<ConfigErrorKey | null>(null);

  const [sortDesc, setSortDesc] = useState(true);

  const [confirmTarget, setConfirmTarget] = useState<Supplier | null>(null);
  const [isMoving, setIsMoving] = useState(false);

  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [partialErrors, setPartialErrors] = useState<
    { supplier: string; errors: { invoice_id: number; error: string }[] } | null
  >(null);

  // Load companies
  useEffect(() => {
    apiFetch<{ companies: Company[] }>("/api/config/companies")
      .then((d) => {
        setCompanies(d.companies);
        if (d.companies.length === 1) {
          setCompanyId(d.companies[0].company_id);
        }
      })
      .catch(() => {});
  }, []);

  const fetchSuppliers = useCallback(async (cid: number) => {
    setIsLoading(true);
    setError(null);
    setMissingConfig(null);
    setData(null);
    try {
      const result = await apiFetch<SuppliersResponse>(
        `/api/sbmov/suppliers?company_id=${cid}`
      );
      setData(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to load suppliers";
      const missing = parseConfigError(msg);
      if (missing) {
        setMissingConfig(missing);
      } else {
        setError(msg);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Load suppliers when company selected
  useEffect(() => {
    if (companyId) fetchSuppliers(companyId);
  }, [companyId, fetchSuppliers]);

  // Auto-dismiss success banner
  useEffect(() => {
    if (!successMsg) return;
    const t = setTimeout(() => setSuccessMsg(null), 4000);
    return () => clearTimeout(t);
  }, [successMsg]);

  const sortedSuppliers = useMemo(() => {
    if (!data) return [];
    const arr = [...data.suppliers];
    arr.sort((a, b) =>
      sortDesc
        ? b.invoice_count - a.invoice_count
        : a.invoice_count - b.invoice_count
    );
    return arr;
  }, [data, sortDesc]);

  const journalNameById = useMemo(() => {
    const map = new Map<number, string>();
    if (data) {
      for (const j of data.search_journals) map.set(j.id, j.name);
      map.set(data.moveto_journal.id, data.moveto_journal.name);
    }
    return map;
  }, [data]);

  const handleConfirmMove = useCallback(async () => {
    if (!confirmTarget || !companyId) return;
    setIsMoving(true);
    const supplierLabel = confirmTarget.partner_name;
    try {
      const result = await apiFetch<MoveResult>("/api/sbmov/move", {
        method: "POST",
        body: JSON.stringify({
          company_id: companyId,
          partner_id: confirmTarget.partner_id,
        }),
      });
      setConfirmTarget(null);
      setPartialErrors(
        result.errors.length > 0
          ? { supplier: supplierLabel, errors: result.errors }
          : null
      );
      if (result.errors.length === 0) {
        setSuccessMsg(
          `Moved and cancelled ${result.cancelled} invoice${
            result.cancelled === 1 ? "" : "s"
          } for ${supplierLabel}.`
        );
      }
      if (companyId) await fetchSuppliers(companyId);
    } catch (err) {
      setPartialErrors({
        supplier: supplierLabel,
        errors: [
          {
            invoice_id: 0,
            error: err instanceof Error ? err.message : "Request failed",
          },
        ],
      });
      setConfirmTarget(null);
    } finally {
      setIsMoving(false);
    }
  }, [confirmTarget, companyId, fetchSuppliers]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-night-blue">
          Self-billing Move
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Move draft self-billing invoices to a dedicated journal and cancel
          them.
        </p>
      </div>

      {/* Company selector */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Company
          </label>
          <select
            value={companyId}
            onChange={(e) =>
              setCompanyId(e.target.value ? Number(e.target.value) : "")
            }
            disabled={companies.length === 0}
            className="rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none focus:ring-1 focus:ring-horizon-blue disabled:bg-sand/30"
          >
            <option value="">Select company...</option>
            {companies.map((c) => (
              <option key={c.company_id} value={c.company_id}>
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
      </div>

      {/* Missing config banner */}
      {missingConfig && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="font-medium">Configuration incomplete</p>
          <p className="mt-1">
            The config key{" "}
            <span className="font-mono font-semibold">
              sbmov.{missingConfig}
            </span>{" "}
            is missing for this company. Both{" "}
            <span className="font-mono">sbmov_search_journals</span> and{" "}
            <span className="font-mono">sbmov_moveto_journal</span> must be set
            under script <span className="font-mono">sbmov</span>.
          </p>
          <p className="mt-2">
            <Link
              href="/settings"
              className="font-medium underline hover:text-amber-900"
            >
              Open Settings to configure
            </Link>
          </p>
        </div>
      )}

      {/* Generic error */}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Success banner */}
      {successMsg && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {successMsg}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          Loading suppliers...
        </div>
      )}

      {/* Info + Table */}
      {data && !isLoading && (
        <>
          <div className="text-xs text-muted-foreground">
            <p>
              Source journals:{" "}
              {data.search_journals.length > 0
                ? data.search_journals.map((j) => j.name).join(", ")
                : "(none)"}
            </p>
            <p>
              Target journal:{" "}
              <span className="text-night-blue">
                {data.moveto_journal.name}
              </span>
            </p>
          </div>

          <div className="overflow-hidden rounded-lg border border-border/80 bg-white">
            <div className="border-b border-border/70 px-6 py-3">
              <div className="grid grid-cols-[minmax(0,1fr)_8rem_minmax(0,1fr)_7rem] items-center gap-x-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span>Supplier</span>
                <button
                  onClick={() => setSortDesc((v) => !v)}
                  className="flex items-center gap-1 text-left uppercase tracking-wide hover:text-night-blue"
                >
                  # Drafts
                  <span aria-hidden>{sortDesc ? "↓" : "↑"}</span>
                </button>
                <span>Source journals</span>
                <span className="text-right">Action</span>
              </div>
            </div>

            {sortedSuppliers.length === 0 ? (
              <div className="px-6 py-12 text-center text-sm text-muted-foreground">
                No self-billing drafts found in configured journals.
              </div>
            ) : (
              <div>
                {sortedSuppliers.map((s) => {
                  const journalNames = s.journal_ids
                    .map((id) => journalNameById.get(id) || `#${id}`)
                    .join(", ");
                  const rowKey =
                    s.partner_id === null ? "__null__" : s.partner_id;
                  return (
                    <div
                      key={rowKey}
                      className="grid grid-cols-[minmax(0,1fr)_8rem_minmax(0,1fr)_7rem] items-center gap-x-4 border-b border-border/50 px-6 py-3 text-sm last:border-b-0 hover:bg-sand/20"
                    >
                      <span className="truncate text-night-blue" title={s.partner_name}>
                        {s.partner_name}
                      </span>
                      <span className="font-mono text-xs text-night-blue">
                        {s.invoice_count}
                      </span>
                      <span
                        className="truncate text-xs text-muted-foreground"
                        title={journalNames}
                      >
                        {journalNames}
                      </span>
                      <div className="text-right">
                        <button
                          onClick={() => setConfirmTarget(s)}
                          className="rounded-md bg-night-blue px-3 py-1.5 text-xs font-medium text-white hover:bg-night-blue/90"
                        >
                          Move
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </>
      )}

      {/* Partial-error inline alert */}
      {partialErrors && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1">
              <p className="font-medium">
                Some invoices failed for {partialErrors.supplier}
              </p>
              <ul className="mt-2 space-y-1">
                {partialErrors.errors.map((e, i) => (
                  <li key={i} className="font-mono text-xs">
                    {e.invoice_id > 0 ? `#${e.invoice_id}: ` : ""}
                    {e.error}
                  </li>
                ))}
              </ul>
            </div>
            <button
              onClick={() => setPartialErrors(null)}
              className="text-xs font-medium text-red-700 hover:underline"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Confirm dialog */}
      {confirmTarget && data && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-night-blue/40"
            onClick={() => !isMoving && setConfirmTarget(null)}
          />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h2 className="text-lg font-semibold text-night-blue">
              Move self-billing drafts
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              You are about to move{" "}
              <span className="font-medium text-night-blue">
                {confirmTarget.invoice_count} draft
                {confirmTarget.invoice_count === 1 ? "" : "s"}
              </span>{" "}
              from{" "}
              <span className="font-medium text-night-blue">
                {confirmTarget.partner_name}
              </span>{" "}
              to journal{" "}
              <span className="font-medium text-night-blue">
                {data.moveto_journal.name}
              </span>{" "}
              and cancel them.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setConfirmTarget(null)}
                disabled={isMoving}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmMove}
                disabled={isMoving}
                className="inline-flex items-center gap-2 rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
              >
                {isMoving && (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white border-t-transparent" />
                )}
                {isMoving ? "Moving..." : "Move & Cancel"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
