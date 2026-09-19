import { useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { BarChart3, FileText, FileType2, Loader2, Play, Sparkles } from "lucide-react";
import type { OutputFormat, SourceKind } from "../api/types";
import { ApiError, type UploadHandle } from "../api/client";
import { useStartTask } from "../hooks/useStartTask";
import { addFiles, fileGroup } from "../utils/files";
import { Dropzone } from "./Dropzone";

export const DEFAULT_GOAL =
  "Read the inspection report, extract wall-thickness readings, calculate corrosion rate and remaining life " +
  "for each location, cite the relevant SOP, and generate an approval note.";

const FORMAT_OPTIONS: { value: OutputFormat; label: string; hint: string; icon: typeof FileText }[] = [
  { value: "docx", label: "Word", hint: ".docx file", icon: FileText },
  { value: "pdf", label: "PDF", hint: ".pdf file", icon: FileType2 },
  { value: "image", label: "Image / chart", hint: "figures or a chart", icon: BarChart3 },
];

const SOURCES: { value: SourceKind; label: string }[] = [
  { value: "upload", label: "Upload files" },
  { value: "sample", label: "Sample report" },
  { value: "none", label: "No file" },
];

export interface ComposerPreset {
  goal: string;
  formats: OutputFormat[];
  source?: SourceKind;
}

function suggestionsFor(source: SourceKind, files: File[]): string[] {
  if (source === "sample") {
    return [
      DEFAULT_GOAL,
      "Create a bar chart of the current thickness of each location",
      "Make a chart comparing previous and current thickness",
    ];
  }
  if (source === "upload" && files.length > 0) {
    const groups = files.map((f) => fileGroup(f.name));
    const list = ["Summarize this document in 5 bullet points", "List the key points", "What are the main conclusions?"];
    if (groups.some((g) => g === "pdf" || g === "word")) {
      list.push("Give me all the images", "Create a Word file and a PDF of the summary");
    }
    if (groups.includes("image")) list.push("Extract all the text", "Create a bar chart of the numbers in the table");
    return list.slice(0, 5);
  }
  return ["Explain what corrosion rate means and how it is calculated", "Write a short checklist for pipe inspections"];
}

export function Composer({ preset }: { preset?: ComposerPreset }) {
  const start = useStartTask();
  const [source, setSource] = useState<SourceKind>(preset?.source ?? "sample");
  const [goal, setGoal] = useState(preset?.goal ?? DEFAULT_GOAL);
  const [files, setFiles] = useState<File[]>([]);
  const [formats, setFormats] = useState<OutputFormat[]>(preset?.formats ?? []);
  const [error, setError] = useState("");
  const [fileErrors, setFileErrors] = useState<string[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const upload = useRef<UploadHandle | null>(null);
  const busy = progress !== null;

  const suggestions = useMemo(() => suggestionsFor(source, files), [source, files]);

  const changeSource = (next: SourceKind) => {
    setSource(next);
    setError("");
    // The prefilled sample instruction only makes sense with the sample report.
    if (next !== "sample" && goal === DEFAULT_GOAL) setGoal("");
    if (next === "sample" && goal.trim() === "") setGoal(DEFAULT_GOAL);
  };

  const onAdd = (incoming: File[]) => {
    const { accepted, errors } = addFiles(files, incoming);
    setFiles(accepted);
    setFileErrors(errors);
    if (accepted.length > files.length) setError("");
  };

  const toggleFormat = (value: OutputFormat) =>
    setFormats((current) => (current.includes(value) ? current.filter((f) => f !== value) : [...current, value]));

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy) return;
    const text = goal.trim();
    if (!text) return setError("Tell the agent what to do.");
    if (source === "upload" && files.length === 0) return setError("Attach at least one file, or choose “No file”.");

    setError("");
    setProgress(0);
    upload.current = start({ goal: text, source, files, formats }, (fraction) => setProgress(fraction));
    try {
      await upload.current.promise; // on success the app navigates to the new run
    } catch (err) {
      if (!(err instanceof ApiError && err.message === "Upload cancelled.")) {
        setError(err instanceof Error ? err.message : "Could not start the task.");
      }
      setProgress(null);
    }
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) void submit();
  };

  const percent = progress === null ? 0 : Math.round(progress * 100);

  return (
    <form className="composer card" onSubmit={submit} noValidate>
      <div className="composer-head">
        <Sparkles size={20} aria-hidden />
        <div>
          <h1>New task</h1>
          <p className="muted">Give the agent an instruction, and optionally the documents it should work from.</p>
        </div>
      </div>

      <label htmlFor="goal" className="field-label">What should the agent do?</label>
      <textarea
        id="goal"
        rows={4}
        value={goal}
        disabled={busy}
        placeholder="e.g. Summarize this document in 5 bullet points, or: give me all the images"
        onChange={(e) => setGoal(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="suggestions" aria-label="Suggestions">
        {suggestions.map((text) => (
          <button key={text} type="button" className="chip" disabled={busy} onClick={() => setGoal(text)} title={text}>
            {text.length > 46 ? `${text.slice(0, 45)}…` : text}
          </button>
        ))}
      </div>

      <fieldset className="source" disabled={busy}>
        <legend className="field-label">Input</legend>
        <div className="segmented wide" role="radiogroup" aria-label="Input source">
          {SOURCES.map((s) => (
            <label key={s.value} className={source === s.value ? "active" : ""}>
              <input
                type="radio"
                name="source"
                value={s.value}
                checked={source === s.value}
                onChange={() => changeSource(s.value)}
              />
              {s.label}
            </label>
          ))}
        </div>

        {source === "upload" && (
          <>
            <Dropzone files={files} onAdd={onAdd} onRemove={(i) => setFiles(files.filter((_, n) => n !== i))} disabled={busy} />
            {fileErrors.length > 0 && (
              <ul className="field-errors" role="alert">
                {fileErrors.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
          </>
        )}
        {source === "sample" && (
          <p className="muted source-note">Uses the bundled Unit 4 piping inspection report (an image with a readings table).</p>
        )}
      </fieldset>

      <fieldset className="formats" disabled={busy}>
        <legend className="field-label">
          Also create <span className="muted">(optional)</span>
        </legend>
        <div className="format-grid">
          {FORMAT_OPTIONS.map(({ value, label, hint, icon: Icon }) => (
            <label key={value} className={`format-card${formats.includes(value) ? " active" : ""}`}>
              <input
                type="checkbox"
                name="format"
                value={value}
                checked={formats.includes(value)}
                onChange={() => toggleFormat(value)}
              />
              <Icon size={18} aria-hidden />
              <span>
                <strong>{label}</strong>
                <span className="muted small">{hint}</span>
              </span>
            </label>
          ))}
        </div>
        <p className="muted small">
          Or just ask for it: “make a PDF of this summary”, “give me the graph image”. Images are extracted from your
          document or drawn as charts (no photo-realistic AI art).
        </p>
      </fieldset>

      {error && (
        <p className="form-error" role="alert">{error}</p>
      )}

      <div className="composer-actions">
        <button type="submit" className="btn primary large" disabled={busy}>
          {busy ? <Loader2 size={18} className="spin" aria-hidden /> : <Play size={18} aria-hidden />}
          {busy ? (percent < 100 ? `Uploading ${percent}%` : "Starting…") : "Run agent"}
        </button>
        {busy && (
          <button type="button" className="btn ghost" onClick={() => upload.current?.abort()}>
            Cancel
          </button>
        )}
        <span className="muted small hint-keys">Ctrl + Enter to run</span>
      </div>
      {busy && (
        <div className="upload-bar" aria-hidden>
          <span style={{ width: `${percent}%` }} />
        </div>
      )}
    </form>
  );
}
