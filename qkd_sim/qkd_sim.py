#!/usr/bin/env python3
"""
Simulatore SD-QKD ispirato a ETSI GS QKD 015 (SDN control, modello YANG)
e ETSI GS QKD 018 (orchestrazione).

Uso:
    python3 qkd_sim.py             # usa OSRM e ANU QRNG se raggiungibili
    python3 qkd_sim.py --offline   # nessuna chiamata di rete (avvio immediato)

Nota: i protocolli NETCONF/REST non sono implementati davvero; il controller
chiama direttamente gli oggetti Python e registra nel log messaggi che ne
imitano il formato.
"""
from __future__ import annotations

import heapq
import json
import math
import secrets
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Parametri globali
# ---------------------------------------------------------------------------
OFFLINE = "--offline" in sys.argv
QBER_SECURITY_THRESHOLD = 0.08   # soglia operativa (il limite teorico BB84 è ~11%)
MAX_KEYS_PER_TICK = 4            # chiavi generate per tick su un link ideale (0 dB, QBER 0)
KEY_SIZE_BYTES = 32              # chiavi da 256 bit
NETWORK_TIMEOUT_S = 5

_services_down: Set[str] = set()                 # servizi esterni già falliti
KEY_SOURCES: Dict[str, int] = {"ANU-QRNG": 0, "locale (secrets)": 0}


# ---------------------------------------------------------------------------
# Modello fisico semplificato
# ---------------------------------------------------------------------------
def binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def secret_key_fraction(qber: float) -> float:
    """Frazione di chiave segreta per BB84 (Shor-Preskill): r = 1 - 2 h(Q).
    Si annulla per Q ~ 11%."""
    return max(0.0, 1.0 - 2.0 * binary_entropy(qber))


@dataclass
class QKDModule:
    """Un'estremità di un link QKD in fibra verso un nodo vicino."""
    module_id: str
    target_node: str
    target_module: str
    fiber_length_km: float
    qber: float = 0.02
    raw_key_rate_bps: int = 2400          # tasso su link ideale (0 dB, QBER 0)
    is_active: bool = True
    attenuation_db: float = field(init=False)
    nominal_qber: float = field(init=False)

    def __post_init__(self) -> None:
        self.attenuation_db = round(self.fiber_length_km * 0.2, 2)  # ~0.2 dB/km a 1550 nm
        self.nominal_qber = self.qber

    @property
    def transmittance(self) -> float:
        return 10 ** (-self.attenuation_db / 10)

    @property
    def is_secure(self) -> bool:
        return self.is_active and self.qber < QBER_SECURITY_THRESHOLD

    @property
    def secure_key_rate_bps(self) -> int:
        if not self.is_secure:
            return 0
        return int(self.raw_key_rate_bps * self.transmittance * secret_key_fraction(self.qber))

    def keys_per_tick(self) -> int:
        if not self.is_secure:
            return 0
        n = MAX_KEYS_PER_TICK * self.transmittance * secret_key_fraction(self.qber)
        return max(1, round(n))

    def restore(self) -> None:
        self.qber = self.nominal_qber
        self.is_active = True


# ---------------------------------------------------------------------------
# Nodo: KMS + agente SDN
# ---------------------------------------------------------------------------
@dataclass
class LocalKMS:
    """Key Management System del nodo: pool di chiavi simmetriche per peer."""
    node_id: str
    key_stores: Dict[str, List[bytes]] = field(default_factory=dict)

    def push_keys(self, peer_id: str, keys: List[bytes]) -> None:
        self.key_stores.setdefault(peer_id, []).extend(keys)

    def pop_key(self, peer_id: str) -> Optional[bytes]:
        store = self.key_stores.get(peer_id)
        return store.pop(0) if store else None

    def get_stock_count(self, peer_id: str) -> int:
        return len(self.key_stores.get(peer_id, []))


