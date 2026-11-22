"use client";

import type { ComponentProps, MouseEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { navigateToAppRoute } from "@/lib/desktop-navigation";
import { isTauri } from "@/lib/tauri-adapter";

interface DesktopLinkProps
  extends Omit<ComponentProps<typeof Link>, "href" | "onClick"> {
  href: string;
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
}

/**
 * A Next link that translates dynamic routes before Next requests their RSC
 * payload. Regular web navigation and modified clicks retain Link behavior.
 */
export function DesktopLink({
  href,
  onClick,
  target,
  ...props
}: DesktopLinkProps) {
  const router = useRouter();

  return (
    <Link
      {...props}
      href={href}
      target={target}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          !isTauri() ||
          target === "_blank" ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        event.preventDefault();
        navigateToAppRoute(router, href);
      }}
    />
  );
}
