import React, { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// ── helpers ───────────────────────────────────────────────────────────────────
const sourceLabel = src => {
  const s = String(src)
  if (s === '0') return 'Webcam 0'
  if (s === '1') return 'Webcam 1'
  if (s === '2') return 'Webcam 2'
  if (s === '3') return 'Webcam 3'
  if (s.startsWith('rtsp')) return 'IP Camera'
  return `Source ${s}`
}

// ── ZonePill ──────────────────────────────────────────────────────────────────
function ZonePill({ zone }) {
  const colors = {
    entrance:   ['#ecfdf5', '#059669'],
    'food-court':['#eff6ff', '#2563eb'],
    parking:    ['#fff7ed', '#d97706'],
    'gate-a':   ['#f5f3ff', '#7c3aed'],
    'gate-b':   ['#fdf2f8', '#a21caf'],
    library:    ['#ecfeff', '#0891b2'],
    terminal:   ['#fefce8', '#a16207'],
    security:   ['#fef2f2', '#dc2626'],
    main:       ['#f9fafb', '#6b7280'],
  }
  const [bg, color] = colors[zone] || ['#f9fafb', '#374151']
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 600,
      background: bg, color, letterSpacing: '0.03em', textTransform: 'uppercase',
    }}>{zone}</span>
  )
}

