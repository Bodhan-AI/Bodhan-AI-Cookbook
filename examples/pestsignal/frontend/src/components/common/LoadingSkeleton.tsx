import React from 'react';

export const LoadingSkeleton: React.FC<{ rows?: number }> = ({ rows = 3 }) => {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="h-8 bg-gray-200/80 rounded-xl w-1/3"></div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-32 bg-gray-200/70 rounded-2xl p-4 flex flex-col justify-between">
            <div className="h-4 bg-gray-300/80 rounded w-1/2"></div>
            <div className="h-8 bg-gray-300/60 rounded w-3/4"></div>
            <div className="h-3 bg-gray-300/40 rounded w-1/4"></div>
          </div>
        ))}
      </div>
    </div>
  );
};
