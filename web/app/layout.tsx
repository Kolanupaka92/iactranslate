import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/**
 * Positioning note, because this string is the first thing anyone reads.
 *
 * This used to say "Convert VMware discovery reports into production-ready
 * Terraform for AWS, Azure, and GCP" — which describes a VMware-to-Terraform
 * converter, and lands on two of the three framings the product direction
 * explicitly rules out. It was also just wrong on the facts: five sources, five
 * clouds, six IaC formats, and the translation is the last stage of a pipeline
 * that assesses, recommends, prices and governs first.
 */
export const metadata: Metadata = {
  metadataBase: new URL("https://iactranslate.com"),
  title: {
    default: "IaCTranslate — Enterprise Cloud Migration Intelligence",
    template: "%s · IaCTranslate",
  },
  description:
    "Assess an estate, compare clouds on real cost, and ship provider-validated " +
    "infrastructure code. Works from an inventory export you already have — " +
    "no agents, no access to your environment.",
  openGraph: {
    title: "IaCTranslate — Enterprise Cloud Migration Intelligence",
    description:
      "Assess an estate, compare clouds on real cost, and ship provider-validated " +
      "infrastructure code from an inventory export you already have.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans">{children}</body>
    </html>
  );
}
