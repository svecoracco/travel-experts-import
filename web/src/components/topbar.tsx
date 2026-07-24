"use client";

import { signOut, useSession } from "next-auth/react";

export function Topbar() {
  const { data } = useSession();
  const user = data?.user;

  const handleLogout = async () => {
    await signOut({ callbackUrl: "/login" });
  };

  return (
    <header className="flex h-16 items-center justify-between border-b border-border bg-white px-6">
      <div />

      <div className="flex items-center gap-4">
        <span className="text-sm text-muted-foreground">
          {user?.display_name || user?.name || user?.email}
        </span>
        <span className="rounded-full bg-sand-light px-2.5 py-0.5 text-xs font-medium text-night-blue">
          {user?.role || "operator"}
        </span>
        <button
          onClick={handleLogout}
          className="text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
