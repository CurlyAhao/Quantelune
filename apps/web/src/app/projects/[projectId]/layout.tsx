/**
 * Emits a desktop shell route during static export. Tauri serves the exported
 * app as an SPA and resolves the real project id from client-side navigation.
 */
export function generateStaticParams() {
  return [{ projectId: "__desktop_project__" }];
}

export default function ProjectLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
