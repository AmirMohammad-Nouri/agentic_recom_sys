"""Server hosting API routes, Professional Tabbed UI, Interactive Graph, and RAG Ingestion."""
import json
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .catalogue import Catalogue
from .db import Database
from .engine import public, recommend
from .features import ReferenceScaler
from .graph import KnowledgeGraph
from .learner import Learner
from .mcp_tools import MCPToolRegistry
from .rag import FoodRAG
from .ranker import DEFAULT_WEIGHTS, normalize_weights
from .supervisor import SupervisorAgent
from .agent import SupervisorChatAgent

cat = Catalogue.load("data/fixture/recipes.json")
with open("data/fixture/ingredients.json") as f:
    ing_db = json.load(f)

scaler = ReferenceScaler.fit(cat)
db = Database("foodrec.db")
kg = KnowledgeGraph()
kg.seed_from_catalogue(cat, ing_db)

rag = FoodRAG()
rag.index_catalogue_and_ingredients(cat, ing_db)

learners = {}


def get_learner(user_id):
    if user_id not in learners:
        learners[user_id] = Learner(cat)
    return learners[user_id]


mcp_registry = MCPToolRegistry(db, cat, scaler, kg, rag, learners, ing_db=ing_db)
supervisor_analyzer = SupervisorAgent(kg, rag, model="qwen3:1.7b")
chat_agent = SupervisorChatAgent(mcp_registry, supervisor_analyzer, model="qwen3:1.7b")


class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_html(HTML_UI)
            return

        if path == "/api/profile":
            uid = query.get("user_id", ["demo_user"])[0]
            prof = db.get_latest_profile(uid)
            learner = get_learner(uid)
            taste_beliefs = learner.taste.top(5) if hasattr(learner, 'taste') else []
            self._send_json({
                "profile": prof or {"version": 0, "declared": {}},
                "inferred_beliefs": [{"concept": c, "belief": round(m, 2)} for c, m, _, _ in taste_beliefs]
            })
            return

        if path == "/api/goal":
            uid = query.get("user_id", ["demo_user"])[0]
            goal = db.get_latest_goal(uid)
            self._send_json(goal or {"version": 0, "weights": DEFAULT_WEIGHTS, "diversity": 0.3, "mode": "lightweight"})
            return

        if path == "/api/kg/graph":
            # Serve structured nodes and edges for 2D visual canvas rendering
            self._send_json(kg.get_vis_network_graph())
            return

        if path == "/api/rag/docs":
            # List all documents currently indexed in RAG
            self._send_json({"documents": rag.get_all_documents()})
            return

        self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/profile/save":
            uid = body.get("user_id", "demo_user")
            declared = body.get("declared", {})
            ver = db.save_profile(uid, declared)
            learner = get_learner(uid)
            learner.apply({
                "event_id": str(uuid.uuid4())[:8], "type": "profile_created",
                "ts": 0.0, "payload": {"declared": declared, "weights": DEFAULT_WEIGHTS}
            })
            self._send_json({"status": "ok", "version": ver})
            return

