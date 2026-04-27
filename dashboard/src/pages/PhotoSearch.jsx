import React, { useEffect, useState } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/* ── Helpers ───────────────────────────────────────── */
function toBase64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader()
    r.onload = e => res(e.target.result.split(',')[1])
    r.onerror = rej
    r.readAsDataURL(file)
  })
}

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso + (iso.endsWith('Z') ? '' : 'Z'))
    .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const METHOD_COLORS = {
  face:             { bg: '#eff6ff', color: '#1d4ed8', label: 'Face' },
  dress_color:      { bg: '#f0fdf4', color: '#166534', label: 'Color' },
  body_structure:   { bg: '#faf5ff', color: '#7c3aed', label: 'Body' },
  multi_feature:    { bg: '#fffbeb', color: '#b45309', label: 'Multi' },
  new_registration: { bg: '#f9fafb', color: '#6b7280', label: 'New' },
}

/* ── Face overlay corner brackets ──────────────────── */
function FaceBracket() {
  const s = { position: 'absolute', width: 16, height: 16, border: '2px solid #22c55e' }
  return (
    <>
      <div style={{ ...s, top: 8, left: 8, borderRight: 'none', borderBottom: 'none' }} />
      <div style={{ ...s, top: 8, right: 8, borderLeft: 'none', borderBottom: 'none' }} />
      <div style={{ ...s, bottom: 8, left: 8, borderRight: 'none', borderTop: 'none' }} />
      <div style={{ ...s, bottom: 8, right: 8, borderLeft: 'none', borderTop: 'none' }} />
    </>
  )
}

/* ── Camera card in result grid ─────────────────────── */
function CameraCard({ cam }) {
  const isLive    = cam.match_type === 'live'
  const isHistory = cam.match_type === 'history'
  const isMatch   = isLive || isHistory
  const borderColor = isLive ? '#22c55e' : isHistory ? '#3b82f6' : '#e8e8e8'
  const borderWidth = isMatch ? '1.5px' : '0.5px'

  return (
    <div style={{
      background: '#fff', borderRadius: 10,
      border: `${borderWidth} solid ${borderColor}`,
      overflow: 'hidden',
      opacity: isMatch ? 1 : 0.55,
    }}>
      {/* Camera screen */}
      <div className="camera-screen" style={{ borderRadius: 0 }}>
        <div className="camera-grid-lines" />

        {/* Bounding box */}
        {isMatch && (
          <div style={{
            position: 'absolute',
            top: '20%', left: '30%', width: '40%', height: '60%',
            border: `1.5px solid ${borderColor}`,
          }}>
            <span style={{
              position: 'absolute', top: -18, left: 0,
              background: borderColor, color: '#fff',
              fontSize: 9, fontWeight: 600, padding: '1px 5px', borderRadius: 3,
            }}>
              {Math.round((cam.confidence || 0) * 100)}% match
            </span>
          </div>
        )}

        {/* Live badge */}
        {isLive && (
          <div style={{ position: 'absolute', top: 6, right: 6 }}>
            <span className="badge badge-green" style={{ fontSize: 9 }}>
              <span className="dot dot-green pulse" style={{ width: 4, height: 4 }} />LIVE
            </span>
          </div>
        )}
        {isHistory && (
          <div style={{ position: 'absolute', top: 6, right: 6 }}>
            <span className="badge badge-blue" style={{ fontSize: 9 }}>{fmtTime(cam.detected_at)}</span>
          </div>
        )}
        {!isMatch && (
          <div style={{ position: 'absolute', top: 6, right: 6 }}>
            <span className="badge badge-gray" style={{ fontSize: 9 }}>—</span>
          </div>
        )}
      </div>

      {/* Info */}
      <div style={{ padding: '8px 10px' }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: '#111', marginBottom: 2 }}>
          {cam.camera_name || cam.camera_id}
        </div>
        <div style={{ fontSize: 10, color: '#aaa' }}>
          {cam.camera_id} · {cam.zone_id || 'main'}
        </div>
      </div>
    </div>
  )
}

