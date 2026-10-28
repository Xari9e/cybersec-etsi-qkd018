# Step 3: ETSI GS QKD 018, lo standard completo

**Obiettivo:** il cuore della presentazione. È l'interfaccia orchestrator ↔ controller per topologia, inventory, provisioning e notifiche multi-dominio.

| Fonte | Cosa leggere | Link |
|---|---|---|
| ETSI GS QKD 018 V1.1.1 (2022-04), *Orchestration Interface for Software Defined Networks* | Tutto, in particolare §4 (use case), §5 (interfaccia), §6.2 (multi-domain provisioning), §7 (sicurezza), Annex A (YANG) | [PDF](https://www.etsi.org/deliver/etsi_gs/QKD/001_099/018/01.01.01_60/gs_qkd018v010101p.pdf) |
| Modelli YANG di QKD 018 (ETSI Forge) | `etsi-qkd-sdn-topology`, `etsi-qkd-sdn-inventory`, `etsi-qkd-sdn-connectivity` | [repo](https://forge.etsi.org/rep/qkd/gs018-orch-int-sdn) |

**Suggerimento:** per vedere l'albero di un modello usa `pip install pyang` e poi `pyang -f tree etsi-qkd-sdn-topology.yang`. È ottimo da mettere in una slide.

**Mattoni normativi** (servono per capire l'Annex A e il §8):
- [RFC 7950](https://www.rfc-editor.org/rfc/rfc7950.html) (YANG 1.1): §4 e §7
- [RFC 8040](https://www.rfc-editor.org/rfc/rfc8040.html) (RESTCONF): introduzione ed esempi
- [RFC 6241](https://www.rfc-editor.org/rfc/rfc6241.html) (NETCONF)
- [RFC 8345](https://www.rfc-editor.org/rfc/rfc8345.html) (YANG per topologie di rete) e [RFC 6991](https://www.rfc-editor.org/rfc/rfc6991.html) (tipi YANG comuni)