# foodrec/server.py (do_POST handler)

        if path == "/api/goal/save":
            uid = body.get("user_id", "demo_user")
            name = body.get("name", "Saved Priorities")
            weights = body.get("weights", DEFAULT_WEIGHTS)
            diversity = body.get("diversity", 0.3)
            mode = body.get("mode", "lightweight")

            res = normalize_weights(weights)
            norm_w = res[0] if isinstance(res, tuple) else res

            ver = db.save_goal(uid, name, norm_w, diversity, mode)
            learner = get_learner(uid)
            learner.apply({
                "event_id": str(uuid.uuid4())[:8],
                "type": "goal_saved",
                "ts": 0.0,
                "payload": {"weights": norm_w}
            })
            self._send_json({"status": "ok", "version": ver, "weights_applied": norm_w})
            return

        if path == "/api/recommend":
            uid = body.get("user_id", "demo_user")
            prof_data = db.get_latest_profile(uid) or {"version": 1, "declared": {}}
            goal_data = db.get_latest_goal(uid) or {
                "version": 1, "weights": DEFAULT_WEIGHTS, "diversity": 0.3, "mode": "lightweight"
            }

            req_weights = body.get("weights") or goal_data["weights"]
            
            res = normalize_weights(req_weights)
            norm_w = res[0] if isinstance(res, tuple) else res

            req_config = {
                "weights": norm_w,
                "diversity": body.get("diversity", goal_data.get("diversity", 0.3)),
                "mode": body.get("mode", goal_data.get("mode", "lightweight")),
                "k": 5
            }

            learner = get_learner(uid)
            rec_res = recommend(cat, scaler, learner, prof_data["declared"], req_config, now=0.0)
            pub_res = public(rec_res)

            sup_res = supervisor_analyzer.analyze(uid, prof_data["declared"], goal_data, learner)
            pub_res["supervisor"] = sup_res

            req_id = str(uuid.uuid4())[:8]
            db.save_snapshot(req_id, uid, prof_data["version"], goal_data["version"],
                             req_config, pub_res.get("results", []), pub_res.get("ranking_table", []),
                             sup_res["reasoning_trace"])
            pub_res["request_id"] = req_id
            self._send_json(pub_res)
            return

        if path == "/api/chat":
            uid = body.get("user_id", "demo_user")
            prompt = body.get("prompt", "")
            res = chat_agent.process_chat_prompt(uid, prompt)
            self._send_json(res)
            return

        if path == "/api/rag/ingest":
            doc_id = body.get("doc_id", f"doc_{str(uuid.uuid4())[:6]}")
            title = body.get("title", "Custom Document")
            text = body.get("text", "")
            rag.add_custom_document(doc_id, title, text)
            self._send_json({"status": "success", "doc_id": doc_id})
            return

        self._send_json({"error": "Not found"}, 404)


