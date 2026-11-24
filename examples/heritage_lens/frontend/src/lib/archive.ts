import { api } from "./api";
import { DEMO_STORIES, demoFacets } from "./demoData";
import type { Facets, Mode } from "./mode-types";
import type { Page, Story } from "./types";

export interface Query { q?: string; country?: string; region?: string; language?: string; category?: string; period?: string; sort?: string; page?: number; page_size?: number }

export async function fetchStories(mode: Mode, p: Query): Promise<Page<Story>> {
  const size = p.page_size ?? 12, page = p.page ?? 1;
  if (mode === "live") return api.stories({ scope: "public", ...p, page, page_size: size });
  let rows = DEMO_STORIES.filter((s) =>
    (!p.q || `${s.title} ${s.description}`.toLowerCase().includes(p.q.toLowerCase())) &&
    (!p.country || s.country === p.country) && (!p.region || s.region === p.region) && (!p.language || s.language === p.language) &&
    (!p.category || s.category === p.category) && (!p.period || s.historical_period === p.period));
  const cmp: Record<string, (a: Story, b: Story) => number> = {
    title: (a, b) => a.title.localeCompare(b.title), year: (a, b) => (a.event_year ?? 9999) - (b.event_year ?? 9999),
    oldest: (a, b) => a.created_at.localeCompare(b.created_at), newest: (a, b) => b.created_at.localeCompare(a.created_at) };
  rows = [...rows].sort(cmp[p.sort ?? "newest"] ?? cmp.newest);
  return { items: rows.slice((page - 1) * size, page * size), total: rows.length, page, page_size: size };
}

export const fetchFacets = (mode: Mode): Promise<Facets> => (mode === "live" ? api.facets() : Promise.resolve(demoFacets()));
