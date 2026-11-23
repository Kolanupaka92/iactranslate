import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Confirm email",
  description: "Confirm the email address on your IaCTranslate account.",
  robots: { index: false },
  // The URL carries a single-use token.
  referrer: "no-referrer",
};

export default function VerifyEmailLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