# Professional Headline Tabbed UI HTML
HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>FoodRec - Controllable Recommender Platform</title>
    <!-- Include vis-network for interactive 2D Graph Visualization -->
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        :root {
            --bg: #090d16;
            --panel: #111827;
            --panel-border: #1e293b;
            --accent: #38bdf8;
            --accent-hover: #0284c7;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --card-bg: #0f172a;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 0;
        }
        /* Headline Tabs Header */
        .navbar {
            background: var(--panel);
            border-bottom: 1px solid var(--panel-border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
        }
        .brand {
            font-size: 1.25rem;
            font-weight: bold;
            color: var(--accent);
        }
        .tabs {
            display: flex;
            gap: 4px;
        }
        .tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 16px 20px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            border-bottom: 3px solid transparent;
            transition: all 0.2s ease;
        }
        .tab-btn:hover {
            color: var(--text);
        }
        .tab-btn.active {
            color: var(--accent);
            border-bottom-color: var(--accent);
            background: rgba(56, 189, 248, 0.05);
        }
        
        /* Tab Content Sections */
        .container {
            max-width: 1200px;
            margin: 24px auto;
            padding: 0 16px;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        
        .card {
            background: var(--panel);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }
        h2, h3 {
            margin-top: 0;
            color: var(--accent);
            font-size: 1.15rem;
        }
        
        /* Grid Layouts */
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .grid-3 { display: grid; grid-template-columns: 340px 1fr; gap: 20px; }
        
        label { display: flex; justify-content: space-between; font-size: 0.88rem; margin-top: 10px; align-items: center; }
        input[type=range] { width: 100%; margin: 6px 0 12px; }
        input[type=text], input[type=number], textarea, select {
            background: var(--card-bg);
            border: 1px solid var(--panel-border);
            color: #fff;
            padding: 8px 12px;
            border-radius: 6px;
            box-sizing: border-box;
        }
        button {
            background: var(--accent);
            color: #000;
            border: none;
            padding: 10px 16px;
            border-radius: 6px;
            font-weight: bold;
            cursor: pointer;
            transition: background 0.2s;
        }
        button:hover { background: var(--accent-hover); }
        button.secondary { background: var(--panel-border); color: #fff; }
        
        /* Recipe Cards */
        .recipe-card {
            background: var(--card-bg);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
        }
        .rank-badge { background: var(--accent); color: #000; padding: 2px 8px; border-radius: 4px; font-weight: bold; }
        .tag { display: inline-block; background: var(--panel-border); padding: 3px 8px; border-radius: 4px; font-size: 0.78rem; margin-right: 6px; margin-top: 6px; }
        
        /* Chat UI */
        .chat-box { height: 520px; overflow-y: auto; background: var(--card-bg); border: 1px solid var(--panel-border); border-radius: 6px; padding: 16px; margin-bottom: 12px; }
        .msg { margin-bottom: 14px; padding: 12px 16px; border-radius: 8px; font-size: 0.92rem; line-height: 1.5; max-width: 92%; }
        .msg.user { background: #1e3a8a; color: #fff; margin-left: auto; }
        .msg.agent { background: #1a2234; color: #e5e7eb; border-left: 4px solid var(--accent); }
        
        /* Graph Visual Canvas */
        #visGraph { width: 100%; height: 500px; background: var(--card-bg); border: 1px solid var(--panel-border); border-radius: 8px; }
    </style>
</head>
<body>

    <!-- Headline Navigation Bar -->
    <div class="navbar">
        <div class="brand">FoodRec Platform</div>
        <div class="tabs">
            <button class="tab-btn active" onclick="openTab('tab-recs', this)">🎯 Recommendations & Priorities</button>
            <button class="tab-btn" onclick="openTab('tab-chat', this)">💬 Supervisor Chat</button>
            <button class="tab-btn" onclick="openTab('tab-graph', this); renderGraphCanvas();">🕸️ Knowledge Graph Canvas</button>
            <button class="tab-btn" onclick="openTab('tab-profile', this)">👤 Profile & Constraints</button>
            <button class="tab-btn" onclick="openTab('tab-rag', this); loadRagDocs();">📚 RAG Knowledge Base</button>
        </div>
    </div>

    <div class="container">
        <!-- TAB 1: RECOMMENDATIONS & PRIORITIES -->
        <div id="tab-recs" class="tab-content active">
            <div class="grid-3">
                <div class="card">
                    <h3>Executable Food Priorities</h3>
                    <label>Preference Match ($w_p$) <span id="val_p">0.30</span></label>
                    <input type="range" id="w_p" min="0" max="1" step="0.05" value="0.30" oninput="updateWeights()">

                    <label>Healthiness ($w_h$) <span id="val_h">0.25</span></label>
                    <input type="range" id="w_h" min="0" max="1" step="0.05" value="0.25" oninput="updateWeights()">

                    <label>Sustainability ($w_s$) <span id="val_s">0.20</span></label>
                    <input type="range" id="w_s" min="0" max="1" step="0.05" value="0.20" oninput="updateWeights()">

                    <label>Affordability ($w_c$) <span id="val_c">0.15</span></label>
                    <input type="range" id="w_c" min="0" max="1" step="0.05" value="0.15" oninput="updateWeights()">

                    <label>Convenience ($w_t$) <span id="val_t">0.10</span></label>
                    <input type="range" id="w_t" min="0" max="1" step="0.05" value="0.10" oninput="updateWeights()">

                    <div style="font-size:0.8rem; color:var(--accent); margin:8px 0;">
                        Applied Weights: <span id="norm_display">0.30 / 0.25 / 0.20 / 0.15 / 0.10</span>
                    </div>

                    <label>Diversity Reranking ($d$) <span id="val_div">0.30</span></label>
                    <input type="range" id="div" min="0" max="1" step="0.05" value="0.30" oninput="document.getElementById('val_div').innerText=this.value">

                    <label>Computation Mode:
                        <select id="mode" style="width:140px;">
                            <option value="lightweight">Lightweight</option>
                            <option value="extended">Extended</option>
                        </select>
                    </label>

                    <button onclick="getRecommendations()" style="width:100%; margin-top:12px;">Compute Recommendations</button>
                    <button class="secondary" onclick="saveGoal()" style="width:100%; margin-top:8px;">Save Priority Goal</button>
                </div>

                <div class="card">
                    <h3>Top 5 Recommended Recipes</h3>
                    <div id="results">Click 'Compute Recommendations' to run pipeline.</div>
                </div>
            </div>
        </div>

        <!-- TAB 2: SUPERVISOR AI CHAT -->
        <div id="tab-chat" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3>Supervisor AI Chat Assistant</h3>
                    <div class="chat-box" id="chatBox">
                        <div class="msg agent">Hello! I am your Supervisor AI. Chat with me to update settings, ask for recommendations, or query knowledge!</div>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <input type="text" id="promptInput" placeholder="e.g. If I want more protein and sugar in my next meal, what do you recommend?" style="flex:1;" onkeypress="if(event.key==='Enter') sendPrompt()">
                        <button onclick="sendPrompt()">Send</button>
                    </div>
                </div>

                <div class="card">
                    <h3>Agent Thought Trace & Tool Reasoning</h3>
                    <div id="thoughtTrace" style="max-height:560px; overflow-y:auto; font-size:0.85rem; color:var(--text-muted);">
                        Reasoning steps and MCP tool execution logs will appear here live...
                    </div>
                </div>
            </div>
        </div>

        <!-- TAB 3: INTERACTIVE KNOWLEDGE GRAPH CANVAS -->
        <div id="tab-graph" class="tab-content">
            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <h3>Interactive Knowledge Graph Visualizer (2D Nodes & Vertices)</h3>
                    <button class="secondary" onclick="renderGraphCanvas()">🔄 Refresh Graph Canvas</button>
                </div>
                <div id="visGraph"></div>
            </div>
        </div>

        <!-- TAB 4: PROFILE & CONSTRAINTS -->
        <div id="tab-profile" class="tab-content">
            <div class="card" style="max-width:600px; margin:0 auto;">
                <h3>User Profile (Current Self) Settings</h3>
                <label>Declared Allergens (comma separated):
                    <input type="text" id="allergens" value="peanuts" style="width:240px;">
                </label>
                <label>Diets:
                    <input type="text" id="diets" value="vegetarian" style="width:240px;">
                </label>
                <label>Disliked Ingredients:
                    <input type="text" id="dislikes" value="miso" style="width:240px;">
                </label>
                <label>Max Preparation Time (Minutes):
                    <input type="number" id="max_time" value="30" style="width:80px;">
                    <span><input type="checkbox" id="strict_time" checked> Strict Limit</span>
                </label>
                <label>Max Cost per Serving (EUR):
                    <input type="number" id="max_cost" value="2.50" step="0.5" style="width:80px;">
                    <span><input type="checkbox" id="strict_cost"> Strict Limit</span>
                </label>
                <button onclick="saveProfile()" style="margin-top:16px;">Save Profile Version</button>
            </div>
        </div>

        <!-- TAB 5: RAG KNOWLEDGE BASE -->
        <div id="tab-rag" class="tab-content">
            <div class="grid-2">
                <div class="card">
                    <h3>Add Custom Information to RAG Retrieval System</h3>
                    <label>Document Title: <input type="text" id="ragTitle" placeholder="e.g. High Protein Mediterranean Guidelines" style="width:100%;"></label>
                    <label style="flex-direction:column; align-items:flex-start; margin-top:12px;">Document Content:
                        <textarea id="ragText" rows="6" style="width:100%; margin-top:6px;" placeholder="Paste dietary guidelines, nutritional rules, or health notes..."></textarea>
                    </label>
                    <button onclick="ingestRagDoc()" style="margin-top:12px;">Ingest into RAG Retriever</button>
                </div>

                <div class="card">
                    <h3>Indexed RAG Knowledge Base Documents</h3>
                    <div id="ragDocList" style="max-height:400px; overflow-y:auto; font-size:0.85rem;">Loading documents...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Tab Switcher
        function openTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }

        function updateWeights() {
            const keys = ['p','h','s','c','t'];
            let raw = {}, sum = 0;
            keys.forEach(k => {
                let v = parseFloat(document.getElementById('w_' + k).value) || 0;
                raw[k] = v;
                sum += v;
            });
            keys.forEach(k => document.getElementById('val_' + k).innerText = raw[k].toFixed(2));
            if (sum === 0) sum = 1;
            let norm = keys.map(k => (raw[k] / sum).toFixed(2)).join(' / ');
            document.getElementById('norm_display').innerText = norm;
        }

        async function saveProfile() {
            const declared = {
                allergens: document.getElementById('allergens').value.split(',').map(s=>s.trim()).filter(Boolean),
                diets: document.getElementById('diets').value.split(',').map(s=>s.trim()).filter(Boolean),
                disliked_ingredients: [{ name: document.getElementById('dislikes').value.trim(), strict: true }],
                max_prep_minutes: { value: parseInt(document.getElementById('max_time').value), strict: document.getElementById('strict_time').checked },
                max_cost_eur: { value: parseFloat(document.getElementById('max_cost').value), strict: document.getElementById('strict_cost').checked }
            };
            await fetch('/api/profile/save', {
                method: 'POST',
                body: JSON.stringify({ user_id: 'demo_user', declared })
            });
            alert('Profile version updated!');
        }

        async function saveGoal() {
            const weights = {
                p: parseFloat(document.getElementById('w_p').value),
                h: parseFloat(document.getElementById('w_h').value),
                s: parseFloat(document.getElementById('w_s').value),
                c: parseFloat(document.getElementById('w_c').value),
                t: parseFloat(document.getElementById('w_t').value)
            };
            await fetch('/api/goal/save', {
                method: 'POST',
                body: JSON.stringify({
                    user_id: 'demo_user', name: 'Saved Priorities', weights,
                    diversity: parseFloat(document.getElementById('div').value),
                    mode: document.getElementById('mode').value
                })
            });
            alert('Priority Goal saved!');
        }

        async function getRecommendations() {
            const weights = {
                p: parseFloat(document.getElementById('w_p').value),
                h: parseFloat(document.getElementById('w_h').value),
                s: parseFloat(document.getElementById('w_s').value),
                c: parseFloat(document.getElementById('w_c').value),
                t: parseFloat(document.getElementById('w_t').value)
            };
            const res = await fetch('/api/recommend', {
                method: 'POST',
                body: JSON.stringify({
                    user_id: 'demo_user', weights,
                    diversity: parseFloat(document.getElementById('div').value),
                    mode: document.getElementById('mode').value
                })
            }).then(r => r.json());

            const container = document.getElementById('results');
            container.innerHTML = '';
            if (res.empty) {
                container.innerHTML = `<div style="color:#f87171; padding:12px; background:#1f1924; border-radius:6px;">${res.empty.message}</div>`;
                return;
            }

            res.results.forEach(r => {
                const card = document.createElement('div');
                card.className = 'recipe-card';
                card.innerHTML = `
                    <div style="display:flex; justify-content:space-between;">
                        <strong><span class="rank-badge">#${r.final_rank}</span> ${r.title}</strong>
                        <span style="color:var(--accent);">Utility Score: ${r.utility.toFixed(3)}</span>
                    </div>
                    <div style="font-size:0.82rem; color:var(--text-muted); margin-top:4px;">
                        Cuisine: ${r.cuisine} | Prep: ${r.prep_minutes} min | Cost: ${r.cost_eur ? r.cost_eur + ' EUR' : 'N/A'} | CO2e: ${r.co2e_kg ? r.co2e_kg + ' kg' : 'N/A'}
                    </div>
                    <div style="margin-top:8px;">
                        ${(r.reasons.fits || []).map(f => `<div class="tag" style="background:#064e3b; color:#34d399;">${f}</div>`).join('')}
                        ${(r.reasons.ranked_here || []).map(rh => `<div class="tag">${rh}</div>`).join('')}
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function formatMessage(txt) {
            if (!txt) return '';
            return txt
                .replace(/### (.*?)\n/g, '<h4 style="margin:12px 0 6px; color:var(--accent); font-size:1.02rem;">$1</h4>')
                .replace(/#### (.*?)\n/g, '<h5 style="margin:10px 0 4px; color:#38bdf8; font-size:0.92rem;">$1</h5>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n- /g, '<br>• ')
                .replace(/\n/g, '<br>');
        }

        async function sendPrompt() {
            const input = document.getElementById('promptInput');
            const prompt = input.value.trim();
            if (!prompt) return;

            const chatBox = document.getElementById('chatBox');
            chatBox.innerHTML += `<div class="msg user">${prompt}</div>`;
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;

            const thoughtBox = document.getElementById('thoughtTrace');
            thoughtBox.innerHTML = '<div style="color:var(--accent);">Analyzing prompt and dispatching MCP tools...</div>';

            const res = await fetch('/api/chat', {
                method: 'POST',
                body: JSON.stringify({ user_id: 'demo_user', prompt })
            }).then(r => r.json());

            chatBox.innerHTML += `<div class="msg agent">${formatMessage(res.response)}</div>`;
            chatBox.scrollTop = chatBox.scrollHeight;

            thoughtBox.innerHTML = '';
            (res.reasoning_trace || []).forEach(t => {
                thoughtBox.innerHTML += `
                    <div style="background:#070a12; border-left:3px solid var(--accent); padding:10px; margin-bottom:12px; border-radius:6px;">
                        <strong style="color:var(--accent); font-size:0.88rem;">${t.step}</strong><br>
                        <div style="margin:4px 0 6px; color:#e2e8f0; font-size:0.84rem; line-height:1.4;">${t.thought}</div>
                        <div style="background:#0f172a; padding:6px 10px; border-radius:4px; font-size:0.77rem; color:#94a3b8; overflow-x:auto;">
                            <strong style="color:#cbd5e1;">Evidence:</strong>
                            <pre style="margin:4px 0 0; white-space:pre-wrap; font-family:monospace; color:#38bdf8;">${JSON.stringify(t.evidence, null, 2)}</pre>
                        </div>
                    </div>
                `;
            });
        }

        // Render Interactive 2D Knowledge Graph Canvas using vis-network
        let graphNetwork = null;
        async function renderGraphCanvas() {
            const data = await fetch('/api/kg/graph').then(r => r.json());
            const container = document.getElementById('visGraph');
            
            const visData = {
                nodes: new vis.DataSet(data.nodes),
                edges: new vis.DataSet(data.edges)
            };
            const options = {
                nodes: { shape: 'dot', size: 16, font: { color: '#ffffff', size: 12 } },
                edges: { arrows: 'to', color: { color: '#334155' } },
                physics: { stabilization: false, barnesHut: { gravitationalConstant: -3000 } }
            };
            if (graphNetwork) graphNetwork.destroy();
            graphNetwork = new vis.Network(container, visData, options);
        }

        // Custom RAG Document Ingestion
        async function ingestRagDoc() {
            const title = document.getElementById('ragTitle').value.trim();
            const text = document.getElementById('ragText').value.trim();
            if (!title || !text) return alert('Please enter both title and content.');

            await fetch('/api/rag/ingest', {
                method: 'POST',
                body: JSON.stringify({ title, text })
            });
            alert('Custom Document successfully ingested into RAG retriever!');
            document.getElementById('ragTitle').value = '';
            document.getElementById('ragText').value = '';
            loadRagDocs();
        }

        async function loadRagDocs() {
            const res = await fetch('/api/rag/docs').then(r => r.json());
            const list = document.getElementById('ragDocList');
            list.innerHTML = '';
            (res.documents || []).forEach(d => {
                list.innerHTML += `
                    <div style="background:var(--card-bg); border:1px solid var(--panel-border); padding:10px; border-radius:6px; margin-bottom:8px;">
                        <strong style="color:var(--accent);">${d.title}</strong> [Type: ${d.type}]<br>
                        <span style="color:var(--text-muted); font-size:0.8rem;">${d.text.substring(0, 120)}...</span>
                    </div>
                `;
            });
        }
    </script>
</body>
</html>
"""


def main():
    server = HTTPServer(("127.0.0.1", 8000), RequestHandler)
    print("Serving FoodRec Platform on http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()