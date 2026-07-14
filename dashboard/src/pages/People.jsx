import React, { useEffect, useState } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/* ── Auth (same pattern as Alerts.jsx) ─────────────── */
function getToken() {
  return localStorage.getItem('smartdetect_token')
}

async function ensureToken() {
  let token = getToken()
  if (token) return token
  try {
    const res = await axios.post(`${API}/auth/login`, { username: 'operator', password: 'smartOp2024' })
    token = res.data.access_token
    localStorage.setItem('smartdetect_token', token)
    return token
  } catch {
    return null
  }
}

function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const TYPE_COLORS = {
  staff:   { bg: '#3b82f618', fg: '#3b82f6' },
  visitor: { bg: '#22c55e18', fg: '#22c55e' },
  unknown: { bg: '#f0f0f0',   fg: '#888' },
}

function PersonRow({ p, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [name, setName]       = useState(p.display_name || '')
  const [saving, setSaving]   = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      const token = await ensureToken()
      await axios.put(`${API}/persons/${p.unique_code}`,
        { display_name: name },
        { headers: authHeaders(token) })
      setEditing(false)
      onSaved()
    } catch { /* keep editing open on failure */ }
    setSaving(false)
  }

  const setType = async (person_type) => {
    try {
      const token = await ensureToken()
      await axios.put(`${API}/persons/${p.unique_code}`,
        { person_type },
        { headers: authHeaders(token) })
      onSaved()
    } catch { /* ignore */ }
  }

  const tc = TYPE_COLORS[p.person_type] || TYPE_COLORS.unknown
  const photoUrl = p.photo_path ? `${API}/${p.photo_path.replace(/^snapshots\//, 'snapshots/')}` : null

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 14px', borderBottom: '0.5px solid #f7f8fa',
    }}>
      {/* Photo */}
      <div style={{
        width: 44, height: 44, borderRadius: 10, flexShrink: 0, overflow: 'hidden',
        background: '#f0f0f0', border: '0.5px solid #e8e8e8',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        {photoUrl ? (
          <img src={photoUrl} alt={p.unique_code}
               style={{ width: '100%', height: '100%', objectFit: 'cover' }}
               onError={e => { e.target.style.display = 'none' }} />
        ) : (
          <svg style={{ width: 18, height: 18, color: '#bbb' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.6}
                  d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
        )}
      </div>

      {/* Name + code */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {editing ? (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input
              autoFocus value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
              placeholder="Person name"
              style={{
                fontSize: 12, padding: '4px 8px', borderRadius: 6,
                border: '1px solid #ddd', outline: 'none', width: 150,
                fontFamily: 'inherit',
              }}
            />
            <button onClick={save} disabled={saving} style={{
              fontSize: 11, padding: '4px 10px', borderRadius: 6, border: 'none',
              background: '#111', color: '#fff', cursor: 'pointer', fontFamily: 'inherit',
            }}>{saving ? '…' : 'Save'}</button>
            <button onClick={() => setEditing(false)} style={{
              fontSize: 11, padding: '4px 8px', borderRadius: 6, border: 'none',
              background: 'transparent', color: '#888', cursor: 'pointer', fontFamily: 'inherit',
            }}>Cancel</button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: '#111' }}>
              {p.display_name || <span style={{ color: '#bbb' }}>Unnamed</span>}
            </span>
            <button onClick={() => { setName(p.display_name || ''); setEditing(true) }} title="Rename" style={{
              border: 'none', background: 'transparent', cursor: 'pointer',
              color: '#bbb', fontSize: 11, padding: 2, fontFamily: 'inherit',
            }}>✎</button>
          </div>
        )}
        <div style={{ fontSize: 11, color: '#aaa', fontFamily: 'monospace', marginTop: 1 }}>
          {p.unique_code}
        </div>
      </div>

      {/* Type selector */}
      <select
        value={p.person_type || 'unknown'}
        onChange={e => setType(e.target.value)}
        style={{
          fontSize: 11, fontWeight: 500, padding: '3px 6px', borderRadius: 6,
          border: 'none', background: tc.bg, color: tc.fg,
          cursor: 'pointer', fontFamily: 'inherit',
        }}
      >
        <option value="unknown">Unknown</option>
        <option value="visitor">Visitor</option>
        <option value="staff">Staff</option>
      </select>

      {/* Stats */}
      <div style={{ textAlign: 'right', flexShrink: 0, width: 110 }}>
        <div style={{ fontSize: 12, fontWeight: 500, color: '#111' }}>
          {p.total_sightings} sighting{p.total_sightings === 1 ? '' : 's'}
        </div>
        <div style={{ fontSize: 10, color: '#aaa', marginTop: 1 }}>
          {p.last_seen_at
            ? `Seen ${new Date(p.last_seen_at + 'Z').toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`
            : 'Never seen'}
        </div>
      </div>
    </div>
  )
}

export default function People() {
  const [persons, setPersons] = useState(null)
  const [query,   setQuery]   = useState('')

  const fetchPersons = () => {
    axios.get(`${API}/persons`).then(r => setPersons(r.data)).catch(() => setPersons([]))
  }

  useEffect(() => {
    fetchPersons()
    const iv = setInterval(fetchPersons, 10000)
    return () => clearInterval(iv)
  }, [])

  const filtered = (persons || []).filter(p => {
    if (!query) return true
    const q = query.toLowerCase()
    return (p.display_name || '').toLowerCase().includes(q)
        || p.unique_code.toLowerCase().includes(q)
  })

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="card" style={{ padding: 0, display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <div style={{
          padding: '12px 14px', borderBottom: '0.5px solid #f0f0f0',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, fontWeight: 500 }}>
            Registered People {persons !== null && <span style={{ color: '#aaa', fontWeight: 400 }}>({filtered.length})</span>}
          </span>
          <input
            value={query} onChange={e => setQuery(e.target.value)}
            placeholder="Search name or code…"
            style={{
              fontSize: 12, padding: '5px 10px', borderRadius: 8,
              border: '0.5px solid #e0e0e0', outline: 'none', width: 190,
              fontFamily: 'inherit', background: '#fafafa',
            }}
          />
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {persons === null ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: '#bbb', fontSize: 12 }}>Loading…</div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: '#bbb', fontSize: 12 }}>
              {query ? 'No matches' : 'No people registered yet — start a camera and step in front of it'}
            </div>
          ) : filtered.map(p => (
            <PersonRow key={p.unique_code} p={p} onSaved={fetchPersons} />
          ))}
        </div>
      </div>
    </div>
  )
}
