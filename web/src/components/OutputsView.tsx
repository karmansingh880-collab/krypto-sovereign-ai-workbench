import { useMemo, useState } from "react";
import { Download, FileArchive, FileText, FileType2 } from "lucide-react";
import { tasks } from "../api/client";
import type { OutputFile, Task } from "../api/types";
import { Lightbox } from "./Lightbox";

const GALLERY_PAGE = 12;

function DocIcon({ file }: { file: OutputFile }) {
  if (file.kind === "pdf") return <FileType2 size={16} aria-hidden />;
  if (file.kind === "docx") return <FileText size={16} aria-hidden />;
  return <FileArchive size={16} aria-hidden />;
}

function labelOf(file: OutputFile): string {
  const isNote = file.kind === "docx" && file.name.startsWith("approval_note_");
  return isNote ? "Download approval note (.docx)" : file.name;
}

/** Download buttons for documents, plus a gallery (with a full-size viewer) for images. */
export function OutputsView({ task }: { task: Task }) {
  const [shown, setShown] = useState(GALLERY_PAGE);
  const [viewer, setViewer] = useState<number | null>(null);

  const documents = task.outputs.filter((f) => f.kind !== "image");
  const images = useMemo(
    () => task.outputs.filter((f) => f.kind === "image").map((f) => ({ file: f, url: tasks.fileUrl(task.id, f.index) })),
    [task.outputs, task.id],
  );
  if (documents.length === 0 && images.length === 0) return null;

  return (
    <section className="outputs" aria-label="Files">
      {documents.length > 0 && (
        <div className="downloads">
          {documents.map((file) => (
            <a
              key={file.index}
              className={`download kind-${file.kind}`}
              href={tasks.fileUrl(task.id, file.index)}
              download={file.name}
            >
              <DocIcon file={file} />
              <span>{labelOf(file)}</span>
              <Download size={15} aria-hidden />
            </a>
          ))}
        </div>
      )}

      {images.length > 0 && (
        <>
          <h3 className="section-title">
            Images <span className="muted">({images.length})</span>
          </h3>
          <div className="gallery" data-testid="gallery">
            {images.slice(0, shown).map(({ file, url }, index) => (
              <figure key={file.index}>
                <button type="button" className="thumb" onClick={() => setViewer(index)} aria-label={`Open ${file.name}`}>
                  <img src={url} alt={file.name} loading="lazy" />
                </button>
                <figcaption>
                  <span className="name" title={file.name}>{file.name}</span>
                  <a href={url} download={file.name}>Download</a>
                </figcaption>
              </figure>
            ))}
          </div>
          {images.length > shown && (
            <button type="button" className="btn ghost small" onClick={() => setShown(images.length)}>
              Show all {images.length} images
            </button>
          )}
        </>
      )}

      {viewer !== null && (
        <Lightbox
          images={images.map(({ file, url }) => ({ url, name: file.name }))}
          index={viewer}
          onIndex={setViewer}
          onClose={() => setViewer(null)}
        />
      )}
    </section>
  );
}
