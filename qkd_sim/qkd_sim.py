#!/usr/bin/env python3
"""
Simulatore SD-QKD ispirato a tre standard ETSI:

* GS QKD 015 (controller SDN <-> nodi): il controller legge lo stato dei nodi
  in un formato che imita il modello YANG etsi-qkd-sdn-node e comanda il
  trusted relay con chiamate RPC;
* GS QKD 018 (orchestrator <-> controller): il controller espone all'SDN
  orchestrator la vista astratta della rete (moduli etsi-qkd-sdn-topology e
  etsi-qkd-sdn-inventory) e le RPC di provisioning del modulo
  etsi-qkd-sdn-connectivity; l'orchestrator sceglie il cammino tra i candidati
  proposti dal controller e reagisce alle notifiche (path restoration);
* GS QKD 014 (applicazione <-> KME): l'applicazione preleva la chiave
  end-to-end dal KME del proprio nodo indicando il key_ID.

Uso:
    python3 qkd_sim.py             # usa OSRM e ANU QRNG se raggiungibili
    python3 qkd_sim.py --offline   # nessuna chiamata di rete (avvio immediato)

Nota: i protocolli NETCONF/RESTCONF/REST non sono implementati davvero; gli
oggetti Python si chiamano direttamente e registrano nel log messaggi che ne
imitano il formato (nomi di contenitori, foglie e RPC presi dai moduli YANG;
i valori degli enumerati di stato e gravità sono indicativi).
"""
from __future__ import annotations

import json
import math
import secrets
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Parametri globali
# ---------------------------------------------------------------------------
OFFLINE = "--offline" in sys.argv
QBER_SECURITY_THRESHOLD = 0.08   # soglia operativa (il limite teorico BB84 è ~11%)
MAX_KEYS_PER_TICK = 4            # chiavi generate per tick su un link ideale (0 dB, QBER 0)
KEY_SIZE_BYTES = 32              # chiavi da 256 bit
NETWORK_TIMEOUT_S = 5
DEFAULT_SLA_BPS = 256            # QoS di default: una chiave AES-256 al secondo
MAX_CANDIDATE_PATHS = 3          # cammini candidati proposti dal controller (QKD 018)

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
    """Key Management System del nodo.

    key_stores: chiavi di link (prodotte dal QKD) condivise con ciascun vicino.
    delivered:  chiavi end-to-end consegnate alle applicazioni, indicizzate per
                app_id e key_ID: è ciò che il KME serve via QKD 014."""
    node_id: str
    key_stores: Dict[str, List[bytes]] = field(default_factory=dict)
    delivered: Dict[str, Dict[str, bytes]] = field(default_factory=dict)

    def push_keys(self, peer_id: str, keys: List[bytes]) -> None:
        self.key_stores.setdefault(peer_id, []).extend(keys)

    def pop_key(self, peer_id: str) -> Optional[bytes]:
        store = self.key_stores.get(peer_id)
        return store.pop(0) if store else None

    def get_stock_count(self, peer_id: str) -> int:
        return len(self.key_stores.get(peer_id, []))

    def deliver(self, app_id: str, key_id: str, key: bytes) -> None:
        self.delivered.setdefault(app_id, {})[key_id] = key

    def e2e_count(self) -> int:
        return sum(len(keys) for keys in self.delivered.values())


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
# Controller SDN: client QKD 015 verso i nodi, server QKD 018 verso l'orchestrator
# ---------------------------------------------------------------------------
@dataclass
class ServiceLink:
    """svc_link di QKD 018: relazione di chiave end-to-end assegnata a un'applicazione."""
    svc_link_id: str
    app_id: str
    local_qkdn: str
    remote_qkdn: str
    bandwidth_bps: int = 0
    include_nodes: List[str] = field(default_factory=list)
    exclude_nodes: List[str] = field(default_factory=list)
    candidate_paths: Dict[int, List[str]] = field(default_factory=dict)  # path_id -> phys_link_id
    deployed_path_id: Optional[int] = None
    deployed_phys_links: List[str] = field(default_factory=list)
    restoration: str = "AUTOMATIC"
    status: str = "PENDING"


