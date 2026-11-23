import Link from "next/link";

/**
 * The IaCTranslate wordmark as a link to the home page. Every page that isn't
 * the landing page carries one, so there is always a way back.
 */
export default function HomeLink({ className = "" }: { className?: string }) {
  return (
    <Link
      href="/"
      className={`font-mono text-sm font-semibold tracking-tight hover:underline ${className}`}
      title="IaCTranslate home"
    >
      IaCTranslate
    </Link>
  );
}
