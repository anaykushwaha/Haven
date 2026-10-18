interface ConnectionBannerProps {
  error: string | null;
  hasSnapshot: boolean;
  paused: boolean;
}

/** Explains a lost connection or a paused feed without hiding the last data. */
export function ConnectionBanner({ error, hasSnapshot, paused }: ConnectionBannerProps) {
  if (error !== null) {
    return (
      <div className="banner banner-bad" role="alert">
        {hasSnapshot ? <strong>Showing the last known state. </strong> : null}
        {error}
      </div>
    );
  }
  if (paused) {
    return (
      <div className="banner banner-warn" role="status">
        Live updates are paused.
      </div>
    );
  }
  return null;
}
