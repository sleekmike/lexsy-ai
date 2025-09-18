'use client';

import { useCallback, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import clsx from 'clsx';

type Props = {
  onFile: (file: File) => Promise<void> | void;
  accept?: string;
  disabled?: boolean;
};

export default function Dropzone({ onFile, accept = '.docx', disabled }: Props) {
  const [isOver, setIsOver] = useState(false);
  const [filename, setFilename] = useState<string>('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  const onFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const f = files[0];
    if (accept && !f.name.toLowerCase().endsWith(accept.replace('.', ''))) {
      // allow if accept is .docx but file endswith .docx (handles simple case)
      if (!f.name.toLowerCase().endsWith('docx')) return;
    }
    setFilename(f.name);
    await onFile(f);
  }, [onFile, accept]);

  return (
    <div
      className={clsx(
        'rounded-2xl border-2 border-dashed transition p-6 cursor-pointer',
        isOver ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50',
        disabled && 'opacity-60 cursor-not-allowed'
      )}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setIsOver(true); }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        if (disabled) return;
        setIsOver(false);
        onFiles(e.dataTransfer.files);
      }}
      onClick={() => !disabled && inputRef.current?.click()}
      role="button"
      aria-disabled={disabled}
    >
      <div className="flex items-center gap-3">
        <div className="h-10 w-10 rounded-xl bg-primary/90 grid place-items-center shadow-soft">
          <Upload className="h-5 w-5 text-white" />
        </div>
        <div>
          <div className="text-sm font-medium">Drop your SAFE (.docx) or click to choose</div>
          <div className="text-xs text-muted-foreground">
            {filename ? `Selected: ${filename}` : 'Your document stays private to your session.'}
          </div>
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => onFiles(e.target.files)}
        disabled={disabled}
      />
    </div>
  );
}
