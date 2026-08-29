import Audience from "@/components/landing/Audience";
import BookACall from "@/components/landing/BookACall";
import Difference from "@/components/landing/Difference";
import Hero from "@/components/landing/Hero";
import HowItWorks from "@/components/landing/HowItWorks";
import Problem from "@/components/landing/Problem";
import Proof from "@/components/landing/Proof";
import SiteFooter from "@/components/landing/SiteFooter";
import SiteNav from "@/components/landing/SiteNav";

/**
 * Static by design — no "use client", no state, no data fetching. The console
 * at /console is the interactive surface; this page's only interaction is a
 * link, so it ships as HTML and stays fast on a conference-wifi first visit.
 */
export default function Landing() {
  return (
    <>
      <SiteNav />
      <main>
        <Hero />
        <Problem />
        <Difference />
        <HowItWorks />
        <Proof />
        <Audience />
        <BookACall />
      </main>
      <SiteFooter />
    </>
  );
}
