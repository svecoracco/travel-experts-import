import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Monorepo-root heeft een eigen package-lock.json (uitsluitend om
  // prisma/schema.prisma tooling-resolvable te houden, zie ../package.json).
  // Zonder deze hint leidt Next.js/Turbopack de workspace-root verkeerd af
  // (kiest de monorepo-root i.p.v. web/) — expliciet vastpinnen op web/ zelf.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
