"use client";

import { useCallback, useRef, useState, type KeyboardEvent } from "react";

interface OtpFormProps {
  length?: number;
  onComplete: (code: string) => void;
  disabled?: boolean;
}

export function OtpForm({ length = 6, onComplete, disabled }: OtpFormProps) {
  const [values, setValues] = useState<string[]>(Array(length).fill(""));
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  const handleChange = useCallback(
    (index: number, value: string) => {
      if (!/^\d*$/.test(value)) return;

      const newValues = [...values];
      newValues[index] = value.slice(-1);
      setValues(newValues);

      if (value && index < length - 1) {
        inputRefs.current[index + 1]?.focus();
      }

      const code = newValues.join("");
      if (code.length === length && newValues.every((v) => v !== "")) {
        onComplete(code);
      }
    },
    [values, length, onComplete]
  );

  const handleKeyDown = useCallback(
    (index: number, e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Backspace" && !values[index] && index > 0) {
        inputRefs.current[index - 1]?.focus();
      }
    },
    [values]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      e.preventDefault();
      const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, length);
      if (!pasted) return;

      const newValues = [...values];
      for (let i = 0; i < pasted.length; i++) {
        newValues[i] = pasted[i];
      }
      setValues(newValues);

      const focusIndex = Math.min(pasted.length, length - 1);
      inputRefs.current[focusIndex]?.focus();

      if (pasted.length === length) {
        onComplete(pasted);
      }
    },
    [values, length, onComplete]
  );

  return (
    <div className="flex gap-3 justify-center" onPaste={handlePaste}>
      {values.map((value, index) => (
        <input
          key={index}
          ref={(el) => { inputRefs.current[index] = el; }}
          type="text"
          inputMode="numeric"
          maxLength={1}
          value={value}
          disabled={disabled}
          onChange={(e) => handleChange(index, e.target.value)}
          onKeyDown={(e) => handleKeyDown(index, e)}
          className="h-14 w-12 rounded-md border border-border bg-background text-center text-2xl font-semibold text-foreground outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/30 disabled:opacity-50"
        />
      ))}
    </div>
  );
}
