import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, Download, X } from "lucide-react";

export interface LightboxImage {
  url: string;
  name: string;
}

interface Props {
  images: LightboxImage[];
  index: number;
  onIndex: (next: number) => void;
  onClose: () => void;
}

/** Full-size image viewer: arrow keys / buttons to move, Escape to close. */
export function Lightbox({ images, index, onIndex, onClose }: Props) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const current = images[index];
  const count = images.length;

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowRight" && count > 1) onIndex((index + 1) % count);
      else if (event.key === "ArrowLeft" && count > 1) onIndex((index - 1 + count) % count);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [index, count, onIndex, onClose]);

  if (!current) return null;

  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={`Image viewer: ${current.name}`} onClick={onClose}>
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <span className="lightbox-title">
          {current.name} <span className="muted">({index + 1} / {count})</span>
        </span>
        <a className="icon-btn" href={current.url} download={current.name} aria-label="Download image" title="Download">
          <Download size={18} />
        </a>
        <button ref={closeButton} type="button" className="icon-btn" aria-label="Close viewer" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {count > 1 && (
        <>
          <button
            type="button"
            className="lightbox-nav prev"
            aria-label="Previous image"
            onClick={(e) => {
              e.stopPropagation();
              onIndex((index - 1 + count) % count);
            }}
          >
            <ChevronLeft size={28} />
          </button>
          <button
            type="button"
            className="lightbox-nav next"
            aria-label="Next image"
            onClick={(e) => {
              e.stopPropagation();
              onIndex((index + 1) % count);
            }}
          >
            <ChevronRight size={28} />
          </button>
        </>
      )}
      <img className="lightbox-img" src={current.url} alt={current.name} onClick={(e) => e.stopPropagation()} />
    </div>
  );
}
