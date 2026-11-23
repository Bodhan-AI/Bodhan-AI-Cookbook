import { describe, expect, it } from "vitest";
import { validateRegistration } from "../pages/Auth";
import { validateInfo } from "../pages/Preserve";
import { composeText } from "../pages/Digitizer";
import { extForMime, pickMime } from "../components/Recorder";
import { fmtDuration, placeOf, yearLabel } from "../lib/format";
import { DEMO_CAPSULE, DEMO_MAP, DEMO_STORIES, DEMO_TRANSLATIONS } from "../lib/demoData";
import type { DocumentRec } from "../lib/types";

describe("registration validation", () => {
  const ok = { username: "asha_k", email: "a@b.co", password: "long-enough", confirm: "long-enough" };
  it("accepts a valid form", () => expect(validateRegistration(ok)).toEqual({}));
  it("rejects bad fields", () => {
    const e = validateRegistration({ username: "x", email: "nope", password: "short", confirm: "different" });
    expect(Object.keys(e).sort()).toEqual(["confirm", "email", "password", "username"]);
  });
  it("rejects passwords over 72 bytes", () => expect(validateRegistration({ ...ok, password: "é".repeat(40), confirm: "é".repeat(40) }).password).toBeTruthy());
});

describe("story info validation", () => {
  const base = { title: "T", description: "", country: "", region: "", city: "", village: "", language: "te", category: "", historical_period: "", event_year: "", event_year_approximate: true, tags: "", latitude: "", longitude: "" };
  it("requires a title", () => expect(validateInfo({ ...base, title: " " }).title).toBeTruthy());
  it("coordinates must come as a valid pair", () => {
    expect(validateInfo({ ...base, latitude: "17.4" }).latitude).toBeTruthy();
    expect(validateInfo({ ...base, latitude: "95", longitude: "10" }).latitude).toBeTruthy();
    expect(validateInfo({ ...base, latitude: "17.38", longitude: "78.48" })).toEqual({});
  });
  it("year must be in range", () => {
    expect(validateInfo({ ...base, event_year: "5000" }).event_year).toBeTruthy();
    expect(validateInfo({ ...base, event_year: "abc" }).event_year).toBeTruthy();
    expect(validateInfo({ ...base, event_year: "1952" })).toEqual({});
  });
});

describe("OCR text composition keeps page numbers and prefers the user's edit", () => {
  const doc = (o: Partial<DocumentRec>) => ({ extracted_text: "A\n\nB", extracted_edited_text: null, ocr_pages: [{ page_number: 1, markdown: "A", blocks: [], error: null, attempts: 1 }, { page_number: 2, markdown: "B", blocks: [], error: null, attempts: 1 }], ...o }) as DocumentRec;
  it("adds page markers for multi-page documents", () => expect(composeText(doc({}))).toContain("— Page 2 —"));
  it("uses edited text when present", () => expect(composeText(doc({ extracted_edited_text: "mine" }))).toBe("mine"));
  it("skips failed pages", () => expect(composeText(doc({ ocr_pages: [{ page_number: 1, markdown: "", blocks: [], error: "x", attempts: 1 }, { page_number: 2, markdown: "B", blocks: [], error: null, attempts: 1 }] }))).toBe("B"));
});

describe("recorder format selection", () => {
  it("prefers webm/opus, falls back to mp4 for Safari", () => {
    expect(pickMime(() => true)).toBe("audio/webm;codecs=opus");
    expect(pickMime((m) => m === "audio/mp4")).toBe("audio/mp4");
    expect(pickMime(() => false)).toBeUndefined();
  });
  it("maps MIME to a backend-accepted extension", () => {
    expect(extForMime("audio/webm;codecs=opus")).toBe(".webm");
    expect(extForMime("audio/mp4")).toBe(".m4a");
    expect(extForMime("audio/ogg;codecs=opus")).toBe(".ogg");
  });
});

describe("formatting", () => {
  it("durations", () => { expect(fmtDuration(65)).toBe("1:05"); expect(fmtDuration(null)).toBe("—"); });
  it("years are never invented", () => { expect(yearLabel(null, true)).toBe("Date unknown"); expect(yearLabel(1952, true)).toBe("c. 1952"); expect(yearLabel(-300, false)).toBe("300 BCE"); });
  it("place", () => expect(placeOf({ region: "Telangana", country: "India" })).toBe("Telangana, India"));
});

describe("demo data is always labelled and never claims to be real", () => {
  it("every demo story has provenance demo", () => DEMO_STORIES.forEach((s) => expect(s.provenance).toBe("demo")));
  it("every demo map pin is flagged approximate", () => DEMO_MAP.forEach((m) => { expect(m.provenance).toBe("demo"); expect(m.note).toContain("approximate"); }));
  it("demo translations exist only for hand-written languages", () => expect(Object.keys(DEMO_TRANSLATIONS).sort()).toEqual(["en", "hi"]));
  it("demo capsule says it is illustrative and not auto-delivered", () => { expect(DEMO_CAPSULE.title).toContain("illustrative"); expect(DEMO_CAPSULE.delivery_note).toContain("does not yet deliver"); });
  it("demo stories do not carry real coordinates unless flagged", () => DEMO_STORIES.filter((s) => s.latitude != null).forEach((s) => expect(DEMO_MAP.some((m) => m.id === s.id)).toBe(true)));
});
