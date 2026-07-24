"use client";

import { useSession } from "next-auth/react";

export default function DashboardPage() {
  const { data } = useSession();
  const user = data?.user;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-night-blue">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Welcome back, {user?.display_name || user?.name || user?.email}
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* BSP Card */}
        <div className="rounded-lg border border-border bg-white p-6">
          <h3 className="text-lg font-medium text-night-blue">
            BSP Purchases
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Import airline ticket files and create purchase invoices + misc
            entries in Odoo.
          </p>
          <p className="mt-4 text-xs text-ground">
            Accepts: .txt files
          </p>
        </div>

        {/* Vivawallet Card */}
        <div className="rounded-lg border border-border bg-white p-6">
          <h3 className="text-lg font-medium text-night-blue">Vivawallet</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Import Vivawallet payment Excel exports and create misc journal
            entries with invoice matching.
          </p>
          <p className="mt-4 text-xs text-ground">
            Accepts: .xlsx files
          </p>
        </div>
      </div>
    </div>
  );
}
