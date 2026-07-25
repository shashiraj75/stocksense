// Validation job-identity view resolution (2026-07 fix).
//
// The backend binds every validation run to an immutable job identity
// (market/universe/horizon). This util is the single decision point for
// whether the currently-selected view may render the active run's live
// progress, or must instead show an explicit "another validation is
// running" banner. Original defect: a US run rendered its progress and
// symbol log under the Nifty 100 tab because the status payload carried
// no identity and the page rendered it unconditionally.

export type ValidationJobIdentity = {
  job_id: string;
  market: string;
  universe_id: string;
  horizon: string;
  status: string;
  processed?: number;
  total?: number;
  [key: string]: unknown;
};

export type ValidationRunStatus = {
  running: boolean;
  progress: number;
  total: number;
  job?: ValidationJobIdentity | null;
};

export type ActiveJobView = {
  /** Render live progress/log under the selected view. */
  showProgressForView: boolean;
  /** The active job when it belongs to a different universe/horizon; null otherwise. */
  foreignJob: ValidationJobIdentity | null;
};

export function resolveActiveJobView(
  status: ValidationRunStatus | undefined,
  selectedUniverse: string,
  selectedHorizon: string,
): ActiveJobView {
  if (status?.running !== true) {
    return { showProgressForView: false, foreignJob: null };
  }
  const job = status.job ?? null;
  if (job == null) {
    // Legacy backend without job identity: the run cannot be attributed to
    // any view — keep it visible rather than hiding a genuinely running job.
    return { showProgressForView: true, foreignJob: null };
  }
  const matches =
    job.universe_id === selectedUniverse && job.horizon === selectedHorizon;
  return {
    showProgressForView: matches,
    foreignJob: matches ? null : job,
  };
}
