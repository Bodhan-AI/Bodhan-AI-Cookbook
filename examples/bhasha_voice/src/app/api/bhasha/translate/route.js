import { handleTranslation } from "@/server/bodhan/handleTranslation.mjs";

export async function POST(request) {
  return handleTranslation(request);
}
