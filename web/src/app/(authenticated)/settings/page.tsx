"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { useSession } from "next-auth/react";
import type { AppConfigEntry, Company, User as ApiUser } from "@/types";

function isJsonValue(val: unknown): boolean {
  return typeof val === "object" && val !== null;
}

function formatValue(val: unknown): string {
  if (isJsonValue(val)) return JSON.stringify(val, null, 2);
  if (val === null || val === undefined) return "";
  return String(val);
}

function parseInputValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === "") return "";
  // Try JSON parse for objects/arrays
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return raw;
    }
  }
  // Try number
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  return raw;
}

export default function SettingsPage() {
  const { data } = useSession();
  const user = data?.user;

  // --- Users section ---
  const [users, setUsers] = useState<ApiUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [userBusy, setUserBusy] = useState<number | null>(null);
  const [userMessage, setUserMessage] = useState<{
    type: "ok" | "err";
    text: string;
  } | null>(null);

  useEffect(() => {
    if (user?.role !== "admin") return;
    (async () => {
      try {
        const data = await apiFetch<{ users: ApiUser[] }>("/api/admin/users");
        setUsers(data.users);
      } catch {
        // Ignore errors
      } finally {
        setUsersLoading(false);
      }
    })();
  }, [user]);

  const handleUpdateUser = async (
    id: number,
    patch: Partial<Pick<ApiUser, "role" | "is_active">> & { company_ids?: number[] },
  ) => {
    setUserBusy(id);
    setUserMessage(null);
    try {
      const { user: updated } = await apiFetch<{ user: ApiUser }>(
        `/api/admin/users/${id}`,
        { method: "PATCH", body: JSON.stringify(patch) },
      );
      setUsers((prev) => prev.map((u) => (u.id === id ? updated : u)));
      setUserMessage({ type: "ok", text: `Updated ${updated.email}` });
    } catch (err) {
      setUserMessage({
        type: "err",
        text: err instanceof Error ? err.message : "Update failed",
      });
    } finally {
      setUserBusy(null);
    }
  };

  const handleAssignCompany = (userId: number, companyId: number) => {
    const target = users.find((u) => u.id === userId);
    if (!target) return;
    const next = Array.from(new Set([...(target.company_ids ?? []), companyId]));
    handleUpdateUser(userId, { company_ids: next });
  };

  const handleRevokeCompany = (userId: number, companyId: number) => {
    const target = users.find((u) => u.id === userId);
    if (!target) return;
    const next = (target.company_ids ?? []).filter((c) => c !== companyId);
    handleUpdateUser(userId, { company_ids: next });
  };

  // --- Config section ---
  const [companies, setCompanies] = useState<Company[]>([]);

  const companyMap = useMemo(() => {
    const map: Record<number, string> = {};
    for (const c of companies) map[c.company_id] = c.name;
    return map;
  }, [companies]);

  const chipPalette = useMemo(
    () => [
      "bg-rose-100 text-rose-800",
      "bg-amber-100 text-amber-800",
      "bg-emerald-100 text-emerald-800",
      "bg-sky-100 text-sky-800",
      "bg-violet-100 text-violet-800",
      "bg-pink-100 text-pink-800",
      "bg-lime-100 text-lime-800",
      "bg-teal-100 text-teal-800",
    ],
    [],
  );
  const chipClass = (cid: number) => chipPalette[cid % chipPalette.length];

  const [selectedCompany, setSelectedCompany] = useState<string>("");
  const [configEntries, setConfigEntries] = useState<AppConfigEntry[]>([]);
  const [configLoading, setConfigLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("");
  const activeTabRef = useRef(activeTab);
  activeTabRef.current = activeTab;
  const [saveMessage, setSaveMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newScope, setNewScope] = useState<string>("__current__"); // "__current__" or "__new__" or a script name
  const [newScopeName, setNewScopeName] = useState("");
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});

  // Fetch companies on mount
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

  // Fetch config when company changes. preserveTab=true keeps the current tab.
  const fetchConfig = useCallback(async (cid: string, preserveTab = false) => {
    if (!cid) return;
    setConfigLoading(true);
    setSaveMessage(null);
    setJsonErrors({});
    try {
      const data = await apiFetch<{ config: AppConfigEntry[] }>(`/api/config/${cid}`);
      setConfigEntries(data.config);
      const scripts = [...new Set(data.config.map((e) => e.script_name))]
        .filter((s) => s !== "")
        .sort();
      if (!preserveTab || !scripts.includes(activeTabRef.current)) {
        setActiveTab(scripts[0] ?? "");
      }
    } catch {
      setConfigEntries([]);
    } finally {
      setConfigLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedCompany) fetchConfig(selectedCompany);
  }, [selectedCompany, fetchConfig]);

  // Derive tabs from entries (exclude company-level empty script_name)
  const tabs = useMemo(() => {
    const scripts = [...new Set(configEntries.map((e) => e.script_name))]
      .filter((s) => s !== "")
      .sort();
    return scripts;
  }, [configEntries]);

  // Entries for active tab
  const tabEntries = useMemo(
    () => configEntries.filter((e) => e.script_name === activeTab).sort((a, b) => a.key.localeCompare(b.key)),
    [configEntries, activeTab]
  );

  // Save a single key on blur (auto-save)
  const handleSaveKey = async (key: string, rawValue: string) => {
    if (!selectedCompany) return;
    const parsed = parseInputValue(rawValue);
    // Check if value actually changed
    const entry = configEntries.find((e) => e.script_name === activeTab && e.key === key);
    if (entry && formatValue(entry.value) === rawValue) return;

    try {
      await apiFetch(`/api/config/${selectedCompany}/${activeTab}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: parsed }),
      });
      setSaveMessage({ type: "ok", text: `Saved "${key}"` });
      await fetchConfig(selectedCompany, true);
    } catch (err) {
      setSaveMessage({
        type: "err",
        text: err instanceof Error ? err.message : `Failed to save "${key}"`,
      });
    }
  };

  const validateJsonField = (key: string, rawValue: string) => {
    const trimmed = rawValue.trim();
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        JSON.parse(trimmed);
        setJsonErrors((prev) => { const n = { ...prev }; delete n[key]; return n; });
      } catch (err) {
        setJsonErrors((prev) => ({
          ...prev,
          [key]: err instanceof Error ? err.message : "Invalid JSON",
        }));
      }
    } else {
      setJsonErrors((prev) => { const n = { ...prev }; delete n[key]; return n; });
    }
  };

  const handleAddKey = async () => {
    if (!selectedCompany || !newKey.trim()) return;

    // Determine target scope
    let targetScope = activeTab;
    if (newScope === "__new__") {
      if (!newScopeName.trim()) return;
      targetScope = newScopeName.trim();
    } else if (newScope !== "__current__") {
      targetScope = newScope;
    }

    const payload: Record<string, unknown> = {
      [newKey.trim()]: parseInputValue(newValue),
    };

    try {
      await apiFetch(`/api/config/${selectedCompany}/${targetScope}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const addedKey = newKey.trim();
      setNewKey("");
      setNewValue("");
      setNewScope("__current__");
      setNewScopeName("");
      // Switch to the target tab and refresh
      setActiveTab(targetScope);
      await fetchConfig(selectedCompany, true);
      setSaveMessage({ type: "ok", text: `Added "${addedKey}" to ${targetScope || "company"}` });
    } catch (err) {
      setSaveMessage({
        type: "err",
        text: err instanceof Error ? err.message : "Failed to add key",
      });
    }
  };

  const handleDeleteKey = async (key: string) => {
    if (!selectedCompany || !confirm(`Delete config key "${key}"?`)) return;
    try {
      await apiFetch(`/api/config/${selectedCompany}/${activeTab}/${key}`, {
        method: "DELETE",
      });
      await fetchConfig(selectedCompany, true);
      setSaveMessage({ type: "ok", text: `Deleted "${key}"` });
    } catch (err) {
      setSaveMessage({
        type: "err",
        text: err instanceof Error ? err.message : "Delete failed",
      });
    }
  };

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
        <h1 className="text-2xl font-semibold text-night-blue">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage users and configuration
        </p>
      </div>

      {/* Users Table */}
      <div className="rounded-lg border border-border bg-white">
        <div className="border-b border-border px-6 py-4">
          <h2 className="text-base font-medium text-foreground">Users</h2>
        </div>
        <div className="border-b border-border px-6 py-3">
          <div className="grid grid-cols-[1.4fr_1fr_0.7fr_2fr_1fr_0.8fr] gap-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <span>Email</span>
            <span>Name</span>
            <span>Role</span>
            <span>Companies</span>
            <span>Status</span>
            <span>Last login</span>
          </div>
        </div>
        {usersLoading ? (
          <div className="px-6 py-8 text-center text-sm text-muted-foreground">
            Loading...
          </div>
        ) : users.length === 0 ? (
          <div className="px-6 py-8 text-center text-sm text-muted-foreground">
            No users yet.
          </div>
        ) : (
          (() => {
            const activeAdminCount = users.filter(
              (u) => u.role === "admin" && u.is_active,
            ).length;
            return users.map((u) => {
              const isSelf = u.email === user?.email;
              const busy = userBusy === u.id;
              const isLastActiveAdmin =
                u.role === "admin" && u.is_active && activeAdminCount === 1;

              const disabled = isSelf || busy || isLastActiveAdmin;
              const roleTitle = isSelf
                ? "You cannot change your own role"
                : isLastActiveAdmin
                  ? "At least one active admin is required"
                  : "";
              const statusTitle = isSelf
                ? "You cannot deactivate your own account"
                : isLastActiveAdmin
                  ? "At least one active admin is required"
                  : "";

              const userCompanies = u.company_ids ?? [];
              const unassigned = companies.filter(
                (c) => !userCompanies.includes(c.company_id),
              );

              return (
                <div
                  key={u.id}
                  className="grid grid-cols-[1.4fr_1fr_0.7fr_2fr_1fr_0.8fr] gap-3 border-b border-border px-6 py-3 text-sm last:border-b-0 items-center"
                >
                  <span className="text-foreground break-all">{u.email}</span>
                  <span className="text-muted-foreground">
                    {u.display_name || "-"}
                  </span>
                  <span>
                    <select
                      value={u.role}
                      disabled={disabled}
                      onChange={(e) =>
                        handleUpdateUser(u.id, {
                          role: e.target.value as "admin" | "operator",
                        })
                      }
                      title={roleTitle}
                      className="h-7 rounded-md border border-border bg-white px-2 text-xs text-night-blue focus:border-horizon-blue focus:outline-none disabled:opacity-60"
                    >
                      <option value="admin">admin</option>
                      <option value="operator">operator</option>
                    </select>
                  </span>
                  <span>
                    {u.role === "admin" ? (
                      <span className="inline-flex items-center rounded-full bg-night-blue/10 px-2.5 py-0.5 text-xs font-medium text-night-blue">
                        All companies
                      </span>
                    ) : (
                      <div className="flex flex-wrap items-center gap-1">
                        {userCompanies.map((cid) => (
                          <span
                            key={cid}
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${chipClass(cid)}`}
                          >
                            {companyMap[cid] ?? `#${cid}`}
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => handleRevokeCompany(u.id, cid)}
                              aria-label={`Remove ${companyMap[cid] ?? `#${cid}`}`}
                              className="rounded-full hover:bg-black/10 disabled:opacity-50"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                        {unassigned.length > 0 && (
                          <select
                            value=""
                            disabled={busy}
                            onChange={(e) => {
                              const cid = Number(e.target.value);
                              if (!Number.isNaN(cid) && cid > 0) {
                                handleAssignCompany(u.id, cid);
                              }
                            }}
                            className="h-6 rounded-md border border-dashed border-border bg-transparent px-1.5 text-xs text-muted-foreground focus:border-horizon-blue focus:outline-none"
                          >
                            <option value="">+ Add…</option>
                            {unassigned.map((c) => (
                              <option key={c.company_id} value={c.company_id}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                        )}
                        {userCompanies.length === 0 && unassigned.length === 0 && (
                          <span className="text-xs text-muted-foreground italic">
                            No companies known
                          </span>
                        )}
                      </div>
                    )}
                  </span>
                  <span
                    className={
                      u.is_active ? "text-horizon-blue" : "text-ground"
                    }
                  >
                    <button
                      disabled={disabled}
                      onClick={() =>
                        handleUpdateUser(u.id, { is_active: !u.is_active })
                      }
                      title={statusTitle}
                      className="text-xs underline-offset-2 hover:underline disabled:no-underline disabled:opacity-60"
                    >
                      {u.is_active
                        ? "Active — Deactivate"
                        : "Inactive — Reactivate"}
                    </button>
                  </span>
                  <span className="text-muted-foreground">
                    {u.last_login_at
                      ? new Date(u.last_login_at).toLocaleDateString()
                      : "Never"}
                  </span>
                </div>
              );
            });
          })()
        )}
        {userMessage && (
          <div className="px-6 py-2 text-sm">
            <span
              className={
                userMessage.type === "ok" ? "text-emerald-600" : "text-red-600"
              }
            >
              {userMessage.text}
            </span>
          </div>
        )}
      </div>

      {/* Configuration Section */}
      <div className="rounded-lg border border-border bg-white">
        <div className="border-b border-border px-6 py-4">
          <h2 className="text-base font-medium text-foreground">Configuration</h2>
        </div>

        <div className="px-6 py-4 space-y-4">
          {/* Company selector */}
          <div className="flex items-center gap-4">
            <label className="text-sm font-medium text-night-blue">Company</label>
            <select
              value={selectedCompany}
              onChange={(e) => setSelectedCompany(e.target.value)}
              className="rounded-md border border-border bg-white px-3 py-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
            >
              <option value="">Select a company...</option>
              {companies.map((c) => (
                <option key={c.company_id} value={String(c.company_id)}>
                  {c.name} (ID: {c.company_id})
                </option>
              ))}
            </select>
          </div>

          {configLoading && (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading config...</div>
          )}

          {/* Tabs */}
          {selectedCompany && !configLoading && tabs.length > 0 && (
            <>
              <div className="flex gap-1 border-b border-border">
                {tabs.map((tab) => (
                  <button
                    key={tab}
                    onClick={() => {
                      setActiveTab(tab);
                      setSaveMessage(null);
                      setJsonErrors({});
                    }}
                    className={`px-4 py-2 text-sm font-medium transition-colors ${
                      activeTab === tab
                        ? "border-b-2 border-horizon-blue text-horizon-blue"
                        : "text-muted-foreground hover:text-night-blue"
                    }`}
                  >
                    {tab}
                  </button>
                ))}
              </div>

              {/* Config table */}
              <div className="space-y-1">
                <div className="grid grid-cols-[200px_1fr_40px] gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground py-2">
                  <span>Key</span>
                  <span>Value</span>
                  <span></span>
                </div>

                {tabEntries.map((entry) => {
                  const displayVal = formatValue(entry.value);
                  const isJson = isJsonValue(entry.value);
                  const jsonError = jsonErrors[entry.key];

                  return (
                    <div key={entry.key} className="grid grid-cols-[200px_1fr_40px] gap-2 items-start py-1">
                      <div className="flex items-center h-9">
                        <span className="text-sm font-mono text-night-blue">{entry.key}</span>
                      </div>
                      <div>
                        {isJson ? (
                          <textarea
                            defaultValue={displayVal}
                            onChange={(e) => validateJsonField(entry.key, e.target.value)}
                            onBlur={(e) => {
                              if (!jsonErrors[entry.key]) {
                                handleSaveKey(entry.key, e.target.value);
                              }
                            }}
                            rows={Math.min(8, displayVal.split("\n").length + 1)}
                            className={`w-full rounded-md border px-3 py-2 font-mono text-xs text-night-blue focus:outline-none ${
                              jsonError
                                ? "border-red-300 focus:border-red-500"
                                : "border-border focus:border-horizon-blue"
                            }`}
                          />
                        ) : (
                          <input
                            type="text"
                            defaultValue={displayVal}
                            onBlur={(e) => handleSaveKey(entry.key, e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                            }}
                            className="h-9 w-full rounded-md border border-border px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                          />
                        )}
                        {jsonError && (
                          <p className="mt-0.5 text-xs text-red-500">{jsonError}</p>
                        )}
                      </div>
                      <div className="flex items-center h-9">
                        <button
                          onClick={() => handleDeleteKey(entry.key)}
                          className="text-muted-foreground hover:text-red-500 text-sm"
                          title="Delete key"
                        >
                          &times;
                        </button>
                      </div>
                    </div>
                  );
                })}

                {tabEntries.length === 0 && (
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    No config keys for this scope.
                  </p>
                )}
              </div>

              {/* Add new key */}
              <div className="border-t border-border pt-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Add new key
                </p>
                <div className="flex flex-wrap gap-2">
                  <select
                    value={newScope}
                    onChange={(e) => setNewScope(e.target.value)}
                    className="h-9 rounded-md border border-border bg-white px-2 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  >
                    <option value="__current__">Current tab ({activeTab})</option>
                    {tabs.filter((t) => t !== activeTab).map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                    <option value="__new__">+ New script...</option>
                  </select>
                  {newScope === "__new__" && (
                    <input
                      type="text"
                      placeholder="script_name"
                      value={newScopeName}
                      onChange={(e) => setNewScopeName(e.target.value)}
                      className="h-9 w-36 rounded-md border border-border px-3 font-mono text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                    />
                  )}
                  <input
                    type="text"
                    placeholder="key_name"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    className="h-9 w-48 rounded-md border border-border px-3 font-mono text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  />
                  <input
                    type="text"
                    placeholder="value"
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    className="h-9 flex-1 min-w-30 rounded-md border border-border px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  />
                  <button
                    onClick={handleAddKey}
                    disabled={!newKey.trim() || (newScope === "__new__" && !newScopeName.trim())}
                    className="h-9 rounded-md bg-night-blue px-4 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Feedback message */}
              {saveMessage && (
                <div className="pt-2">
                  <span
                    className={`text-sm ${
                      saveMessage.type === "ok" ? "text-emerald-600" : "text-red-600"
                    }`}
                  >
                    {saveMessage.text}
                  </span>
                </div>
              )}
            </>
          )}

          {selectedCompany && !configLoading && tabs.length === 0 && (
            <div className="space-y-4">
              <p className="py-4 text-center text-sm text-muted-foreground">
                No configuration found for this company. Add the first key below.
              </p>
              {/* Add new key (when no tabs exist yet) */}
              <div className="border-t border-border pt-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Add new key
                </p>
                <div className="flex flex-wrap gap-2">
                  <input
                    type="text"
                    placeholder="script_name (e.g. bsp)"
                    value={newScopeName}
                    onChange={(e) => {
                      setNewScopeName(e.target.value);
                      setNewScope("__new__");
                    }}
                    className="h-9 w-36 rounded-md border border-border px-3 font-mono text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  />
                  <input
                    type="text"
                    placeholder="key_name"
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    className="h-9 w-48 rounded-md border border-border px-3 font-mono text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  />
                  <input
                    type="text"
                    placeholder="value"
                    value={newValue}
                    onChange={(e) => setNewValue(e.target.value)}
                    className="h-9 flex-1 min-w-30 rounded-md border border-border px-3 text-sm text-night-blue focus:border-horizon-blue focus:outline-none"
                  />
                  <button
                    onClick={handleAddKey}
                    disabled={!newKey.trim() || !newScopeName.trim()}
                    className="h-9 rounded-md bg-night-blue px-4 text-sm font-medium text-white hover:bg-night-blue/90 disabled:opacity-50"
                  >
                    Add
                  </button>
                </div>
              </div>
              {/* Feedback message */}
              {saveMessage && (
                <div className="pt-2">
                  <span
                    className={`text-sm ${
                      saveMessage.type === "ok" ? "text-emerald-600" : "text-red-600"
                    }`}
                  >
                    {saveMessage.text}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
