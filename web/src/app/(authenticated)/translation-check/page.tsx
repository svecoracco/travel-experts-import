"use client";

import { useEffect, useMemo, useState } from "react";
import { useSession } from "next-auth/react";
import { apiFetch } from "@/lib/api";
import type { Company } from "@/types";

interface Language {
  code: string;
  name: string;
}

interface PlanOption {
  id: number;
  name: string;
}

interface Mismatch {
  account_id: number;
  plan_id: number | null;
  plan_name: string;
  reference_name: string;
  translations: Record<string, string>;
  deviating_langs: string[];
}

interface CheckResponse {
  languages: Language[];
  plans: PlanOption[];
  mismatches: Mismatch[];
  total_checked: number;
  total_mismatched: number;
}

interface FixResult {
  account_id: number;
  correct_name: string;
  fixed_langs: string[];
  already_ok_langs: string[];
  errors: string[];
}

interface FixResponse {
  results: FixResult[];
  total_fixed: number;
  total_errors: number;
}

export default function TranslationCheckPage() {
  const { data: session } = useSession();
  const user = session?.user;

  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<string>("");
  const [plans, setPlans] = useState<PlanOption[]>([]);
  const [selectedPlan, setSelectedPlan] = useState<string>("");
  const [languages, setLanguages] = useState<Language[]>([]);
  const [mismatches, setMismatches] = useState<Mismatch[]>([]);
  const [editedNames, setEditedNames] = useState<Record<number, string>>({});
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [summary, setSummary] = useState<{ checked: number; mismatched: number } | null>(null);

  const [loading, setLoading] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [hasRun, setHasRun] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  // Fetch companies on mount (admin only)
  useEffect(() => {
    if (user?.role !== "admin") return;
    apiFetch<{ companies: Company[] }>("/api/config/companies")
      .then((data) => {
        setCompanies(data.companies);
        if (data.companies.length === 1) {
          setSelectedCompany(String(data.companies[0].company_id));
        }
      })
      .catch(() => {});
  }, [user]);

  // Auto-dismiss feedback banner
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(() => setMessage(null), 4000);
    return () => clearTimeout(t);
  }, [message]);

  const runCheck = async () => {
    if (!selectedCompany) return;
    setLoading(true);
    setMessage(null);
    setSelectedIds(new Set());
    setEditedNames({});
    try {
      const qs = new URLSearchParams({ company_id: selectedCompany });
      if (selectedPlan) qs.set("plan_id", selectedPlan);
      const data = await apiFetch<CheckResponse>(
        `/api/translation-check/check?${qs.toString()}`,
      );
      setLanguages(data.languages);
      setPlans(data.plans);
      setMismatches(data.mismatches);
      setSummary({ checked: data.total_checked, mismatched: data.total_mismatched });
      setHasRun(true);
    } catch (err) {
      setMessage({
        type: "err",
        text: err instanceof Error ? err.message : "Check failed",
      });
    } finally {
      setLoading(false);
    }
  };

  const refReference = (m: Mismatch): string =>
    editedNames[m.account_id] ?? m.reference_name;

  const toggleSelected = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === mismatches.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(mismatches.map((m) => m.account_id)));
    }
  };

  const applyFixes = async () => {
    if (!selectedCompany || selectedIds.size === 0) return;
    setShowConfirm(false);
    setFixing(true);
    setMessage(null);
    try {
      const fixes = mismatches
        .filter((m) => selectedIds.has(m.account_id))
        .map((m) => ({
          account_id: m.account_id,
          correct_name: refReference(m).trim(),
        }))
        .filter((f) => f.correct_name);

      if (fixes.length === 0) {
        setMessage({ type: "err", text: "Selected rows have empty reference names" });
        setFixing(false);
        return;
      }

      const res = await apiFetch<FixResponse>("/api/translation-check/fix", {
        method: "POST",
        body: JSON.stringify({
          company_id: Number(selectedCompany),
          fixes,
        }),
      });
      setMessage({
        type: res.total_errors > 0 ? "err" : "ok",
        text: `${res.total_fixed} fixed, ${res.total_errors} errors`,
      });
      await runCheck();
    } catch (err) {
      setMessage({
        type: "err",
        text: err instanceof Error ? err.message : "Fix failed",
      });
    } finally {
      setFixing(false);
    }
  };

  const gridTemplateColumns = useMemo(() => {
    const langCols = languages.map(() => "minmax(140px,1fr)").join(" ");
    return `40px 120px 160px minmax(220px,1.4fr)${langCols ? " " + langCols : ""}`;
  }, [languages]);

  if (user?.role !== "admin") {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
        You don&apos;t have permission to view this page.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-night-blue">Translation Check</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Verify analytic account name translations are consistent across languages.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-white">
        <div className="border-b border-border px-6 py-4">
          <h2 className="text-base font-medium text-foreground">Controls</h2>
        </div>
        <div className="px-6 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Company
              </label>
              <select
                value={selectedCompany}
                onChange={(e) => setSelectedCompany(e.target.value)}
                className="h-9 rounded-md border border-border bg-white px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
              >
                <option value="">Select a company...</option>
                {companies.map((c) => (
                  <option key={c.company_id} value={String(c.company_id)}>
                    {c.name} (ID: {c.company_id})
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Plan filter
              </label>
              <select
                value={selectedPlan}
                onChange={(e) => setSelectedPlan(e.target.value)}
                disabled={plans.length === 0}
                className="h-9 rounded-md border border-border bg-white px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none disabled:opacity-60"
              >
                <option value="">All plans</option>
                {plans.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

            <button
              type="button"
              onClick={runCheck}
              disabled={!selectedCompany || loading}
              className="h-9 rounded-md bg-night-blue px-4 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
            >
              {loading ? "Checking..." : "Run Check"}
            </button>
          </div>
        </div>
      </div>

      {summary && (
        <div
          className={`rounded-lg border px-6 py-4 text-sm ${
            summary.mismatched === 0
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-red-200 bg-red-50 text-destructive"
          }`}
        >
          {summary.checked} accounts checked — {summary.mismatched}{" "}
          {summary.mismatched === 1 ? "mismatch" : "mismatches"} found
        </div>
      )}

      {hasRun && mismatches.length > 0 && (
        <div className="rounded-lg border border-border bg-white">
          <div className="border-b border-border px-6 py-4">
            <h2 className="text-base font-medium text-foreground">Mismatches</h2>
          </div>
          <div className="overflow-x-auto px-6 py-4">
            <div className="min-w-fit space-y-1">
              <div
                className="grid gap-2 border-b border-border py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground"
                style={{ gridTemplateColumns }}
              >
                <span className="flex items-center">
                  <input
                    type="checkbox"
                    checked={
                      selectedIds.size === mismatches.length && mismatches.length > 0
                    }
                    onChange={toggleSelectAll}
                  />
                </span>
                <span>Account ID</span>
                <span>Plan</span>
                <span>Reference name</span>
                {languages.map((lg) => (
                  <span key={lg.code} title={lg.name}>
                    {lg.code}
                  </span>
                ))}
              </div>

              {mismatches.map((m) => {
                const reference = refReference(m);
                return (
                  <div
                    key={m.account_id}
                    className="grid gap-2 items-center border-b border-border/60 py-2 text-sm last:border-b-0"
                    style={{ gridTemplateColumns }}
                  >
                    <span className="flex items-center">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(m.account_id)}
                        onChange={() => toggleSelected(m.account_id)}
                      />
                    </span>
                    <span className="font-mono text-xs text-night-blue">
                      {m.account_id}
                    </span>
                    <span className="text-muted-foreground">
                      {m.plan_name || "—"}
                    </span>
                    <input
                      type="text"
                      value={reference}
                      onChange={(e) =>
                        setEditedNames((prev) => ({
                          ...prev,
                          [m.account_id]: e.target.value,
                        }))
                      }
                      className="h-9 w-full rounded-md border border-border px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                    />
                    {languages.map((lg) => {
                      const val = m.translations[lg.code] ?? "";
                      const matches = val === reference;
                      return (
                        <span
                          key={lg.code}
                          className={`truncate rounded-sm px-2 py-1 text-xs ${
                            matches
                              ? "text-emerald-700"
                              : "bg-red-50 text-destructive"
                          }`}
                          title={val}
                        >
                          {val || <em className="text-muted-foreground">(empty)</em>}
                        </span>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-end gap-3 border-t border-border px-6 py-4">
            <span className="text-sm text-muted-foreground">
              {selectedIds.size} selected
            </span>
            <button
              type="button"
              onClick={() => setShowConfirm(true)}
              disabled={selectedIds.size === 0 || fixing}
              className="h-9 rounded-md bg-night-blue px-4 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
            >
              {fixing ? "Fixing..." : `Fix Selected (${selectedIds.size})`}
            </button>
          </div>
        </div>
      )}

      {hasRun && mismatches.length === 0 && !loading && (
        <div className="rounded-lg border border-border bg-white px-6 py-10 text-center text-sm text-muted-foreground">
          No translation mismatches found.
        </div>
      )}

      {!hasRun && !loading && (
        <div className="rounded-lg border border-dashed border-border bg-white px-6 py-10 text-center text-sm text-muted-foreground">
          Select a company and click Run Check to verify translations.
        </div>
      )}

      {message && (
        <div
          className={`rounded-md border px-4 py-3 text-sm ${
            message.type === "ok"
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-red-200 bg-red-50 text-destructive"
          }`}
        >
          {message.text}
        </div>
      )}

      {showConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-night-blue/40"
            onClick={() => setShowConfirm(false)}
          />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h2 className="text-base font-semibold text-night-blue">
              Confirm translation fix
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              This will overwrite translations for {selectedIds.size} analytic{" "}
              {selectedIds.size === 1 ? "account" : "accounts"} in all active
              languages. Continue?
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowConfirm(false)}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-night-blue hover:bg-sand/50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={applyFixes}
                className="rounded-md bg-night-blue px-4 py-2 text-sm font-medium text-white hover:bg-night-blue/90"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
