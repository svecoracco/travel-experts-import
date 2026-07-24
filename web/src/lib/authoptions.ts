import { AuthOptions } from 'next-auth';
import AzureADProvider from 'next-auth/providers/azure-ad';

const nextAuthSecret = process.env.NEXTAUTH_SECRET ?? (process.env.NODE_ENV === 'development' ? 'dev-only-nextauth-secret-change-me' : undefined);
const tenantId = process.env.AZURE_AD_TENANT_ID;
const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000';

if (!nextAuthSecret) {
  throw new Error('NEXTAUTH_SECRET is required in production');
}

if (!tenantId) {
  throw new Error('AZURE_AD_TENANT_ID is required');
}

/**
 * Exchange an Azure AD token for a backend-issued JWT.
 *
 * POST /api/auth/token-exchange
 * Body: { "azure_token": "<id_token>" }
 * Response: { "access_token": "<backend_jwt>", "user": { id, email, role, display_name } }
 */
async function exchangeTokenWithBackend(
  azureToken: string,
): Promise<{
  access_token: string;
  user: { id: number; email: string; role: 'admin' | 'operator'; display_name: string | null; company_ids?: number[] };
} | null> {
  try {
    const res = await fetch(`${backendUrl}/api/auth/token-exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ azure_token: azureToken }),
    });
    if (!res.ok) {
      console.error(`Token exchange failed: ${res.status} ${await res.text().catch(() => '')}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.error('Token exchange error:', err);
    return null;
  }
}

const useSecureCookies = process.env.NEXTAUTH_URL?.startsWith('https://') ?? false;
const cookiePrefix = useSecureCookies ? '__Secure-' : '';

export const authOptions: AuthOptions = {
  providers: [
    AzureADProvider({
      clientId: process.env.AZURE_AD_CLIENT_ID!,
      clientSecret: process.env.AZURE_AD_CLIENT_SECRET!,
      tenantId,
      issuer: `https://login.microsoftonline.com/${tenantId}/v2.0`,
      authorization: {
        params: {
          prompt: 'select_account',
          scope: 'openid profile email',
        },
      },
    }),
  ],
  secret: nextAuthSecret,
  cookies: {
    sessionToken: {
      name: `${cookiePrefix}next-auth.session-token`,
      options: {
        httpOnly: true,
        sameSite: 'lax',
        path: '/',
        secure: useSecureCookies,
      },
    },
    callbackUrl: {
      name: `${cookiePrefix}next-auth.callback-url`,
      options: {
        sameSite: 'lax',
        path: '/',
        secure: useSecureCookies,
      },
    },
    csrfToken: {
      name: `${useSecureCookies ? '__Host-' : ''}next-auth.csrf-token`,
      options: {
        httpOnly: true,
        sameSite: 'lax',
        path: '/',
        secure: useSecureCookies,
      },
    },
  },
  callbacks: {
    async jwt({ token, account }) {
      // On initial sign-in: exchange Azure token for backend JWT
      if (account) {
        const azureToken = account.id_token ?? account.access_token;
        if (azureToken) {
          const result = await exchangeTokenWithBackend(azureToken);
          if (result) {
            token.backendAccessToken = result.access_token;
            token.role = result.user.role;
            token.display_name = result.user.display_name;
            token.email = result.user.email;
            token.company_ids = Array.isArray(result.user.company_ids) ? result.user.company_ids : [];
          } else {
            // Exchange failed — token will have no backend access
            console.error('Backend token exchange failed during sign-in');
          }
        }
      }

      // Default role fallback
      if (typeof token.role !== 'string') {
        token.role = 'operator';
      }

      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = token.role === 'admin' ? 'admin' : 'operator';
        session.user.display_name = typeof token.display_name === 'string'
          ? token.display_name
          : session.user.name ?? null;
        session.user.company_ids = Array.isArray(token.company_ids) ? token.company_ids : [];
      }
      session.accessToken = typeof token.backendAccessToken === 'string'
        ? token.backendAccessToken
        : undefined;
      return session;
    },
  },
  pages: {
    signIn: '/login',
  },
};

export default authOptions;
