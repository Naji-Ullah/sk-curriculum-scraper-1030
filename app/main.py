"""FastAPI app for Saskatchewan Curriculum Scraper (Levels 10, 20, 30)."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.curriculum_config import CURRICULA
from app.scraper import OUTPUT_DIR, scrape_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SK Curriculum Scraper (Levels 10, 20, 30)")

scraping_state = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current": "",
    "errors": [],
    "completed": False,
}


def progress_callback(stage: str, current: int, total: int, message: str = ""):
    if stage == "overall":
        scraping_state["progress"] = current
        scraping_state["total"] = total
        scraping_state["current"] = message


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.post("/api/scrape")
async def start_scrape():
    if scraping_state["is_running"]:
        return JSONResponse({"status": "already_running"}, status_code=409)

    scraping_state["is_running"] = True
    scraping_state["progress"] = 0
    scraping_state["total"] = 0
    scraping_state["current"] = "Starting..."
    scraping_state["errors"] = []
    scraping_state["completed"] = False

    asyncio.create_task(run_scraper())
    return {"status": "started"}


async def run_scraper():
    try:
        results = await scrape_all(progress_callback=progress_callback)
        errors = []
        for r in results:
            for name, data in r.items():
                if "error" in data:
                    errors.append(f"{name}: {data['error']}")
        scraping_state["errors"] = errors
        scraping_state["completed"] = True
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        scraping_state["errors"].append(str(e))
    finally:
        scraping_state["is_running"] = False


@app.get("/api/progress")
async def get_progress():
    return scraping_state


@app.get("/api/results")
async def get_results():
    if not OUTPUT_DIR.exists():
        return {"files": []}

    files = []
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        subject_name = list(data.keys())[0] if data else f.stem
        level_names = [k for k in data.get(subject_name, {}) if k.startswith("Level")]
        outcome_count = 0
        for key, val in data.get(subject_name, {}).items():
            if isinstance(val, dict) and "Outcomes" in val:
                outcome_count += len(val["Outcomes"])

        files.append({
            "filename": f.name,
            "subject": subject_name,
            "level": level_names[0] if level_names else "N/A",
            "outcomes": outcome_count,
            "size": f.stat().st_size,
        })

    return {"files": files}


@app.get("/api/results/{filename}")
async def get_result_file(filename: str):
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(filepath, media_type="application/json", filename=filename)


@app.get("/api/curricula")
async def get_curricula():
    active = [
        {"name": c["name"], "id": c["id"], "category": c["category"]}
        for c in CURRICULA
        if c.get("id") is not None and not c.get("skip") and not c.get("treaty")
    ]
    return {"curricula": active, "total": len(active)}


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SK Curriculum Scraper - Levels 10, 20, 30</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #333; }
        .header { background: #1a5632; color: white; padding: 24px 32px; }
        .header h1 { font-size: 24px; margin-bottom: 4px; }
        .header p { opacity: 0.85; font-size: 14px; }
        .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
        .controls { display: flex; gap: 12px; margin-bottom: 24px; }
        .btn { padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.2s; }
        .btn-primary { background: #1a5632; color: white; }
        .btn-primary:hover { background: #0d3a1f; }
        .btn-primary:disabled { background: #999; cursor: not-allowed; }
        .btn-secondary { background: #e0e0e0; color: #333; }
        .btn-secondary:hover { background: #ccc; }
        .progress-section { background: white; border-radius: 8px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .progress-bar { height: 8px; background: #e0e0e0; border-radius: 4px; overflow: hidden; margin: 12px 0; }
        .progress-fill { height: 100%; background: #1a5632; transition: width 0.3s; border-radius: 4px; }
        .progress-text { font-size: 13px; color: #666; }
        .results { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .results h2 { margin-bottom: 16px; font-size: 18px; }
        .results-table { width: 100%; border-collapse: collapse; }
        .results-table th, .results-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }
        .results-table th { background: #f8f8f8; font-weight: 600; }
        .results-table tr:hover { background: #f9f9f9; }
        .results-table a { color: #1a5632; text-decoration: none; font-weight: 500; }
        .results-table a:hover { text-decoration: underline; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
        .badge-level { background: #e8f5e9; color: #1a5632; }
        .badge-outcome { background: #e3f2fd; color: #1565c0; }
        .error-list { margin-top: 12px; }
        .error-item { background: #fff3e0; padding: 8px 12px; border-radius: 4px; margin-bottom: 4px; font-size: 13px; color: #e65100; }
        .category-header { background: #f0f7f3; padding: 8px 12px; font-weight: 600; color: #1a5632; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }
        .json-viewer { background: #1e1e1e; color: #d4d4d4; padding: 20px; border-radius: 8px; max-height: 600px; overflow: auto; font-family: 'Consolas', monospace; font-size: 13px; white-space: pre-wrap; margin-top: 12px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; }
        .modal-content { background: white; margin: 40px auto; max-width: 900px; border-radius: 8px; max-height: calc(100vh - 80px); display: flex; flex-direction: column; }
        .modal-header { padding: 16px 20px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
        .modal-body { padding: 20px; overflow: auto; flex: 1; }
        .modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #666; }
        .stats { display: flex; gap: 24px; margin-bottom: 24px; }
        .stat-card { background: white; padding: 16px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); flex: 1; text-align: center; }
        .stat-value { font-size: 28px; font-weight: 700; color: #1a5632; }
        .stat-label { font-size: 12px; color: #888; margin-top: 4px; text-transform: uppercase; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Saskatchewan Curriculum Scraper</h1>
        <p>Scrape Levels 10, 20, 30 curriculum data from <a href="https://curriculum.gov.sk.ca/" style="color:#a5d6a7;">curriculum.gov.sk.ca</a></p>
    </div>
    <div class="container">
        <div class="controls">
            <button class="btn btn-primary" id="startBtn" onclick="startScraping()">Start Scraping</button>
            <button class="btn btn-secondary" onclick="refreshResults()">Refresh Results</button>
        </div>

        <div class="stats" id="stats" style="display:none;">
            <div class="stat-card"><div class="stat-value" id="statSubjects">0</div><div class="stat-label">Subjects</div></div>
            <div class="stat-card"><div class="stat-value" id="statOutcomes">0</div><div class="stat-label">Outcomes</div></div>
            <div class="stat-card"><div class="stat-value" id="statFiles">0</div><div class="stat-label">JSON Files</div></div>
        </div>

        <div class="progress-section" id="progressSection" style="display:none;">
            <strong>Scraping Progress</strong>
            <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
            <div class="progress-text" id="progressText">0%</div>
            <div class="error-list" id="errorList"></div>
        </div>

        <div class="results" id="resultsSection">
            <h2>Results</h2>
            <table class="results-table">
                <thead>
                    <tr>
                        <th>Subject</th>
                        <th>Level</th>
                        <th>Outcomes</th>
                        <th>Size</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="resultsBody">
                    <tr><td colspan="5" style="text-align:center;color:#999;padding:40px;">No results yet. Click "Start Scraping" to begin.</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="modal" id="jsonModal">
        <div class="modal-content">
            <div class="modal-header">
                <strong id="modalTitle">JSON Data</strong>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="json-viewer" id="jsonViewer"></div>
            </div>
        </div>
    </div>

    <script>
        let pollInterval = null;

        async function startScraping() {
            const btn = document.getElementById('startBtn');
            btn.disabled = true;
            btn.textContent = 'Scraping...';

            document.getElementById('progressSection').style.display = 'block';

            try {
                const resp = await fetch('/api/scrape', { method: 'POST' });
                const data = await resp.json();
                if (data.status === 'already_running') {
                    startPolling();
                    return;
                }
                startPolling();
            } catch (e) {
                alert('Failed to start scraping: ' + e.message);
                btn.disabled = false;
                btn.textContent = 'Start Scraping';
            }
        }

        function startPolling() {
            if (pollInterval) clearInterval(pollInterval);
            pollInterval = setInterval(pollProgress, 1500);
        }

        async function pollProgress() {
            try {
                const resp = await fetch('/api/progress');
                const data = await resp.json();

                const pct = data.total > 0 ? Math.round((data.progress / data.total) * 100) : 0;
                document.getElementById('progressFill').style.width = pct + '%';
                document.getElementById('progressText').textContent =
                    data.is_running ? (pct + '% (' + data.progress + '/' + data.total + ') - ' + (data.current || 'Working...'))
                    : data.completed ? '100% - Complete!'
                    : '0%';

                if (data.is_running) {
                    document.getElementById('progressSection').style.display = 'block';
                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('startBtn').textContent = 'Scraping...';
                }

                if (data.errors && data.errors.length > 0) {
                    const el = document.getElementById('errorList');
                    el.innerHTML = data.errors.map(e => '<div class="error-item">' + e + '</div>').join('');
                }

                refreshResults();

                if (!data.is_running && (data.completed || data.progress > 0)) {
                    clearInterval(pollInterval);
                    pollInterval = null;
                    document.getElementById('startBtn').disabled = false;
                    document.getElementById('startBtn').textContent = 'Start Scraping';
                    if (data.completed) {
                        document.getElementById('progressFill').style.width = '100%';
                        document.getElementById('progressText').textContent = '100% - Complete!';
                    }
                    refreshResults();
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }

        async function refreshResults() {
            try {
                const resp = await fetch('/api/results');
                const data = await resp.json();
                const tbody = document.getElementById('resultsBody');

                if (!data.files || data.files.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#999;padding:40px;">No results yet. Click "Start Scraping" to begin.</td></tr>';
                    document.getElementById('stats').style.display = 'none';
                    return;
                }

                let totalOutcomes = 0;
                let html = '';

                for (const file of data.files) {
                    totalOutcomes += file.outcomes;
                    html += '<tr>';
                    html += '<td>' + file.subject + '</td>';
                    html += '<td><span class="badge badge-level">' + file.level + '</span></td>';
                    html += '<td><span class="badge badge-outcome">' + file.outcomes + ' outcomes</span></td>';
                    html += '<td>' + formatSize(file.size) + '</td>';
                    html += '<td><a href="/api/results/' + encodeURIComponent(file.filename) + '" target="_blank">Download</a> | ';
                    html += '<a href="#" onclick="viewJSON(\\'' + file.filename.replace(/'/g, "\\\\'") + '\\');return false;">View</a></td>';
                    html += '</tr>';
                }

                tbody.innerHTML = html;

                document.getElementById('stats').style.display = 'flex';
                document.getElementById('statSubjects').textContent = data.files.length;
                document.getElementById('statOutcomes').textContent = totalOutcomes;
                document.getElementById('statFiles').textContent = data.files.length;
            } catch (e) {
                console.error('Refresh error:', e);
            }
        }

        async function viewJSON(filename) {
            try {
                const resp = await fetch('/api/results/' + encodeURIComponent(filename));
                const data = await resp.json();
                document.getElementById('modalTitle').textContent = filename;
                document.getElementById('jsonViewer').textContent = JSON.stringify(data, null, 2);
                document.getElementById('jsonModal').style.display = 'block';
            } catch (e) {
                alert('Failed to load JSON: ' + e.message);
            }
        }

        function closeModal() {
            document.getElementById('jsonModal').style.display = 'none';
        }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        document.getElementById('jsonModal').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });

        // On page load: check if scraping is already running, load existing results
        async function init() {
            refreshResults();
            try {
                const resp = await fetch('/api/progress');
                const data = await resp.json();
                if (data.is_running) {
                    document.getElementById('progressSection').style.display = 'block';
                    document.getElementById('startBtn').disabled = true;
                    document.getElementById('startBtn').textContent = 'Scraping...';
                    startPolling();
                } else if (data.completed) {
                    document.getElementById('progressSection').style.display = 'block';
                    document.getElementById('progressFill').style.width = '100%';
                    document.getElementById('progressText').textContent = '100% - Complete!';
                }
            } catch(e) { console.error('Init error:', e); }
        }
        init();
    </script>
</body>
</html>
"""
