// Tiny synchronous event bus shared by every voyage module.
// A throwing listener is isolated (logged) so one broken module never silences the others.

const listeners = new Map(); // evt -> Set<fn>

export const bus = {
  /** Subscribe; returns an unsubscribe function. */
  on(evt, fn) {
    if (typeof fn !== 'function') return () => {};
    let set = listeners.get(evt);
    if (!set) listeners.set(evt, (set = new Set()));
    set.add(fn);
    return () => bus.off(evt, fn);
  },

  off(evt, fn) {
    const set = listeners.get(evt);
    if (set) set.delete(fn);
  },

  /** Subscribe for a single delivery. */
  once(evt, fn) {
    const off = bus.on(evt, (p) => { off(); fn(p); });
    return off;
  },

  emit(evt, payload) {
    const set = listeners.get(evt);
    if (!set || !set.size) return;
    // Copy so listeners may unsubscribe while we iterate.
    for (const fn of [...set]) {
      try {
        fn(payload);
      } catch (err) {
        console.error(`[bus] listener for "${evt}" failed:`, err);
      }
    }
  },
};

export default bus;
