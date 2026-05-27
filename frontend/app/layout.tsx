import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SEVA — Smart EV Assistant",
  description: "Find the best charging station for your route",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
