import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapItem } from "../lib/types";

/** Leaflet map with OpenStreetMap tiles. Pins are only drawn for coordinates a contributor supplied. */
export default function MapView({ items, selectedId, onSelect }: { items: MapItem[]; selectedId: number | string | null; onSelect: (id: number | string) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { worldCopyJump: true, minZoom: 2 }).setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(map.current);
    layer.current = L.layerGroup().addTo(map.current);
    return () => { map.current?.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    if (!map.current || !layer.current) return;
    layer.current.clearLayers();
    items.forEach((it) => {
      const active = it.id === selectedId;
      const icon = L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
        html: `<span style="display:block;width:26px;height:26px;border-radius:9999px;background:${active ? "#713D31" : "#985541"};border:3px solid #FAF7F2;box-shadow:0 2px 6px rgba(0,0,0,.35)"></span>` });
      L.marker([it.latitude, it.longitude], { icon, title: it.title, keyboard: true, alt: it.title }).on("click", () => onSelect(it.id)).addTo(layer.current!);
    });
    if (items.length) map.current.fitBounds(L.latLngBounds(items.map((i) => [i.latitude, i.longitude] as [number, number])).pad(0.4), { maxZoom: 7 });
  }, [items, selectedId, onSelect]);

  return <div ref={el} className="h-[420px] w-full rounded-2xl border border-line md:h-[560px]" role="application" aria-label="Interactive heritage map. A list of the same stories follows for keyboard and screen reader users." />;
}
