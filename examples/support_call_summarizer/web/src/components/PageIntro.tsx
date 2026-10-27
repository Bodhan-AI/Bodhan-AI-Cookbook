interface PageIntroProps {
  /** Align with full-width screens (the result) instead of the centred form column. */
  wide?: boolean;
}

export function PageIntro({ wide = false }: PageIntroProps) {
  return (
    <section className={`page-intro${wide ? " page-intro--wide" : ""}`} aria-labelledby="page-title">
      <h1 id="page-title" className="page-intro__title">
        Support Call Summarizer
      </h1>
      <p className="page-intro__lede">
        Timestamped transcripts and English translations of recorded support calls in Indian languages, ready to
        export to your CRM.
      </p>
      <p className="model-chain">
        <span className="visually-hidden">Pipeline: </span>
        <code className="model-id">indic-transcribe</code>
        <span className="model-chain__step">
          <span className="model-chain__arrow" aria-hidden="true">
            →
          </span>
          <span className="visually-hidden">then</span>
          <code className="model-id">indic-translate</code>
        </span>
        <span className="model-chain__step">
          <span className="model-chain__arrow" aria-hidden="true">
            →
          </span>
          <span className="visually-hidden">then</span>
          <span className="model-chain__out">JSON · CSV</span>
        </span>
      </p>
    </section>
  );
}
