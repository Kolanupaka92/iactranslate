import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Reset password",
  description: "Choose a new password for your IaCTranslate account.",
  robots: { index: false },
  // The URL carries a single-use token; nothing on this page should hand
  // even the origin onward.
  referrer: "no-referrer",
};

export default function ResetPasswordLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
