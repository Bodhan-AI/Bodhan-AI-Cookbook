export const SAMPLE_LETTER_LINES = [
  "Dear Friend,", "", "I hope this letter finds you well. The harvest festival", "begins next week and the whole village is preparing.", "", "Yours sincerely,", "Annapurna",
];

/** Draws a clearly labelled, invented letter on a canvas and returns it as a PNG. */
export function makeSampleLetter(): Promise<Blob> {
  const c = document.createElement("canvas");
  c.width = 900; c.height = 520;
  const g = c.getContext("2d");
  if (!g) return Promise.reject(new Error("Canvas is not available in this browser."));
  g.fillStyle = "#F3E7DC"; g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = "#D5A18B"; g.lineWidth = 3; g.strokeRect(14, 14, c.width - 28, c.height - 28);
  g.fillStyle = "#3b2a24"; g.font = "30px Georgia, serif";
  SAMPLE_LETTER_LINES.forEach((l, i) => g.fillText(l, 60, 80 + i * 46));
  g.fillStyle = "#8B756B"; g.font = "14px Arial, sans-serif";
  g.fillText("SAMPLE DOCUMENT: invented for the HeritageLens demo", 60, c.height - 34);
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("Could not create the sample image."))), "image/png"));
}
