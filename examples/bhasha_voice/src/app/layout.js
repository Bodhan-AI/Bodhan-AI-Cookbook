import "./globals.css";
import "@bhashavoice/react/styles.css";

export const metadata = {
  title: "BhashaVoice — Write with your voice",
  description: "Record, transcribe and translate your voice in English and Hindi.",
};

export default function RootLayout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}
