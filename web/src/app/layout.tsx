import type { Metadata } from "next";
import { Poppins } from "next/font/google";
import ClientSessionProvider from '@/lib/clientSessionProvider';

import "./globals.css";

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "Travel Experts",
  description: "Odoo import management for BTS Travel",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={poppins.variable}>
      <body className="antialiased">
        <ClientSessionProvider>{children}</ClientSessionProvider>
      </body>
    </html>
  );
}