class SDNAgent:
    """Interfaccia del nodo verso il controller (lato 'south-bound', QKD 015)."""

    def __init__(self, node_id: str, kms: LocalKMS, modules: List[QKDModule]):
        self.node_id = node_id
        self.kms = kms
        self.modules: Dict[str, QKDModule] = {m.module_id: m for m in modules}

    def get_yang_state(self) -> dict:
        """Stato locale in un formato che imita il modello YANG etsi-qkd-sdn-node."""
        return {
            "etsi-qkd-sdn-node:sd-qkd-node": {
                "node-id": self.node_id,
                "status": "OPERATIONAL",
                "qkd-modules": [
                    {
                        "module-id": mod.module_id,
                        "target-node": mod.target_node,
                        "status": "UP" if mod.is_active else "DOWN",
                        "metrics": {
                            "qber": round(mod.qber, 4),
                            "secure-key-rate-bps": mod.secure_key_rate_bps,
                            "fiber-length-km": mod.fiber_length_km,
                            "attenuation-db": mod.attenuation_db,
                            "available-keys": self.kms.get_stock_count(mod.target_node),
                        },
                    }
                    for mod in self.modules.values()
                ],
            }
        }

    def module_towards(self, peer_id: str) -> Optional[QKDModule]:
        for mod in self.modules.values():
            if mod.target_node == peer_id:
                return mod
        return None

    def rpc_configure_link(self, module_id: str, active: bool) -> bool:
        if module_id in self.modules:
            self.modules[module_id].is_active = active
            return True
        return False

    @staticmethod
    def rpc_execute_relay_xor(data: bytes, key: bytes) -> bytes:
        """One-time pad: XOR byte per byte (cifra e decifra sono la stessa operazione)."""
        if len(data) != len(key):
            raise ValueError("Lunghezze diverse: impossibile applicare il one-time pad.")
        return bytes(a ^ b for a, b in zip(data, key))


class SDQKDNode:
    def __init__(self, node_id: str, modules: List[QKDModule]):
        self.node_id = node_id
        self.kms = LocalKMS(node_id)
        self.agent = SDNAgent(node_id, self.kms, modules)


