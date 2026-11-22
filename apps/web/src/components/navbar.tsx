"use client";

import Image from "next/image";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  FolderIcon,
  HouseIcon,
  LanguagesIcon,
  LogOutIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  SettingsIcon,
  UserRoundIcon,
} from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { useLanguage } from "@/providers/language-provider";
import { useAppearance } from "@/providers/appearance-provider";
import { DesktopLink } from "./desktop-link";
import { AppearanceControls } from "./appearance-controls";
import { Button } from "./ui/button";
import { isTauri } from "@/lib/desktop-startup";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

export function Navbar() {
  const path = usePathname();
  const router = useRouter();
  const { user, isInitialized, fetchUser, logout } = useAuthStore();
  const [signingOut, setSigningOut] = useState(false);
  const { locale, setLocale, t, preferenceSaved } = useLanguage();
  const { value, update, ready } = useAppearance();
  useEffect(() => {
    if (!isInitialized) void fetchUser();
  }, [isInitialized, fetchUser]);
  if (path.startsWith("/login") || path.startsWith("/register")) return null;
  const links = [
    {
      href: "/dashboard",
      label: t("entry.home"),
      icon: HouseIcon,
      active: path === "/" || path === "/dashboard",
    },
    {
      href: "/projects",
      label: t("ui.dashboard.projects"),
      icon: FolderIcon,
      active: path.startsWith("/projects") && path !== "/projects/new",
    },
    {
      href: "/settings",
      label: t("entry.settings"),
      icon: SettingsIcon,
      active: path.startsWith("/settings"),
    },
  ];
  return (
    <>
      <a className="app-skip" href="#main-content">
        {t("entry.skip")}
      </a>
      <aside className="app-navigation">
        <DesktopLink
          href="/dashboard"
          className="app-rail-brand"
          aria-label={t("entry.home")}
        >
          <Image src="/brand/quantelune.png" width={32} height={32} alt="" />
        </DesktopLink>
        <Button
          variant="ghost"
          size="icon"
          disabled={!ready}
          aria-label={t(
            value.collapsed ? "entry.expandNav" : "entry.collapseNav",
          )}
          aria-expanded={!value.collapsed}
          onClick={() => update({ collapsed: !value.collapsed })}
        >
          {value.collapsed ? (
            <PanelLeftOpenIcon className="size-4" />
          ) : (
            <PanelLeftCloseIcon className="size-4" />
          )}
        </Button>
        <nav aria-label={t("ui.common.primary")}>
          {links.map(({ href, label, icon: Icon, active }) => (
            <DesktopLink
              key={href}
              href={href}
              className="app-navigation-link"
              aria-label={label}
              title={label}
              aria-current={active ? "page" : undefined}
            >
              <Icon className="size-5 shrink-0" aria-hidden />
              <span className="app-navigation-label">{label}</span>
            </DesktopLink>
          ))}
        </nav>
      </aside>
      <header className="app-header">
        <DesktopLink href="/dashboard" className="app-brand">
          <Image src="/brand/quantelune.png" width={28} height={28} alt="" className="shrink-0" />
          <span>Quantelune</span>
        </DesktopLink>
        <div className="app-header-controls">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t(
              locale === "zh-CN"
                ? "locale.switchToEnglish"
                : "locale.switchToChinese",
            )}
            onClick={() => setLocale(locale === "zh-CN" ? "en" : "zh-CN")}
          >
            <LanguagesIcon className="size-4" />
          </Button>
          <AppearanceControls />
          {user && !isTauri() && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<button type="button" className="rounded-md p-2 hover:bg-muted" aria-label={t("ui.common.accountMenu")} />}>
                <UserRoundIcon className="size-4" aria-hidden />
                <span className="sr-only">{user.email}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>
                  {user.display_name ?? t("ui.common.researcher")}
                  <span className="block text-xs font-normal">{user.email}</span>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem disabled={signingOut} onClick={() => {
                  setSigningOut(true);
                  void logout().then(() => router.push("/login")).finally(() => setSigningOut(false));
                }}>
                  <LogOutIcon className="size-4" />
                  {t(signingOut ? "ui.common.signingOut" : "ui.common.signOut")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!preferenceSaved && (
            <span role="status" className="entry-help">
              {t("locale.preferenceNotSaved")}
            </span>
          )}
        </div>
      </header>
    </>
  );
}
