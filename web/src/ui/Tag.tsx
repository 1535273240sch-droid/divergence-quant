import type { HTMLAttributes } from "react";

export type TagTone = "up" | "down" | "warn" | "neutral";

export interface TagProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: TagTone;
}

/** 状态标签：12% 底 + 24% 边框 + 同色字，禁止高饱和实心色块 */
export function Tag({ tone = "neutral", className, children, ...rest }: TagProps) {
  const classes = ["tag", className].filter(Boolean).join(" ");
  return (
    <span {...rest} className={classes} data-tone={tone}>
      {children}
    </span>
  );
}
