/**
 * Fixed, ambient background: graph-paper rulings with two soft tonal washes.
 * Sits behind every page; pointer-events disabled.
 */
export function BackgroundLayers() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10">
      {/* Base tint + washes */}
      <div className="absolute inset-0 bg-background" />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(60rem 34rem at -8% -12%, oklch(0.62 0.17 255 / 0.09), transparent 62%), radial-gradient(52rem 30rem at 108% 8%, oklch(0.73 0.13 203 / 0.07), transparent 60%), radial-gradient(46rem 30rem at 50% 118%, oklch(0.77 0.16 76 / 0.045), transparent 65%)",
        }}
      />
      {/* Graph paper rulings */}
      <div className="bg-grid-paper absolute inset-0" />
      {/* Vignette to keep edges calm */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(to bottom, transparent 0%, transparent 82%, oklch(0.24 0.035 255 / 0.03) 100%)",
        }}
      />
    </div>
  );
}
