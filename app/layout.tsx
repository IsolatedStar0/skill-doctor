import type { Metadata } from "next";
import "./globals.css";

const title = "Skill Doctor — 可归因的 Agent Skill 修复 Demo";
const description =
  "从失败 Trace 到 scoped Skill patch，再到 replay 与 regression 验证的确定性演示。";

export const metadata: Metadata = {
  title,
  description,
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
  openGraph: {
    title,
    description,
    type: "website",
    images: [{ url: "/og.png", width: 1536, height: 910, alt: "Skill Doctor 工作流" }],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/og.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
