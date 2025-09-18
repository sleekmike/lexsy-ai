'use client';

import Dropzone from '@/components/Dropzone';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Upload, FileDown, MessageSquareMore, Wand2, CheckCircle2, AlertCircle } from 'lucide-react';

type Placeholder = {
  key: string;
  label: string;
  type: string;
  occurrences: number;
  value?: string | null;
};

type UploadResponse = {
  session_id: string;
  placeholders: Placeholder[];
};

type AskNext = {
  key: string;
  label: string;
  type: string;
  question: string;
  examples?: string[];
  suggestion?: string | null;
};

type AskResponse = {
  next: AskNext | null;
  remaining: number;
  missing_keys: string[];
};

type FillResponse = {
  ok: boolean;
  remaining: number;
  placeholders: Placeholder[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

export default function Page() {
  const [sessionId, setSessionId] = useState<string>('');
  const [placeholders, setPlaceholders] = useState<Placeholder[]>([]);
  const [uploading, setUploading] = useState(false);
  const [asking, setAsking] = useState(false);
  const [filling, setFilling] = useState(false);
  const [nextQ, setNextQ] = useState<AskNext | null>(null);
  const [llmSource, setLlmSource] = useState<string | null>(null);
  const [error, setError] = useState<string>('');
  const [info, setInfo] = useState<string>('');
  const [valueInput, setValueInput] = useState<string>('');
  const autoDownloadedRef = useRef(false);

  const hasSession = !!sessionId;

  async function onUploadFile(file: File) {
    setError('');
    setInfo('');
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(`${API_BASE}/upload`, { method: 'POST', body: form });
      if (!res.ok) throw new Error(`Upload failed (${res.status})`);
      const data = (await res.json()) as UploadResponse;
      setSessionId(data.session_id);
      setPlaceholders(data.placeholders);
      setNextQ(null);
      setValueInput('');
      setInfo('Upload successful. You can ask to fill the next field or pick one below.');
      autoDownloadedRef.current = false; // reset for new session
      setLlmSource(null);
    } catch (e: any) {
      setError(e?.message ?? 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  async function onAskNext() {
    if (!sessionId) return;
    setError('');
    setAsking(true);
    setValueInput('');
    try {
      const res = await fetch(`${API_BASE}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const source = res.headers.get('X-Ask-Source');
      setLlmSource(source);
      if (!res.ok) throw new Error(`Ask failed (${res.status})`);
      const data = (await res.json()) as AskResponse;
      setNextQ(data.next);
      if (!data.next) setInfo('All fields are filled 🎉');
    } catch (e: any) {
      setError(e?.message ?? 'Ask failed');
    } finally {
      setAsking(false);
    }
  }

  async function onFill(key: string, value: string) {
    if (!sessionId) return;
    if (!value.trim()) {
      setError('Please provide a value.');
      return;
    }
    setError('');
    setFilling(true);
    try {
      const res = await fetch(`${API_BASE}/fill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, key, value }),
      });
      if (!res.ok) throw new Error(`Fill failed (${res.status})`);
      const data = (await res.json()) as FillResponse;
      setPlaceholders(data.placeholders);
      setValueInput('');
      setInfo('Saved.');
      // Refresh next question
      await onAskNext();
    } catch (e: any) {
      setError(e?.message ?? 'Fill failed');
    } finally {
      setFilling(false);
    }
  }

  function onDownload() {
    if (!sessionId) return;
    const url = `${API_BASE}/download?session_id=${encodeURIComponent(sessionId)}`;
    window.open(url, '_blank');
  }

  const missing = useMemo(() => placeholders.filter(p => !p.value), [placeholders]);

  // ✅ Only show "all filled" when a session exists AND there are placeholders AND none are missing
  const allFilled = hasSession && placeholders.length > 0 && missing.length === 0;

  // Auto-download once when everything is filled
  useEffect(() => {
    if (allFilled && !autoDownloadedRef.current) {
      autoDownloadedRef.current = true;
      onDownload();
    }
  }, [allFilled]);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Left: Upload + Ask */}
      <section className="card p-6 lg:col-span-2">
        <div className="flex items-center gap-3 mb-4">
          <div className="h-9 w-9 rounded-xl bg-primary/90 grid place-items-center">
            <Upload className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">Upload SAFE Document</h1>
            <p className="text-sm text-muted-foreground">.docx only — it remains private to your session.</p>
          </div>
        </div>

        <div className="space-y-3">
          <Dropzone onFile={onUploadFile} accept=".docx" disabled={uploading} />
          {hasSession && (
            <div className="flex items-center gap-2">
              <a
                href={`${API_BASE}/preview?session_id=${encodeURIComponent(sessionId)}`}
                target="_blank"
                rel="noreferrer"
                className="btn-muted"
              >
                Preview
              </a>
              <button className="btn-muted" onClick={onDownload}>
                <FileDown className="h-4 w-4" />
                Download
              </button>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-4 flex items-center gap-2 text-destructive">
            <AlertCircle className="h-4 w-4" /> <span className="text-sm">{error}</span>
          </div>
        )}
        {info && !error && (
          <div className="mt-4 flex items-center gap-2 text-success">
            <CheckCircle2 className="h-4 w-4" /> <span className="text-sm">{info}</span>
          </div>
        )}

        {/* Big CTA when ready */}
        {allFilled && (
          <div className="mt-4">
            <button className="btn-primary" onClick={onDownload}>
              Download filled .docx
            </button>
          </div>
        )}

        {/* Ask / Fill */}
        <div className="mt-8">
          <div className="flex items-center gap-3 mb-3">
            <div className="h-9 w-9 rounded-xl bg-primary/90 grid place-items-center">
              <MessageSquareMore className="h-5 w-5 text-white" />
            </div>
            <div>
              <h2 className="text-base font-semibold">Conversational Fill</h2>
              <p className="text-sm text-muted-foreground">Ask for the next missing field and answer it inline.</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              className="btn-muted"
              onClick={onAskNext}
              disabled={!hasSession || asking || missing.length === 0}
            >
              <Wand2 className="h-4 w-4" />
              {asking ? 'Thinking…' : 'Ask Next'}
            </button>
            {llmSource && (
              <span className="text-xs text-muted-foreground">
                Source: {llmSource === 'openai' ? 'GPT-4o' : 'Deterministic'}
              </span>
            )}
            {allFilled && (
              <span className="text-xs text-success ml-2">All fields are filled 🎉</span>
            )}
          </div>

          {nextQ && (
            <div className="mt-4 p-4 rounded-xl border border-border bg-muted/50">
              <div className="text-sm text-muted-foreground mb-1">{nextQ.label}</div>
              <div className="text-base font-medium mb-2">{nextQ.question}</div>
              {nextQ.examples && nextQ.examples.length > 0 && (
                <div className="text-xs text-muted-foreground mb-2">
                  Examples: {nextQ.examples.slice(0, 3).join(' · ')}
                </div>
              )}
              {!!nextQ.suggestion && (
                <div className="text-xs text-muted-foreground mb-2">Suggestion: {nextQ.suggestion}</div>
              )}
              <div className="flex items-center gap-2">
                <input
                  className="input"
                  placeholder="Type your answer…"
                  value={valueInput}
                  onChange={(e) => setValueInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') onFill(nextQ.key, valueInput);
                  }}
                />
                <button
                  className="btn-primary"
                  onClick={() => onFill(nextQ.key, valueInput)}
                  disabled={filling}
                >
                  {filling ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Right: Placeholders list */}
      <aside className="card p-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-semibold">Placeholders</h2>
          <span className="text-xs text-muted-foreground">
            {placeholders.length === 0 ? '—' : `${placeholders.filter(p => p.value).length}/${placeholders.length} filled`}
          </span>
        </div>

        <div className="space-y-3 max-h-[70vh] overflow-auto pr-1">
          {placeholders.map((p) => (
            <div key={p.key} className="rounded-xl border border-border p-3 bg-white/50 dark:bg-black/20">
              <div className="text-sm font-medium">{p.key}</div>
              <div className="text-xs text-muted-foreground">
                {p.label} — {p.type}{p.occurrences > 1 ? ` · ${p.occurrences}×` : ''}
              </div>
              <div className="mt-1 text-sm">
                {p.value ? (
                  <span className="inline-block px-2 py-0.5 rounded-lg bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200">
                    {p.value}
                  </span>
                ) : (
                  <span className="text-muted-foreground">Not set</span>
                )}
              </div>
              {p.value && (
                <div className="mt-2 flex gap-2">
                  <button
                    className="btn-muted text-xs"
                    onClick={() => {
                      setNextQ({
                        key: p.key,
                        label: p.label,
                        type: p.type,
                        question: `Update value for ${p.label}`,
                        examples: [],
                        suggestion: p.value || null,
                      });
                      setValueInput(p.value || '');
                    }}
                  >
                    Edit
                  </button>
                </div>
              )}
            </div>
          ))}

          {placeholders.length === 0 && (
            <div className="text-sm text-muted-foreground">Upload a .docx to extract fields.</div>
          )}
        </div>
      </aside>
    </div>
  );
}