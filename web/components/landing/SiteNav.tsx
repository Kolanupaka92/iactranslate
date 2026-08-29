import Link from "next/link";

import { DOCS_URL, REPO_URL, bookACallHref } from "@/lib/site";

const LINKS = [
  { href: "#problem", label: "The problem" },
  { href: "#difference", label: "Why us" },
  { href: "#how", label: "How it works" },
  { href: "#proof", label: "Proof" },
];

export default function SiteNav() {
  return (
    <header className="sticky top-0 z-50 border-b border-border-subtle bg-background/80 backdrop-blur">
      <nav className="mx-auto flex h-16 max-w-6xl items-center gap-8 px-6">
        <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
          IaC<span className="text-accent">Translate</span>
        </Link>

        {/* Hidden below `md` rather than collapsed into a hamburger: four
            anchor links do not justify a menu, and a drawer would be the only
            piece of client-side state on an otherwise static page. */}
        <ul className="hidden gap-6 md:flex">
          {LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                className="text-sm text-muted transition-colors hover:text-foreground"
              >
                {link.label}
              </a>
            </li>
          ))}
        </ul>

        <div className="ml-auto flex items-center gap-4">
          <a
            href={DOCS_URL}
            className="hidden text-sm text-muted transition-colors hover:text-foreground sm:block"
          >
            Docs
          </a>
          <a
            href={REPO_URL}
            className="hidden text-sm text-muted transition-colors hover:text-foreground sm:block"
          >
            GitHub
          </a>
          <a
            href={bookACallHref}
            className="rounded-lg bg-foreground px-4 py-2 text-sm font-medium text-background transition-opacity hover:opacity-90"
          >
            Book a call
          </a>
        </div>
      </nav>
    </header>
  );
}