class SDNController:
    """SDQNC: vede tutta la rete del dominio tramite QKD 015, calcola i cammini
    candidati, li attiva su richiesta e coordina il trusted relay.
    Verso l'orchestrator espone l'interfaccia di QKD 018."""

    def __init__(self) -> None:
        self.nodes: Dict[str, SDQKDNode] = {}
        self.transaction_logs: List[dict] = []
        self.last_relay_trace: List[dict] = []
        # stato del modulo etsi-qkd-sdn-connectivity
        self.apps: Dict[str, dict] = {}
        self.svc_links: Dict[str, ServiceLink] = {}
        self.reserved_bps: Dict[str, int] = {}      # phys_link_id -> expected_consumption
        self._subscribers: List[Callable[[str, dict], None]] = []

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

    # ------------------------------------------------------------------ QKD 015
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
        """Applica lo stesso stato a entrambe le estremità del link a-b e, se lo stato
        del link cambia, lo notifica all'orchestrator (QKD 018, §5.5)."""
        before = self.phys_link_status(a, b)
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
        after = self.phys_link_status(a, b)
        if after != before:
            self._notify_link_change(a, b, after)

    # ------------------------------------------------------- identificatori 018
    @staticmethod
    def phys_link_id(a: str, b: str) -> str:
        a, b = sorted((a, b))
        return f"plink-{a}-{b}"

    def phys_link_ends(self, plid: str) -> Tuple[str, str]:
        for a, b in self.links():
            if self.phys_link_id(a, b) == plid:
                return a, b
        raise KeyError(plid)

    def nodes_to_phys_links(self, nodes: List[str]) -> List[str]:
        return [self.phys_link_id(u, v) for u, v in zip(nodes, nodes[1:])]

    def phys_links_to_nodes(self, start: str, plids: List[str]) -> List[str]:
        nodes = [start]
        for plid in plids:
            a, b = self.phys_link_ends(plid)
            nodes.append(b if nodes[-1] == a else a)
        return nodes

    @staticmethod
    def kme_address(node_id: str) -> str:
        return f"kme.{node_id.lower()}.qkd.local:443"

    # ------------------------------------------------------ stato e prestazioni
    def phys_link_status(self, a: str, b: str) -> str:
        mod = self.nodes[a].agent.module_towards(b)
        if not mod.is_active:
            return "OFF"
        return "ACTIVE" if mod.is_secure else "DEGRADED"

    def phys_link_perf(self, a: str, b: str) -> dict:
        mod = self.nodes[a].agent.module_towards(b)
        skr = mod.secure_key_rate_bps
        used = self.reserved_bps.get(self.phys_link_id(a, b), 0)
        return {"skr": skr, "expected_consumption": used, "eskr": max(0, skr - used),
                "qber": round(mod.qber, 4)}

    # ---------------------------------------------------------- notifiche 018
    def subscribe(self, callback: Callable[[str, dict], None]) -> None:
        self._subscribers.append(callback)

    def notify(self, name: str, payload: dict) -> None:
        self.log_transaction("ETSI_QKD_018_NOTIFICATION", "CONTROLLER", "ORCHESTRATOR",
                             {"notification": name, **payload})
        for callback in list(self._subscribers):
            callback(name, payload)

    def _notify_link_change(self, a: str, b: str, status: str) -> None:
        plid = self.phys_link_id(a, b)
        if status == "ACTIVE":
            self.notify("event", {"phys_link": {
                "phys_link_id": plid, "status": status, "severity": "INFO",
                "message": f"Link {a}-{b} di nuovo operativo."}})
            perf = self.phys_link_perf(a, b)
            if perf["expected_consumption"] > perf["skr"]:
                self.notify("sdqkdn_phys_link_overloaded", {"phys_link": {"phys_link_id": plid, **perf}})
            return

        reason = "fibra interrotta" if status == "OFF" else \
            f"QBER oltre la soglia del {QBER_SECURITY_THRESHOLD:.0%}"
        self.notify("alarm", {"phys_link": {
            "phys_link_id": plid, "status": status,
            "severity": "CRITICAL" if status == "OFF" else "MAJOR",
            "message": f"Link {a}-{b}: {reason}."}})
        # I service link che passano dal link guasto perdono il cammino
        affected = [sl.svc_link_id for sl in self.svc_links.values()
                    if sl.deployed_path_id is not None and plid in sl.deployed_phys_links]
        for sl_id in affected:
            sl = self.svc_links[sl_id]
            sl.status = "OFF"
            self.notify("alarm", {"svc_link": {
                "svc_link_id": sl_id, "app_id": sl.app_id, "status": "OFF", "severity": "MAJOR",
                "failed_phys_link": plid,
                "message": f"Il cammino del service link attraversa {a}-{b} ({reason})."}})

    # ------------------------------------------------- RESTCONF GET (018 §5.1-5.2)
    def nbi_get_network(self, requester: str = "ORCHESTRATOR") -> dict:
        """Vista astratta del dominio: topology + inventory (augment) di QKD 018.
        Per costruirla il controller aggiorna prima lo stato dei nodi via QKD 015."""
        nodes_015 = self.collect_global_topology()
        qkdn = [{
            "qkdn_id": node_id,
            "qkdn_status": "UP",
            "qkdn_location_id": node_id,
            "kme_address": self.kme_address(node_id),
            "qkd_interfaces": {"qkdi": [
                {"qkdi_id": m["module-id"], "qkdi_status": m["status"]} for m in data["qkd-modules"]
            ]},
        } for node_id, data in nodes_015.items()]

        phys_links = []
        for a, b in self.links():
            mod = self.nodes[a].agent.module_towards(b)
            phys_links.append({
                "phys_link_id": self.phys_link_id(a, b),
                "link_type": "PHYS",
                "phys_link_local_qkdn": {"qkdn_id_ref": a, "qkdi_id_ref": mod.module_id},
                "phys_link_remote_qkdn": {"qkdn_id_ref": b, "qkdi_id_ref": mod.target_module},
                "enable": mod.is_active,
                "wavelength": 1550,
                "performance": self.phys_link_perf(a, b),
                "phys_link_status": self.phys_link_status(a, b),
            })

        svc_links = []
        for sl in self.svc_links.values():
            if sl.deployed_path_id is None:
                continue
            perf = [self.phys_link_perf(*self.phys_link_ends(p)) for p in sl.deployed_phys_links]
            svc_links.append({
                "svc_link_id": sl.svc_link_id,
                "link_type": "VIRT",
                "svc_link_local_qkdn": {"qkdn_id_ref": sl.local_qkdn},
                "svc_link_remote_qkdn": {"qkdn_id_ref": sl.remote_qkdn},
                "bandwidth": sl.bandwidth_bps,
                "performance": {
                    "skr": min(p["skr"] for p in perf),
                    "eskr": min(p["eskr"] for p in perf),
                    "expected_consumption": sl.bandwidth_bps,
                },
                "path_restoration": {"type": sl.restoration, "hold_off_time": 0},
                "svc_link_status": sl.status,
            })

        network = {"etsi-qkd-sdn-topology:qkd_network": {
            "sdqkd_nodes": {"qkdn": qkdn},
            "qkd_links": {
                "qkd_phys_links": {"phys_link": phys_links},
                "qkd_svc_links": {"svc_link": svc_links},
            },
        }}
        self.log_transaction("ETSI_QKD_018_RESTCONF_GET", "CONTROLLER", requester, network)
        return network

    # ------------------------------------------------- RPC connectivity (018 §5.4)
    def _rpc_log(self, rpc: str, inp: dict, out: dict) -> dict:
        self.log_transaction("ETSI_QKD_018_RPC", "ORCHESTRATOR", "CONTROLLER",
                             {"rpc": rpc, "input": inp, "output": out})
        return out

    def rpc_app_registration(self, app_id: str, app_details: dict) -> dict:
        self.apps[app_id] = dict(app_details)
        return self._rpc_log("app-registration", {"app_id_ref": app_id, **app_details},
                             {"response": "SUCCESS"})

    def rpc_create_service_link_id_for_app(self, app_id: str, local: str, remote: str) -> dict:
        inp = {"app_id_ref": app_id, "local_qkdn": {"qkdn_id_ref": local},
               "remote_qkdn": {"qkdn_id_ref": remote}}
        if app_id not in self.apps or local not in self.nodes or remote not in self.nodes:
            return self._rpc_log("create-service-link-id-for-app", inp, {"response": "FAILED"})
        svc_link_id = str(uuid.uuid4())
        self.svc_links[svc_link_id] = ServiceLink(svc_link_id, app_id, local, remote)
        return self._rpc_log("create-service-link-id-for-app", inp, {"svc_link_id_ref": svc_link_id})

    def rpc_create_candidate_paths(self, app_id: str, svc_link_id: str, app_qos: dict,
                                   include_nodes: Iterable[str] = (), exclude_nodes: Iterable[str] = (),
                                   update: bool = False) -> dict:
        """Il controller propone fino a MAX_CANDIDATE_PATHS cammini; path_id = priorità (1 = migliore)."""
        rpc = ("update" if update else "create") + "-candidate-paths-per-service-link-for-app"
        sl = self.svc_links[svc_link_id]
        sl.bandwidth_bps = int(app_qos.get("min_bandwidth", 0))
        sl.include_nodes, sl.exclude_nodes = list(include_nodes), list(exclude_nodes)

        paths = [nodes for _, nodes in self.enumerate_secure_paths(sl.local_qkdn, sl.remote_qkdn,
                                                                    set(sl.exclude_nodes))
                 if all(n in nodes for n in sl.include_nodes)][:MAX_CANDIDATE_PATHS]
        sl.candidate_paths = {i: self.nodes_to_phys_links(nodes) for i, nodes in enumerate(paths, 1)}

        inp = {"app_id_ref": app_id, "app_qos": app_qos, "app_mapped_svc_link_id_ref": svc_link_id,
               "path_constraints": {"include_nodes": sl.include_nodes, "exclude_nodes": sl.exclude_nodes}}
        out = {"candidate_paths": {
            "num_of_candidate_paths": len(sl.candidate_paths),
            "candidate_path": [{"path_id": pid, "phys_links": pls} for pid, pls in sl.candidate_paths.items()],
        }}
        return self._rpc_log(rpc, inp, out)

    def rpc_deploy_candidate_path(self, app_id: str, svc_link_id: str, path_id: int) -> dict:
        """Attiva il cammino scelto dall'orchestrator: riserva la banda sui link fisici
        e trasporta la prima chiave end-to-end fino ai KME delle due estremità."""
        inp = {"app_id_ref": app_id, "app_mapped_svc_link_id_ref": svc_link_id, "path_id": path_id}
        sl = self.svc_links.get(svc_link_id)
        plids = sl.candidate_paths.get(path_id) if sl else None
        if plids is None:
            return self._rpc_log("deploy-a-candidate-path-per-service-link-for-app", inp,
                                 {"response": "FAILED", "message": "path_id sconosciuto"})

        nodes = self.phys_links_to_nodes(sl.local_qkdn, plids)
        ok, message = self._relay_key_over_path(nodes, app_id)
        if not ok:
            return self._rpc_log("deploy-a-candidate-path-per-service-link-for-app", inp,
                                 {"response": "FAILED", "message": message})

        for plid in plids:
            self.reserved_bps[plid] = self.reserved_bps.get(plid, 0) + sl.bandwidth_bps
        sl.deployed_path_id, sl.deployed_phys_links, sl.status = path_id, list(plids), "ACTIVE"
        out = self._rpc_log("deploy-a-candidate-path-per-service-link-for-app", inp, {
            "response": "SUCCESS", "deploy_path": {"path_id": path_id, "phys_links": plids}})
        self.notify("sdqkdn_application_deployed", {"qkd_application": {
            "app_id": app_id, "svc_link_id": svc_link_id, "deploy_path": {"path_id": path_id,
                                                                          "phys_links": plids}}})
        return out

    def rpc_delete_deployed_path(self, app_id: str, svc_link_id: str, path_id: int) -> dict:
        inp = {"app_id_ref": app_id, "app_mapped_svc_link_id_ref": svc_link_id, "path_id": path_id}
        sl = self.svc_links.get(svc_link_id)
        if sl is None or sl.deployed_path_id != path_id:
            return self._rpc_log("delete-deployed-path-per-service-link-for-app", inp, {"response": "FAILED"})
        for plid in sl.deployed_phys_links:
            self.reserved_bps[plid] = max(0, self.reserved_bps.get(plid, 0) - sl.bandwidth_bps)
        sl.deployed_path_id, sl.deployed_phys_links, sl.status = None, [], "OFF"
        return self._rpc_log("delete-deployed-path-per-service-link-for-app", inp, {
            "response": "SUCCESS", "app_time": {"end_time": time.strftime("%Y-%m-%dT%H:%M:%S")}})

    # ---------------------------------------------------- calcolo dei cammini
    def _link_usable(self, u: str, mod: QKDModule) -> bool:
        v = mod.target_node
        if not mod.is_secure:
            return False
        return min(self.nodes[u].kms.get_stock_count(v),
                   self.nodes[v].kms.get_stock_count(u)) > 0

    @staticmethod
    def hop_weight(mod: QKDModule) -> float:
        return mod.fiber_length_km * (1.0 + mod.qber * 10.0)

    def enumerate_secure_paths(self, src: str, dst: str,
                               exclude: Set[str] = frozenset()) -> List[Tuple[float, List[str]]]:
        """Tutti i cammini semplici src->dst sui soli link sicuri e con chiavi, ordinati per
        peso w = somma di L * (1 + 10*QBER). La ricerca è esaustiva (DFS), adatta a una rete
        piccola come questa; su reti grandi si userebbe l'algoritmo dei k cammini di Yen."""
        results: List[Tuple[float, List[str]]] = []
        if src not in self.nodes or dst not in self.nodes or src == dst or {src, dst} & exclude:
            return results

        def dfs(u: str, path: List[str], cost: float) -> None:
            if u == dst:
                results.append((cost, list(path)))
                return
            for mod in self.nodes[u].agent.modules.values():
                v = mod.target_node
                if v in path or v in exclude or not self._link_usable(u, mod):
                    continue
                path.append(v)
                dfs(v, path, cost + self.hop_weight(mod))
                path.pop()

        dfs(src, [src], 0.0)
        results.sort(key=lambda r: r[0])
        return results

    # ------------------------------------------- trusted relay (via QKD 015)
    def _relay_key_over_path(self, path: List[str], app_id: str) -> Tuple[bool, str]:
        """Trasporta una chiave master K da path[0] a path[-1] con trusted relay hop-by-hop:
        su ogni tratto u->v, u invia c = K XOR k_uv e v recupera K = c XOR k_uv.
        I nodi intermedi vedono K in chiaro: per questo devono essere 'fidati'.
        Alla fine K è consegnata ai KME delle due estremità con un key_ID comune."""
        self.last_relay_trace = []
        hops = list(zip(path, path[1:]))

        # Verifica preliminare: non si consuma nulla se anche un solo tratto è scoperto.
        for u, v in hops:
            if self.nodes[u].kms.get_stock_count(v) == 0 or self.nodes[v].kms.get_stock_count(u) == 0:
                return False, f"Chiavi esaurite sul tratto {u}-{v}."

        master_key = fetch_real_quantum_key()
        plaintext = master_key

        for i, (u, v) in enumerate(hops):
            k_u = self.nodes[u].kms.pop_key(v)
            k_v = self.nodes[v].kms.pop_key(u)
            if k_u is None or k_u != k_v:
                return False, f"Desincronizzazione KMS sul tratto {u}-{v}."

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
            return False, "Errore di relay: la chiave ricevuta non coincide."

        key_id = str(uuid.uuid4())
        self.nodes[path[0]].kms.deliver(app_id, key_id, master_key)
        self.nodes[path[-1]].kms.deliver(app_id, key_id, plaintext)
        return True, f"Chiave consegnata ai KME di {path[0]} e {path[-1]} (key_ID {key_id})."


