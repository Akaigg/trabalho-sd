from flask import Flask, request, jsonify, render_template
import requests
import threading
import time
import random
import os
import socket
import concurrent.futures

app = Flask(__name__)

NODE_ID = os.getenv("NODE_ID", socket.gethostname())
PORT = int(os.getenv("PORT", 5000))

# Filtra o próprio nó da lista de peers para evitar auto-requisições
raw_peers = os.getenv("PEERS", "").split(",")
PEERS = []
for p in raw_peers:
    p = p.strip()
    # Ignora strings vazias, o próprio ID e domínios k8s associados a si mesmo
    if p and p != NODE_ID and not p.startswith(f"{NODE_ID}."):
        PEERS.append(p)

ELECTION_MIN = float(os.getenv("ELECTION_MIN", 3))
ELECTION_MAX = float(os.getenv("ELECTION_MAX", 6))
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", 1.5))

state = {
    "id": NODE_ID,
    "role": "Follower",
    "term": 0,
    "voted_for": None,
    "votes": 0,
    "leader_id": None,
    "last_heartbeat": time.time(),
    "alive": True,
    "history": []
}

lock = threading.Lock()

def log_event(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    print(entry, flush=True)
    with lock:
        state["history"].append(entry)
        state["history"] = state["history"][-30:]

def majority_count() -> int:
    return ((len(PEERS) + 1) // 2) + 1

@app.route("/")
def dashboard():
    return render_template("index.html", node_id=NODE_ID)

@app.route("/status", methods=["GET"])
def status():
    with lock:
        snapshot = dict(state)
    snapshot["peers"] = PEERS
    snapshot["majority_needed"] = majority_count()
    return jsonify(snapshot)

@app.route("/cluster", methods=["GET"])
def cluster():
    nodes = []
    # Lê o estado local de forma segura e direta, sem fazer requisição HTTP a si mesmo
    with lock:
        local_snapshot = dict(state)
    local_snapshot["peers"] = PEERS
    local_snapshot["majority_needed"] = majority_count()
    nodes.append(local_snapshot)

    def fetch_peer(peer):
        try:
            return requests.get(f"http://{peer}:{PORT}/status", timeout=0.5).json()
        except Exception as exc:
            return {
                "id": peer, "role": "Unreachable", "term": None,
                "leader_id": None, "alive": False, "error": str(exc)
            }

    # Busca os status dos outros nós simultaneamente
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = executor.map(fetch_peer, PEERS)
        for res in results:
            nodes.append(res)
            
    return jsonify({"nodes": nodes})

@app.route("/vote", methods=["POST"])
def vote():
    data = request.json
    candidate_term = data["term"]
    candidate_id = data["candidate_id"]

    with lock:
        if not state["alive"]:
            return jsonify({"vote_granted": False, "term": state["term"]}), 503

        if candidate_term > state["term"]:
            state["term"] = candidate_term
            state["voted_for"] = None
            state["role"] = "Follower"
            state["leader_id"] = None

        if candidate_term == state["term"] and (state["voted_for"] is None or state["voted_for"] == candidate_id):
            state["voted_for"] = candidate_id
            state["last_heartbeat"] = time.time()
            log_event(f"Votou em {candidate_id} no termo {state['term']}")
            return jsonify({"vote_granted": True, "term": state["term"]})

        return jsonify({"vote_granted": False, "term": state["term"]})

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json
    with lock:
        if not state["alive"]:
            return jsonify({"status": "down"}), 503

        if data["term"] >= state["term"]:
            previous_leader = state.get("leader_id")
            state["term"] = data["term"]
            state["role"] = "Follower"
            state["leader_id"] = data.get("leader_id")
            state["last_heartbeat"] = time.time()
            if previous_leader != state["leader_id"]:
                log_event(f"Heartbeat recebido do líder {state['leader_id']} no termo {state['term']}")
    return jsonify({"status": "ok"})

@app.route("/toggle", methods=["POST"])
def toggle_alive():
    with lock:
        state["alive"] = not state["alive"]
        if not state["alive"]:
            state["role"] = "Offline"
            state["leader_id"] = None
            log_event("Nó colocado em falha simulada")
        else:
            state["role"] = "Follower"
            state["last_heartbeat"] = time.time()
            log_event("Nó reativado")
        return jsonify({"alive": state["alive"]})

@app.route("/reset", methods=["POST"])
def reset_node():
    with lock:
        state["role"] = "Follower"
        state["term"] = 0
        state["voted_for"] = None
        state["votes"] = 0
        state["leader_id"] = None
        state["last_heartbeat"] = time.time()
        state["alive"] = True
        state["history"] = []
    log_event("Nó reiniciado")
    return jsonify({"status": "resetado"})

def request_votes():
    with lock:
        if not state["alive"]:
            return
        state["role"] = "Candidate"
        state["term"] += 1
        state["voted_for"] = NODE_ID
        state["votes"] = 1
        state["leader_id"] = None
        current_term = state["term"]

    log_event(f"Eleição iniciada no termo {current_term}")

    def ask_vote(peer):
        try:
            response = requests.post(
                f"http://{peer}:{PORT}/vote",
                json={"term": current_term, "candidate_id": NODE_ID},
                timeout=1.0,
            )
            if response.ok and response.json().get("vote_granted"):
                with lock:
                    state["votes"] += 1
        except Exception:
            pass

    # Realiza pedidos de votos concorrentes para não perder tempo aguardando falhas de rede
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(ask_vote, PEERS))

    with lock:
        if state["role"] == "Candidate" and state["votes"] >= majority_count():
            state["role"] = "Leader"
            state["leader_id"] = NODE_ID
            log_event(f"Virou líder com {state['votes']} votos no termo {state['term']}")
            # Ao se eleger, anuncia a autoridade instantaneamente 
            threading.Thread(target=broadcast_heartbeats, daemon=True).start()
        elif state["role"] == "Candidate":
            state["role"] = "Follower"
            log_event(f"Não conseguiu maioria no termo {state['term']}")

def broadcast_heartbeats():
    with lock:
        if state["role"] != "Leader" or not state["alive"]:
            return
        current_term = state["term"]

    for peer in PEERS:
        try:
            requests.post(
                f"http://{peer}:{PORT}/heartbeat",
                json={"term": current_term, "leader_id": NODE_ID},
                timeout=1.0,
            )
        except Exception:
            pass

def send_heartbeats():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        broadcast_heartbeats()

def election_timer():
    # Sorteia o timeout apenas no início ou fim de um ciclo (mantém a aleatoriedade efetiva)
    timeout = random.uniform(ELECTION_MIN, ELECTION_MAX)
    while True:
        time.sleep(0.5)
        with lock:
            elapsed = time.time() - state["last_heartbeat"]
            is_leader = state["role"] == "Leader"
            is_alive = state["alive"]

        if is_alive and not is_leader and elapsed >= timeout:
            request_votes()
            with lock:
                state["last_heartbeat"] = time.time()
            # Sorteia um novo tempo de espera caso a eleição falhe/termine
            timeout = random.uniform(ELECTION_MIN, ELECTION_MAX)

if __name__ == "__main__":
    threading.Thread(target=election_timer, daemon=True).start()
    threading.Thread(target=send_heartbeats, daemon=True).start()
    log_event(f"Nó {NODE_ID} iniciado na porta {PORT} com peers: {PEERS}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)