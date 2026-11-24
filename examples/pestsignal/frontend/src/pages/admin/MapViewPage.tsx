import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MapContainer, TileLayer, Marker, Popup, CircleMarker } from 'react-leaflet';
import L from 'leaflet';
import {
  MapPin,
  Filter,
  Flame,
  Sprout,
  ArrowRight,
  Sparkles,
  Info,
} from 'lucide-react';
import { reportApi, alertApi } from '../../services/api';
import { Report, Alert } from '../../types';

// Fallback Indian District Coordinates when report lacks explicit GPS
const DISTRICT_GEO: Record<string, [number, number]> = {
  Guntur: [16.3067, 80.4365],
  Karimnagar: [18.4386, 79.1288],
  Khammam: [17.2473, 80.1514],
  Ludhiana: [30.9010, 75.8573],
  Kolar: [13.1367, 78.1340],
  Rajkot: [22.3039, 70.8022],
  Nashik: [19.9975, 73.7898],
  Varanasi: [25.3176, 82.9739],
  Pune: [18.5204, 73.8567],
  Patna: [25.5941, 85.1376],
};

// Custom Leaflet DivIcon generator
function createCustomIcon(isAlert: boolean, label: string) {
  const bg = isAlert ? '#DC2626' : '#268653';
  return L.divIcon({
    className: 'custom-leaflet-marker',
    html: `
      <div style="
        background-color: ${bg};
        color: white;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
        border: 2px solid white;
        font-weight: 800;
        font-size: 11px;
      ">
        ${isAlert ? '⚠️' : '🌱'}
      </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -18],
  });
}

export const MapViewPage: React.FC = () => {
  const [reports, setReports] = useState<Report[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cropFilter, setCropFilter] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function loadMapData() {
      setIsLoading(true);
      try {
        const [repData, altData] = await Promise.all([
          reportApi.list({ crop: cropFilter || undefined }),
          alertApi.list(),
        ]);
        setReports(repData);
        setAlerts(altData);
      } catch (e) {
        console.error('Failed to load map data', e);
      } finally {
        setIsLoading(false);
      }
    }
    loadMapData();
  }, [cropFilter]);

  // Center of India
  const centerPosition: [number, number] = [20.5937, 78.9629];

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl sm:text-3xl font-black text-[#174D35] tracking-tight">
            Geographical Outbreak & Disease Map
          </h1>
          <p className="text-xs sm:text-sm text-[#738076]">
            Interactive map displaying field observation coordinates and statistical disease cluster warnings across India.
          </p>
        </div>

        {/* Filter bar */}
        <div className="flex items-center gap-3">
          <select
            value={cropFilter}
            onChange={(e) => setCropFilter(e.target.value)}
            className="px-3.5 py-2 border border-gray-300 rounded-xl text-xs bg-white focus:outline-none focus:ring-2 focus:ring-[#268653] font-semibold"
          >
            <option value="">All Crops</option>
            <option value="Cotton">Cotton</option>
            <option value="Rice">Rice (Paddy)</option>
            <option value="Chilli">Chilli</option>
            <option value="Wheat">Wheat</option>
          </select>
        </div>
      </div>

      {/* Map Card */}
      <div className="bg-white rounded-3xl p-4 border border-gray-200/80 shadow-xs space-y-4">
        <div className="h-[580px] w-full rounded-2xl overflow-hidden relative">
          <MapContainer
            center={centerPosition}
            zoom={5}
            scrollWheelZoom={true}
            className="h-full w-full"
          >
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            {/* Outbreak Cluster Rings */}
            {alerts.map((alt) => {
              const coords = DISTRICT_GEO[alt.district] || [16.5, 80.5];
              return (
                <CircleMarker
                  key={alt.id}
                  center={coords}
                  radius={alt.severity === 'High' ? 36 : 24}
                  pathOptions={{
                    color: '#DC2626',
                    fillColor: '#EF4444',
                    fillOpacity: 0.25,
                    weight: 2,
                    dashArray: '4, 4',
                  }}
                >
                  <Popup>
                    <div className="p-1 space-y-1 text-xs">
                      <div className="font-bold text-red-700 flex items-center gap-1">
                        <Flame className="w-3.5 h-3.5" />
                        <span>OUTBREAK SIGNAL: {alt.crop_name}</span>
                      </div>
                      <div className="text-gray-800 font-semibold">{alt.district}, {alt.state}</div>
                      <div className="text-gray-600">Matching Reports: {alt.matching_report_count} ({alt.detection_period})</div>
                      <div className="text-gray-600">Symptoms: {alt.symptoms}</div>
                      <div className="text-[10px] text-amber-700 bg-amber-50 p-1 rounded font-semibold mt-1">
                        ⚠️ {alt.disclaimer}
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}

            {/* Individual Report Markers */}
            {reports.map((rep) => {
              // Use report's lat/lng or district fallback
              const coords: [number, number] =
                rep.latitude && rep.longitude
                  ? [rep.latitude, rep.longitude]
                  : DISTRICT_GEO[rep.district] || [16.3, 80.4];

              return (
                <Marker
                  key={rep.id}
                  position={coords}
                  icon={createCustomIcon(false, rep.crop_name)}
                >
                  <Popup>
                    <div className="p-1 space-y-1.5 text-xs max-w-xs">
                      <div className="flex items-center justify-between border-b pb-1">
                        <span className="font-mono font-bold text-[#268653]">{rep.id}</span>
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-800">
                          {rep.investigation_status}
                        </span>
                      </div>
                      <div className="font-bold text-[#18251D] text-sm">{rep.crop_name}</div>
                      <div className="text-gray-600">
                        <strong>Location:</strong> {rep.village}, {rep.district}
                      </div>
                      <div className="text-gray-600">
                        <strong>Symptoms:</strong> {rep.symptoms}
                      </div>
                      <div className="text-gray-700 italic bg-gray-50 p-1.5 rounded">
                        "{rep.english_translation}"
                      </div>
                      <div className="pt-1">
                        <Link
                          to={`/admin/reports/${rep.id}`}
                          className="text-[#268653] font-bold text-xs hover:underline inline-flex items-center gap-1"
                        >
                          <span>Open Case Investigation</span>
                          <ArrowRight className="w-3 h-3" />
                        </Link>
                      </div>
                    </div>
                  </Popup>
                </Marker>
              );
            })}
          </MapContainer>
        </div>

        {/* Legend */}
        <div className="flex flex-wrap items-center justify-between gap-4 px-2 text-xs text-[#738076]">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-1.5">
              <div className="w-3.5 h-3.5 rounded-full bg-[#268653]"></div>
              <span>Crop Observation Report</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-3.5 h-3.5 rounded-full bg-red-600 border border-red-300"></div>
              <span>Potential Outbreak Cluster Warning</span>
            </div>
          </div>
          <div className="text-[11px] text-gray-500">
            Click any marker to inspect original observation and AI English translation.
          </div>
        </div>
      </div>
    </div>
  );
};
