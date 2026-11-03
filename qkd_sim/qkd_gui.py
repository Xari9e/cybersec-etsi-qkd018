#!/usr/bin/env python3
"""
Interfaccia grafica per il simulatore SD-QKD (qkd_sim.py).

Uso (qkd_sim.py deve stare nella stessa cartella):
    python3 qkd_gui.py             # usa OSRM e ANU QRNG se raggiungibili
    python3 qkd_gui.py --offline   # nessuna chiamata di rete

Richiede solo Tkinter, incluso in Python su Windows e macOS
(su Linux: sudo apt install python3-tk).
"""
from __future__ import annotations

import json
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable, Iterator, List, Optional, Tuple

import qkd_sim as q

# ---------------------------------------------------------------------------
# Stile
# ---------------------------------------------------------------------------
C = {
    "bg": "#f4f6fb", "panel": "#ffffff", "ink": "#1d2433", "muted": "#6b7280",
    "ok": "#16a34a", "bad": "#dc2626", "empty": "#ea8c00", "down": "#a3a9b5",
    "path": "#2563eb", "node": "#1e3a8a", "sel": "#7c3aed",
    "key": "#f59e0b", "cipher": "#4b5563", "label_bg": "#ffffff",
}
FONT = ("Helvetica", 10)
FONT_B = ("Helvetica", 10, "bold")
FONT_H = ("Helvetica", 12, "bold")
FONT_NODE = ("Helvetica", 11, "bold")
FONT_SMALL = ("Helvetica", 9)

NODE_INFO = {
    "PoliTo": "Politecnico di Torino",
    "Fisica": "Dip. di Fisica (UniTo)",
    "INRiM": "Ist. Naz. Ricerca Metrologica",
    "CSI": "CSI Piemonte",
}
# Schema ad anello (non in scala): PoliTo–Fisica / | | / INRiM–CSI
POS = {"PoliTo": (180, 115), "Fisica": (540, 115), "INRiM": (180, 335), "CSI": (540, 335)}
NODE_R = 38


def short(hex_str: str, n: int = 8) -> str:
    return hex_str[:n] + "…"


class QKDApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.sim = q.NetworkSimulator()
        self.ctrl = self.sim.controller
        self.selected: Optional[Tuple[str, str]] = None
        self.highlight: Optional[List[str]] = None
        self.busy = False

        root.title("Simulatore di rete QKD – Torino")
        root.configure(bg=C["bg"])
        root.geometry("1220x780")
        root.minsize(1100, 700)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TLabelframe", background=C["panel"])
        style.configure("TLabelframe.Label", background=C["panel"], font=FONT_B, foreground=C["ink"])
        style.configure("TLabel", background=C["panel"], font=FONT, foreground=C["ink"])
        style.configure("Muted.TLabel", foreground=C["muted"], font=FONT_SMALL)
        style.configure("TButton", font=FONT, padding=5)
        style.configure("Accent.TButton", font=FONT_B, padding=6)
        style.configure("TCheckbutton", background=C["panel"], font=FONT)

        self._build_layout()
        self.redraw()
        self.refresh_log()
        self.intro()
        self.root.after(3000, self._auto_tick_loop)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=0)
        self.root.rowconfigure(0, weight=1)

        left = tk.Frame(self.root, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        left.rowconfigure(0, weight=0)
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        right = tk.Frame(self.root, bg=C["bg"], width=400)
        right.grid(row=0, column=1, sticky="ns", padx=(6, 12), pady=12)

        # --- mappa
        map_box = ttk.LabelFrame(left, text="  Mappa della rete  ")
        map_box.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(map_box, width=720, height=420, bg=C["panel"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)

        # --- legenda
        legend = tk.Canvas(left, height=30, bg=C["bg"], highlightthickness=0)
        legend.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        x = 4
        small = tkfont.Font(font=FONT_SMALL)
        for color, text, dash in [
            (C["ok"], "link sicuro", None), (C["empty"], "chiavi esaurite", None),
            (C["bad"], "QBER oltre soglia", None), (C["down"], "fibra interrotta", (6, 4)),
            (C["path"], "cammino in uso", None), (C["sel"], "selezionato", None),
        ]:
            legend.create_line(x, 15, x + 22, 15, fill=color, width=5, dash=dash)
            legend.create_text(x + 28, 15, text=text, anchor="w", font=FONT_SMALL, fill=C["ink"])
            x += 28 + small.measure(text) + 14

        # --- spiegazione
        story_box = ttk.LabelFrame(left, text="  Cosa sta succedendo  ")
        story_box.grid(row=2, column=0, sticky="nsew")
        self.story = tk.Text(story_box, wrap="word", font=FONT, bg=C["panel"], fg=C["ink"],
                             relief="flat", padx=10, pady=8, height=10)
        self.story.pack(fill="both", expand=True, padx=4, pady=4)
        self.story.tag_configure("h", font=FONT_H, spacing3=4)
        self.story.tag_configure("b", font=FONT_B)
        self.story.tag_configure("ok", foreground=C["ok"], font=FONT_B)
        self.story.tag_configure("bad", foreground=C["bad"], font=FONT_B)
        self.story.tag_configure("mono", font="TkFixedFont", foreground=C["sel"])
        self.story.tag_configure("muted", foreground=C["muted"])
        self.story.configure(state="disabled")

        # --- 1. richiesta
        box1 = ttk.LabelFrame(right, text="  1 · Richiedi una chiave end-to-end  ")
        box1.pack(fill="x", pady=(0, 8))
        nodes = list(self.ctrl.nodes)
        self.src = tk.StringVar(value="PoliTo")
        self.dst = tk.StringVar(value="CSI")
        self.app_name = tk.StringVar(value="Ospedale-San-Giovanni")
        for r, (lbl, widget) in enumerate([
            ("Da:", ttk.Combobox(box1, textvariable=self.src, values=nodes, state="readonly", width=22)),
            ("A:", ttk.Combobox(box1, textvariable=self.dst, values=nodes, state="readonly", width=22)),
            ("Applicazione:", ttk.Entry(box1, textvariable=self.app_name, width=24)),
        ]):
            ttk.Label(box1, text=lbl).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            widget.grid(row=r, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(box1, text="Richiedi chiave", style="Accent.TButton",
                   command=self.on_request).grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 8))

        # --- 2. perturbazioni
        box2 = ttk.LabelFrame(right, text="  2 · Disturba un collegamento  ")
        box2.pack(fill="x", pady=(0, 8))
        self.sel_label = ttk.Label(box2, text="Clicca un collegamento sulla mappa.", wraplength=350)
        self.sel_label.pack(fill="x", padx=8, pady=(4, 4))
        row = tk.Frame(box2, bg=C["panel"])
        row.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(row, text="Intercettazione (QBER 14,5%)", command=self.on_noise).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(row, text="Taglia la fibra", command=self.on_cut).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(box2, text="Ripristina tutta la rete", command=self.on_restore).pack(fill="x", padx=8, pady=(2, 8))

        # --- 3. generazione
        box3 = ttk.LabelFrame(right, text="  3 · Generazione delle chiavi quantistiche  ")
        box3.pack(fill="x", pady=(0, 8))
        ttk.Button(box3, text="Genera nuove chiavi (1 ciclo)", command=self.on_tick).pack(fill="x", padx=8, pady=(6, 4))
        self.auto_tick = tk.BooleanVar(value=False)
        ttk.Checkbutton(box3, text="Generazione automatica ogni 3 secondi",
                        variable=self.auto_tick).pack(anchor="w", padx=8, pady=(0, 8))

        # --- 4. demo
        box4 = ttk.LabelFrame(right, text="  4 · Demo guidata  ")
        box4.pack(fill="x", pady=(0, 8))
        ttk.Button(box4, text="Guasto e re-routing automatico", style="Accent.TButton",
                   command=self.on_demo).pack(fill="x", padx=8, pady=8)

        # --- log
        box5 = ttk.LabelFrame(right, text="  Messaggi ETSI (doppio clic per il JSON)  ")
        box5.pack(fill="both", expand=True)
        frame = tk.Frame(box5, bg=C["panel"])
        frame.pack(fill="both", expand=True, padx=6, pady=6)
        self.log = tk.Listbox(frame, font=("Courier", 9), activestyle="none", relief="flat",
                              bg="#fbfbfd", highlightthickness=0, width=52, height=12)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.bind("<Double-Button-1>", self.on_log_open)

    # ------------------------------------------------------------- stato rete
    def link_state(self, a: str, b: str) -> Tuple[str, q.QKDModule, int]:
        mod = self.ctrl.nodes[a].agent.module_towards(b)
        stock = min(self.ctrl.nodes[a].kms.get_stock_count(b), self.ctrl.nodes[b].kms.get_stock_count(a))
        if not mod.is_active:
            return "down", mod, stock
        if mod.qber >= q.QBER_SECURITY_THRESHOLD:
            return "bad", mod, stock
        if stock == 0:
            return "empty", mod, stock
        return "ok", mod, stock

    def e2e_keys(self, node_id: str) -> int:
        stores = self.ctrl.nodes[node_id].kms.key_stores
        return sum(len(v) for k, v in stores.items() if k.startswith("VIRTUAL_"))

    # ---------------------------------------------------------------- disegno
    def redraw(self) -> None:
        cv = self.canvas
        cv.delete("net")
        cv.create_text(16, 16, text="Schema ad anello (non in scala) · clicca un collegamento per selezionarlo",
                       anchor="w", font=FONT_SMALL, fill=C["muted"], tags="net")

        path_edges = set()
        if self.highlight:
            path_edges = {tuple(sorted(e)) for e in zip(self.highlight, self.highlight[1:])}

        for a, b in self.ctrl.links():
            state, mod, stock = self.link_state(a, b)
            (x1, y1), (x2, y2) = POS[a], POS[b]
            tag = f"link:{a}|{b}"
            # zona cliccabile più larga
            cv.create_line(x1, y1, x2, y2, width=26, fill=C["panel"], tags=("net", tag))
            if self.selected == (a, b):
                cv.create_line(x1, y1, x2, y2, width=16, fill=C["sel"], tags=("net", tag))
            color = {"ok": C["ok"], "bad": C["bad"], "empty": C["empty"], "down": C["down"]}[state]
            cv.create_line(x1, y1, x2, y2, width=7, fill=color,
                           dash=(10, 6) if state == "down" else None, tags=("net", tag))
            if (a, b) in path_edges:
                cv.create_line(x1, y1, x2, y2, width=3, fill=C["path"], tags=("net", tag))

            if state == "down":
                text = "FIBRA INTERROTTA"
            elif state == "bad":
                text = f"QBER {mod.qber * 100:.1f}%  ·  NON SICURO\nescluso dal routing"
            else:
                text = (f"QBER {mod.qber * 100:.1f}%  ·  {mod.fiber_length_km:.1f} km\n"
                        f"{stock} chiavi  ·  {mod.secure_key_rate_bps} bit/s")
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            t = cv.create_text(mx, my, text=text, font=FONT_SMALL, fill=C["ink"], justify="center",
                               tags=("net", tag))
            bx = cv.bbox(t)
            r = cv.create_rectangle(bx[0] - 8, bx[1] - 5, bx[2] + 8, bx[3] + 5, fill=C["label_bg"],
                                    outline=color, width=2, tags=("net", tag))
            cv.tag_raise(t, r)
            cv.tag_bind(tag, "<Button-1>", lambda e, l=(a, b): self.on_select(l))
            cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

        for node_id, (x, y) in POS.items():
            on_path = bool(self.highlight) and node_id in self.highlight
            cv.create_oval(x - NODE_R, y - NODE_R, x + NODE_R, y + NODE_R,
                           fill=C["node"], outline=C["path"] if on_path else C["panel"],
                           width=5 if on_path else 2, tags="net")
            cv.create_text(x, y, text=node_id, font=FONT_NODE, fill="white", tags="net")
            top = y < 250
            dy = -NODE_R - 30 if top else NODE_R + 14
            e2e = self.e2e_keys(node_id)
            cv.create_text(x, y + dy, text=NODE_INFO[node_id], font=FONT_SMALL, fill=C["muted"],
                           anchor="n", tags="net")
            cv.create_text(x, y + dy + 14, text=f"chiavi end-to-end ricevute: {e2e}",
                           font=FONT_SMALL, fill=C["path"] if e2e else C["muted"], anchor="n", tags="net")
        cv.tag_raise("packet")

    # --------------------------------------------------------------- narrazione
    def clear(self) -> None:
        self.story.configure(state="normal")
        self.story.delete("1.0", "end")
        self.story.configure(state="disabled")

    def say(self, *parts) -> None:
        """say(("testo", "tag"), "testo semplice", ...) e va a capo."""
        self.story.configure(state="normal")
        for p in parts:
            if isinstance(p, tuple):
                self.story.insert("end", p[0], p[1])
            else:
                self.story.insert("end", p)
        self.story.insert("end", "\n")
        self.story.see("end")
        self.story.configure(state="disabled")

    def intro(self) -> None:
        self.clear()
        self.say(("Cosa simula questo programma", "h"))
        self.say("Quattro sedi di Torino sono collegate ad anello da fibre ottiche con apparati ",
                 ("QKD", "b"), " (distribuzione quantistica di chiavi). Ogni fibra produce di continuo "
                 "chiavi segrete condivise solo dai due nodi ai suoi capi: il numero accanto a ogni "
                 "collegamento è la scorta di chiavi disponibili.")
        self.say("Due sedi non collegate direttamente (es. PoliTo e CSI) ottengono una chiave comune "
                 "facendola passare per un nodo intermedio ", ("fidato", "b"),
                 ", che la ricifra tratto per tratto. Il ", ("QBER", "b"),
                 " è la percentuale di bit errati: se supera l'8% (rumore o un intercettatore) "
                 "il collegamento non è più considerato sicuro.")
        self.say(("Prova così: ", "b"), "1) premi «Richiedi chiave»; 2) clicca il collegamento usato e "
                 "simula un'intercettazione; 3) richiedi di nuovo la chiave e guarda il percorso cambiare. "
                 "Oppure lancia la demo guidata.")
        self.story.see("1.0")

    # --------------------------------------------------------------- animazione
    def run_steps(self, gen: Iterator[int]) -> None:
        try:
            delay = next(gen)
        except StopIteration:
            return
        self.root.after(delay, lambda: self.run_steps(gen))

    def relay_steps(self, res: dict, trace: List[dict], on_done: Optional[Callable] = None) -> Iterator[int]:
        self.busy = True
        path = res["path"]
        src, dst = path[0], path[-1]
        K = res["allocated_key_hex"]
        self.highlight = path
        self.redraw()

        self.clear()
        self.say((f"Chiave end-to-end {src} → {dst}", "h"))
        self.say("1. Il controller SDN sceglie il cammino più sicuro: ", (" → ".join(path), "b"),
                 ("   (peso di ogni tratto = km × (1 + 10·QBER), solo link sicuri e con chiavi)", "muted"))
        yield 1500
        self.say(f"2. {src} genera una chiave casuale K = ", (short(K, 16), "mono"))
        pk = self.canvas.create_oval(0, 0, 0, 0, fill=C["key"], outline="", tags="packet")
        pt = self.canvas.create_text(0, 0, text="K", font=FONT_B, fill="white", tags="packet")
        self._place_packet(pk, pt, *POS[src])
        yield 1400

        for i, hop in enumerate(trace, start=3):
            u, v = hop["from"], hop["to"]
            self.say(f"{i}. {u} cifra K con la chiave quantistica del tratto {u}–{v}:  c = K ⊕ k = ",
                     (short(hop["ciphertext"]), "mono"),
                     ("   (sul canale viaggia solo c: senza k è rumore puro)", "muted"))
            self.canvas.itemconfigure(pk, fill=C["cipher"])
            self.canvas.itemconfigure(pt, text="c")
            yield 900
            (x1, y1), (x2, y2) = POS[u], POS[v]
            steps = 30
            for s in range(1, steps + 1):
                f = s / steps
                self._place_packet(pk, pt, x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)
                yield 25
            self.canvas.itemconfigure(pk, fill=C["key"])
            self.canvas.itemconfigure(pt, text="K")
            if v != dst:
                self.say(f"   {v} decifra con la sua copia di k e riottiene K: è un ", ("nodo fidato", "b"),
                         ", perché vede la chiave in chiaro.")
            else:
                self.say(f"   {v} decifra e ottiene K = ", (short(hop["recovered"], 16), "mono"), "  ",
                         ("✓ identica a quella di partenza", "ok"))
            yield 1300

        self.canvas.delete("packet")
        bottleneck = res.get("bottleneck_key_rate_bps")
        ok = res.get("sla_satisfied")
        self.say(("Risultato: ", "b"), f"{src} e {dst} condividono la stessa chiave a 256 bit. "
                 f"È stata consumata una chiave per ogni tratto attraversato. Velocità del tratto più lento: "
                 f"{bottleneck} bit/s ", (f"(richiesti 1000: {'SLA rispettata' if ok else 'SLA NON rispettata'})",
                                          "ok" if ok else "bad"))
        self.redraw()
        self.refresh_log()
        self.busy = False
        if on_done:
            on_done()

    def _place_packet(self, pk: int, pt: int, x: float, y: float, r: int = 12) -> None:
        x, y = x + 24, y - 24   # leggermente spostato, per non coprire i nomi dei nodi
        self.canvas.coords(pk, x - r, y - r, x + r, y + r)
        self.canvas.coords(pt, x, y)
        self.canvas.tag_raise("packet")

    # ------------------------------------------------------------------ azioni
    def _guard(self) -> bool:
        if self.busy:
            self.root.bell()
            return False
        return True

    def request(self, src: str, dst: str, app: str) -> Tuple[dict, List[dict]]:
        res = self.sim.orchestrator.request_e2e_key_service(app, src, dst, key_rate_sla=1000)
        return res, list(self.ctrl.last_relay_trace)

    def explain_failure(self, res: dict, src: str, dst: str) -> None:
        self.clear()
        self.say((f"Richiesta {src} → {dst} rifiutata", "h"))
        self.say(("✗ ", "bad"), res["message"])
        reasons = {"down": "fibra interrotta", "bad": "QBER oltre l'8%", "empty": "nessuna chiave in scorta"}
        blocked = [(a, b, reasons[s]) for a, b in self.ctrl.links()
                   for s in [self.link_state(a, b)[0]] if s != "ok"]
        if blocked:
            self.say("Collegamenti inutilizzabili in questo momento:")
            for a, b, why in blocked:
                self.say(f"   • {a}–{b}: {why}")
        self.say(("Suggerimento: ", "b"), "genera nuove chiavi (sezione 3) o ripristina la rete (sezione 2).")

    def on_request(self) -> None:
        if not self._guard():
            return
        src, dst = self.src.get(), self.dst.get()
        if src == dst:
            self.clear()
            self.say(("Scegli due nodi diversi.", "bad"))
            return
        res, trace = self.request(src, dst, self.app_name.get().strip() or "App")
        self.refresh_log()
        if res["status"] != "PROVISIONED":
            self.highlight = None
            self.redraw()
            self.explain_failure(res, src, dst)
            return
        self.run_steps(self.relay_steps(res, trace))

    def on_select(self, link: Tuple[str, str]) -> None:
        self.selected = link
        a, b = link
        state, mod, stock = self.link_state(a, b)
        self.sel_label.configure(text=f"Selezionato: {a} ↔ {b}   (QBER {mod.qber * 100:.1f}%, "
                                      f"{'acceso' if mod.is_active else 'spento'}, {stock} chiavi)")
        self.redraw()

    def _need_selection(self) -> Optional[Tuple[str, str]]:
        if not self.selected:
            self.clear()
            self.say(("Prima clicca un collegamento sulla mappa.", "bad"))
            return None
        return self.selected

    def on_noise(self) -> None:
        if not self._guard() or not (link := self._need_selection()):
            return
        a, b = link
        self.ctrl.set_link_state(a, b, qber=0.145)
        self.highlight = None
        self.on_select(link)
        self.clear()
        self.say((f"Intercettazione sul collegamento {a}–{b}", "h"))
        self.say("Chi prova a misurare i fotoni in transito ne altera lo stato quantistico: i due capi "
                 "lo scoprono perché gli errori salgono. Qui il QBER passa a ", ("14,5%", "bad"),
                 ", oltre la soglia dell'8%.")
        self.say("Conseguenze: il link smette di produrre chiavi e il controller lo esclude dal calcolo "
                 "dei cammini. Prova ora a richiedere una chiave.")

    def on_cut(self) -> None:
        if not self._guard() or not (link := self._need_selection()):
            return
        a, b = link
        self.ctrl.set_link_state(a, b, active=False)
        self.highlight = None
        self.on_select(link)
        self.clear()
        self.say((f"Fibra interrotta tra {a} e {b}", "h"))
        self.say("Il collegamento è spento: niente nuove chiavi e niente traffico. "
                 "Se esiste un'altra strada nell'anello, il controller la userà.")

    def on_restore(self) -> None:
        if not self._guard():
            return
        self.sim.reset_network()
        self.highlight = None
        self.redraw()
        if self.selected:
            self.on_select(self.selected)
        self.clear()
        self.say(("Rete ripristinata", "h"))
        self.say("Tutti i collegamenti sono accesi e con il QBER nominale. Le scorte di chiavi restano "
                 "quelle attuali: se sono finite, generane di nuove.")

    def on_tick(self) -> None:
        n = self.sim.tick_key_generation()
        self.redraw()
        if self.busy:
            return
        self.clear()
        self.say(("Nuovo ciclo di generazione", "h"))
        self.say(f"I collegamenti sicuri hanno prodotto {n} nuove chiavi. Ne producono di più quelli "
                 "corti (meno perdite nella fibra) e con QBER basso; i link disturbati o tagliati non ne producono.")
        detail = []
        for a, b in self.ctrl.links():
            mod = self.ctrl.nodes[a].agent.module_towards(b)
            detail.append(f"   • {a}–{b}: +{mod.keys_per_tick()}")
        self.say("\n".join(detail))

    def _auto_tick_loop(self) -> None:
        if self.auto_tick.get():
            self.sim.tick_key_generation()
            self.redraw()
        self.root.after(3000, self._auto_tick_loop)

    # -------------------------------------------------------------------- demo
    def on_demo(self) -> None:
        if not self._guard():
            return
        self.sim.reset_network()
        self.sim.tick_key_generation()
        res, trace = self.request("PoliTo", "CSI", "Banca-Centrale")
        if res["status"] != "PROVISIONED":
            self.explain_failure(res, "PoliTo", "CSI")
            return
        self._demo_link = (res["path"][0], res["path"][1])
        self.run_steps(self.relay_steps(res, trace, on_done=lambda: self.root.after(2500, self._demo_attack)))

    def _demo_attack(self) -> None:
        a, b = self._demo_link
        self.ctrl.set_link_state(a, b, qber=0.15)
        self.highlight = None
        self.selected = None
        self.sel_label.configure(text="Clicca un collegamento sulla mappa.")
        self.redraw()
        self.clear()
        self.say(("Demo – arriva un intercettatore", "h"))
        self.say(f"Qualcuno spia la fibra {a}–{b}: il QBER sale al ", ("15%", "bad"),
                 ". Gli agenti dei nodi lo comunicano al controller (interfaccia ETSI 015), che esclude il link.")
        self.say("Tra poco la banca chiede un'altra chiave PoliTo → CSI…")
        self.root.after(3500, self._demo_reroute)

    def _demo_reroute(self) -> None:
        res, trace = self.request("PoliTo", "CSI", "Banca-Centrale")
        if res["status"] != "PROVISIONED":
            self.explain_failure(res, "PoliTo", "CSI")
            return

        def done() -> None:
            self.say(("Re-routing riuscito: ", "ok"), "il controller ha scelto da solo un percorso alternativo, "
                     "senza che l'applicazione se ne accorgesse. Usa «Ripristina tutta la rete» per tornare allo stato normale.")
        self.run_steps(self.relay_steps(res, trace, on_done=done))

    # --------------------------------------------------------------------- log
    @staticmethod
    def summarize(log: dict) -> Tuple[str, str]:
        p, itf = log["payload"], log["interface"]
        if itf == "ETSI_QKD_018_REST_REQUEST":
            e = p["endpoints"]
            return f"018 ▶ richiesta {e['source_node']}→{e['destination_node']}", C["path"]
        if itf == "ETSI_QKD_018_REST_RESPONSE":
            ok = p["status"] == "PROVISIONED"
            via = " → ".join(p["path"]) if p.get("path") else ""
            return f"018 ◀ {p['status']} {via}", C["ok"] if ok else C["bad"]
        if itf == "ETSI_QKD_015_NETCONF_RPC":
            return f"015 ▶ {log['to']}: {p['rpc']} {p['link']}", C["ink"]
        return f"015 · stato letto da {log['from']}", C["muted"]

    def refresh_log(self) -> None:
        self.log.delete(0, "end")
        self._log_items = self.ctrl.transaction_logs[-300:]
        for i, entry in enumerate(self._log_items):
            text, color = self.summarize(entry)
            self.log.insert("end", f"{entry['timestamp']}  {text}")
            self.log.itemconfigure(i, foreground=color)
        self.log.see("end")

    def on_log_open(self, _event) -> None:
        sel = self.log.curselection()
        if not sel:
            return
        entry = self._log_items[sel[0]]
        win = tk.Toplevel(self.root)
        win.title(f"{entry['interface']} – {entry['timestamp']}")
        win.geometry("560x460")
        txt = tk.Text(win, font="TkFixedFont", wrap="none", padx=10, pady=10)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", f"{entry['interface']}\n{entry['from']} → {entry['to']}\n\n"
                          + json.dumps(entry["payload"], indent=2, ensure_ascii=False))
        txt.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    QKDApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
