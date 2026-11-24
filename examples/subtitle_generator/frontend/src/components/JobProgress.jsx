import { LoaderCircle } from 'lucide-react'

// The running job's current step and how far along it is.
export default function JobProgress({ job }) {
  return (
    <div className="job-progress" role="status" aria-live="polite">
      <div className="job-progress-row">
        <LoaderCircle size={16} className="spin" />
        <span className="job-progress-step">{job.step || 'Starting'}</span>
        <span className="job-progress-percent">{job.progress}%</span>
      </div>
      <div
        className="job-progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={job.progress}
        aria-label="Job progress"
      >
        <div className="job-progress-fill" style={{ width: `${job.progress}%` }} />
      </div>
      <p className="job-progress-note">
        You can close or reload this page: the job keeps running on the backend and stays in your history.
      </p>
    </div>
  )
}
