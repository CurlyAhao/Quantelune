"use client";

import { AuthGuard } from "@/components/auth-guard";
import { ResearchHome } from "@/components/research-home";

export default function AgentPage() {
  return (
    <AuthGuard>
      <ResearchHome />
    </AuthGuard>
  );
}