// ── AddCameraForm ─────────────────────────────────────────────────────────────
function AddCameraForm({ locationId, token, onCreated, onCancel }) {
  const [zone,    setZone]    = useState('')
  const [label,   setLabel]   = useState('')
  const [srcType, setSrcType] = useState('0')
  const [rtsp,    setRtsp]    = useState('')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  const submit = async () => {
    if (!zone.trim() || !label.trim()) { setError('Zone and label are required.'); return }
    const source = srcType === 'rtsp' ? rtsp.trim() : srcType
    if (srcType === 'rtsp' && !source) { setError('Enter an RTSP URL.'); return }
    setLoading(true); setError(null)
    try {
      const { data } = await axios.post(`${API}/cameras`,
        { location_id: locationId, zone_id: zone.trim(), label: label.trim(), source },
        { headers: { Authorization: `Bearer ${token}` } }
      )
      onCreated(data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to create camera.')
    } finally { setLoading(false) }
  }

  const inputStyle = {
    width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #e2e8f0',
    fontSize: 12, fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box',
    background: '#fff', marginBottom: 8,
  }

  return (
    <div style={{
      padding: 14, borderRadius: 10, border: '1.5px dashed #a5b4fc',
      background: '#f5f3ff', marginTop: 10,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: '#4c1d95', marginBottom: 10 }}>
        ＋ Add Camera
      </div>
      <input style={inputStyle} placeholder="Zone ID (e.g. entrance)"
        value={zone} onChange={e => setZone(e.target.value)} />
      <input style={inputStyle} placeholder="Camera label (e.g. Main Entrance Camera)"
        value={label} onChange={e => setLabel(e.target.value)} />

      <select value={srcType} onChange={e => setSrcType(e.target.value)}
        style={{ ...inputStyle, marginBottom: srcType === 'rtsp' ? 8 : 12 }}>
        <option value="0">Webcam 0</option>
        <option value="1">Webcam 1</option>
        <option value="2">Webcam 2</option>
        <option value="3">Webcam 3</option>
        <option value="rtsp">IP Camera (RTSP)</option>
      </select>

      {srcType === 'rtsp' && (
        <input style={{ ...inputStyle, fontFamily: 'monospace', marginBottom: 12 }}
          placeholder="rtsp://192.168.1.x:554/stream"
          value={rtsp} onChange={e => setRtsp(e.target.value)} />
      )}

      {error && (
        <div style={{ fontSize: 11, color: '#dc2626', marginBottom: 8 }}>{error}</div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={submit} disabled={loading} style={{
          flex: 1, padding: '7px 0', borderRadius: 8, border: 'none', cursor: 'pointer',
          background: '#7c3aed', color: '#fff', fontSize: 12, fontWeight: 600,
          opacity: loading ? 0.6 : 1,
        }}>
          {loading ? 'Creating…' : 'Create Camera'}
        </button>
        <button onClick={onCancel} style={{
          padding: '7px 14px', borderRadius: 8, border: '1px solid #e2e8f0',
          background: '#fff', fontSize: 12, cursor: 'pointer', color: '#555',
        }}>Cancel</button>
      </div>
    </div>
  )
}

// ── CameraCard ────────────────────────────────────────────────────────────────
function CameraCard({ cam, token, onDeleted }) {
  const [active,  setActive]  = useState(cam.is_active)
  const [loading, setLoading] = useState(false)
  const [imgErr,  setImgErr]  = useState(false)
  const imgRef = useRef(null)
  const retryTimerRef = useRef(null)
  const streamUrl = `${API}/camera/stream/${cam.id}`

  // Auto-retry stream every 3 seconds on error
  useEffect(() => {
    if (imgErr && active) {
      retryTimerRef.current = setTimeout(() => {
        setImgErr(false)
        if (imgRef.current) {
          imgRef.current.src = streamUrl + '?t=' + Date.now()
        }
      }, 3000)
    }
    return () => { if (retryTimerRef.current) clearTimeout(retryTimerRef.current) }
  }, [imgErr, active, streamUrl])

  const start = async () => {
    setLoading(true)
    try {
      await axios.post(`${API}/camera/start`, { camera_id: cam.id },
        { headers: { Authorization: `Bearer ${token}` } })
      setActive(true); setImgErr(false)
    } catch (e) {
      alert(e.response?.data?.detail || 'Cannot start camera.')
    } finally { setLoading(false) }
  }

  const stop = async () => {
    setLoading(true)
    try {
      await axios.post(`${API}/camera/stop`, { camera_id: cam.id },
        { headers: { Authorization: `Bearer ${token}` } })
      setActive(false)
    } catch { /* ignore */ } finally { setLoading(false) }
  }

  return (
    <div style={{
      borderRadius: 12, border: `1.5px solid ${active ? '#bbf7d0' : '#e2e8f0'}`,
      background: '#fff', overflow: 'hidden',
      boxShadow: active ? '0 2px 10px rgba(16,185,129,0.08)' : '0 1px 4px rgba(0,0,0,0.04)',
      transition: 'border-color 0.2s, box-shadow 0.2s',
    }}>
      {/* Live MJPEG stream (shown when active) */}
      {active && (
        <div style={{ position: 'relative', background: '#0a0a0a', aspectRatio: '16/9', overflow: 'hidden' }}>
          {imgErr ? (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', gap: 6,
              color: '#555', fontSize: 12,
            }}>
              <span>📡 Reconnecting in 3s…</span>
              <button onClick={() => { setImgErr(false); if (imgRef.current) imgRef.current.src = streamUrl + '?t=' + Date.now() }}
                style={{ fontSize: 11, padding: '4px 10px', borderRadius: 6, border: '1px solid #555', background: 'transparent', color: '#aaa', cursor: 'pointer' }}>
                Retry Now
              </button>
            </div>
          ) : (
            <img ref={imgRef} src={streamUrl} alt="Live"
              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
              onError={() => setImgErr(true)} />
          )}
          {/* LIVE badge */}
          <div style={{
            position: 'absolute', top: 8, left: 8, display: 'flex', alignItems: 'center', gap: 4,
            background: 'rgba(0,0,0,0.55)', borderRadius: 20, padding: '3px 8px',
          }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#22c55e', display: 'inline-block', animation: 'pulse 1.5s infinite' }} />
            <span style={{ color: '#fff', fontSize: 10, fontWeight: 600 }}>LIVE</span>
          </div>
        </div>
      )}

      {/* Card body */}
      <div style={{ padding: '12px 14px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 6 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#111', marginBottom: 3 }}>{cam.label}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <ZonePill zone={cam.zone_id} />
              <span style={{ fontSize: 10, color: '#94a3b8' }}>·</span>
              <span style={{ fontSize: 10, color: '#94a3b8' }}>{sourceLabel(cam.source)}</span>
              <span style={{ fontSize: 10, color: '#94a3b8' }}>·</span>
              <span style={{ fontSize: 10, fontFamily: 'monospace', color: '#64748b' }}>{cam.id}</span>
            </div>
          </div>
          {/* Status dot */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0, marginTop: 2 }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
              background: active ? '#22c55e' : '#d1d5db',
            }} />
            <span style={{ fontSize: 11, color: active ? '#16a34a' : '#9ca3af', fontWeight: 500 }}>
              {active ? 'Active' : 'Inactive'}
            </span>
          </div>
        </div>

        {/* Action row */}
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          {active ? (
            <button onClick={stop} disabled={loading} style={{
              flex: 1, padding: '7px 0', borderRadius: 8, border: 'none', cursor: 'pointer',
              background: loading ? '#fee2e2' : '#ef4444', color: '#fff',
              fontSize: 12, fontWeight: 600, opacity: loading ? 0.7 : 1, transition: 'opacity 0.2s',
            }}>
              {loading ? 'Stopping…' : '⏹ Stop'}
            </button>
          ) : (
            <button onClick={start} disabled={loading} style={{
              flex: 1, padding: '7px 0', borderRadius: 8, border: 'none', cursor: 'pointer',
              background: loading ? '#d1fae5' : '#10b981', color: '#fff',
              fontSize: 12, fontWeight: 600, opacity: loading ? 0.7 : 1, transition: 'opacity 0.2s',
            }}>
              {loading ? 'Starting…' : '▶ Start'}
            </button>
          )}
          <button onClick={() => onDeleted(cam.id)} style={{
            padding: '7px 10px', borderRadius: 8, border: '1px solid #e2e8f0',
            background: '#fff', fontSize: 12, cursor: 'pointer', color: '#9ca3af',
          }}>🗑</button>
        </div>
      </div>
    </div>
  )
}

