'use client';

import type { ReactNode } from 'react';
import type { Session } from 'next-auth';
import { SessionProvider } from 'next-auth/react';

interface ClientSessionProviderProps {
  children: ReactNode;
  session?: Session | null;
}

export default function ClientSessionProvider({
  children,
  session,
}: ClientSessionProviderProps) {
  return <SessionProvider session={session}>{children}</SessionProvider>;
}
