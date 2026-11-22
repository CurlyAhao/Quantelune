"use client";

import { usePathname } from "next/navigation";
import { useAppearance } from "@/providers/appearance-provider";
import { Navbar } from "./navbar";

export function AppFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { value } = useAppearance();
  const auth = path.startsWith("/login") || path.startsWith("/register");
  const workspace = /\/projects\/[^/]+\/workspace\/?$/.test(path);
  return (
    <div
      className={workspace ? "real-workspace-frame" : auth ? "auth-frame" : "app-frame"}
      data-collapsed={value.collapsed}
      data-surface={
        path === "/" || path === "/dashboard"
          ? "home"
          : path.startsWith("/projects/")
            ? "project"
            : "other"
      }
    >
      {!workspace && <Navbar />}
      <main id="main-content" className={workspace || auth ? "" : "app-main"}>
        {children}
      </main>
    </div>
  );
}
