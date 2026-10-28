# I moduli YANG di ETSI GS QKD 018: cosa sono e a cosa servono

Fonte: i file in [forge.etsi.org/rep/qkd/gs018-orch-int-sdn](https://forge.etsi.org/rep/qkd/gs018-orch-int-sdn), revisione 2022-02-22, allineata a QKD 015 V2.1.1.

## Lo scopo generale

I tre file sono l'**Annex A (normativo)** di QKD 018 in forma leggibile da una macchina. Definiscono il "vocabolario" con cui l'**SDN orchestrator** (client) e l'**SDN controller** di un dominio QKD (server) si scambiano informazioni via NETCONF o RESTCONF:
- i dati che l'orchestrator può **leggere**: topologia, stato, prestazioni;
- i dati che può **configurare**: abilitazione dei link, restoration;
- le **operazioni** che può invocare (RPC), cioè registrare un'applicazione, calcolare i cammini e farne il deploy;
- le **notifiche** che riceve.

Un controller che implementa questi moduli è interoperabile con un orchestrator di qualunque vendor. È questo che rende possibile il multi-dominio.

Non sono autonomi: importano due moduli di **QKD 015**, `etsi-qkd-sdn-node` e `etsi-qkd-node-types`, per i tipi base come gli ID dei nodi, i tipi di link, i parametri delle applicazioni e la QoS. Quindi 018 riusa e **astrae** il modello del nodo di 015. Per usare `pyang` servono anche i file di 015.

I tre moduli seguono una catena precisa: *topology* (lo scheletro) → *inventory* (cosa c'è e come sta) → *connectivity* (cosa ci faccio).

---

## 1. `etsi-qkd-sdn-topology.yang`: lo scheletro della rete

Prefisso `qkdnw-tplg`. Descrive **solo la struttura**: chi è collegato a chi, senza parametri.

```
qkd_network
├── sdqkd_nodes
│   └── qkdn* [qkdn_id]                 nodi SD-QKD
│       └── qkd_interfaces/qkdi* [qkdi_id]   interfacce (moduli QKD)
└── qkd_links
    ├── qkd_phys_links/phys_link* [phys_link_id]   link fisici (quantum channel)
    │   ├── link_type
    │   ├── phys_link_local_qkdn  (qkdn_id_ref, qkdi_id_ref)
    │   └── phys_link_remote_qkdn (qkdn_id_ref, qkdi_id_ref)
    └── qkd_svc_links/svc_link* [svc_link_id]      service link (key association end-to-end)
        └── ... stessa struttura local/remote
```

- È un **grafo** $`G=(V,E)`$: i nodi sono le `qkdn`, gli archi sono i `phys_link` (adiacenze fisiche) e i `svc_link` (relazioni logiche di chiave, anche multi-hop via trusted node).
- I link sono identificati da UUID e puntano alle estremità tramite *leafref*, cioè riferimenti validati verso nodi e interfacce esistenti.
- Serve alla **topology discovery** (§5.1 dello standard): l'orchestrator ottiene una vista astratta e indipendente dal vendor di ogni dominio e la può comporre in una vista multi-dominio.

## 2. `etsi-qkd-sdn-inventory.yang`: risorse, stato e prestazioni

Prefisso `qkdnw-invt`. Non crea un albero nuovo: fa **augment** dell'albero di topology, cioè aggiunge foglie ai nodi, alle interfacce e ai link già definiti.

| Elemento aumentato | Cosa aggiunge |
|---|---|
| `qkdn` | versione, capability, location, **indirizzo IP:porta del KME** (dove le applicazioni prendono le chiavi via QKD 014), stato |
| `qkdi` | ruolo supportato, modello, tipo, punto di attacco, stato |
| `phys_link` | `enable` (configurabile), `wavelength`, `performance` (`skr`, `eskr`, `expected_consumption` in bps e metriche fisiche come QBER e SNR), `phys_link_status` |
| `svc_link` | `bandwidth`, `performance` (skr/eskr/consumo), **`path_restoration`** (`AUTOMATIC` o `MANUAL`, `hold_off_time` in ms), `svc_link_status` |

Le **notifiche** sono la parte reattiva della gestione delle risorse:
- `sdqkdn_new`, `sdqkdn_interface_new`, `sdqkdn_phys_link_new`, `sdqkdn_svc_link_new`: un nuovo elemento è comparso;
- `..._perf_update`: le prestazioni sono cambiate;
- `..._overloaded`: il consumo di chiave supera la generazione;
- `alarm` ed `event` con `severity`, per nodo, interfaccia e link.

Serve a §5.2 (stato della rete e inventario delle risorse) e §5.5 (notifiche). È il modulo da cui si vede **quanta chiave è disponibile** su ogni link:

```math
\text{eskr} \approx \text{skr} - \text{expected\_consumption}
```

(lettura intuitiva dei tre campi, non una formula dello standard). È il dato su cui l'orchestrator decide se un cammino regge una nuova applicazione.

## 3. `etsi-qkd-sdn-connectivity.yang`: provisioning dei servizi

Prefisso `qkdnw-conn`. È il modulo **operativo**: gestisce le applicazioni che consumano chiave e il cammino su cui vengono servite.

```
connectivity_service/qkd_service/qkd_app* [app_id]
├── (dati app da QKD 015: basic, details, time, statistics, qos)
└── app_mapped_svc_link
    ├── svc_link_and_qkdn_ids   (svc link + estremi locale/remoto)
    ├── path_constraints        (include_nodes / exclude_nodes)
    ├── candidate_paths         (num + lista di path = sequenze di phys_link)
    └── deploy_path             (il path scelto)
```

Le **RPC** sono esattamente i passi del workflow di provisioning (§5.4 e §6.2):

1. `app-registration`: registra l'applicazione nel dominio;
2. `create-service-link-id-for-app`: crea il service link tra il nodo locale e quello remoto;
3. `create-candidate-paths-per-service-link-for-app`: con QoS e vincoli include/exclude, il controller calcola i cammini candidati;
4. `update-candidate-paths-...`: li ricalcola, ad esempio con QoS diversa;
5. `deploy-a-candidate-path-...`: l'orchestrator **sceglie** il `path_id` e il controller lo attiva;
6. `delete-deployed-path-...`: rilascia il cammino.

Notifiche: `sdqkdn_application_new`, `..._deployed`, `..._qos_update`, `..._disconnected`, più `alarm` ed `event`.

La divisione dei ruoli è il punto chiave: **il controller calcola i candidati nel proprio dominio, l'orchestrator decide**. In uno scenario multi-dominio l'orchestrator ripete i passi 2–5 su ogni dominio, collegandoli tramite i nodi di bordo, come nel caso SK Telecom di Sim et al.

---

## Riassunto per una slide

| Modulo | Domanda a cui risponde | Sezione dello standard | Tipo di dati |
|---|---|---|---|
| topology | *Com'è fatta la rete?* | §5.1 | struttura (grafo) |
| inventory | *Quante risorse ci sono e in che stato sono?* | §5.2, §5.5 | stato, prestazioni, configurazione, notifiche |
| connectivity | *Come servo un'applicazione end-to-end?* | §5.3, §5.4, §6.2 | applicazioni, RPC di path computation e deploy |

Nota di sicurezza: in nessun modulo compare materiale di chiave, solo **rate** di chiave e l'indirizzo del KME. Le chiavi restano fuori dal piano di orchestrazione (§7).
