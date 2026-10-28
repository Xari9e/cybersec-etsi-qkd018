# Step 2: ETSI GS QKD 015, cosa vede il controller

**Obiettivo:** capire il modello del nodo SD-QKD (SDN agent, interfacce QKD, link fisici e virtuali, applicazioni) che il controller gestisce. QKD 018 si appoggia direttamente su questo modello.

| Fonte | Cosa leggere | Link |
|---|---|---|
| ETSI GS QKD 015 V2.1.1 (2022-04), *Control Interface for Software Defined Networks* | §4 (architettura SD-QKD), §5 (modello informativo) | [PDF](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/015/02.01.01_60/gs_QKD015v020101p.pdf) |
| Modello YANG di QKD 015 (ETSI Forge) | `etsi-qkd-sdn-node.yang`, da sfogliare | [repo](https://forge.etsi.org/rep/qkd/gs015-ctrl-int-sdn) |

**Domande guida:** che differenza c'è tra un link `PHYS` e un link `VIRT`? Quali parametri (SKR, QBER…) espone un nodo?
