export function generateStaticParams() {
  return [{ datasetId: "__desktop_dataset__" }];
}

export default function DatasetLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