# ---------------------------------------------------------------------------
# Controller SDN (QKD 015)
# ---------------------------------------------------------------------------
class SDNController:
    """Vede tutta la rete, calcola i cammini e coordina il trusted relay."""

    def __init__(self) -> None:
        self.nodes: Dict[str, SDQKDNode] = {}
        self.transaction_logs: List[dict] = []
        self.last_relay_trace: List[dict] = []

    def register_node(self, node: SDQKDNode) -> None:
        self.nodes[node.node_id] = node

    def log_transaction(self, interface: str, source: str, destination: str, payload: dict) -> None:
        self.transaction_logs.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "interface": interface,
            "from": source,
            "to": destination,
            "payload": payload,
        })

    def collect_global_topology(self) -> dict:
        topology = {}
        for node_id, node in self.nodes.items():
            yang = node.agent.get_yang_state()
            topology[node_id] = yang["etsi-qkd-sdn-node:sd-qkd-node"]
            self.log_transaction("ETSI_QKD_015_NETCONF_GET", node_id, "CONTROLLER", yang)
        return topology

    def links(self) -> List[Tuple[str, str]]:
        """Elenco dei link (non orientati) della rete."""
        seen: Set[Tuple[str, str]] = set()
        for u_id, node in self.nodes.items():
            for mod in node.agent.modules.values():
                seen.add(tuple(sorted((u_id, mod.target_node))))
        return sorted(seen)

    def set_link_state(self, a: str, b: str, qber: Optional[float] = None,
                       active: Optional[bool] = None, restore: bool = False) -> None:
        """Applica lo stesso stato a entrambe le estremità del link a-b."""
        for x, y in ((a, b), (b, a)):
            mod = self.nodes[x].agent.module_towards(y)
            if mod is None:
                continue
            if restore:
                mod.restore()
            if qber is not None:
                mod.qber = qber
            if active is not None:
                self.nodes[x].agent.rpc_configure_link(mod.module_id, active)

    def _link_usable(self, u: str, mod: QKDModule) -> bool:
        v = mod.target_node
        if not mod.is_secure:
            return False
        return min(self.nodes[u].kms.get_stock_count(v),
                   self.nodes[v].kms.get_stock_count(u)) > 0

    def compute_secure_path(self, src: str, dst: str) -> Optional[List[str]]:
        """Dijkstra con peso w = L * (1 + 10*QBER) sui soli link sicuri e con chiavi."""
        if src not in self.nodes or dst not in self.nodes:
            return None
        distances: Dict[str, float] = {n: float("inf") for n in self.nodes}
        previous: Dict[str, Optional[str]] = {n: None for n in self.nodes}
        distances[src] = 0.0
        pq: List[Tuple[float, str]] = [(0.0, src)]

        while pq:
            curr_dist, curr = heapq.heappop(pq)
            if curr == dst:
                break
            if curr_dist > distances[curr]:
                continue
            for mod in self.nodes[curr].agent.modules.values():
                if not self._link_usable(curr, mod):
                    continue
                weight = mod.fiber_length_km * (1.0 + mod.qber * 10.0)
                alt = curr_dist + weight
                if alt < distances[mod.target_node]:
                    distances[mod.target_node] = alt
                    previous[mod.target_node] = curr
                    heapq.heappush(pq, (alt, mod.target_node))

        if distances[dst] == float("inf"):
            return None
        path, step = [], dst
        while step is not None:
            path.append(step)
            step = previous[step]
        return path[::-1]

    def provision_key_association(self, src: str, dst: str
                                  ) -> Tuple[bool, str, Optional[str], Optional[List[str]]]:
        """Trasporta una chiave master K da src a dst con trusted relay hop-by-hop:
        su ogni tratto u->v, u invia c = K XOR k_uv e v recupera K = c XOR k_uv.
        I nodi intermedi vedono K in chiaro: per questo devono essere 'fidati'."""
        self.last_relay_trace = []
        if src == dst:
            return False, "Sorgente e destinazione coincidono.", None, None

        path = self.compute_secure_path(src, dst)
        if not path:
            return (False, f"Nessun cammino sicuro tra {src} e {dst} "
                           "(QBER oltre soglia, fibra interrotta o chiavi esaurite).", None, None)

        hops = list(zip(path, path[1:]))

        # Verifica preliminare: non si consuma nulla se anche un solo tratto è scoperto.
        for u, v in hops:
            if self.nodes[u].kms.get_stock_count(v) == 0 or self.nodes[v].kms.get_stock_count(u) == 0:
                return False, f"Chiavi esaurite sul tratto {u}-{v}.", None, None

        master_key = fetch_real_quantum_key()
        plaintext = master_key

        for i, (u, v) in enumerate(hops):
            k_u = self.nodes[u].kms.pop_key(v)
            k_v = self.nodes[v].kms.pop_key(u)
            if k_u is None or k_u != k_v:
                return False, f"Desincronizzazione KMS sul tratto {u}-{v}.", None, None

            # u cifra (sorgente) o ri-cifra (relay) con la chiave del tratto
            ciphertext = self.nodes[u].agent.rpc_execute_relay_xor(plaintext, k_u)
            self.log_transaction("ETSI_QKD_015_NETCONF_RPC", "CONTROLLER", u, {
                "rpc": "relay-encrypt",
                "role": "source" if i == 0 else "trusted-relay",
                "link": f"{u}->{v}",
                "key-len": len(k_u),
            })
            # v decifra con la propria copia della chiave del tratto
            plaintext = self.nodes[v].agent.rpc_execute_relay_xor(ciphertext, k_v)
            # Traccia del passo, usata dall'interfaccia grafica per mostrare il relay
            self.last_relay_trace.append({
                "from": u, "to": v,
                "link_key": k_u.hex(), "ciphertext": ciphertext.hex(),
                "recovered": plaintext.hex(),
            })

        if plaintext != master_key:
            return False, "Errore di relay: la chiave ricevuta non coincide.", None, None

        self.nodes[src].kms.push_keys(f"VIRTUAL_{dst}", [master_key])
        self.nodes[dst].kms.push_keys(f"VIRTUAL_{src}", [plaintext])

        return (True, f"Associazione completata lungo il cammino: [{' -> '.join(path)}]",
                master_key.hex(), path)


