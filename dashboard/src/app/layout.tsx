import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import StatusBar from "@/components/StatusBar";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "JARVIS Dashboard",
  description: "JARVIS AI Assistant Dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className="dark">
      <body className={`${inter.className} h-screen flex flex-col overflow-hidden`}>
        <div className="flex flex-1 overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-6">{children}</main>
        </div>
        <StatusBar />
      </body>
    </html>
  );
}
