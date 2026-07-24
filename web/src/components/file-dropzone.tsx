"use client";

import { useCallback, useState } from "react";
import { cn } from "@/lib/utils";

interface FileDropzoneProps {
  accept?: string[];
  onFileSelect: (file: File) => void;
  selectedFile: File | null;
}

export function FileDropzone({
  accept,
  onFileSelect,
  selectedFile,
}: FileDropzoneProps) {
  const [isDragging, setIsDragging] = useState(false);

  const acceptStr = accept?.join(",") || "";

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) onFileSelect(file);
    },
    [onFileSelect]
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handleClick = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    if (acceptStr) input.accept = acceptStr;
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) onFileSelect(file);
    };
    input.click();
  }, [acceptStr, onFileSelect]);

  return (
    <div
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 transition-colors",
        isDragging
          ? "border-horizon-blue bg-horizon-blue/5"
          : "border-border hover:border-ground"
      )}
    >
      <svg
        className="mb-3 h-8 w-8 text-ground"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
        />
      </svg>

      {selectedFile ? (
        <div className="text-center">
          <p className="text-sm font-medium text-night-blue">
            {selectedFile.name}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {(selectedFile.size / 1024).toFixed(1)} KB — click or drop to
            replace
          </p>
        </div>
      ) : (
        <div className="text-center">
          <p className="text-sm font-medium text-night-blue">
            Drop file here or click to browse
          </p>
          {accept && (
            <p className="mt-1 text-xs text-muted-foreground">
              Accepts: {accept.join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