/* ── Timeline ──────────────────────────────────────── */
function Timeline({ stops }) {
  if (!stops || stops.length === 0) return null
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Movement timeline today</div>
      <div style={{ display: 'flex', overflowX: 'auto', gap: 0, paddingBottom: 4 }}>
        {stops.map((s, i) => {
          const isFirst = i === 0
          const isLast  = i === stops.length - 1
          const mc      = METHOD_COLORS[s.method] || METHOD_COLORS.new_registration
          return (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', flexShrink: 0 }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 80 }}>
                {/* Dot */}
                <div style={{
                  width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                  background: isFirst ? '#111' : isLast ? '#22c55e' : '#d1d5db',
                  border: '2px solid #fff',
                  boxShadow: '0 0 0 1.5px ' + (isFirst ? '#111' : isLast ? '#22c55e' : '#d1d5db'),
                  margin: '4px 0',
                }} />
                <div style={{ fontSize: 10, fontWeight: 500, color: '#111', textAlign: 'center', marginTop: 4 }}>
                  {s.zone_id || s.location_name}
                </div>
                <div style={{ fontSize: 10, color: '#aaa', marginTop: 2 }}>{fmtTime(s.seen_at)}</div>
                <span style={{
                  marginTop: 4, padding: '1px 6px', borderRadius: 4, fontSize: 9,
                  background: mc.bg, color: mc.color,
                }}>
                  {mc.label}
                </span>
              </div>
              {/* Connecting line */}
              {!isLast && (
                <div style={{ width: 24, height: 1, background: '#e8e8e8', marginTop: 8.5, flexShrink: 0 }} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* ── PhotoSearch page ────────────────────────────── */
export default function PhotoSearch() {
  const [file, setFile]           = useState(null)
  const [preview, setPreview]     = useState(null)
  const [faceOk, setFaceOk]       = useState(null)   // true | false | null
  const [scope, setScope]         = useState('live_and_history')
  const [checking, setChecking]   = useState(false)
  const [searching, setSearching] = useState(false)
  const [result, setResult]       = useState(null)
  const [filter, setFilter]       = useState('all')

  // Camera count from status
  const [camCount, setCamCount] = useState(1)
  useEffect(() => {
    axios.get(`${API}/camera/status`).then(r => setCamCount(r.data?.connected ? 1 : 1)).catch(() => {})
  }, [])

  /* ── Auto-refresh live matches every 10s ─────────── */
  useEffect(() => {
    if (!result?.matched) return
    const iv = setInterval(async () => {
      try {
        const b64 = await toBase64(file)
        const res = await axios.post(`${API}/search/by-photo`, { base64_image: b64, scope: 'live_only' })
        if (res.data.matched) {
          setResult(prev => ({ ...prev, live_matches: res.data.live_matches, is_live_now: res.data.is_live_now }))
        }
      } catch {}
    }, 10000)
    return () => clearInterval(iv)
  }, [result?.matched, file])

  /* ── Upload handler ──────────────────────────────── */
  const handleFile = async (f) => {
    if (!f) return
    setFile(f)
    setResult(null)
    setFaceOk(null)
    const url = URL.createObjectURL(f)
    setPreview(url)

    // Check face
    setChecking(true)
    try {
      const b64 = await toBase64(f)
      const res = await axios.post(`${API}/search/by-photo`, { base64_image: b64, scope: 'live_only', check_only: true })
      setFaceOk(res.data.face_detected !== false)
    } catch {
      setFaceOk(false)
    } finally {
      setChecking(false)
    }
  }

  /* ── Search ──────────────────────────────────────── */
  const handleSearch = async () => {
    if (!file || !faceOk) return
    setSearching(true)
    try {
      const b64 = await toBase64(file)
      const res = await axios.post(`${API}/search/by-photo`, { base64_image: b64, scope })
      setResult(res.data)
    } catch (err) {
      setResult({ error: err.response?.data?.detail || 'Search failed' })
    } finally {
      setSearching(false)
    }
  }

  /* ── Build camera grid ───────────────────────────── */
  const buildGrid = () => {
    if (!result) return []
    const live    = (result.live_matches    || []).map(c => ({ ...c, match_type: 'live' }))
    const history = (result.history_matches || []).map(c => ({ ...c, match_type: 'history' }))
    const allMatch = [...live, ...history]

    // Add placeholder "no match" card for current camera if not in results
    const knownIds = new Set(allMatch.map(c => c.camera_id))
    const noCam    = knownIds.size === 0 ? [{ camera_id: 'CAM-001', camera_name: 'Camera 1', zone_id: 'main', match_type: 'none' }] : []

    const grid = [...allMatch, ...noCam]

    if (filter === 'live')    return grid.filter(c => c.match_type === 'live')
    if (filter === 'history') return grid.filter(c => c.match_type === 'history')
    if (filter === 'none')    return grid.filter(c => c.match_type === 'none')
    return grid
  }

  const grid = buildGrid()
  const liveCount    = result?.live_matches?.length    || 0
  const historyCount = result?.history_matches?.length || 0

  return (
    /* FIX 6 — photosearch page uses grid with overflow constraints */
    <div className="fade-in" style={{
      display: 'grid',
      gridTemplateColumns: '280px 1fr',
      gap: 20,
      height: '100%',
      overflow: 'hidden',
    }}>

      {/* LEFT PANEL — scrollable */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto' }}>

        {/* Upload card */}
        <div className="card">
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Upload photo</div>
            <div style={{ fontSize: 11, color: '#aaa', marginTop: 2 }}>Any clear photo of the person</div>
          </div>

          {/* Drop zone */}
          <label htmlFor="photo-input" style={{ cursor: 'pointer', display: 'block' }}>
            <div style={{
              position: 'relative', borderRadius: 10, overflow: 'hidden',
              aspectRatio: '1/1', background: preview ? '#000' : '#f5f5f5',
              border: preview ? 'none' : '1px dashed #e0e0e0',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {preview ? (
                <>
                  <img src={preview} alt="preview" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  {faceOk === true && <FaceBracket />}
                  {faceOk === true && (
                    <div style={{
                      position: 'absolute', bottom: 8, left: '50%', transform: 'translateX(-50%)',
                      background: 'rgba(22,163,74,0.9)', color: '#fff', borderRadius: 20,
                      fontSize: 10, fontWeight: 500, padding: '3px 10px', whiteSpace: 'nowrap',
                    }}>
                      Face detected ✓
                    </div>
                  )}
                  {faceOk === false && (
                    <div style={{
                      position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.55)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <span style={{ fontSize: 11, color: '#f87171', fontWeight: 500, textAlign: 'center', padding: '0 8px' }}>
                        No face found —<br />try another photo
                      </span>
                    </div>
                  )}
                  {checking && (
                    <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <div className="spinner spinner-white" />
                    </div>
                  )}
                </>
              ) : (
                <div style={{ textAlign: 'center', color: '#bbb' }}>
                  <svg style={{ width: 24, height: 24, margin: '0 auto 6px', display: 'block' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  <span style={{ fontSize: 11 }}>Click to upload</span>
                  <div style={{ fontSize: 10, marginTop: 3 }}>JPG · PNG · WEBP</div>
                </div>
              )}
            </div>
          </label>
          <input id="photo-input" type="file" accept="image/*" style={{ display: 'none' }}
            onChange={e => handleFile(e.target.files?.[0])} />
        </div>

        {/* Scope toggle */}
        <div className="card" style={{ padding: 12 }}>
          <div style={{ fontSize: 11, color: '#aaa', marginBottom: 8 }}>Search scope</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {[['live_and_history', 'Live + History'], ['live_only', 'Live only']].map(([v, l]) => (
              <button key={v} onClick={() => setScope(v)} style={{
                flex: 1, padding: '6px 0', borderRadius: 7, border: 'none', cursor: 'pointer',
                fontFamily: 'inherit', fontSize: 11, fontWeight: 500,
                background: scope === v ? '#111' : '#f5f5f5',
                color: scope === v ? '#fff' : '#888',
                transition: 'all 0.12s',
              }}>{l}</button>
            ))}
          </div>
        </div>

        {/* Search button */}
        <button
          id="search-btn"
          className="btn btn-black"
          style={{ width: '100%', padding: '10px', fontSize: 13 }}
          onClick={handleSearch}
          disabled={!faceOk || searching}
        >
          {searching ? (
            <><div className="spinner spinner-white" />Searching {camCount} camera{camCount !== 1 ? 's' : ''}…</>
          ) : 'Search all cameras →'}
        </button>

        {/* Result summary */}
        {result && result.matched !== undefined && (
          <div className="card fade-in" style={{ padding: 12 }}>
            {result.matched ? (
              <>
                <div style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 600, color: '#111', marginBottom: 8 }}>
                  {result.unique_code}
                </div>
                <dl style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {[
                    ['Cameras searched', result.cameras_searched ?? 1],
                    ['Live matches',     liveCount,    '#22c55e'],
                    ['History matches',  historyCount, '#3b82f6'],
                    ['First seen',       fmtTime(result.first_seen)],
                    ['Last seen',        result.is_live_now ? '🟢 Live now' : fmtTime(result.last_seen), result.is_live_now ? '#22c55e' : undefined],
                  ].map(([k, v, c]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <dt style={{ fontSize: 11, color: '#aaa' }}>{k}</dt>
                      <dd style={{ fontSize: 12, fontWeight: 500, color: c || '#111' }}>{v}</dd>
                    </div>
                  ))}
                </dl>
              </>
            ) : (
              <div style={{ color: '#aaa', fontSize: 12, textAlign: 'center' }}>
                {result.message || 'No match found'}
              </div>
            )}
          </div>
        )}

        {/* Clear */}
        {(result || preview) && (
          <button className="btn btn-white" style={{ width: '100%' }}
            onClick={() => { setFile(null); setPreview(null); setFaceOk(null); setResult(null) }}>
            Clear search
          </button>
        )}
      </div>

      {/* RIGHT PANEL — scrollable */}
      <div style={{ height: '100%', overflowY: 'auto' }}>
        {/* Header */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>
            Camera grid {result ? `— ${liveCount + historyCount} detections` : ''}
          </div>
          <div style={{ fontSize: 11, color: '#aaa', marginTop: 2 }}>
            Green = live now · Blue = history · Gray = not detected
          </div>
        </div>

        {/* Filter tabs */}
        {result && (
          <div className="tab-strip" style={{ marginBottom: 14, width: 'fit-content' }}>
            {[['all','All cameras'], ['live','Live matches'], ['history','History only'], ['none','Not detected']].map(([v, l]) => (
              <button key={v} className={`tab-item ${filter === v ? 'active' : ''}`} onClick={() => setFilter(v)}>{l}</button>
            ))}
          </div>
        )}

        {/* Camera grid — FIX 6: auto-fill responsive */}
        {result ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 10 }}>
            {grid.map((cam, i) => <CameraCard key={i} cam={cam} />)}
          </div>
        ) : (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            height: 300, color: '#bbb', gap: 8,
          }}>
            <svg style={{ width: 40, height: 40 }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.2}
                d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <span style={{ fontSize: 13 }}>Upload a photo to search cameras</span>
          </div>
        )}

        {/* Timeline */}
        {result?.timeline && <Timeline stops={result.timeline} />}
      </div>
    </div>
  )
}
