import { Link } from "react-router-dom";
import { MapPin, Languages } from "lucide-react";
import type { Story } from "../lib/types";
import { categoryLabel } from "../lib/types";
import { placeOf } from "../lib/format";
import { StoryCover } from "./CoverArt";
import { ProvenanceBadge } from "./ui";

export default function StoryCard({ story }: { story: Story }) {
  const seed = typeof story.id === "number" ? story.id : story.title.length;
  return (
    <article className="card group flex flex-col overflow-hidden transition hover:-translate-y-0.5 hover:shadow-lg motion-reduce:hover:translate-y-0">
      <StoryCover imageId={story.cover_image_id} seed={seed} alt={`Cover for ${story.title}`} />
      <div className="flex flex-1 flex-col p-5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="eyebrow">{categoryLabel(story.category)}</span>
          {story.provenance === "demo" && <ProvenanceBadge value="demo" />}
          {story.publication_status === "draft" && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-900">Draft</span>}
          {story.privacy === "private" && story.publication_status !== "draft" && <span className="rounded-full bg-line px-2 py-0.5 text-[11px] font-semibold">Private</span>}
        </div>
        <h3 className="text-xl leading-snug">{story.title}</h3>
        <p className="mt-2 line-clamp-3 flex-1 text-sm text-clay-deep/80">{story.description || "No description yet."}</p>
        <dl className="mt-4 space-y-1 text-xs text-clay-deep/80">
          <div className="flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5 shrink-0" aria-hidden /><dt className="sr-only">Place</dt><dd>{placeOf(story)}</dd></div>
          <div className="flex items-center gap-1.5"><Languages className="h-3.5 w-3.5 shrink-0" aria-hidden /><dt className="sr-only">Language</dt><dd>{story.language ?? "Language not given"}</dd></div>
        </dl>
        <Link to={`/stories/${story.id}`} className="btn-secondary mt-4 self-start" aria-label={`Explore ${story.title}`}>Explore</Link>
      </div>
    </article>
  );
}
