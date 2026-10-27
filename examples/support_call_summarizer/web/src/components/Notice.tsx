import type { ReactNode } from "react";

export type NoticeTone = "info" | "success" | "warning" | "danger";

interface NoticeProps {
  tone?: NoticeTone;
  title?: string;
  children?: ReactNode;
  /** "alert" interrupts screen readers; use for errors that need attention now. */
  role?: "alert" | "status";
  actions?: ReactNode;
  className?: string;
}

export function Notice({ tone = "info", title, children, role, actions, className }: NoticeProps) {
  return (
    <div className={`notice notice--${tone}${className ? ` ${className}` : ""}`} role={role}>
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children}
      </div>
      {actions && <div className="notice__actions">{actions}</div>}
    </div>
  );
}
