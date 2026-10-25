import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "long" | "short" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** 加载中：禁用并阻止重复提交 */
  loading?: boolean;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  type = "button",
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = ["btn", className].filter(Boolean).join(" ");
  return (
    <button
      {...rest}
      type={type}
      className={classes}
      data-variant={variant}
      data-size={size}
      disabled={disabled || loading}
      aria-busy={loading ? true : undefined}
    >
      {children}
    </button>
  );
}