# ---------------------------------------------------------------------------
# Orchestratore (QKD 018)
# ---------------------------------------------------------------------------
class QKDOrchestrator:
    """Livello di servizio: riceve richieste dalle applicazioni e verifica la SLA."""

    def __init__(self, controller: SDNController):
        self.controller = controller
        self.active_services: Dict[str, dict] = {}

    def request_e2e_key_service(self, client_app: str, src: str, dst: str, key_rate_sla: int) -> dict:
        service_id = f"srv-qkd-{uuid.uuid4().hex[:8]}"
        self.controller.log_transaction("ETSI_QKD_018_REST_REQUEST", client_app, "ORCHESTRATOR", {
            "service_id": service_id,
            "client_app": client_app,
            "endpoints": {"source_node": src, "destination_node": dst},
            "sla": {"required_bps": key_rate_sla, "crypto_profile": "AES_256_GCM"},
        })

        success, message, key_hex, path = self.controller.provision_key_association(src, dst)

        response = {
            "service_id": service_id,
            "status": "PROVISIONED" if success else "FAILED",
            "message": message,
            "path": path,
            "allocated_key_id": f"key-id-{uuid.uuid4().hex[:12]}" if success else None,
            "allocated_key_hex": key_hex,
        }

        if success and path:
            # Il tasso end-to-end è limitato dal tratto più lento del cammino
            bottleneck = min(
                self.controller.nodes[u].agent.module_towards(v).secure_key_rate_bps
                for u, v in zip(path, path[1:])
            )
            response["bottleneck_key_rate_bps"] = bottleneck
            response["sla_satisfied"] = bottleneck >= key_rate_sla
            self.active_services[service_id] = response

        self.controller.log_transaction("ETSI_QKD_018_REST_RESPONSE", "ORCHESTRATOR", client_app, response)
        return response


# ---------------------------------------------------------------------------
# Rete di laboratorio
# ---------------------------------------------------------------------------
class NetworkSimulator:
    COORDS = {
        "PoliTo": (45.063, 7.662),   # Politecnico di Torino
        "Fisica": (45.053, 7.681),   # Dipartimento di Fisica (UniTo)
        "INRiM": (45.016, 7.640),    # Istituto Nazionale di Ricerca Metrologica
        "CSI": (45.032, 7.633),      # CSI Piemonte
    }
    # (nodo A, nodo B, QBER nominale): topologia ad anello
    LINKS = [
        ("PoliTo", "Fisica", 0.015),
        ("PoliTo", "INRiM", 0.028),
        ("Fisica", "CSI", 0.018),
        ("INRiM", "CSI", 0.035),
    ]
    ABBR = {"PoliTo": "P", "Fisica": "F", "INRiM": "I", "CSI": "C"}

    def __init__(self) -> None:
        self.controller = SDNController()
        self.orchestrator = QKDOrchestrator(self.controller)
        self._build_topology()

    def _build_topology(self) -> None:
        if OFFLINE:
            print("Modalità offline: distanze stimate in linea d'aria, chiavi generate localmente.")
        else:
            print("Interrogazione OSRM per le distanze (percorso stradale come stima della fibra)...")

        modules: Dict[str, List[QKDModule]] = {n: [] for n in self.COORDS}
        for a, b, qber in self.LINKS:
            dist = fetch_real_fiber_distance(*self.COORDS[a], *self.COORDS[b])
            ma, mb = f"mod-{self.ABBR[a]}-{self.ABBR[b]}", f"mod-{self.ABBR[b]}-{self.ABBR[a]}"
            modules[a].append(QKDModule(ma, b, mb, fiber_length_km=dist, qber=qber))
            modules[b].append(QKDModule(mb, a, ma, fiber_length_km=dist, qber=qber))

        for node_id, mods in modules.items():
            self.controller.register_node(SDQKDNode(node_id, mods))

        for _ in range(3):
            self.tick_key_generation()

    def tick_key_generation(self) -> int:
        """Ogni link sicuro genera un numero di chiavi che dipende da perdite e QBER."""
        generated = 0
        for a, b in self.controller.links():
            mod = self.controller.nodes[a].agent.module_towards(b)
            for _ in range(mod.keys_per_tick()):
                key = fetch_real_quantum_key()
                self.controller.nodes[a].kms.push_keys(b, [key])
                self.controller.nodes[b].kms.push_keys(a, [key])
                generated += 1
        return generated

    def reset_network(self) -> None:
        for a, b in self.controller.links():
            self.controller.set_link_state(a, b, restore=True)