# ---------------------------------------------------------------------------
# Orchestratore SDN (QKD 018)
# ---------------------------------------------------------------------------
class QKDOrchestrator:
    """SDNO: riceve le richieste delle applicazioni e le traduce nel workflow di QKD 018
    (§6.2) verso il controller del dominio: discovery -> app-registration ->
    create-service-link -> candidate paths -> scelta -> deploy.

    La decisione sul cammino spetta all'orchestrator: sceglie il candidato a priorità
    più alta il cui eskr (tasso di chiave ancora libero) regge la QoS richiesta.
    Non vede mai materiale di chiave: alle applicazioni restituisce solo l'indirizzo
    dei KME, da cui prenderanno la chiave via QKD 014."""

    def __init__(self, controller: SDNController):
        self.controller = controller
        self.services: Dict[str, dict] = {}        # app_id -> servizio attivo
        self.restorations: List[dict] = []         # esiti di restoration non ancora mostrati
        controller.subscribe(self.on_notification)

    # ------------------------------------------------------------ helper
    @staticmethod
    def _phys_link_index(network: dict) -> Dict[str, dict]:
        links = network["etsi-qkd-sdn-topology:qkd_network"]["qkd_links"]["qkd_phys_links"]["phys_link"]
        return {pl["phys_link_id"]: pl for pl in links}

    def _evaluate(self, candidates: dict, network: dict, local: str, required_bps: int) -> List[dict]:
        """Valuta i candidati del controller con i dati di inventory (eskr per link)."""
        index = self._phys_link_index(network)
        evaluated = []
        for cand in candidates["candidate_paths"]["candidate_path"]:
            nodes = [local]
            for plid in cand["phys_links"]:
                pl = index[plid]
                a = pl["phys_link_local_qkdn"]["qkdn_id_ref"]
                b = pl["phys_link_remote_qkdn"]["qkdn_id_ref"]
                nodes.append(b if nodes[-1] == a else a)
            bottleneck = min(index[p]["performance"]["eskr"] for p in cand["phys_links"])
            evaluated.append({
                "path_id": cand["path_id"],
                "nodes": nodes,
                "phys_links": cand["phys_links"],
                "bottleneck_eskr_bps": bottleneck,
                "sla_ok": bottleneck >= required_bps,
            })
        return evaluated

    @staticmethod
    def _qos(required_bps: int) -> dict:
        return {"min_bandwidth": required_bps, "max_bandwidth": required_bps}

    # ------------------------------------------------- richiesta di servizio
    def request_e2e_key_service(self, client_app: str, src: str, dst: str,
                                key_rate_sla: int = DEFAULT_SLA_BPS,
                                include_nodes: Iterable[str] = (), exclude_nodes: Iterable[str] = (),
                                restoration: str = "AUTOMATIC") -> dict:
        ctrl = self.controller
        app_id = str(uuid.uuid4())
        include_nodes, exclude_nodes = list(include_nodes), list(exclude_nodes)
        ctrl.log_transaction("APP_SERVICE_REQUEST", client_app, "ORCHESTRATOR", {
            "client_app": client_app,
            "endpoints": {"source_node": src, "destination_node": dst},
            "qos": {"required_bps": key_rate_sla},
            "path_constraints": {"include_nodes": include_nodes, "exclude_nodes": exclude_nodes},
            "path_restoration": restoration,
        })
        response = {
            "service_id": app_id, "client_app": client_app,
            "endpoints": {"source_node": src, "destination_node": dst},
            "required_bps": key_rate_sla,
            "status": "FAILED", "message": "", "path": None, "candidates": [],
        }

        def finish() -> dict:
            ctrl.log_transaction("APP_SERVICE_RESPONSE", "ORCHESTRATOR", client_app, response)
            return response

        if src == dst:
            response["message"] = "Sorgente e destinazione coincidono."
            return finish()

        # 1. Topology discovery + inventory (§5.1, §5.2)
        network = ctrl.nbi_get_network()
        # 2-3. Registrazione dell'applicazione e creazione del service link (§6.2)
        ctrl.rpc_app_registration(app_id, {"client_app": client_app, "app_type": "SYMMETRIC_KEY"})
        out = ctrl.rpc_create_service_link_id_for_app(app_id, src, dst)
        svc_link_id = out.get("svc_link_id_ref")
        if not svc_link_id:
            response["message"] = "Il controller non ha creato il service link."
            return finish()
        response["svc_link_id"] = svc_link_id
        # 4. Il controller calcola i candidati...
        candidates = ctrl.rpc_create_candidate_paths(app_id, svc_link_id, self._qos(key_rate_sla),
                                                     include_nodes, exclude_nodes)
        evaluated = self._evaluate(candidates, network, src, key_rate_sla)
        response["candidates"] = evaluated
        if not evaluated:
            response["message"] = (f"Nessun cammino sicuro tra {src} e {dst} "
                                   "(QBER oltre soglia, fibra interrotta, chiavi esaurite o vincoli).")
            return finish()
        # 5. ...e l'orchestrator decide
        chosen = next((c for c in evaluated if c["sla_ok"]), None)
        if chosen is None:
            best = max(c["bottleneck_eskr_bps"] for c in evaluated)
            response["status"] = "REJECTED"
            response["message"] = (f"Nessun candidato garantisce {key_rate_sla} bit/s liberi "
                                   f"(il migliore ne ha {best}): servizio rifiutato senza consumare chiavi.")
            return finish()
        # 6. Deploy del cammino scelto
        dep = ctrl.rpc_deploy_candidate_path(app_id, svc_link_id, chosen["path_id"])
        if dep.get("response") != "SUCCESS":
            response["message"] = dep.get("message", "Deploy fallito.")
            return finish()

        response.update({
            "status": "PROVISIONED",
            "message": f"Cammino {chosen['path_id']} attivato: [{' -> '.join(chosen['nodes'])}]",
            "path": chosen["nodes"],
            "path_id": chosen["path_id"],
            "bottleneck_eskr_bps": chosen["bottleneck_eskr_bps"],
            "sla_satisfied": True,
            "kme": {src: ctrl.kme_address(src), dst: ctrl.kme_address(dst)},
        })
        self.services[app_id] = {
            "service_id": app_id, "client_app": client_app, "svc_link_id": svc_link_id,
            "src": src, "dst": dst, "required_bps": key_rate_sla,
            "include_nodes": include_nodes, "exclude_nodes": exclude_nodes,
            "restoration": restoration, "status": "ACTIVE",
            "path": chosen["nodes"], "path_id": chosen["path_id"],
        }
        return finish()

    def release_service(self, app_id: str) -> bool:
        svc = self.services.pop(app_id, None)
        if svc is None:
            return False
        if svc["path_id"] is not None:
            self.controller.rpc_delete_deployed_path(app_id, svc["svc_link_id"], svc["path_id"])
        return True

    def release_all(self) -> int:
        ids = list(self.services)
        for app_id in ids:
            self.release_service(app_id)
        return len(ids)

    # ----------------------------------------------- notifiche e restoration
    def on_notification(self, name: str, payload: dict) -> None:
        if name == "alarm" and "svc_link" in payload:
            sl_id = payload["svc_link"]["svc_link_id"]
            svc = next((s for s in self.services.values() if s["svc_link_id"] == sl_id), None)
            if svc is None or svc["status"] != "ACTIVE":
                return
            if svc["restoration"] != "AUTOMATIC":
                svc["status"] = "DOWN"
                self.restorations.append({**self._event_base(svc), "outcome": "DOWN",
                                          "reason": payload["svc_link"]["message"]})
                return
            self._restore(svc, payload["svc_link"]["message"])
        elif name == "event" and "phys_link" in payload:
            # Un link è tornato su: si riprova a servire chi era rimasto senza cammino
            for svc in list(self.services.values()):
                if svc["status"] == "DISCONNECTED" and svc["restoration"] == "AUTOMATIC":
                    self._restore(svc, payload["phys_link"]["message"])

    @staticmethod
    def _event_base(svc: dict) -> dict:
        return {"service_id": svc["service_id"], "client_app": svc["client_app"],
                "src": svc["src"], "dst": svc["dst"], "old_path": svc["path"]}

    def _restore(self, svc: dict, reason: str) -> None:
        """Path restoration AUTOMATIC: rilascia il cammino guasto, chiede nuovi candidati
        al controller (update-candidate-paths) e attiva il migliore che regge la QoS."""
        ctrl = self.controller
        event = {**self._event_base(svc), "reason": reason}
        if svc["path_id"] is not None:
            ctrl.rpc_delete_deployed_path(svc["service_id"], svc["svc_link_id"], svc["path_id"])
            svc["path_id"] = None
        network = ctrl.nbi_get_network()
        candidates = ctrl.rpc_create_candidate_paths(svc["service_id"], svc["svc_link_id"],
                                                     self._qos(svc["required_bps"]),
                                                     svc["include_nodes"], svc["exclude_nodes"], update=True)
        evaluated = self._evaluate(candidates, network, svc["src"], svc["required_bps"])
        event["candidates"] = evaluated
        chosen = next((c for c in evaluated if c["sla_ok"]), None)
        if chosen is not None:
            dep = ctrl.rpc_deploy_candidate_path(svc["service_id"], svc["svc_link_id"], chosen["path_id"])
            if dep.get("response") == "SUCCESS":
                svc.update(status="ACTIVE", path=chosen["nodes"], path_id=chosen["path_id"])
                event.update(outcome="RESTORED", new_path=chosen["nodes"],
                             trace=list(ctrl.last_relay_trace))
                self.restorations.append(event)
                return
        svc.update(status="DISCONNECTED", path=None)
        event.update(outcome="DISCONNECTED", new_path=None)
        self.restorations.append(event)

    def drain_restorations(self) -> List[dict]:
        events, self.restorations = self.restorations, []
        return events


