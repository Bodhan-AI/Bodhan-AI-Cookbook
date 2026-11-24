// Grow from the actual launcher bounds, even when placement flips to another
// side. The content fades separately so text isn't visible while compressed.
export function cardOpeningFrames(from, to) {
  return [
    {
      transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})`,
      transformOrigin: "top left", borderRadius: "24px", opacity: 0.35,
    },
    { offset: 0.6, transformOrigin: "top left", opacity: 1 },
    { transform: "none", transformOrigin: "top left", borderRadius: "14px", opacity: 1 },
  ];
}

export function animateCardOpening(card, from, browser = window) {
  if (!card?.animate || !from || browser.matchMedia("(prefers-reduced-motion: reduce)").matches) return () => {};
  const to = card.getBoundingClientRect();
  if (!from.width || !from.height || !to.width || !to.height) return () => {};
  const animation = card.animate(cardOpeningFrames(from, to), {
    duration: 480, easing: "cubic-bezier(.22,1,.36,1)",
  });
  return () => animation.cancel();
}
