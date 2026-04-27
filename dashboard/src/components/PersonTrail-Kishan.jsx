import React, { useEffect, useState } from 'react'
import axios from 'axios'
import SightingCard from './SightingCard.jsx'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * PersonTrail — Timeline of sightings for a person.
 * Change 3: shows person_type badge, location_name + location_type per entry.
 */
export default function PersonTrail({ unique_code }) {
  const [trail, setTrail]     = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  useEffect(() => {
    if (!unique_code) return
    const fetchTrail = async () => {
      setLoading(true); setError(null)
      try {
        const res = await axios.get(`${API}/person/${encodeURIComponent(unique_code)}/trail`)
        setTrail(res.data)
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to fetch trail. Is the backend running?')
        setTrail([])
      } finally {
        setLoading(false)
      }
    }
    fetchTrail()
  }, [unique_code])

  // Loading
  if (loading) return (
    <div className="flex flex-col items-center justify-center py-20 gap-4 animate-fade-in">
      <div className="w-12 h-12 border-4 border-metro-600 border-t-metro-accent rounded-full animate-spin" />
      <p className="text-slate-400 text-sm">Fetching movement trail…</p>
    </div>
  )

  // Error
  if (error) return (
    <div className="glass-card p-6 border-red-500/30 animate-fade-in">
      <div className="flex items-start gap-3">
        <svg className="w-5 h-5 text-metro-red flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
        </svg>
        <p className="text-metro-red text-sm">{error}</p>
      </div>
    </div>
  )

  // Empty
  if (trail.length === 0 && unique_code) return (
    <div className="glass-card p-12 text-center animate-fade-in">
      <svg className="w-16 h-16 mx-auto text-metro-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
          d="M9.172 16.172a4 4 0 0 1 5.656 0M9 10h.01M15 10h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
      </svg>
      <h3 className="text-lg font-semibold text-slate-300 mb-1">No Sightings Found</h3>
      <p className="text-slate-500 text-sm">
        No sightings recorded for <span className="font-mono text-slate-300">{unique_code}</span>.
      </p>
    </div>
  )

  if (!unique_code) return null

  // Summary stats
  const firstSeen   = new Date(trail[0]?.seen_at)
  const lastSeen    = new Date(trail[trail.length - 1]?.seen_at)
  const durationMin = Math.round((lastSeen - firstSeen) / 60000)
  const uniqueZones = new Set(trail.map(s => s.zone_id)).size
  const uniqueLocs  = new Set(trail.map(s => s.location_id)).size

  return (
    <div className="animate-fade-in">
      {/* Summary bar */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        {[
          { label: 'Sightings',        value: trail.length,                                color: 'text-metro-accent' },
          { label: 'Zones Visited',    value: uniqueZones,                                 color: 'text-metro-teal' },
          { label: 'Locations',        value: uniqueLocs,                                  color: 'text-purple-400' },
          { label: 'Duration',         value: durationMin < 1 ? '<1 min' : `${durationMin} min`, color: 'text-metro-amber' },
        ].map(({ label, value, color }) => (
          <div key={label} className="glass-card p-3 text-center">
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
            <p className="text-xs text-slate-500 mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      {/* Timeline */}
      <div>
        {trail.map((sighting, idx) => (
          <div key={idx} className="relative pl-8 pb-6 last:pb-0">
            {/* Timeline line */}
            {idx < trail.length - 1 && (
              <div className="absolute left-3 top-6 bottom-0 w-px bg-metro-700" />
            )}
            {/* Dot */}
            <div className={`absolute left-0 top-1.5 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold
              ${idx === 0 ? 'bg-metro-green text-white' : idx === trail.length - 1 ? 'bg-metro-accent text-white' : 'bg-metro-700 text-slate-300'}`}>
              {idx + 1}
            </div>

            {/* Card */}
            <div className="glass-card p-4 ml-2">
              <div className="flex items-start justify-between gap-2 flex-wrap">
                <div>
                  {/* Location + type */}
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-semibold text-white">{sighting.location_name}</span>
                    {sighting.location_type && (
                      <span className="text-xs text-slate-500 bg-metro-700/60 px-1.5 py-0.5 rounded capitalize">
                        {sighting.location_type}
                      </span>
                    )}
                  </div>
                  {/* Zone + camera */}
                  <p className="text-xs text-slate-400">
                    Zone: <span className="text-slate-300">{sighting.zone_id || '—'}</span>
                    {' · '}Cam: <span className="font-mono text-slate-300">{sighting.camera_id}</span>
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-slate-500">{new Date(sighting.seen_at).toLocaleString()}</p>
                  <p className="text-xs">
                    <span className={`font-medium ${sighting.confidence > 0.85 ? 'text-metro-green' : 'text-metro-amber'}`}>
                      {(sighting.confidence * 100).toFixed(0)}% conf
                    </span>
                  </p>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