# ---------------------------------------------------------------------------
# Applicazione <-> KME (QKD 014)
# ---------------------------------------------------------------------------
def qkd014_get_key(ctrl: SDNController, client_app: str, node_id: str, app_id: str,
                   key_id: Optional[str] = None) -> Optional[Tuple[str, bytes]]:
    """get_key (key_id assente: chiave più recente) o get_key_with_key_IDs.
    Nel log compare solo il key_ID, mai la chiave."""
    method = "get_key" if key_id is None else "get_key_with_key_IDs"
    keys = ctrl.nodes[node_id].kms.delivered.get(app_id, {})
    if key_id is None and keys:
        key_id = list(keys)[-1]
    key = keys.get(key_id) if key_id else None
    ctrl.log_transaction("ETSI_QKD_014_GET_KEY", client_app, f"KME-{node_id}", {
        "method": method,
        "kme": ctrl.kme_address(node_id),
        "keys": [{"key_ID": key_id, "key": f"<{len(key) * 8} bit>"}] if key else [],
    })
    return (key_id, key) if key else None


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
    print(" SIMULATORE SD-QKD: ETSI GS QKD 015 (controller) & QKD 018 (orchestrator)")
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


def display_018_view(sim: NetworkSimulator) -> None:
    net = sim.controller.nbi_get_network()["etsi-qkd-sdn-topology:qkd_network"]
    print("\n--- VISTA DELL'ORCHESTRATOR VIA QKD 018 (topology + inventory) ---")
    print("\nNodi SD-QKD e KME:")
    for n in net["sdqkd_nodes"]["qkdn"]:
        ifaces = ", ".join(i["qkdi_id"] for i in n["qkd_interfaces"]["qkdi"])
        print(f"  {n['qkdn_id']:<7} KME {n['kme_address']:<28} interfacce: {ifaces}")
    print("\nLink fisici (phys_link):")
    for pl in net["qkd_links"]["qkd_phys_links"]["phys_link"]:
        p = pl["performance"]
        print(f"  {pl['phys_link_id']:<22} {pl['phys_link_status']:<9} skr {p['skr']:>5} bps"
              f" | riservati {p['expected_consumption']:>5} | eskr {p['eskr']:>5} bps")
    svc = net["qkd_links"]["qkd_svc_links"]["svc_link"]
    print("\nService link attivi (svc_link):" + ("" if svc else " nessuno"))
    for s in svc:
        print(f"  {s['svc_link_id'][:8]}… {s['svc_link_local_qkdn']['qkdn_id_ref']} <-> "
              f"{s['svc_link_remote_qkdn']['qkdn_id_ref']}  banda {s['bandwidth']} bps"
              f"  restoration {s['path_restoration']['type']}  stato {s['svc_link_status']}")


