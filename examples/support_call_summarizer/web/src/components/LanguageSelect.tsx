import { useId } from "react";
import type { Language } from "../api";

interface LanguageSelectProps {
  languages: Language[];
  value: string;
  disabled?: boolean;
  onChange: (code: string) => void;
}

export const AUTO_DETECT = "";

export function LanguageSelect({ languages, value, disabled, onChange }: LanguageSelectProps) {
  const id = useId();
  const hintId = useId();
  const noteId = useId();
  const selected = languages.find((l) => l.code === value);
  const noTranslation = selected !== undefined && !selected.translation;

  return (
    <div className="field">
      <label htmlFor={id} className="field__label">
        Call language
      </label>
      <select
        id={id}
        className="input select"
        value={value}
        disabled={disabled}
        aria-describedby={noTranslation ? `${hintId} ${noteId}` : hintId}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value={AUTO_DETECT}>Auto-detect</option>
        {languages.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.name}
          </option>
        ))}
      </select>
      <p id={hintId} className="field__hint">
        Choosing the language spoken in the call improves accuracy.
      </p>
      {noTranslation && (
        <p id={noteId} className="field__note">
          English translation isn't available for {selected.name} yet. You'll get the {selected.name} transcript
          only.
        </p>
      )}
    </div>
  );
}