# ---------------------------------------------------------------------------
# Servizi esterni (con fallback)
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_real_fiber_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distanza stradale da OSRM (approssimazione del tracciato in fibra);
    in caso di errore, distanza in linea d'aria."""
    if not OFFLINE and "osrm" not in _services_down:
        url = (f"http://router.project-osrm.org/route/v1/driving/"
               f"{lon1},{lat1};{lon2},{lat2}?overview=false")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QKD-Simulator/1.0"})
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as response:
                data = json.loads(response.read().decode())
                return round(data["routes"][0]["distance"] / 1000.0, 2)
        except Exception:
            _services_down.add("osrm")
            print("  (OSRM non raggiungibile: uso la distanza in linea d'aria)")
    return round(haversine_km(lat1, lon1, lat2, lon2), 2)


def fetch_real_quantum_key() -> bytes:
    """Prova l'ANU QRNG; se non risponde (o in offline) usa secrets, una sola volta per sessione."""
    if not OFFLINE and "anu" not in _services_down:
        url = f"https://qrng.anu.edu.au/API/jsonI.php?length=1&type=hex16&size={KEY_SIZE_BYTES}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QKD-Simulator/1.0"})
            with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as response:
                data = json.loads(response.read().decode())
                if data.get("success"):
                    key = bytes.fromhex(data["data"][0])
                    if len(key) == KEY_SIZE_BYTES:
                        KEY_SOURCES["ANU-QRNG"] += 1
                        return key
        except Exception:
            pass
        _services_down.add("anu")
        print("  (ANU QRNG non raggiungibile: uso il generatore locale)")
    KEY_SOURCES["locale (secrets)"] += 1
    return secrets.token_bytes(KEY_SIZE_BYTES)


# ---------------------------------------------------------------------------
# Interfaccia testuale
# ---------------------------------------------------------------------------
def print_banner() -> None:
    print("=" * 80)
    print(" SIMULATORE SDN/QKD: ETSI GS QKD 015 (SBI) & ETSI GS QKD 018 (ORCHESTRATION)")
    print("=" * 80)


def resolve_node(name: str, sim: NetworkSimulator) -> Optional[str]:
    for node_id in sim.controller.nodes:
        if node_id.lower() == name.strip().lower():
            return node_id
    return None


def display_topology(sim: NetworkSimulator) -> None:
    print("\n--- STATO TOPOLOGICO RACCOLTO VIA QKD 015 (YANG / NETCONF GET) ---")
    for node_id, data in sim.controller.collect_global_topology().items():
        print(f"\n[SD-QKD Node: {node_id}] Status: {data['status']}")
        for mod in data["qkd-modules"]:
            m = mod["metrics"]
            secure = m["qber"] < QBER_SECURITY_THRESHOLD and mod["status"] == "UP"
            print(f"  |-- Link verso {mod['target-node']} via {mod['module-id']}")
            print(f"      Stato: {mod['status']} ({'OK (SECURE)' if secure else 'DEGRADED / DOWN'})")
            print(f"      QBER: {m['qber'] * 100:.2f}% | Attenuazione: {m['attenuation-db']} dB"
                  f" | Lunghezza: {m['fiber-length-km']} km")
            print(f"      Key rate sicuro: {m['secure-key-rate-bps']} bps"
                  f" | Stock KMS: {m['available-keys']} chiavi da 256 bit")
    print(f"\nOrigine delle chiavi generate finora: {KEY_SOURCES}")


