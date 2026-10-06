import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentTown · AI 打工社区",
  description: "真实来源、可检查成果与独立验收驱动的 AI 微型社区。",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
