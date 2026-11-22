"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { PageLoader } from "@/components/loading";
import { useAuthStore } from "@/stores/auth-store";
import { useLanguage } from "@/providers/language-provider";
import { isTauri, reportDesktopFault } from "@/lib/desktop-startup";

interface AuthGuardProps {
  children: React.ReactNode;
  /** Optional label shown while the session check runs. */
  label?: string;
}

/**
 * Wraps protected pages: verifies the session on mount, shows a loader while
 * checking, and redirects to /login when unauthenticated.
 */
export function AuthGuard({ children, label }: AuthGuardProps) {
  const { t } = useLanguage();
  const router = useRouter();
  const { user, isInitialized, fetchUser } = useAuthStore();

  useEffect(() => {
    if (!isInitialized) {
      void fetchUser();
    }
  }, [isInitialized, fetchUser]);

  useEffect(() => {
    if (isInitialized && !user) {
      if (isTauri()) reportDesktopFault("LOCAL_SESSION_UNAVAILABLE");
      else router.replace("/login");
    }
  }, [isInitialized, user, router]);

  if (!isInitialized || !user) {
    return <PageLoader label={label ?? t("auth.checkingSession")} />;
  }

  return <>{children}</>;
}
