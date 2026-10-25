import { type InputHTMLAttributes, forwardRef, useId } from "react";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  label: string;
  /** 数字输入：右对齐、等宽 */
  numeric?: boolean;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, numeric = false, error, id, className, ...rest },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const errorId = `${inputId}-error`;
  return (
    <div className="field">
      <label className="field-label" htmlFor={inputId}>
        {label}
      </label>
      <input
        {...rest}
        ref={ref}
        id={inputId}
        className={["input", numeric ? "num" : "", className].filter(Boolean).join(" ")}
        data-numeric={numeric ? "true" : undefined}
        inputMode={numeric ? "decimal" : rest.inputMode}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error ? (
        <p id={errorId} className="field-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
});
