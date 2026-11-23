/* Ambient HUD backdrop (particle network that reacts to the cursor) + arc-reactor SVG emblem factory. */
(function (global) {
  'use strict';

  // ------------------------------------------------------------------ reactor emblem
  let uid = 0;
  function reactorSVG() {
    const id = 'rc' + (++uid);
    const segs = Array.from({ length: 10 }, (_, i) => {
      const a0 = (i * 36 + 4) * Math.PI / 180, a1 = (i * 36 + 30) * Math.PI / 180;
      const p = (r, a) => `${(100 + r * Math.cos(a)).toFixed(2)},${(100 + r * Math.sin(a)).toFixed(2)}`;
      return `<path d="M${p(42, a0)} A42 42 0 0 1 ${p(42, a1)} L${p(52, a1)} A52 52 0 0 0 ${p(52, a0)} Z"/>`;
    }).join('');
    return `<svg viewBox="0 0 200 200" aria-hidden="true">
      <defs><radialGradient id="${id}">
        <stop offset="0" style="stop-color:#fff"/><stop offset=".35" style="stop-color:var(--accent)"/>
        <stop offset="1" style="stop-color:var(--accent);stop-opacity:0"/></radialGradient></defs>
      <circle class="r-halo" cx="100" cy="100" r="70"/>
      <circle class="r-ring r1" cx="100" cy="100" r="94" stroke-dasharray="3 9"/>
      <circle class="r-ring r2" cx="100" cy="100" r="80" stroke-dasharray="70 18 12 18"/>
      <circle class="r-ring r3" cx="100" cy="100" r="64" stroke-dasharray="1.5 5"/>
      <g class="r-seg">${segs}</g>
      <circle class="r-core" cx="100" cy="100" r="30" fill="url(#${id})"/>
    </svg>`;
  }
  function mountReactors(root = document) {
    root.querySelectorAll('[data-reactor]:not([data-mounted])').forEach(el => { el.innerHTML = reactorSVG(); el.dataset.mounted = '1'; });
  }

  // ------------------------------------------------------------------ particle field
  function startBackground(canvas) {
    const ctx = canvas.getContext('2d');
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let W = 0, H = 0, dpr = 1, pts = [], raf = 0, rgb = '56,225,255';
    const mouse = { x: -9999, y: -9999 };
    let scrollY = 0;

    function readColor() {
      rgb = getComputedStyle(document.documentElement).getPropertyValue('--particle').trim() || rgb;
    }
    function resize() {
      dpr = Math.min(2, global.devicePixelRatio || 1);
      W = innerWidth; H = innerHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.round(Math.min(110, (W * H) / 16000));
      pts = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
        r: Math.random() * 1.6 + 0.4, z: Math.random() * 0.8 + 0.2,
      }));
    }
    function frame() {
      ctx.clearRect(0, 0, W, H);
      const LINK = 130;
      for (const p of pts) {
        if (!reduce) {
          p.x += p.vx * p.z; p.y += p.vy * p.z;
          const dx = p.x - mouse.x, dy = p.y - mouse.y, d2 = dx * dx + dy * dy;
          if (d2 < 22000) { const f = (22000 - d2) / 22000 * 0.6; p.x += dx / Math.sqrt(d2 + 1) * f; p.y += dy / Math.sqrt(d2 + 1) * f; }
          if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
          if (p.y < -20) p.y = H + 20; if (p.y > H + 20) p.y = -20;
        }
      }
      const off = (scrollY * 0.05) % H;
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i];
        const ay = (a.y - off * a.z + H) % H;
        for (let j = i + 1; j < pts.length; j++) {
          const b = pts[j];
          const by = (b.y - off * b.z + H) % H;
          const dx = a.x - b.x, dy = ay - by, d = Math.sqrt(dx * dx + dy * dy);
          if (d < LINK) {
            ctx.strokeStyle = `rgba(${rgb},${(1 - d / LINK) * 0.16 * Math.min(a.z, b.z)})`;
            ctx.lineWidth = 0.7;
            ctx.beginPath(); ctx.moveTo(a.x, ay); ctx.lineTo(b.x, by); ctx.stroke();
          }
        }
        ctx.fillStyle = `rgba(${rgb},${0.25 + a.z * 0.45})`;
        ctx.beginPath(); ctx.arc(a.x, ay, a.r * a.z + 0.3, 0, Math.PI * 2); ctx.fill();
      }
      if (!reduce) raf = requestAnimationFrame(frame);
    }
    addEventListener('resize', () => { resize(); if (reduce) frame(); });
    addEventListener('pointermove', e => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });
    addEventListener('pointerleave', () => { mouse.x = mouse.y = -9999; });
    addEventListener('scroll', () => { scrollY = global.scrollY; }, { passive: true });
    document.addEventListener('visibilitychange', () => {
      cancelAnimationFrame(raf);
      if (!document.hidden && !reduce) raf = requestAnimationFrame(frame);
    });
    readColor(); resize(); frame();
    return { recolor() { readColor(); if (reduce) frame(); } };
  }

  global.JarvisFX = { reactorSVG, mountReactors, startBackground };
})(window);
