import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Join organisation",
  description: "Accept an invitation to an IaCTranslate organisation.",
  robots: { index: false },
  // The URL carries a single-use token.
  referrer: "no-referrer",
};

export default function InviteLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
