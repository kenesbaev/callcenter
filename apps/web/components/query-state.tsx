export function QueryError({
  title,
  error,
  retry,
}: {
  title: string;
  error: Error;
  retry: () => void;
}) {
  return (
    <div className="error-state panel">
      <div>
        <h2>{title}</h2>
        <p>{error.message}</p>
        <button
          className="tv-button tv-button-secondary"
          onClick={retry}
          type="button"
        >
          Повторить
        </button>
      </div>
    </div>
  );
}

export function SectionSkeleton() {
  return (
    <div aria-label="Загрузка" className="section-skeleton">
      <div className="panel skeleton">Загрузка</div>
      <div className="panel skeleton">Загрузка</div>
      <div className="panel skeleton">Загрузка</div>
    </div>
  );
}
