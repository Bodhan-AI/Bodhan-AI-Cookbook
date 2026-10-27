// The Bodhan.AI mark and wordmark. Both theme variants are rendered and CSS shows
// the one matching data-theme, so a reader who picked Light on a dark OS gets the light mark.
export function BrandLockup() {
  return (
    <span className="lockup">
      <img className="lockup__mark lockup__mark--light" src="/brand/bodhan-mark-light.svg" alt="" width={30} height={30} />
      <img className="lockup__mark lockup__mark--dark" src="/brand/bodhan-mark-dark.svg" alt="" width={30} height={30} />
      <span className="lockup__word">
        Bodhan<span>.AI</span>
      </span>
    </span>
  );
}
