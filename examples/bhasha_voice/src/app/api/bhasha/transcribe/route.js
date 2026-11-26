import { handleAudioUpload } from "@/server/audio/handleAudioUpload.mjs";

// Cache Components already uses the Node.js runtime.
export async function POST(request) {
  return handleAudioUpload(request);
}
