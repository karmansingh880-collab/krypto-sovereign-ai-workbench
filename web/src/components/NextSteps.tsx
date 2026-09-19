import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart3, FileText, FileType2, Loader2, MessageSquarePlus, Send } from "lucide-react";
import { ApiError, tasks } from "../api/client";
import type { OutputFormat, Task } from "../api/types";
import { useTasks } from "../context/TasksContext";
import { useToast } from "../context/ToastContext";

const FORMATS: { value: OutputFormat; label: string; icon: typeof FileText }[] = [
  { value: "docx", label: "Word", icon: FileText },
  { value: "pdf", label: "PDF", icon: FileType2 },
  { value: "image", label: "Image", icon: BarChart3 },
];

const FOLLOW_UPS = ["Explain that in more detail", "Give the key points as a list", "Summarize it in one sentence"];

/** Save this run's answer as a file, or keep going with a follow-up question. */
export function NextSteps({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const { toast } = useToast();
  const { refresh } = useTasks();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<OutputFormat | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");

  const save = async (format: OutputFormat) => {
    setBusy(format);
    setError("");
    try {
      const { output, created } = await tasks.export(task.id, format);
      onChanged(); // the new file now appears in the list of files
      // Start the download of the file that was just made (or that already existed).
      const link = document.createElement("a");
      link.href = tasks.fileUrl(task.id, output.index);
      link.download = output.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast(created ? `Saved as ${output.name}` : `${output.name} is ready`, "success");
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not save the file.";
      setError(message);
      toast(message, "error");
    } finally {
      setBusy(null);
    }
  };

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();
    const goal = question.trim();
    if (!goal || asking) return;
    setAsking(true);
    setError("");
    try {
      const created = await tasks.followUp(task.id, goal);
      void refresh();
      toast("Follow-up started", "success");
      navigate(`/task/${created.id}`);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Could not start the follow-up.";
      setError(message);
      setAsking(false);
    }
  };

  const subject = task.files.length > 0 ? "document" : "answer";

  return (
    <section className="next-steps" aria-label="What next?" data-testid="next-steps">
      <h3>
        <MessageSquarePlus size={17} aria-hidden /> What next?
      </h3>
      <p className="muted small">Save this as a file, or keep going with a follow-up about the same {subject}.</p>

      <div className="next-save" role="group" aria-label="Save as">
        {FORMATS.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            className="btn ghost"
            data-format={value}
            disabled={busy !== null}
            onClick={() => void save(value)}
          >
            {busy === value ? <Loader2 size={15} className="spin" aria-hidden /> : <Icon size={15} aria-hidden />}
            Download as {label}
          </button>
        ))}
      </div>

      <form className="next-ask" onSubmit={ask}>
        <input
          type="text"
          value={question}
          placeholder={`Ask a follow-up about the same ${subject}…`}
          aria-label="Follow-up question"
          maxLength={4000}
          disabled={asking}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" className="btn primary" disabled={asking || !question.trim()}>
          {asking ? <Loader2 size={15} className="spin" aria-hidden /> : <Send size={15} aria-hidden />}
          Ask
        </button>
      </form>
      <div className="suggestions">
        {FOLLOW_UPS.map((text) => (
          <button key={text} type="button" className="chip" disabled={asking} onClick={() => setQuestion(text)}>
            {text}
          </button>
        ))}
      </div>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
