import { useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { File as FileIcon, FileText, Image as ImageIcon, UploadCloud, X } from "lucide-react";
import { ACCEPT_ATTR, MAX_FILES, MAX_FILE_BYTES, fileGroup } from "../utils/files";
import { formatBytes } from "../utils/format";

interface Props {
  files: File[];
  onAdd: (incoming: File[]) => void;
  onRemove: (index: number) => void;
  disabled?: boolean;
}

function FileGlyph({ name }: { name: string }) {
  const group = fileGroup(name);
  if (group === "image") return <ImageIcon size={18} />;
  if (group === "other") return <FileIcon size={18} />;
  return <FileText size={18} />;
}

/** Drag-and-drop (or click) file picker with a removable list of what is attached. */
export function Dropzone({ files, onAdd, onRemove, disabled }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const open = () => !disabled && input.current?.click();
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    if (!disabled) onAdd(Array.from(event.dataTransfer.files));
  };

  return (
    <div className="dropzone-wrap">
      <div
        className={`dropzone${over ? " over" : ""}${disabled ? " disabled" : ""}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label="Attach files: drop them here or press Enter to browse"
        onClick={open}
        onKeyDown={onKey}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <UploadCloud size={26} aria-hidden />
        <p>
          <strong>Drop files here</strong> or click to browse
        </p>
        <p className="muted small">
          PDF, Word (.docx), text, or images · up to {MAX_FILES} files · {formatBytes(MAX_FILE_BYTES)} each
        </p>
        <input
          ref={input}
          data-testid="file-input"
          type="file"
          multiple
          hidden
          accept={ACCEPT_ATTR}
          disabled={disabled}
          onChange={(e) => {
            onAdd(Array.from(e.target.files ?? []));
            e.target.value = ""; // allow picking the same file again after removing it
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="file-list" aria-label="Attached files">
          {files.map((file, index) => (
            <li key={`${file.name}-${file.size}-${index}`}>
              <FileGlyph name={file.name} />
              <span className="file-name" title={file.name}>{file.name}</span>
              <span className="muted small">{formatBytes(file.size)}</span>
              <button
                type="button"
                className="icon-btn"
                aria-label={`Remove ${file.name}`}
                disabled={disabled}
                onClick={() => onRemove(index)}
              >
                <X size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
