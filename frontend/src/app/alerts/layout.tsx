import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Price Alerts",
  description: "Set price alerts for US and Indian stocks. Get browser notifications when a stock crosses your target price.",
};

export default function AlertsLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
