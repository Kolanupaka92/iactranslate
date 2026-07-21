"use client";

import { useCallback, useRef, useState } from "react";

const ACCEPTED = [".xlsx", ".xls", ".xlsm", ".csv"];
const MAX_MB = 25;

export default function UploadDropzone({
  onFile,
  disabled,
  fileName,
}: {
  onFile: (file: File) => void;
  disabled?: boolean;
  fileName?: string | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const accept = useCallback(
    (file: File) => {
      setLocalError(null);
      const suffix = `.${file.name.split(".").pop()?.toLowerCase()}`;
      if (!ACCEPTED.includes(suffix)) {
        setLocalError(
          `Unsupported file type "${suffix}" — expected an RVTools .xlsx or a VMware .csv export.`,
        );
        return;
      }
      if (file.size > MAX_MB * 1024 * 1024) {
        setLocalError(`File is larger than the ${MAX_MB} MB limit.`);
        return;
      }
      onFile(file);
    },
    [onFile],
  );

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (disabled) return;
          const file = e.dataTransfer.files?.[0];
          if (file) accept(file);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
          disabled ? "cursor-not-allowed opacity-50" : ""
        } ${
          dragging
            ? "border-emerald-500 bg-emerald-500/10"
            : "border-neutral-300 hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
        }`}
      >
        <p className="font-medium">
          {fileName ? `Uploaded: ${fileName}` : "Drop your discovery export here"}
        </p>
        <p className="mt-1 text-sm opacity-70">
          Any inventory export (.xlsx / .csv) — max {MAX_MB} MB. Click to browse.
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          className="hidden"
          disabled={disabled}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) accept(file);
            e.target.value = "";
          }}
        />
      </div>
      {localError && (
        <p className="mt-2 text-sm text-red-600 dark:text-red-400">{localError}</p>
      )}
    </div>
  );
}
