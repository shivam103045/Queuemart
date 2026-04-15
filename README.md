# QueueSmart — Intelligent Crowd Monitoring & Queue Optimization System

A full-stack Python Flask mini project using:
- **Flask** — web framework & REST API
- **SQLite** — database (auto-created, no setup needed)
- **OpenCV + NumPy** — YOLO-style annotated camera frame generation
- **Pillow** — QR code generation
- **Chart.js** — live density graphs (CDN, no install)

---

## Project Structure

```
queuemart/
├── app.py                  ← Main Flask app (backend + API)
├── requirements.txt        ← Python dependencies
├── queue.db                ← SQLite database (auto-created on first run)
└── templates/
    ├── base.html           ← Shared nav, toast, CSS variables
    ├── index.html          ← Landing page with live stats
    ├── register.html       ← Registration form (name, phone, email, service)
    ├── token.html          ← Token confirmation + QR code + wait time
    └── dashboard.html      ← Admin dashboard with YOLO feed + queue management
```

---

## Setup & Run

### 1. Install dependencies
```bash
pip install flask Pillow numpy opencv-python
```

### 2. Run the app
```bash
cd queuemart
python app.py
```

### 3. Open in browser
```
http://localhost:5000              ← Home / Landing page
http://localhost:5000/register     ← User registration
http://localhost:5000/dashboard    ← Admin live dashboard
```

---

## User Flow

1. **QR Scan** → User scans QR code at venue → opens `http://localhost:5000`
2. **Select Service** → Click hospital / bank / event / govt / retail
3. **Register** → Fill name, phone, email, select priority (normal / senior / disabled)
4. **Token Generated** → Unique token (T-001, T-002...) with:
   - Estimated wait time (calculated from queue position × service speed)
   - YOLO crowd density (live people count from OpenCV simulation)
   - QR code for the token
   - Queue progress bar
5. **Dashboard** → Admin sees:
   - Live YOLO annotated camera feed (switches between 4 zones)
   - Zone-wise crowd density bars
   - Crowd density chart (last 30 min)
   - Full live queue list with priorities
   - "Call Next Token" button

---

## REST API Endpoints

| Method | Endpoint              | Description                        |
|--------|-----------------------|------------------------------------|
| POST   | `/api/register`       | Register user, get token           |
| GET    | `/api/status/<token>` | Check token wait time & status     |
| GET    | `/api/queue`          | Get full queue list                |
| POST   | `/api/next`           | Admin: call next token             |
| GET    | `/api/crowd`          | YOLO crowd data for all zones      |
| GET    | `/api/crowd/frame`    | YOLO annotated camera frame (b64)  |
| GET    | `/api/crowd/history`  | Crowd density history for chart    |
| GET    | `/api/stats`          | Summary stats                      |

---

## Upgrade to Real YOLO

To use actual YOLOv8 instead of the simulation:

```bash
pip install ultralytics
```

In `app.py`, replace `yolo_detect_crowd()` with:

```python
from ultralytics import YOLO
model = YOLO('yolov8n.pt')  # downloads automatically

def yolo_detect_crowd(zone='main'):
    cap = cv2.VideoCapture(0)  # or camera index / RTSP URL
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return {...}  # fallback

    results = model(frame, classes=[0])  # class 0 = person
    boxes = []
    for r in results[0].boxes:
        x1, y1, x2, y2 = map(int, r.xyxy[0])
        conf = float(r.conf[0])
        boxes.append({"x":x1,"y":y1,"w":x2-x1,"h":y2-y1,"conf":conf,"label":"person"})

    count = len(boxes)
    # annotate frame and return...
```

---

## Features

- ✅ QR-based registration portal
- ✅ Real-time token generation with SQLite persistence
- ✅ Priority queue (senior / disabled / pregnant)
- ✅ YOLO OpenCV annotated camera feed (4 zones)
- ✅ Zone-wise crowd density monitoring
- ✅ Estimated wait time from crowd density
- ✅ Admin dashboard with live queue management
- ✅ Background simulation thread (auto-adds users, auto-serves tokens)
- ✅ REST API for all operations
- ✅ Responsive dark UI with Chart.js density graph
- ✅ QR code generation per token (no external library)
