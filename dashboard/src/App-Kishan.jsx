import React, { useState } from 'react'
import SearchBar from './components/SearchBar.jsx'
import PersonTrail from './components/PersonTrail.jsx'
import StatsPanel from './components/StatsPanel.jsx'
import RegisterPerson from './components/RegisterPerson.jsx'
import ObjectFeed from './components/ObjectFeed.jsx'

export default function App() {
  const [activeTab, setActiveTab] = useState('trail')
  const [searchValue, setSearchValue] = useState('')
  const [activeCode, setActiveCode] = useState('')
  const [searching, setSearching] = useState(false)

  const handleSearch = () => {
    const code = searchValue.trim().toUpperCase()
    if (!code) return
    setSearching(true)
    setActiveCode(code)
    setTimeout(() => setSearching(false), 200)
  }

  const handleRegistered = (code) => {
    setSearchValue(code)
    setActiveCode(code)
    setActiveTab('trail')
  }

  const tabs = [
    { id: 'trail',    label: 'Person Trail',    icon: 'M9 20l-5.447-2.724A1 1 0 0 1 3 16.382V5.618a1 1 0 0 1 1.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0 0 21 18.382V7.618a1 1 0 0 0-1.447-.894L15 9m0 8V9m0 0L9 7' },
    { id: 'register', label: 'Register Person', icon: 'M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM3 20a6 6 0 0 1 12 0v1H3v-1z' },
    { id: 'objects',  label: 'Object Feed',     icon: 'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z' },
  ]

  return (
    <div className="min-h-screen bg-metro-900 bg-grid-pattern flex flex-col">
      {/* Header */}
      <header className="border-b border-metro-700/50 bg-metro-800/80 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="w-9 h-9 rounded-xl bg-metro-accent flex items-center justify-center shadow-lg shadow-blue-500/30">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
            </div>
            <div>
              <h1 className="text-base font-bold text-white leading-tight">SmartDetect</h1>
              <p className="text-xs text-slate-400">Universal Detection System</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-metro-green font-medium">
            <span className="w-2 h-2 rounded-full bg-metro-green animate-pulse-slow" />
            LIVE
          </div>
        </div>
      </header>

      <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 py-8 space-y-8">
        {/* Stats */}
        <section aria-label="System Statistics"><StatsPanel /></section>

        {/* Tab nav */}
        <div className="flex gap-1 p-1 bg-metro-800/60 rounded-xl border border-metro-700/50 w-fit">
          {tabs.map(tab => (
            <button
              key={tab.id}
              id={`tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-150
                ${activeTab === tab.id
                  ? 'bg-metro-accent text-white shadow-lg shadow-blue-500/20'
                  : 'text-slate-400 hover:text-white hover:bg-metro-700/60'
                }`}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={tab.icon} />
              </svg>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Person Trail Tab */}
        {activeTab === 'trail' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-6 animate-fade-in">
              <div className="glass-card p-5">
                <label className="block text-sm font-semibold text-slate-300 mb-3">Search by Tracking Code</label>
                <SearchBar value={searchValue} onChange={setSearchValue} onSearch={handleSearch} loading={searching} />
              </div>
              {activeCode && (
                <div className="glass-card p-5">
                  <div className="flex items-center justify-between mb-5">
                    <div>
                      <h2 className="text-base font-semibold text-white">Movement Trail</h2>
                      <p className="text-xs text-slate-400 mt-0.5 font-mono">{activeCode}</p>
                    </div>
                    <button onClick={() => { setActiveCode(''); setSearchValue('') }}
                      className="text-slate-500 hover:text-white transition-colors p-1 rounded">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  <PersonTrail unique_code={activeCode} />
                </div>
              )}
              {!activeCode && (
                <div className="glass-card p-14 text-center animate-fade-in">
                  <svg className="w-20 h-20 mx-auto text-metro-700 mb-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.2}
                      d="M9 20l-5.447-2.724A1 1 0 0 1 3 16.382V5.618a1 1 0 0 1 1.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0 0 21 18.382V7.618a1 1 0 0 0-1.447-.894L15 9m0 8V9m0 0L9 7" />
                  </svg>
                  <h3 className="text-lg font-semibold text-slate-400 mb-1">Enter a Tracking Code</h3>
                  <p className="text-slate-600 text-sm">Search for an SDT-XXXX code above<br />to view their full movement trail.</p>
                </div>
              )}
            </div>
            <aside className="space-y-4 animate-fade-in">
              <div className="glass-card p-5">
                <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
                  <svg className="w-4 h-4 text-metro-teal" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
                  </svg>
                  How It Works
                </h3>
                <ol className="space-y-3 text-sm text-slate-400">
                  {[
                    'Person is registered at any location via face scan.',
                    'AI assigns a unique SDT-XXXX tracking code.',
                    'Cameras across zones identify the person in real-time.',
                    'Each detection is logged as a sighting with confidence.',
                    'Trail view shows the complete journey chronologically.',
                  ].map((step, i) => (
                    <li key={i} className="flex gap-3">
                      <span className="w-5 h-5 rounded-full bg-metro-700 text-metro-accent text-xs font-bold flex items-center justify-center flex-shrink-0 mt-0.5">{i + 1}</span>
                      {step}
                    </li>
                  ))}
                </ol>
              </div>
              <div className="glass-card p-5">
                <h3 className="text-sm font-semibold text-slate-300 mb-3 flex items-center gap-2">
                  <svg className="w-4 h-4 text-metro-amber" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                  Tech Stack
                </h3>
                <div className="flex flex-wrap gap-2">
                  {['InsightFace', 'YOLOv8', 'DeepSORT', 'torchreid', 'FastAPI', 'SQLite', 'OpenCV'].map(tech => (
                    <span key={tech} className="badge-teal text-xs">{tech}</span>
                  ))}
                </div>
              </div>
            </aside>
          </div>
        )}

        {/* Register Tab */}
        {activeTab === 'register' && (
          <div className="max-w-xl mx-auto animate-fade-in">
            <div className="glass-card p-6">
              <div className="mb-5">
                <h2 className="text-lg font-semibold text-white">Register a Person</h2>
                <p className="text-sm text-slate-400 mt-1">
                  Upload a clear face photo, select location and zone.
                  A unique SDT-XXXX tracking code will be assigned instantly.
                </p>
              </div>
              <RegisterPerson onRegistered={handleRegistered} />
            </div>
          </div>
        )}

        {/* Object Feed Tab */}
        {activeTab === 'objects' && (
          <div className="animate-fade-in">
            <ObjectFeed />
          </div>
        )}
      </main>

      <footer className="border-t border-metro-700/40 py-4 text-center text-xs text-slate-600">
        SmartDetect &mdash; Universal Camera Detection System &mdash; v2.0.0
      </footer>
    </div>
  )
}
