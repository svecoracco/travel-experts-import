"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import type { Company } from "@/types";

// --- Types ---

interface CorrectionMapping {
  source_vat_grid: string;
  target_base_grid: string;
}

interface VatReturnConfig {
  correction_mappings: CorrectionMapping[];
  remainder_grid: string;
  standard_vat_rate: number;
  correction_account: number;
  vat_return_journal_id: number;
}

interface TagInfo {
  [grid: string]: { plus_id?: number; minus_id?: number };
}

interface VatReturnResponse {
  period: string;
  config: VatReturnConfig;
  data: Record<string, Record<string, number>>;
  tag_info: TagInfo;
}

interface CorrectionLine {
  account: number;
  description: string;
  grid: string;
  amount: number;
  tag_id: number | null;
}

interface ExistingEntry {
  exists: boolean;
  move_id?: number;
  move_name?: string;
  created_at?: string;
  created_by?: string;
}

// --- Helpers ---

function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

function fmtNum(n: number | undefined | null): string {
  if (n === undefined || n === null) return "\u2014";
  return n.toLocaleString("nl-BE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function resolveTagId(
  tagInfo: TagInfo,
  grid: string,
  amount: number
): number | null {
  const info = tagInfo[grid];
  if (!info) return null;
  if (amount >= 0) return info.plus_id ?? info.minus_id ?? null;
  return info.minus_id ?? info.plus_id ?? null;
}

const ODOO_BASE_URL =
  process.env.NEXT_PUBLIC_ODOO_URL || "https://travel-experts.odoo.com";

// --- Component ---

export default function VatReturnPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState<number | "">("");
  const [period, setPeriod] = useState(() => {
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    const prev = m === 0 ? new Date(y - 1, 11, 1) : new Date(y, m - 1, 1);
    return `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}`;
  });

  const [responseData, setResponseData] = useState<VatReturnResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [isBooking, setIsBooking] = useState(false);
  const [bookResult, setBookResult] = useState<{
    success?: boolean;
    move_id?: number;
    odoo_move_name?: string;
    error?: string;
    warning?: string;
  } | null>(null);

  // Security banner state
  const [existingEntry, setExistingEntry] = useState<ExistingEntry | null>(null);

  // Confirmation dialog state
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [confirmInput, setConfirmInput] = useState("");

  // Dismiss dialog state
  const [showDismissDialog, setShowDismissDialog] = useState(false);
  const [dismissInput, setDismissInput] = useState("");
  const [isDismissing, setIsDismissing] = useState(false);

  // Calculation details panel state
  const [showCalcDetails, setShowCalcDetails] = useState(false);

  // Load companies
  useEffect(() => {
    apiFetch<{ companies: Company[] }>("/api/config/companies")
      .then((d) => setCompanies(d.companies))
      .catch(() => {});
  }, []);

  // Fetch data + check for existing entry
  const handleFetch = useCallback(async () => {
    if (!companyId || !period) return;
    setIsLoading(true);
    setError(null);
    setResponseData(null);
    setBookResult(null);
    setExistingEntry(null);
    try {
      const [data, check] = await Promise.all([
        apiFetch<VatReturnResponse>(
          `/api/vat-return/data?company_id=${companyId}&period=${period}`
        ),
        apiFetch<ExistingEntry>(
          `/api/vat-return/check?company_id=${companyId}&period=${period}`
        ),
      ]);
      setResponseData(data);
      if (check.exists) {
        setExistingEntry(check);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch data");
    } finally {
      setIsLoading(false);
    }
  }, [companyId, period]);

  // Compute corrected values
  const computed = useMemo(() => {
    if (!responseData) return null;

    const { config, data, tag_info } = responseData;
    const { correction_mappings, remainder_grid, standard_vat_rate } = config;

    const baseGrids = new Set<string>();
    const vatGrids = new Set<string>();
    baseGrids.add(remainder_grid);
    for (const m of correction_mappings) {
      baseGrids.add(m.target_base_grid);
      vatGrids.add(m.source_vat_grid);
    }

    // Collect all grids that appear in data
    const allGrids = new Set<string>();
    for (const codeData of Object.values(data)) {
      for (const grid of Object.keys(codeData)) {
        allGrids.add(grid);
      }
    }
    for (const g of baseGrids) allGrids.add(g);
    for (const g of vatGrids) allGrids.add(g);

    const sortedBaseGrids = [...baseGrids].sort();
    const sortedVatGrids = [...vatGrids].sort();

    // Compute corrected values per VAT code
    const corrected: Record<string, Record<string, number>> = {};
    const vatCodes = Object.keys(data);

    for (const code of vatCodes) {
      const start = data[code];
      const corr: Record<string, number> = {};

      for (const g of sortedVatGrids) {
        corr[g] = start[g] ?? 0;
      }

      for (const m of correction_mappings) {
        const vatAmount = start[m.source_vat_grid] ?? 0;
        corr[m.target_base_grid] = round2(vatAmount / standard_vat_rate);
      }

      const mappedBaseGrids = correction_mappings.map((m) => m.target_base_grid);
      const sumStartBases = mappedBaseGrids.reduce(
        (sum, g) => sum + (start[g] ?? 0),
        0
      );
      const sumCorrBases = mappedBaseGrids.reduce(
        (sum, g) => sum + (corr[g] ?? 0),
        0
      );
      corr[remainder_grid] = round2(sumStartBases - sumCorrBases);

      corrected[code] = corr;
    }

    // Build correction lines
    const correctionLines: CorrectionLine[] = [];

    for (const code of vatCodes) {
      const start = data[code];
      const corr = corrected[code];
      const mappedBaseGrids = correction_mappings.map((m) => m.target_base_grid);

      for (const grid of mappedBaseGrids) {
        const reversalAmount = round2(-(start[grid] ?? 0));
        correctionLines.push({
          account: config.correction_account,
          description: `Correction VAT ${responseData.period} - ${code} - Tax grid: ${grid} - Start situation`,
          grid,
          amount: reversalAmount,
          tag_id: resolveTagId(tag_info, grid, reversalAmount),
        });

        const corrAmount = round2(corr[grid]);
        correctionLines.push({
          account: config.correction_account,
          description: `Correction VAT ${responseData.period} - ${code} - Tax grid: ${grid} - Corrected situation`,
          grid,
          amount: corrAmount,
          tag_id: resolveTagId(tag_info, grid, corrAmount),
        });
      }

      const remainderAmount = round2(corr[remainder_grid]);
      correctionLines.push({
        account: config.correction_account,
        description: `Correction VAT ${responseData.period} - ${code} - Tax grid: ${remainder_grid} - Corrected situation`,
        grid: remainder_grid,
        amount: remainderAmount,
        tag_id: resolveTagId(tag_info, remainder_grid, remainderAmount),
      });
    }

    // Filter out zero-amount lines — they contribute nothing to the journal entry
    const filteredCorrectionLines = correctionLines.filter(
      (l) => round2(l.amount) !== 0
    );

    return {
      sortedBaseGrids,
      sortedVatGrids,
      vatCodes,
      corrected,
      correctionLines: filteredCorrectionLines,
    };
  }, [responseData]);

  // Book correction
  const handleBook = useCallback(async () => {
    if (!responseData || !computed || !companyId) return;
    setIsBooking(true);
    setBookResult(null);
    setShowConfirmDialog(false);
    setConfirmInput("");
    try {
      const result = await apiFetch<{
        success?: boolean;
        move_id?: number;
        odoo_move_name?: string;
        error?: string;
        warning?: string;
      }>("/api/vat-return/book", {
        method: "POST",
        body: JSON.stringify({
          company_id: companyId,
          period: responseData.period,
          correction_lines: computed.correctionLines.map((l) => ({
            description: l.description,
            grid: l.grid,
            amount: l.amount,
            tag_id: l.tag_id,
          })),
          start_data: responseData.data,
        }),
      });
      setBookResult(result);
    } catch (err) {
      setBookResult({
        error: err instanceof Error ? err.message : "Booking failed",
      });
    } finally {
      setIsBooking(false);
    }
  }, [responseData, computed, companyId]);

  // Handle book button click — show confirmation if existing entry
  const handleBookClick = useCallback(() => {
    if (existingEntry?.exists) {
      setShowConfirmDialog(true);
      setConfirmInput("");
    } else {
      handleBook();
    }
  }, [existingEntry, handleBook]);

  // Dismiss existing entry
  const handleDismiss = useCallback(async () => {
    if (!companyId || !period) return;
    setIsDismissing(true);
    try {
      await apiFetch("/api/vat-return/dismiss", {
        method: "POST",
        body: JSON.stringify({ company_id: companyId, period }),
      });
      setExistingEntry(null);
      setShowDismissDialog(false);
      setDismissInput("");
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to dismiss entry");
    } finally {
      setIsDismissing(false);
    }
  }, [companyId, period]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-night-blue">VAT Return</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Review and correct VAT return base amounts
        </p>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Company
          </label>
          <select
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value ? Number(e.target.value) : "")}
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
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Period
          </label>
          <input
            type="month"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none focus:ring-1 focus:ring-horizon-blue"
          />
        </div>
        <button
          onClick={handleFetch}
          disabled={!companyId || !period || isLoading}
          className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "Fetching..." : "Fetch Data"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Security Banner — existing entry warning */}
      {existingEntry?.exists && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
          <div className="flex items-start gap-3">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="mt-0.5 flex-shrink-0 text-amber-600"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-800">
                Be careful! There has already been a correction entry imported.
              </p>
              <p className="mt-1 text-sm text-amber-700">
                Take a look at entry{" "}
                <a
                  href={`${ODOO_BASE_URL}/web#id=${existingEntry.move_id}&model=account.move&view_type=form`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium underline hover:text-amber-900"
                >
                  {existingEntry.move_name || `#${existingEntry.move_id}`}
                </a>
                {existingEntry.created_by && (
                  <span> — created by {existingEntry.created_by}</span>
                )}
                {existingEntry.created_at && (
                  <span>
                    {" "}
                    on{" "}
                    {new Date(existingEntry.created_at).toLocaleDateString(
                      "nl-BE"
                    )}
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={() => {
                setShowDismissDialog(true);
                setDismissInput("");
              }}
              className="flex-shrink-0 rounded-md border border-amber-400 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100"
            >
              I&apos;ve deleted the entry
            </button>
          </div>
        </div>
      )}

      {/* Tables & Preview */}
      {responseData && computed && (
        <>
          {/* Start Situation */}
          <section>
            <h2 className="mb-3 text-lg font-medium text-night-blue">
              Start Situation
            </h2>
            <DataTable
              vatCodes={computed.vatCodes}
              baseGrids={computed.sortedBaseGrids}
              vatGrids={computed.sortedVatGrids}
              values={responseData.data}
            />
          </section>

          {/* Correct Situation */}
          <section>
            <h2 className="mb-3 text-lg font-medium text-night-blue">
              Correct Situation
            </h2>
            <DataTable
              vatCodes={computed.vatCodes}
              baseGrids={computed.sortedBaseGrids}
              vatGrids={computed.sortedVatGrids}
              values={computed.corrected}
            />
          </section>

          {/* Calculation Details Panel */}
          <CalculationDetails
            startData={responseData.data}
            correctedData={computed.corrected}
            config={responseData.config}
            isOpen={showCalcDetails}
            onToggle={() => setShowCalcDetails((v) => !v)}
          />

          {/* Correction Entry Preview */}
          <section>
            <h2 className="mb-3 text-lg font-medium text-night-blue">
              Correction Entry Preview
            </h2>
            <div className="overflow-hidden rounded-lg border border-border/80 bg-white">
              <div className="border-b border-border/70 px-6 py-3">
                <div className="grid grid-cols-[6rem_minmax(0,1fr)_5rem_8rem] gap-x-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  <span>Account</span>
                  <span>Description</span>
                  <span>Tax Grid</span>
                  <span className="text-right">Amount</span>
                </div>
              </div>
              <div>
                {computed.correctionLines.map((line, i) => (
                  <div
                    key={i}
                    className="grid grid-cols-[6rem_minmax(0,1fr)_5rem_8rem] gap-x-4 items-center border-b border-border/50 px-6 py-2.5 text-sm last:border-b-0 hover:bg-sand/20"
                  >
                    <span className="font-mono text-xs text-muted-foreground">
                      {line.account}
                    </span>
                    <span className="truncate text-night-blue" title={line.description}>
                      {line.description}
                    </span>
                    <span className="text-muted-foreground">{line.grid}</span>
                    <span
                      className={`text-right font-mono text-xs ${
                        line.amount < 0
                          ? "text-red-600"
                          : line.amount > 0
                          ? "text-emerald-600"
                          : "text-muted-foreground"
                      }`}
                    >
                      {fmtNum(line.amount)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Book Button */}
          <div className="flex items-center gap-4">
            <button
              onClick={handleBookClick}
              disabled={isBooking || !!bookResult?.success}
              className="rounded-md bg-night-blue px-6 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isBooking ? "Booking..." : "Book Correction"}
            </button>

            {bookResult && (
              <span
                className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${
                  bookResult.error
                    ? "bg-red-100 text-red-700"
                    : "bg-emerald-100 text-emerald-700"
                }`}
              >
                {bookResult.error
                  ? bookResult.error
                  : `Booked successfully — ${bookResult.odoo_move_name || `move #${bookResult.move_id}`}`}
                {bookResult.warning && (
                  <span className="ml-2 text-amber-600">
                    {bookResult.warning}
                  </span>
                )}
              </span>
            )}
          </div>
        </>
      )}

      {/* Confirmation Dialog — requires typing CONFIRM when existing entry */}
      {showConfirmDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-night-blue/40"
            onClick={() => setShowConfirmDialog(false)}
          />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h2 className="text-base font-semibold text-night-blue">
              Confirm booking
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              A correction entry already exists for this period. Booking another
              correction may result in duplicate entries. If you are sure you
              want to proceed, type{" "}
              <span className="font-mono font-semibold text-night-blue">
                CONFIRM
              </span>{" "}
              below.
            </p>
            <input
              type="text"
              value={confirmInput}
              onChange={(e) => setConfirmInput(e.target.value)}
              placeholder="Type CONFIRM to proceed"
              className="mt-4 w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none focus:ring-1 focus:ring-horizon-blue"
              autoFocus
            />
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowConfirmDialog(false);
                  setConfirmInput("");
                }}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50"
              >
                Cancel
              </button>
              <button
                onClick={handleBook}
                disabled={confirmInput !== "CONFIRM"}
                className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Book Correction
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Dismiss Dialog — release the lock after user deleted the entry in Odoo */}
      {showDismissDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-night-blue/40"
            onClick={() => !isDismissing && setShowDismissDialog(false)}
          />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h2 className="text-base font-semibold text-night-blue">
              Confirm entry deletion
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Are you sure the original correction entry has been deleted in
              Odoo? This will release the lock and allow a new correction to be
              booked. Type{" "}
              <span className="font-mono font-semibold text-night-blue">
                CONFIRM
              </span>{" "}
              to proceed.
            </p>
            <input
              type="text"
              value={dismissInput}
              onChange={(e) => setDismissInput(e.target.value)}
              placeholder="Type CONFIRM to proceed"
              className="mt-4 w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none focus:ring-1 focus:ring-horizon-blue"
              autoFocus
            />
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowDismissDialog(false);
                  setDismissInput("");
                }}
                disabled={isDismissing}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleDismiss}
                disabled={dismissInput !== "CONFIRM" || isDismissing}
                className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isDismissing ? "Releasing..." : "Release Lock"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Sub-components ---

function DataTable({
  vatCodes,
  baseGrids,
  vatGrids,
  values,
}: {
  vatCodes: string[];
  baseGrids: string[];
  vatGrids: string[];
  values: Record<string, Record<string, number>>;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border/80 bg-white">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/70">
            <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground">
              VAT Code
            </th>
            {baseGrids.map((g) => (
              <th
                key={`base-${g}`}
                className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wide text-muted-foreground"
              >
                Base: {g}
              </th>
            ))}
            {vatGrids.map((g) => (
              <th
                key={`vat-${g}`}
                className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wide text-muted-foreground"
              >
                VAT: {g}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {vatCodes.map((code) => (
            <tr
              key={code}
              className="border-b border-border/50 last:border-b-0 hover:bg-sand/20"
            >
              <td className="px-4 py-2.5 font-medium text-night-blue">
                {code}
              </td>
              {baseGrids.map((g) => (
                <td
                  key={`base-${g}`}
                  className="px-4 py-2.5 text-right font-mono text-xs text-night-blue"
                >
                  {fmtNum(values[code]?.[g])}
                </td>
              ))}
              {vatGrids.map((g) => (
                <td
                  key={`vat-${g}`}
                  className="px-4 py-2.5 text-right font-mono text-xs text-night-blue"
                >
                  {fmtNum(values[code]?.[g])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CalculationDetails({
  startData,
  correctedData,
  config,
  isOpen,
  onToggle,
}: {
  startData: Record<string, Record<string, number>>;
  correctedData: Record<string, Record<string, number>>;
  config: {
    correction_mappings: CorrectionMapping[];
    remainder_grid: string;
    standard_vat_rate: number;
  };
  isOpen: boolean;
  onToggle: () => void;
}) {
  const { correction_mappings, remainder_grid, standard_vat_rate } = config;
  const vatCodes = Object.keys(startData);
  const mappedBaseGrids = correction_mappings.map((m) => m.target_base_grid);

  return (
    <section>
      {/* Toggle header */}
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left hover:bg-sand/20"
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`text-muted-foreground transition-transform duration-200 ${
            isOpen ? "rotate-90" : ""
          }`}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-muted-foreground"
        >
          <rect x="4" y="2" width="16" height="20" rx="2" />
          <line x1="8" y1="6" x2="16" y2="6" />
          <line x1="8" y1="10" x2="10" y2="10" />
          <line x1="14" y1="10" x2="16" y2="10" />
          <line x1="8" y1="14" x2="10" y2="14" />
          <line x1="14" y1="14" x2="16" y2="14" />
          <line x1="8" y1="18" x2="10" y2="18" />
          <line x1="14" y1="18" x2="16" y2="18" />
        </svg>
        <span className="text-sm font-medium text-night-blue">
          Calculation Details
        </span>
      </button>

      {/* Expanded content */}
      {isOpen && (
        <div className="mt-3 space-y-4">
          {vatCodes.map((code) => {
            const start = startData[code];
            const corr = correctedData[code];

            return (
              <div
                key={code}
                className="rounded-lg border border-border bg-white p-5"
              >
                <h3 className="text-sm font-medium text-night-blue">
                  VAT Code: {code}
                </h3>

                {/* Step 1 */}
                <div className="mt-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Step 1 — Recalculate base amounts from VAT
                  </p>
                  <div className="mt-2 space-y-1">
                    {correction_mappings.map((m) => {
                      const vatVal = start[m.source_vat_grid] ?? 0;
                      const corrVal = corr[m.target_base_grid] ?? 0;
                      return (
                        <p
                          key={m.target_base_grid}
                          className="font-mono text-sm text-night-blue"
                        >
                          Grid {m.target_base_grid} = Grid{" "}
                          {m.source_vat_grid} / {standard_vat_rate} ={" "}
                          {fmtNum(vatVal)} / {standard_vat_rate} ={" "}
                          <span className="font-semibold">
                            {fmtNum(corrVal)}
                          </span>
                        </p>
                      );
                    })}
                  </div>
                </div>

                {/* Step 2 */}
                <div className="mt-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Step 2 — Calculate remainder for grid {remainder_grid}
                  </p>
                  <div className="mt-2 space-y-1 font-mono text-sm text-night-blue">
                    <p>
                      Grid {remainder_grid} = (
                      {mappedBaseGrids
                        .map((g) => `Start ${g}`)
                        .join(" + ")}
                      ) &minus; (
                      {mappedBaseGrids
                        .map((g) => `Corrected ${g}`)
                        .join(" + ")}
                      )
                    </p>
                    <p>
                      {"= ("}
                      {mappedBaseGrids
                        .map((g) => fmtNum(start[g] ?? 0))
                        .join(" + ")}
                      {") \u2212 ("}
                      {mappedBaseGrids
                        .map((g) => fmtNum(corr[g] ?? 0))
                        .join(" + ")}
                      {")"}
                    </p>
                    {(() => {
                      const sumStart = mappedBaseGrids.reduce(
                        (s, g) => s + (start[g] ?? 0),
                        0
                      );
                      const sumCorr = mappedBaseGrids.reduce(
                        (s, g) => s + (corr[g] ?? 0),
                        0
                      );
                      return (
                        <>
                          <p>
                            = {fmtNum(sumStart)} &minus; {fmtNum(sumCorr)}
                          </p>
                          <p>
                            ={" "}
                            <span className="font-semibold">
                              {fmtNum(corr[remainder_grid] ?? 0)}
                            </span>
                          </p>
                        </>
                      );
                    })()}
                  </div>
                </div>

                {/* Step 3 */}
                <div className="mt-4">
                  <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Step 3 — Correction line net amounts
                  </p>
                  <div className="mt-2 space-y-1 font-mono text-sm text-night-blue">
                    {mappedBaseGrids.map((g) => (
                      <p key={g}>
                        Grid {g}: Start reversal ={" "}
                        <span
                          className={
                            -(start[g] ?? 0) < 0
                              ? "text-red-600"
                              : "text-emerald-600"
                          }
                        >
                          {fmtNum(-(start[g] ?? 0))}
                        </span>{" "}
                        | Corrected ={" "}
                        <span
                          className={
                            (corr[g] ?? 0) < 0
                              ? "text-red-600"
                              : "text-emerald-600"
                          }
                        >
                          {fmtNum(corr[g] ?? 0)}
                        </span>
                      </p>
                    ))}
                    <p>
                      Grid {remainder_grid}: Corrected ={" "}
                      <span
                        className={
                          (corr[remainder_grid] ?? 0) < 0
                            ? "text-red-600"
                            : "text-emerald-600"
                        }
                      >
                        {fmtNum(corr[remainder_grid] ?? 0)}
                      </span>
                    </p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
