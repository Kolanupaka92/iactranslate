import Link from "next/link";

import { DOCS_URL, OVERVIEW_URL, REPO_URL, bookACallHref } from "@/lib/site";

export default function SiteFooter() {
  return (
    <footer className="mt-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-12 sm:flex-row sm:items-center">
        <Link href="/" className="font-mono text-sm font-semibold tracking-tight">
          IaC<span className="text-accent">Translate</span>
        </Link>
        <nav className="flex flex-wrap gap-6 text-sm text-muted sm:ml-auto">
          <Link href="/console" className="transition-colors hover:text-foreground">
            Console
          </Link>
          <a href={DOCS_URL} className="transition-colors hover:text-foreground">
            Documentation
          </a>
          <a href={OVERVIEW_URL} className="transition-colors hover:text-foreground">
            Overview
          </a>
          <a href={REPO_URL} className="transition-colors hover:text-foreground">
            GitHub
          </a>
          <a href={bookACallHref} className="transition-colors hover:text-foreground">
            Contact
          </a>
        </nav>
      </div>
    </footer>
  );
}
