import VoiceWorkspace from "./VoiceWorkspace";
import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.page}>
      <header className={styles.siteHeader}><span className={styles.wordmark}>bhasha<span>voice</span></span><span>Indian languages, powered by Bodhan</span></header>
      <section className={styles.card} aria-labelledby="page-title">
        <div className={styles.intro}>
          <h1 id="page-title" className={styles.title}>Write with your voice.</h1>
          <p className={styles.description}>Speak, insert, translate.</p>
        </div>
        <VoiceWorkspace />
      </section>
    </main>
  );
}
