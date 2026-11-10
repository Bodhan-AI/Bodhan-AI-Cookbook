// File: src/components/ContactActionCard.tsx
import React, { useState } from 'react';
import { PhoneCall, Copy, Check, ExternalLink } from 'lucide-react';

interface ContactActionCardProps {
  phoneNumbers: string[];
}

export const ContactActionCard: React.FC<ContactActionCardProps> = ({ phoneNumbers }) => {
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);

  if (!phoneNumbers || phoneNumbers.length === 0) return null;

  const handleCopy = (num: string, idx: number) => {
    navigator.clipboard.writeText(num);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 1800);
  };

  return (
    <div
      style={{
        backgroundColor: 'var(--warn-wash)',
        border: '1px solid rgba(234, 88, 12, 0.3)',
        borderRadius: 'var(--r-lg)',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <div
          style={{
            width: '26px',
            height: '26px',
            borderRadius: '50%',
            backgroundColor: '#EA580C',
            color: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <PhoneCall size={14} />
        </div>
        <div>
          <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--ink)' }}>
            Store Contact
          </div>
          <div style={{ fontSize: '0.74rem', color: 'var(--ink-2)' }}>
            Detected phone numbers with 1-tap dialer integration
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {phoneNumbers.map((phone, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--rule)',
              borderRadius: 'var(--r-md)',
              padding: '10px 14px'
            }}
          >
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, fontSize: '0.95rem' }}>
              {phone}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {/* Copy Button */}
              <button
                onClick={() => handleCopy(phone, idx)}
                className="btn"
                style={{
                  padding: '6px 12px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  background: copiedIdx === idx ? 'rgba(16, 185, 129, 0.15)' : 'rgba(99, 102, 241, 0.12)',
                  color: copiedIdx === idx ? '#059669' : '#4f46e5',
                  border: `1px solid ${copiedIdx === idx ? 'rgba(16, 185, 129, 0.35)' : 'rgba(99, 102, 241, 0.3)'}`
                }}
                title="Copy phone number"
              >
                {copiedIdx === idx ? <Check size={14} color="#059669" /> : <Copy size={14} color="#4f46e5" />}
                {copiedIdx === idx ? 'Copied' : 'Copy'}
              </button>

              {/* Call Native Button */}
              <a
                href={`tel:${phone}`}
                className="btn"
                style={{
                  padding: '6px 14px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  textDecoration: 'none',
                  background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                  color: '#ffffff',
                  boxShadow: '0 2px 8px rgba(16, 185, 129, 0.35)',
                  border: 'none'
                }}
              >
                <PhoneCall size={14} />
                Call Now
                <ExternalLink size={12} style={{ opacity: 0.9 }} />
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
