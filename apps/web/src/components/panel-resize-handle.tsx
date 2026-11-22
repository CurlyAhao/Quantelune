"use client";

import { useRef, type PointerEvent } from "react";
import { useLanguage } from "@/providers/language-provider";

/** Live layout stays local; persist only the completed gesture. */
export function PanelResizeHandle({
  width,
  onResize,
  onCommit,
}: {
  width: number;
  onResize: (width: number) => void;
  onCommit: (width: number) => void;
}) {
  const { locale } = useLanguage();
  const drag = useRef<{ x: number; width: number; latest: number } | null>(
    null,
  );
  const clamp = (value: number) =>
    Math.round(Math.max(280, Math.min(420, value)));
  function finish(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    const value = drag.current.latest;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    onCommit(value);
  }
  return (
    <div
      role="separator"
      aria-label={
        locale === "zh-CN" ? "调整右侧面板宽度" : "Resize right panel"
      }
      aria-orientation="vertical"
      aria-valuemin={280}
      aria-valuemax={420}
      aria-valuenow={width}
      tabIndex={0}
      className="panel-resize-handle"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { x: event.clientX, width, latest: width };
      }}
      onPointerMove={(event) => {
        if (!drag.current) return;
        const next = clamp(drag.current.width + drag.current.x - event.clientX);
        drag.current.latest = next;
        onResize(next);
      }}
      onPointerUp={finish}
      onPointerCancel={finish}
      onLostPointerCapture={(event) => {
        if (drag.current) finish(event);
      }}
      onKeyDown={(event) => {
        const next =
          event.key === "ArrowLeft"
            ? width + 16
            : event.key === "ArrowRight"
              ? width - 16
              : event.key === "Home"
                ? 280
                : event.key === "End"
                  ? 420
                  : null;
        if (next === null) return;
        event.preventDefault();
        onResize(clamp(next));
        onCommit(clamp(next));
      }}
    >
      <span aria-hidden />
    </div>
  );
}