def print_service_response(res: dict) -> None:
    print(f"\nEsito: {res['status']} - {res['message']}")
    if res["candidates"]:
        print(f"Cammini candidati proposti dal controller (QoS richiesta {res['required_bps']} bps):")
        for c in res["candidates"]:
            mark = "<- scelto" if c["path_id"] == res.get("path_id") else ""
            print(f"  path_id {c['path_id']}: {' -> '.join(c['nodes']):<32} eskr collo di bottiglia "
                  f"{c['bottleneck_eskr_bps']:>5} bps  {'OK ' if c['sla_ok'] else 'NO '} {mark}")
    if res["status"] == "PROVISIONED":
        print(f"KME da contattare (QKD 014): {res['kme']}")


def print_restorations(sim: NetworkSimulator) -> None:
    for ev in sim.orchestrator.drain_restorations():
        old = " -> ".join(ev["old_path"]) if ev["old_path"] else "-"
        print(f"\n[QKD 018] Allarme sul servizio {ev['client_app']} ({ev['src']} -> {ev['dst']}): {ev['reason']}")
        if ev["outcome"] == "RESTORED":
            print(f"[QKD 018] Path restoration AUTOMATICA: {old}  ==>  {' -> '.join(ev['new_path'])}")
        elif ev["outcome"] == "DISCONNECTED":
            print(f"[QKD 018] Nessun cammino alternativo: servizio sospeso (era {old}).")
        else:
            print("[QKD 018] Restoration MANUALE: servizio fermo in attesa dell'operatore.")


