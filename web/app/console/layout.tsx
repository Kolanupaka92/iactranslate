import type { Metadata } from "next";

/**
 * The console is a client component, so it cannot export `metadata` itself.
 * Without this layout it inherited the landing page's title and every project
 * workspace tab read "Enterprise Cloud Migration Intelligence" — unhelpful when
 * a consultant has four of them open.
 */
export const metadata: Metadata = {
  title: "Console",
  description: "Assess an estate, compare clouds, and generate infrastructure code.",
  robots: { index: false },
};

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
