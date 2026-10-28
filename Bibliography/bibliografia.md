# Bibliografia: ETSI GS QKD 018, Orchestration Interface for SDN

> Corso di Cybersecurity, Politecnico di Torino. Ricerca del 28/10/2026.
> Obiettivo: presentazione su come l'interfaccia SDN orchestra e gestisce risorse QKD, topologie e provisioning multi-dominio.
> **Organizzazione delle cartelle** (una per step di lettura, ognuna con un `README.md`):
> `01_basi_qkd_sdn/` · `02_qkd015_controller/` · `03_qkd018_standard_e_yang/` · `04_rfc8453_analogia_actn/` · `05_casi_uso/` · `99_approfondimenti/`.
> Per scaricare i PDF liberi in automatico nelle cartelle giuste, esegui `scarica_pdf.ps1` (PowerShell).
>
> Legenda priorità: ★★★ da leggere per forza · ★★ molto utile · ★ approfondimento

---

## 0. In una slide: cosa definisce QKD 018

- **Chi parla con chi:** SDN **orchestrator** (client) ↔ **SDN controller** del dominio QKD (server). La gerarchia è: orchestrator → controller (QKD 018) → SDN agent sui nodi (QKD 015) → applicazioni che ricevono chiavi (QKD 004 / 014).
- **Tre moduli YANG 1.1** (Annex A, normativo):
  - `etsi-qkd-sdn-topology`: nodi SD-QKD, interfacce, link fisici (`PHYS`) e service link / key association link (`VIRT`);
  - `etsi-qkd-sdn-inventory`: stato, prestazioni e risorse di chiave (SKR, *effective* SKR, consumo previsto, QBER, SNR), abilitazione link, restoration;
  - `etsi-qkd-sdn-connectivity`: service link, calcolo dei cammini candidati (vincoli include/exclude), deployment.
- **Workflow multi-dominio (§6.2):** richiesta → matching degli indirizzi tra domini → richiesta di service link e cammini candidati → scelta e deploy del cammino → provisioning dell'applicazione con QoS (bandwidth, jitter, TTL) → notifica al controller della rete ottica classica (OTN).
- **Notifiche:** nodo (new/down/failed), applicazione (deployed), link fisico e service link (down, performance update, overloaded).
- **Trasporto:** NETCONF (RFC 6241) o RESTCONF (RFC 8040), codifica XML/JSON.
- **Sicurezza (§7):** il materiale di chiave **non** transita mai per controller/orchestrator.

Una metrica utile da mettere in slide (vista "di inventario" di un link $\ell$):

$$
\text{ESKR}_\ell(t) \;=\; \text{SKR}_\ell(t) \;-\; \sum_{a \,\in\, \mathcal{A}_\ell} r_a(t),
\qquad
\text{path ammissibile se } \min_{\ell \in p} \text{ESKR}_\ell \;\ge\; r_{\text{richiesta}}
$$

dove $\mathcal{A}_\ell$ sono le applicazioni che consumano chiave sul link e $r_a$ il loro rate. (Formalizzazione nostra, ispirata ai parametri SKR/ESKR dei modelli YANG: non è una formula dello standard.)

---

## 1. Fonti di studio (da cui costruire la presentazione)

