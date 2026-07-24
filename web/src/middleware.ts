import { withAuth } from 'next-auth/middleware';

export default withAuth({
  pages: {
    signIn: '/login',
  },
  callbacks: {
    authorized: ({ token }) => !!token,
  },
});

export const config = {
  matcher: ['/', '/dashboard', '/database', '/imports', '/imports/:path*', '/settings', '/settings/:path*', '/api/:path*', '/api/((?!auth).*)'],
};