def choose_link(sim: NetworkSimulator) -> Optional[Tuple[str, str]]:
    links = sim.controller.links()
    for i, (a, b) in enumerate(links, 1):
        print(f"{i}. {a} <---> {b}")
    choice = input(f"Scelta [1-{len(links)}]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(links):
        return links[int(choice) - 1]
    print("\nScelta non valida.")
    return None


def run_demo(sim: NetworkSimulator) -> None:
    print("\n" + "=" * 80)
    print(" DIMOSTRAZIONE AUTOMATICA: RE-ROUTING CENTRALIZZATO")
    print("=" * 80)

    print("\n1. Richiesta nominale PoliTo -> CSI")
    res1 = sim.orchestrator.request_e2e_key_service("Banca-Centrale", "PoliTo", "CSI", 1000)
    print(json.dumps(res1, indent=2))
    if res1["status"] != "PROVISIONED":
        print("\nRichiesta fallita: esegui qualche tick di generazione (opzione 4) e riprova.")
        return

    a, b = res1["path"][0], res1["path"][1]
    print(f"\n2. Iniezione di rumore sul primo tratto del cammino ({a} <-> {b}): QBER al 15%")
    sim.controller.set_link_state(a, b, qber=0.15)

    print("\n3. Seconda richiesta PoliTo -> CSI")
    print(f"[QKD 015] Il controller vede QBER > {QBER_SECURITY_THRESHOLD:.0%} ed esclude il link {a}-{b}.")
    res2 = sim.orchestrator.request_e2e_key_service("Banca-Centrale", "PoliTo", "CSI", 1000)
    print(json.dumps(res2, indent=2))
    print("\n(Il guasto resta attivo: usa l'opzione 7 per ripristinare la rete.)")


def main() -> None:
    sim = NetworkSimulator()

    while True:
        print()
        print_banner()
        print("1. Visualizza topologia e modelli dati YANG (QKD 015)")
        print("2. Richiedi un servizio di chiave end-to-end (QKD 018)")
        print("3. Inietta una perturbazione (QBER alto o fibra interrotta)")
        print("4. Esegui un ciclo di generazione chiavi (tick)")
        print("5. Ispeziona il log dei messaggi (QKD 015 / QKD 018)")
        print("6. Dimostrazione automatica: guasto e re-routing")
        print("7. Ripristina tutti i link ai valori nominali")
        print("0. Esci")
        print("-" * 80)
        print("Nodi disponibili: " + ", ".join(sim.controller.nodes))

        choice = input("Seleziona un'operazione [0-7]: ").strip()

        if choice == "1":
            display_topology(sim)

        elif choice == "2":
            src = resolve_node(input("Nodo sorgente [PoliTo]: ") or "PoliTo", sim)
            dst = resolve_node(input("Nodo destinazione [CSI]: ") or "CSI", sim)
            if not src or not dst:
                print("\nNome di nodo non valido.")
            else:
                app_id = input("ID applicazione [Ospedale-San-Giovanni]: ").strip() or "Ospedale-San-Giovanni"
                print(f"\n[QKD 018] Richiesta di servizio per {app_id} tra {src} e {dst}...")
                response = sim.orchestrator.request_e2e_key_service(app_id, src, dst, key_rate_sla=1000)
                print(json.dumps(response, indent=2))

        elif choice == "3":
            print("\nSu quale link vuoi agire?")
            link = choose_link(sim)
            if link:
                a, b = link
                kind = input("1. Rumore / intercettazione (QBER 14.5%)   2. Taglio della fibra\nScelta [1-2]: ").strip()
                if kind == "1":
                    sim.controller.set_link_state(a, b, qber=0.145)
                    print(f"\n[ALERT] QBER su {a} <-> {b} al 14.50%: soglia di sicurezza superata.")
                elif kind == "2":
                    sim.controller.set_link_state(a, b, active=False)
                    print(f"\n[ALERT] Fibra interrotta tra {a} e {b}.")
                else:
                    print("\nScelta non valida.")

        elif choice == "4":
            n = sim.tick_key_generation()
            print(f"\n[TICK] Generate {n} nuove chiavi di link nei KMS.")

        elif choice == "5":
            print("\n--- ULTIME 6 TRANSAZIONI (QKD 015 & QKD 018) ---")
            if not sim.controller.transaction_logs:
                print("Nessuna transazione registrata.")
            for log in sim.controller.transaction_logs[-6:]:
                print(f"\n[{log['timestamp']}] {log['interface']}  {log['from']} -> {log['to']}")
                print(json.dumps(log["payload"], indent=2))

        elif choice == "6":
            run_demo(sim)

        elif choice == "7":
            sim.reset_network()
            print("\nTutti i link riportati a QBER nominale e accesi.")

        elif choice == "0":
            print("\nChiusura della simulazione.")
            break

        else:
            print("\nScelta non valida.")

        input("\nPremi Invio per continuare...")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nChiusura della simulazione.")