def fetch_keys_014(sim: NetworkSimulator, res: dict) -> None:
    """L'applicazione preleva la chiave dai due KME (get_key, poi get_key_with_key_IDs)."""
    src, dst = res["path"][0], res["path"][-1]
    got = qkd014_get_key(sim.controller, res["client_app"], src, res["service_id"])
    if not got:
        return
    key_id, key = got
    other = qkd014_get_key(sim.controller, res["client_app"], dst, res["service_id"], key_id)
    same = other is not None and other[1] == key
    print(f"[QKD 014] {src}: get_key -> key_ID {key_id}, chiave {key.hex()[:16]}…")
    print(f"[QKD 014] {dst}: get_key_with_key_IDs({key_id[:8]}…) -> "
          f"{'stessa chiave ✓' if same else 'chiave diversa ✗'}")


def choose_link(sim: NetworkSimulator) -> Optional[Tuple[str, str]]:
    links = sim.controller.links()
    for i, (a, b) in enumerate(links, 1):
        print(f"{i}. {a} <---> {b}")
    choice = input(f"Scelta [1-{len(links)}]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(links):
        return links[int(choice) - 1]
    print("\nScelta non valida.")
    return None


def manage_services(sim: NetworkSimulator) -> None:
    services = list(sim.orchestrator.services.values())
    if not services:
        print("\nNessun servizio attivo.")
        return
    print("\nServizi gestiti dall'orchestrator:")
    for i, s in enumerate(services, 1):
        path = " -> ".join(s["path"]) if s["path"] else "nessun cammino"
        print(f"{i}. {s['client_app']:<24} {s['src']} -> {s['dst']}  {s['required_bps']} bps  "
              f"[{s['status']}] {path}")
    choice = input("Numero del servizio da rilasciare (Invio per nessuno, 'a' per tutti): ").strip().lower()
    if choice == "a":
        print(f"\nRilasciati {sim.orchestrator.release_all()} servizi (delete-deployed-path).")
    elif choice.isdigit() and 1 <= int(choice) <= len(services):
        sim.orchestrator.release_service(services[int(choice) - 1]["service_id"])
        print("\nServizio rilasciato: banda liberata sui link del cammino.")


def run_demo(sim: NetworkSimulator) -> None:
    print("\n" + "=" * 80)
    print(" DIMOSTRAZIONE AUTOMATICA: GUASTO E PATH RESTORATION (QKD 018)")
    print("=" * 80)
    sim.orchestrator.release_all()
    sim.reset_network()
    sim.orchestrator.drain_restorations()

    print("\n1. La Banca-Centrale chiede un servizio di chiave PoliTo -> CSI")
    res = sim.orchestrator.request_e2e_key_service("Banca-Centrale", "PoliTo", "CSI")
    print_service_response(res)
    if res["status"] != "PROVISIONED":
        print("\nRichiesta fallita: esegui qualche tick di generazione (opzione 4) e riprova.")
        return
    fetch_keys_014(sim, res)

    a, b = res["path"][0], res["path"][1]
    print(f"\n2. Iniezione di rumore sul primo tratto del cammino ({a} <-> {b}): QBER al 15%")
    print("[QKD 015] Il controller rileva il QBER oltre soglia e invia una notifica 'alarm' all'orchestrator.")
    sim.controller.set_link_state(a, b, qber=0.15)
    print_restorations(sim)
    print("\n(Il guasto resta attivo: usa l'opzione 7 per ripristinare la rete.)")


def main() -> None:
    sim = NetworkSimulator()

    while True:
        print()
        print_banner()
        print("1. Visualizza topologia e modelli dati YANG dei nodi (QKD 015)")
        print("2. Richiedi un servizio di chiave end-to-end (QKD 018 + QKD 014)")
        print("3. Inietta una perturbazione (QBER alto o fibra interrotta)")
        print("4. Esegui un ciclo di generazione chiavi (tick)")
        print("5. Ispeziona il log dei messaggi (QKD 014 / 015 / 018)")
        print("6. Dimostrazione automatica: guasto e path restoration")
        print("7. Ripristina tutti i link ai valori nominali")
        print("8. Vista dell'orchestrator: topology e inventory (QKD 018)")
        print("9. Servizi attivi e rilascio")
        print("0. Esci")
        print("-" * 80)
        print("Nodi disponibili: " + ", ".join(sim.controller.nodes))

        choice = input("Seleziona un'operazione [0-9]: ").strip()

        if choice == "1":
            display_topology(sim)

        elif choice == "2":
            src = resolve_node(input("Nodo sorgente [PoliTo]: ") or "PoliTo", sim)
            dst = resolve_node(input("Nodo destinazione [CSI]: ") or "CSI", sim)
            if not src or not dst:
                print("\nNome di nodo non valido.")
            else:
                app_id = input("ID applicazione [Ospedale-San-Giovanni]: ").strip() or "Ospedale-San-Giovanni"
                sla = input(f"Tasso di chiave richiesto in bit/s [{DEFAULT_SLA_BPS}]: ").strip()
                sla_bps = int(sla) if sla.isdigit() else DEFAULT_SLA_BPS
                excl = input("Nodi da escludere, separati da virgola [nessuno]: ").strip()
                exclude = [n for n in (resolve_node(x, sim) for x in excl.split(",") if x.strip()) if n]
                print(f"\n[QKD 018] L'orchestrator serve {app_id} tra {src} e {dst}...")
                res = sim.orchestrator.request_e2e_key_service(app_id, src, dst, sla_bps,
                                                               exclude_nodes=exclude)
                print_service_response(res)
                if res["status"] == "PROVISIONED":
                    fetch_keys_014(sim, res)

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
                print_restorations(sim)

        elif choice == "4":
            n = sim.tick_key_generation()
            print(f"\n[TICK] Generate {n} nuove chiavi di link nei KMS.")

        elif choice == "5":
            print("\n--- ULTIME 8 TRANSAZIONI ---")
            if not sim.controller.transaction_logs:
                print("Nessuna transazione registrata.")
            for log in sim.controller.transaction_logs[-8:]:
                print(f"\n[{log['timestamp']}] {log['interface']}  {log['from']} -> {log['to']}")
                print(json.dumps(log["payload"], indent=2, ensure_ascii=False))

        elif choice == "6":
            run_demo(sim)

        elif choice == "7":
            sim.reset_network()
            print("\nTutti i link riportati a QBER nominale e accesi.")
            print_restorations(sim)

        elif choice == "8":
            display_018_view(sim)

        elif choice == "9":
            manage_services(sim)

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
