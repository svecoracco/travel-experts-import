import type { DefaultSession } from 'next-auth';

declare module 'next-auth' {
  interface Session {
    accessToken?: string;
    user: {
      role?: 'admin' | 'operator';
      display_name?: string | null;
      company_ids?: number[];
    } & DefaultSession['user'];
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    backendAccessToken?: string;
    role?: 'admin' | 'operator';
    display_name?: string | null;
    company_ids?: number[];
  }
}