// ── LocationSection ───────────────────────────────────────────────────────────
function LocationSection({ group, token, onRefresh }) {
  const [showAdd,  setShowAdd]  = useState(false)
  const [cameras,  setCameras]  = useState(group.cameras)

  const handleDelete = async (camId) => {
    if (!window.confirm(`Delete camera ${camId}?`)) return
    try {
      await axios.delete(`${API}/cameras/${camId}`,
        { headers: { Authorization: `Bearer ${token}` } })
      setCameras(cs => cs.filter(c => c.id !== camId))
    } catch (e) {
      alert(e.response?.data?.detail || 'Delete failed')
    }
  }

  const handleCreated = (cam) => {
    setCameras(cs => [...cs, cam])
    setShowAdd(false)
  }

  return (
    <div>
      <div style={{ padding: '14px 0 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <span style={{ fontSize: 14, fontWeight: 700, color: '#1e293b' }}>{group.location_name}</span>
          <span style={{
            marginLeft: 8, fontSize: 11, fontWeight: 500, padding: '2px 8px',
            borderRadius: 12, background: '#f1f5f9', color: '#64748b',
          }}>{group.location_type}</span>
        </div>
        <div style={{ fontSize: 11, color: '#94a3b8' }}>
          {cameras.filter(c => c.is_active).length}/{cameras.length} active
        </div>
      </div>

      {cameras.length === 0 ? (
        <div style={{
          padding: '20px', textAlign: 'center', color: '#94a3b8', fontSize: 12,
          background: '#f8fafc', borderRadius: 10, border: '1px dashed #e2e8f0',
        }}>
          No cameras yet — add one below
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
          {cameras.map(cam => (
            <CameraCard key={cam.id} cam={cam} token={token}
              onDeleted={handleDelete} />
          ))}
        </div>
      )}

      {showAdd ? (
        <AddCameraForm
          locationId={group.location_id}
          token={token}
          onCreated={handleCreated}
          onCancel={() => setShowAdd(false)}
        />
      ) : (
        <button onClick={() => setShowAdd(true)} style={{
          marginTop: 10, padding: '7px 14px', borderRadius: 8,
          border: '1.5px dashed #c7d2fe', background: '#fafaff',
          color: '#6366f1', fontSize: 12, fontWeight: 600, cursor: 'pointer',
          width: '100%', transition: 'background 0.15s',
        }}>
          ＋ Add Camera to {group.location_name}
        </button>
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function LiveCamera() {
  const [groups,      setGroups]      = useState([])
  const [locations,   setLocations]   = useState([])
  const [activeTab,   setActiveTab]   = useState(null)
  const [stats,       setStats]       = useState({ active: 0, total: 0, persons: 0 })
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState(null)
  const [token,       setToken]       = useState(null)

  // ── Auth token ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const cached = sessionStorage.getItem('sd_token')
    if (cached) { setToken(cached); return }
    // Auto-login with default operator credentials
    axios.post(`${API}/auth/login`, { username: 'operator', password: 'metroOp2024' })
      .then(r => { sessionStorage.setItem('sd_token', r.data.access_token); setToken(r.data.access_token) })
      .catch(() => setError('Backend unreachable. Start: uvicorn backend.main:app --port 8000'))
  }, [])

  // ── Fetch cameras + locations ──────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    if (!token) return
    try {
      const [camsRes, locsRes, statusRes, analyticsRes] = await Promise.all([
        axios.get(`${API}/cameras`, { headers: { Authorization: `Bearer ${token}` } }),
        axios.get(`${API}/locations`, { headers: { Authorization: `Bearer ${token}` } }),
        axios.get(`${API}/camera/status`).catch(() => ({ data: { active_cameras: 0, total_cameras: 0 } })),
        axios.get(`${API}/analytics/count/live`).catch(() => ({ data: { total: 0 } })),
      ])
      setGroups(camsRes.data || [])
      setLocations(locsRes.data || [])
      setStats({
        active:  statusRes.data.active_cameras || 0,
        total:   statusRes.data.total_cameras  || 0,
        persons: analyticsRes.data.total       || 0,
      })
      if (!activeTab && camsRes.data?.length > 0) {
        setActiveTab(camsRes.data[0].location_id)
      }
      setError(null)
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to load cameras.')
    } finally {
      setLoading(false)
    }
  }, [token, activeTab])

  useEffect(() => {
    fetchData()
    const iv = setInterval(fetchData, 10000)
    return () => clearInterval(iv)
  }, [fetchData])

  // ── Error state ─────────────────────────────────────────────────────────────
  if (error) return (
    <div className="fade-in" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 300 }}>
      <div className="card" style={{ maxWidth: 460, textAlign: 'center' }}>
        <div style={{ fontSize: 28, marginBottom: 12 }}>📡</div>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 6 }}>Cannot connect</div>
        <div style={{ fontSize: 12, color: '#666', marginBottom: 14, lineHeight: 1.6 }}>{error}</div>
        <code style={{ fontSize: 11, background: '#f8f8f8', borderRadius: 6, padding: '8px 12px', display: 'block', color: '#555' }}>
          python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
        </code>
        <button className="btn btn-black" style={{ marginTop: 14, width: '100%' }}
          onClick={() => { setError(null); setLoading(true); fetchData() }}>
          Retry Connection
        </button>
      </div>
    </div>
  )

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 200, gap: 10 }}>
      <div className="spinner" />
      <span style={{ fontSize: 13, color: '#aaa' }}>Loading cameras…</span>
    </div>
  )

  const visibleGroups = activeTab
    ? groups.filter(g => g.location_id === activeTab)
    : groups

  return (
    <div className="fade-in" style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* ── Location tab strip ─────────────────────────────────────────── */}
      <div className="loc-tabs">
        <button
          className={`loc-tab-pill ${!activeTab ? 'active' : 'inactive'}`}
          onClick={() => setActiveTab(null)}
        >All Locations</button>
        {locations.map(loc => (
          <button
            key={loc.id}
            className={`loc-tab-pill ${activeTab === loc.id ? 'active' : 'inactive'}`}
            onClick={() => setActiveTab(loc.id)}
          >{loc.name}</button>
        ))}
      </div>

      {/* ── Camera sections ────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 70 }}>
      {visibleGroups.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: 40, color: '#94a3b8', fontSize: 13,
          background: '#f8fafc', borderRadius: 12, border: '1px dashed #e2e8f0',
        }}>
          No cameras configured. Run <code style={{ fontFamily: 'monospace', fontSize: 12 }}>python scripts/demo_setup.py</code> to seed demo data.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
          {visibleGroups.map(group => (
            <div key={group.location_id} className="card" style={{ padding: '16px 20px' }}>
              <LocationSection
                group={group}
                token={token}
                onRefresh={fetchData}
              />
            </div>
          ))}
        </div>
      )}

      </div>

      {/* ── Bottom stats bar ──────────────────────────────────────────── */}
      <div style={{
        position: 'sticky', bottom: 0, width: '100%', flexShrink: 0, zIndex: 10,
        padding: '12px 20px', borderRadius: 12, marginTop: 8,
        background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: stats.active > 0 ? '#22c55e' : '#475569', display: 'inline-block' }} />
          <span style={{ color: '#e2e8f0', fontSize: 13, fontWeight: 500 }}>
            <strong style={{ color: '#fff' }}>{stats.active}</strong> camera{stats.active !== 1 ? 's' : ''} active
            {' '}across{' '}
            <strong style={{ color: '#fff' }}>{locations.length}</strong> location{locations.length !== 1 ? 's' : ''}
          </span>
        </div>
        <div style={{ color: '#94a3b8', fontSize: 12 }}>
          <strong style={{ color: '#e2e8f0' }}>{stats.persons}</strong> persons detected today
          <span style={{ marginLeft: 12, fontSize: 10, color: '#475569' }}>• auto-refreshes every 10s</span>
        </div>
      </div>
    </div>
  )
}
