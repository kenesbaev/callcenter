import { FlaskConical } from "lucide-react";
import { StatusBadge } from "@teamora/ui";

export default function LanguageLabPage() {
  return (
    <>
      <div className="page-heading">
        <h1>Language Lab</h1>
        <p>A quality-evaluation surface for STT and TTS provider tests.</p>
      </div>
      <section className="panel">
        <div className="row-between">
          <div>
            <h2>Language readiness</h2>
            <p className="panel-subtitle">
              Readiness labels are product policy, not unverified provider
              claims.
            </p>
          </div>
          <FlaskConical aria-hidden="true" size={24} />
        </div>
        <div className="language-readiness-table">
          <div>
            <strong>Русский</strong>
            <span>ru</span>
            <StatusBadge tone="success">Production target</StatusBadge>
          </div>
          <div>
            <strong>English</strong>
            <span>en</span>
            <StatusBadge tone="success">Production target</StatusBadge>
          </div>
          <div>
            <strong>O‘zbekcha</strong>
            <span>uz</span>
            <StatusBadge tone="warning">
              Beta — quality validation required
            </StatusBadge>
          </div>
          <div>
            <strong>Qaraqalpaqsha</strong>
            <span>kaa</span>
            <StatusBadge tone="warning">Experimental</StatusBadge>
          </div>
        </div>
        <div className="unavailable-action">
          <StatusBadge>Coming later</StatusBadge>
          <p>
            Audio upload, provider comparison, manual correction, scoring, and
            TTS playback remain disabled until the transcription and synthesis
            backends are implemented.
          </p>
          <button
            className="tv-button tv-button-secondary"
            disabled
            type="button"
          >
            Upload test audio — unavailable
          </button>
        </div>
      </section>
    </>
  );
}
