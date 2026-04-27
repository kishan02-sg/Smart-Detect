import React, { useEffect, useState } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function StatCard({ icon, label, value, color = 'text-metro-accent', loading }) {
  return (
    <div className="glass-card p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center bg-metro-700/60 ${color}`}>{icon}</div>
      <div>
        {loading
          ? <div className="h-8 w-16 bg-metro-700 rounded animate-pulse mb-1" />
          : <p className={`text-3xl font-bold tabular-nums ${color}`}>{value}</p>
        }
        <p className="text-xs text-slate-500 font-medium uppercase tracking-wider">{label}</p>
      </div>
    </div>
  )
}

export default function StatsPanel() {
  const [stats, setStats]   = useState({ persons: 0, locations: 0 })
  const [loading, setLoading] = useState(true)

  const fetchStats = async () => {
    try {
      const [locRes] = await Promise.all([axios.get(`${API}/locations`)])
      setStats(prev => ({ ...prev, locations: locRes.data.length }))
    } catch { /* non-critical */ } finally { setLoading(false) }
  }

  useEffect(() => {
    fetchStats()
    const interval = setInterval(fetchStats, 30_000)
    return () => clearInterval(interval)
  }, [])

  const iconPerson = <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 0 0-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 0 1 5.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 0 1 9.288 0M15 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" /></svg>
  const iconLocation = <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 0 1-2.827 0l-4.244-4.243a8 8 0 1 1 11.314 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" /></svg>
  const iconCamera = <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 10l4.553-2.069A1 1 0 0 1 21 8.845v6.31a1 1 0 0 1-1.447.894L15 14M3 8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8z" /></svg>
  const iconShield = <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0 1 12 2.944a11.955 11.955 0 0 1-8.618 3.04A12.02 12.02 0 0 0 3 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard icon={iconPerson}   label="Registered Persons" value={stats.persons}   color="text-metro-accent" loading={loading} />
      <StatCard icon={iconLocation} label="Active Locations"   value={stats.locations} color="text-metro-teal"   loading={loading} />
      <StatCard icon={iconCamera}   label="Live Cameras"       value="—"               color="text-metro-amber"  loading={false}   />
      <StatCard icon={iconShield}   label="System Status"      value="Online"          color="text-metro-green"  loading={false}   />
    </div>
  )
}