| # | Fonte | Perché | Prio |
|---|---|---|---|
| 1.1 | **ETSI GS QKD 018 V1.1.1 (04/2022)**, *Orchestration Interface for Software Defined Networks*. [PDF gratuito](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/018/01.01.01_60/gs_qkd018v010101p.pdf) | Il testo dello standard. Leggere §4 (use case e funzioni dell'orchestrator), §5 (topology discovery, inventory, service status, provisioning, notifiche), §6.2 (sequence diagram multi-dominio). | ★★★ |
| 1.2 | **Modelli YANG di QKD 018 su ETSI Forge**: [forge.etsi.org/rep/qkd/gs018-orch-int-sdn](https://forge.etsi.org/rep/qkd/gs018-orch-int-sdn/-/blame/main/README.md) | I file `.yang` reali: ottimi per mostrare in slide un frammento di albero (`pyang -f tree`). | ★★★ |
| 1.3 | D.-H. Sim, J. Shin, M. H. Kim (SK Telecom), *Software-Defined Networking Orchestration for Interoperable Key Management of QKD Networks*, **Entropy** 25(6):943, 2023. [Open access](https://pmc.ncbi.nlm.nih.gov/articles/PMC10297498) · DOI 10.3390/e25060943 | Caso reale (testbed KOREN): un orchestrator gestisce due controller di vendor diversi, path computation tra domini via border node, QoS per applicazione, interoperabilità KMS con QKD 020. È l'esempio multi-dominio perfetto. | ★★★ |
| 1.4 | E. Vicario, *Hierarchical SDN control for advanced quantum-secured metropolitan networks*, tesi magistrale, **Politecnico di Milano**, 2025. [POLITesi](https://www.politesi.polimi.it/handle/10589/246504) | Prototipo end-to-end: ONOS + QKD 015 sul controller, interfaccia **QKD 018 via RESTCONF** verso un orchestrator Python che aggrega la topologia di più domini. Molto vicina al taglio della presentazione. | ★★★ |
| 1.5 | A. Sebastián-Lombraña et al., *A blueprint for large-scale quantum-network deployments* (MadQCI), 2024. [arXiv:2409.01069](https://arxiv.org/pdf/2409.01069v1) | Rete reale a Madrid (26 moduli QKD multi-vendor, >130 km) con SD-QKD stack. Spiega il ruolo di 015 vs 018; nota che l'orchestrazione QKD 018 è ancora "ongoing task". | ★★ |
| 1.6 | A. Aguado, V. López, J. P. Brito, A. Pastor, D. R. López, V. Martín, *Enabling Quantum Key Distribution Networks via Software-Defined Networking*, **ONDM 2020** (tutorial). [Pagina UPM](https://www.ccs.upm.es/research/publications/enabling-quantum-key-distribution-networks-via-software-defined-networking/) · DOI 10.23919/ONDM48393.2020.9133024 | Tutorial degli autori stessi degli standard SDN-QKD (UPM/Telefónica): ottimo per la parte "perché SDN per la QKD". | ★★ |

---

## 2. Fonti per studiare le basi

### 2a. QKD (fisica e protocolli)
- S. Pirandola et al., *Advances in quantum cryptography*, **Advances in Optics and Photonics** 12(4), 2020. [Repository Edimburgo](https://www.research.ed.ac.uk/en/publications/advances-in-quantum-cryptography/) — review di riferimento su BB84, decoy state, CV-QKD, sicurezza. ★★
- GÉANT GN5-1, *White Paper: QKD Concepts and Considerations*, 2024. [PDF](https://resources.geant.org/wp-content/uploads/2024/02/GN5-1_White-Paper_QKD-Concepts-and-Considerations.pdf) — introduzione leggibile a QKD, trusted node, panoramica degli standard ETSI (incluso 015/018) e ITU. ★★★

### 2b. Reti QKD (trusted relay, key management, routing)
- M. Mehic et al., *Quantum Key Distribution: A Networking Perspective*, **ACM Computing Surveys** 53(5), 2020. [Open access UPM](https://oa.upm.es/79638/) — architettura a strati di una QKD network, key relay, routing. ★★★
- Y. Cao, Y. Zhao, Q. Wang, J. Zhang, S. X. Ng, L. Hanzo, *The Evolution of QKD Networks: On the Road to the Qinternet*, **IEEE Communications Surveys & Tutorials**, 2022. [Manoscritto](https://eprints.soton.ac.uk/454194/1/COMST_00444_2021_final_manuscript.pdf) — sezione su SDN-aided QKD networking e standardizzazione. ★★
- *QKD Routing Protocol in Quantum Networks: Overview and Challenges*, 2024. [arXiv:2407.13156](https://arxiv.org/pdf/2407.13156) — utile per la parte "path calculation". ★

### 2c. SDN e orchestrazione gerarchica
- D. Kreutz et al., *Software-Defined Networking: A Comprehensive Survey*, **Proceedings of the IEEE** 103(1), 2015. [PDF](https://www.cs.utsa.edu/~korkmaz/teaching/ds-resources/sharvari-papers/survey-2015-Kreutz-sdn-comp-survey.pdf) — piani dati/controllo/management, northbound/southbound. ★★★
- **IETF RFC 8453**, *Framework for Abstraction and Control of TE Networks (ACTN)*, 2018. [rfc-editor](https://www.rfc-editor.org/rfc/rfc8453.html) — il modello classico orchestrator (MDSC) ↔ controller di dominio (PNC) con topologia astratta: è esattamente il pattern che QKD 018 applica alla QKD. Ottimo per un'analogia in slide. ★★

### 2d. YANG, NETCONF, RESTCONF (i "mattoni" normativi di QKD 018)
- RFC 7950, *The YANG 1.1 Data Modeling Language* — [rfc-editor](https://www.rfc-editor.org/rfc/rfc7950.html)
- RFC 6241, *NETCONF* — [rfc-editor](https://www.rfc-editor.org/rfc/rfc6241.html)
- RFC 8040, *RESTCONF* — [rfc-editor](https://www.rfc-editor.org/rfc/rfc8040.html)
- RFC 8345, *A YANG Data Model for Network Topologies* (citato nell'Annex B di QKD 018) — [rfc-editor](https://www.rfc-editor.org/rfc/rfc8345.html)
- RFC 6991, *Common YANG Data Types* — [rfc-editor](https://www.rfc-editor.org/rfc/rfc6991.html)

Basta leggere l'introduzione e gli esempi di RFC 8040 e i capitoli 4 e 7 di RFC 7950.

---

## 3. Quadro generale (dove si colloca QKD 018)

- **ETSI ISG QKD**: [pagina del gruppo](https://www.etsi.org/committee/qkd) e [Activity Report](https://www.etsi.org/committee-activity/activity-report-qkd): elenco e stato di tutte le specifiche.
- **Mappa degli standard ETSI rilevanti** (dal basso verso l'alto):

| Livello | Standard | Ruolo |
|---|---|---|
| Applicazione ↔ KMS | GS QKD 004 (Application Interface), GS QKD 014 (REST key delivery API) | consegna delle chiavi alle SAE |
| KMS ↔ KMS | GS QKD 020 (Interoperable KMS) | scambio chiavi tra domini/vendor |
| Controller ↔ nodo | **GS QKD 015** (Control Interface for SDN) | modello del nodo SD-QKD, agent, link |
| Orchestrator ↔ controller | **GS QKD 018** | topologia astratta, inventory, provisioning multi-dominio |
| Terminologia | GR QKD 007 (Vocabulary) | definizioni |

- **ITU-T**: serie Y.3800 (Y.3800 overview, Y.3804 *QKDN control and management*, Y.3805 *SDN control for QKDN*). [Sommario Y.3804 (04/2025)](https://www.itu.int/dms_pubrec/itu-t/rec/y/T-REC-Y.3804-202504-I!!SUM-HTM-E.htm). Utile per confrontare l'approccio ITU con quello ETSI.
- **Stato dell'arte e limiti**: MadQCI (1.5) e Laschet et al. (4.9) mostrano che 015 è molto usato, mentre 018 è ancora poco implementato. Spunto per una slide critica finale.

---

## 4. Documenti tecnici

| # | Documento | Link |
|---|---|---|
| 4.1 | ETSI GS QKD 018 V1.1.1 (2022-04) | [PDF](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/018/01.01.01_60/gs_qkd018v010101p.pdf) |
| 4.2 | YANG di QKD 018 (ETSI Forge) | [gs018-orch-int-sdn](https://forge.etsi.org/rep/qkd/gs018-orch-int-sdn/-/blame/main/README.md) |
| 4.3 | ETSI GS QKD 015 V2.1.1 (2022-04), Control Interface for SDN, + YANG | [PDF](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/015/02.01.01_60/gs_QKD015v020101p.pdf) · [Forge](https://forge.etsi.org/rep/qkd/gs015-ctrl-int-sdn) |
| 4.4 | ETSI GS QKD 004 V2.1.1, Application Interface | [PDF](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/004/02.01.01_60/gs_QKD004v020101p.pdf) |
| 4.5 | ETSI GS QKD 014 V1.1.1, REST-based Key Delivery API | [Scheda StandICT](https://standict.eu/quantum-computing/297?page=3) · [spiegazione pratica](https://brunorijsman.github.io/openssl-qkd/doc/the-etsi-qkd-api.html) |
| 4.6 | ETSI GS QKD 020, Interoperable KMS (repo) | [Forge](https://forge.etsi.org/rep/qkd/gs020-interop-kms/-/blame/232736397552e26748785568f4885964957212af/README.md) |
| 4.7 | A. Aguado et al., *First experimental demonstration of secure NFV orchestration over an SDN-controlled optical network with time-shared QKD resources* (ECOC 2016) | [arXiv:1604.05861](https://arxiv.org/pdf/1604.05861) |
| 4.8 | R. B. Méndez et al., *Switching Coordinator: An SDN Application for Flexible QKD Networks*, Entropy 28:219, 2026 | [arXiv:2603.13812](https://arxiv.org/pdf/2603.13812) (usa 015, utile per la gestione dinamica delle risorse) |
| 4.9 | S. Laschet et al. (AIT), *SDN Key Relay for Large-Scale QKD Networks*, IEEE OCNC 2026 | [arXiv:2608.17539](https://arxiv.org/pdf/2608.17539) (KPI per path selection e routing inter-dominio che preserva la privacy; critica a ESKR di 015) |
| 4.10 | *Interconnecting Regional QKD Networks: Hybrid Key Delivery Across Quantum Domains*, 2026 | [arXiv:2604.20376](https://arxiv.org/pdf/2604.20376) (multi-dominio, non ancora letto in dettaglio) |

---

## Percorso di lettura consigliato

1. GÉANT white paper (2a) → Kreutz §I–IV (2c): basi QKD + SDN.
2. QKD 015 §4–5 (4.3): cosa vede il controller.
3. **QKD 018 completo (1.1)** con i file YANG aperti (1.2).
4. RFC 8453 §2–4 (2c): analogia ACTN.
5. Casi d'uso: Sim et al. (1.3) e tesi PoliMi (1.4), poi MadQCI (1.5).

## Struttura di presentazione suggerita

1. Problema: perché una rete QKD ha bisogno di SDN.
2. Architettura gerarchica: orchestrator / controller / agent / KMS / SAE.
3. Modello informativo QKD 018: topology, inventory, connectivity.
4. Gestione delle risorse: SKR, ESKR, notifiche, restoration.
5. Provisioning multi-dominio: sequence diagram §6.2 e caso SK Telecom.
6. Sicurezza e limiti: separazione delle chiavi dal piano di controllo, adozione ancora scarsa, confronto con ITU-T Y.3805.
