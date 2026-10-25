import { type KeyboardEvent, useRef } from "react";

export interface SegmentedOption {
  value: string;
  label: string;
}

export interface SegmentedProps {
  ariaLabel: string;
  options: readonly SegmentedOption[];
  value: string;
  onChange: (value: string) => void;
}

/** 分段选择器（radiogroup）：方向键循环切换，roving tabindex */
export function Segmented({ ariaLabel, options, value, onChange }: SegmentedProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  if (options.length === 0) {
    throw new Error("Segmented 的 options 不能为空");
  }
  const activeIndex = options.findIndex((o) => o.value === value);
  if (activeIndex < 0) {
    throw new Error(`Segmented 的 value "${value}" 不在 options 中`);
  }

  function move(delta: number) {
    const next = (activeIndex + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    }
  }

  return (
    <div className="segmented" role="radiogroup" aria-label={ariaLabel}>
      {options.map((option, index) => {
        const checked = index === activeIndex;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            className="segmented-item"
            onClick={() => {
              if (!checked) onChange(option.value);
            }}
            onKeyDown={onKeyDown}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
