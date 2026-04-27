# Metro Person Tracking System

<div align="center">

**AI-powered real-time person tracking and re-identification for metro networks**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react)](https://react.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## Overview

The **Metro Person Tracking System** is a production-grade AI surveillance platform designed for metropolitan transit networks. It uses deep learning to automatically detect, register, and track individuals across multiple camera feeds in real time — assigning each person a unique tracking code and building a chronological movement trail as they travel through the network.

The system combines **InsightFace** ArcFace embeddings for face recognition with **OSNet** person re-identification as a fallback when faces are obscured, and uses **DeepSORT** for stable multi-object tracking across video frames. A **FastAPI** backend stores all data in PostgreSQL with `pgvector` for fast similarity search, while a **React** dashboard gives operators an intuitive interface to search persons and view movement trails in real time.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    React Dashboard                       │
│         (Vite + Tailwind · http://localhost:5173)        │
└─────────────────────┬───────────────────────────────────┘
                      │ REST API (JWT Bearer)
┌─────────────────────▼───────────────────────────────────┐
│                   FastAPI Backend                        │
│  /auth/login  /register  /sighting  /person/trail        │
│  /stations    /logs      /health                         │
└──────┬──────────────┬────────────────────┬──────────────┘
       │              │                    │
┌──────▼──────┐ ┌─────▼──────┐  ┌─────────▼────────────┐
│  PostgreSQL │ │ InsightFace│  │  Camera Processors    │
│  pgvector   │ │  buffalo_l │  │  (DeepSORT + Re-ID)   │
│  (SQLite in │ │  OSNet     │  │  Auto-reconnect logic  │
│   dev mode) │ └────────────┘  └──────────────────────┘
└─────────────┘
```

**Key components:**

| Layer | Technology | Purpose |
|---|---|---|
| Face Recognition | InsightFace / buffalo_l | 512-dim ArcFace embeddings |
| Person Re-ID | torchreid / OSNet-x1.0 | Appearance features (face fallback) |
| Object Tracking | DeepSORT (deep-sort-realtime) | Stable IDs across frames |
| Backend API | FastAPI + Uvicorn | REST endpoints, JWT auth |
| Database | PostgreSQL + pgvector | Embeddings + sighting storage |
| Dev Database | SQLite | Zero-config local development |
| Frontend | React 18 + Vite + Tailwind | Operator dashboard |
| Containerisation | Docker Compose | One-command full-stack startup |

---

## Quick Start — Docker (Recommended)

> **Requirements:** Docker Desktop 4.x+, Docker Compose v2

```bash
# 1. Clone the repository
git clone https://github.com/your-org/metro-tracking.git
cd metro-tracking

# 2. Start all services (Postgres + Backend + Frontend)
docker compose up

# 3. Dashboard will be available at:
#    http://localhost:5173    ← React dashboard
#    http://localhost:8000/docs ← Swagger API docs

# 4. (Optional) Load demo data
docker compose exec backend python scripts/demo_setup.py --days 3
```

On first start, `docker/init.sql` automatically:
- Enables the `pgvector` extension
- Creates all tables
- Seeds 8 metro stations

---

## Local Development (No Docker)

### Prerequisites
- Python 3.10+
- Node.js 18+

### Backend setup

```bash
# Install Python dependencies
pip install fastapi uvicorn sqlalchemy python-dotenv pydantic \
            opencv-python numpy requests python-multipart deep-sort-realtime \
            pyjwt

# Optional ML packages (for full accuracy)
pip install insightface onnxruntime      # face recognition
pip install torch torchvision torchreid  # person re-identification

# Configure environment (SQLite by default)
cp .env.example .env   # Edit DATABASE_URL if using PostgreSQL

# Start backend
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend setup

```bash
cd dashboard
npm install
npm run dev
# Opens at http://localhost:5173
```

### Seed demo data

```bash
# Seed 8 stations
python scripts/seed_db.py

# Seed 10 demo persons with 3 days of movement history
python scripts/demo_setup.py --days 3
```

---

## Running the Demo

After starting the system (Docker or local):

1. Open **http://localhost:5173**
2. Click **"Register Person"** → upload a face photo → select entry station
3. Copy the assigned `MET-YYYYMMDD-XXXX` code
4. Click **"Person Trail"** → paste the code → view the movement timeline

To see a pre-populated dashboard with realistic data:
```bash
python scripts/demo_setup.py --days 3
```
Then search for any code like `MET-<today>-D000` through `MET-<today>-D009`.

---

## API Documentation

Full interactive docs at: **http://localhost:8000/docs**

### Authentication

All protected routes require a **JWT Bearer token**.

```bash
# Get a token
curl -X POST http://localhost:8000/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username": "operator", "password": "metroOp2024"}'

# Use the token
curl http://localhost:8000/stations \
     -H "Authorization: Bearer <token>"
```

**Default credentials:**

| Username | Password | Role |
|---|---|---|
| `admin` | `metroAdmin2024` | Full access |
| `operator` | `metroOp2024` | Search & view |

### Endpoints

| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/auth/login` | Public | Obtain JWT token |
| `GET` | `/health` | Public | Service health check |
| `POST` | `/register` | operator+ | Register person from base64 image |
| `GET` | `/person/{code}/trail` | operator+ | Get movement trail |
| `POST` | `/sighting` | operator+ | Log a camera sighting |
| `GET` | `/stations` | operator+ | List all stations |
| `POST` | `/stations` | admin | Create a new station |
| `GET` | `/logs?lines=100` | admin | Fetch recent system logs |

---

## Testing

```bash
# End-to-end integration test
python scripts/e2e_test.py

# Load test (50 concurrent users, 60 seconds)
python scripts/load_test.py --standalone --users 50 --duration 60s

# Accuracy evaluation (100 probe images)
python scripts/accuracy_test.py --images 100 --persons 10
```

---

## Tech Stack

| Category | Technology |
|---|---|
| **ML — Face Recognition** | InsightFace, ArcFace (buffalo_l), ONNX Runtime |
| **ML — Person Re-ID** | torchreid, OSNet-x1.0, PyTorch |
| **ML — Tracking** | DeepSORT (deep-sort-realtime) |
| **ML — Detection** | OpenCV (placeholder: YOLO-ready) |
| **Backend** | FastAPI, Uvicorn, Pydantic v2, SQLAlchemy |
| **Auth** | PyJWT, Bearer tokens, RBAC (operator/admin) |
| **Database** | PostgreSQL + pgvector (prod), SQLite (dev) |
| **Logging** | Python logging, RotatingFileHandler, structured events |
| **Frontend** | React 18, Vite 5, Tailwind CSS, Axios |
| **Containerisation** | Docker Compose, multi-stage Dockerfile |
| **Testing** | Custom E2E suite, Locust load test, accuracy evaluator |

---

## Project Structure

```
metro-tracking/
├── backend/          # FastAPI app, auth, logger
├── cameras/          # CameraProcessor with auto-reconnect
├── database/         # SQLAlchemy models, queries, db setup
├── docker/           # Dockerfiles + init.sql
├── dashboard/        # React + Vite frontend
├── models/           # Model cache directory
├── recognition/      # FaceRecognizer, PersonReID, registration
├── scripts/          # e2e_test, load_test, accuracy_test,
│                     # demo_setup, seed_db, run_camera
├── tracker/          # DeepSORT wrapper
├── logs/             # Rotating system.log (auto-created)
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## License

MIT © 2026 Metro Tracking Team
