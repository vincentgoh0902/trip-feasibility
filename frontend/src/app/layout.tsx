import type { Metadata } from "next";
import { Hanken_Grotesk, JetBrains_Mono, Nunito } from "next/font/google";

import { SiteFooter } from "@/components/site/site-footer";
import { SiteHeader } from "@/components/site/site-header";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

// Nunito (rounded, to match the illustration) for headings, Hanken Grotesk for text and UI,
// JetBrains Mono for data (times, coordinates, evidence).
const nunito = Nunito({ variable: "--font-nunito", subsets: ["latin"] });
const hankenGrotesk = Hanken_Grotesk({ variable: "--font-hanken-grotesk", subsets: ["latin"] });
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Trip Feasibility Checker", template: "%s · Trip Feasibility Checker" },
  description:
    "Paste an itinerary and see what will break: opening hours, public holidays, travel times, hotel red flags and overloaded days, with evidence and re-checked fixes.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${nunito.variable} ${hankenGrotesk.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <SiteHeader />
        <main className="flex flex-1 flex-col">{children}</main>
        <SiteFooter />
        <Toaster theme="light" />
      </body>
    </html>
  );
}
