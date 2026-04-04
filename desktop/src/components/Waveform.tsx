import type { CSSProperties } from "react";
import "./Waveform.css";

/** Matches quickshell/orateur/WaveformPreview.qml */
const BAR_COUNT = 60;
const BAR_WIDTH = 2;
const BAR_SPACING = 1;
const MAX_BAR_HEIGHT = 20;

function padLevels(levels: number[]): number[] {
  const slice = levels.slice(-BAR_COUNT);
  if (slice.length >= BAR_COUNT) return slice;
  return [...Array(BAR_COUNT - slice.length).fill(0), ...slice];
}

interface WaveformProps {
  levels: number[];
}

export function Waveform({ levels }: WaveformProps) {
  const padded = levels.length > 0 ? padLevels(levels) : Array(BAR_COUNT).fill(0);

  return (
    <div
      className="waveform"
      style={
        {
          gap: `${BAR_SPACING}px`,
          "--bar-width": `${BAR_WIDTH}px`,
          "--max-bar-height": `${MAX_BAR_HEIGHT}px`,
        } as CSSProperties
      }
    >
      {padded.map((v, i) => {
        const h = Math.max(2, v * MAX_BAR_HEIGHT);
        return (
          <div
            key={i}
            className="waveform__bar"
            style={{ width: BAR_WIDTH, height: `${h}px` }}
          />
        );
      })}
    </div>
  );
}
