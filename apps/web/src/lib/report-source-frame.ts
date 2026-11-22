import type { ReportBlock, ReportSource } from "./workspace-api";
import type { ResolvedSource } from "./project-research";

export const externalReportFrame = (block: ReportBlock) =>
  block.source_snapshot?.storage === "statforge-report-source-v1";

export function sameReportSource(
  left?: ReportSource | null,
  right?: ReportSource | null,
) {
  return (
    !!left &&
    !!right &&
    left.kind === right.kind &&
    left.id === right.id &&
    left.revision === right.revision &&
    left.sha256 === right.sha256
  );
}

export function displayReportBlock(
  block: ReportBlock,
  source?: ResolvedSource,
): ReportBlock {
  if (!externalReportFrame(block)) return block;
  return {
    ...block,
    source_snapshot:
      source?.available && sameReportSource(block.source, source.reference)
        ? source.payload
        : null,
  };
}
