# Supervisione dei servizi e problemi di connessione

Complementare a [`REBOOT_AUTOSTART.md`](REBOOT_AUTOSTART.md), che copre
l'avvio dopo un riavvio. Questo documento copre il *durante*: cosa può
rompersi mentre il sistema gira e chi se ne accorge.

## 1. Non tutti i "problemi di connessione" sono uguali

Distinguerli è la parte che fa risparmiare tempo: solo la seconda categoria si
risolve riavviando qualcosa.

### A. Semantica di sessione e protocollo — si risolvono nel client

Riavviare il processo non serve, e su un sistema che manda ordini può fare
danni. Vanno gestiti dentro il client:

| Sintomo | Causa | Rimedio nel client |
|---------|-------|--------------------|
| `401` a metà giornata | token scaduto per inattività (30 min su DXtrade) | `POST /ping` periodico + re-login trasparente sul 401 |
| `409 SERVICE_ERROR` su ogni chiamata dopo un login riuscito | collisione di sessione: la stessa utenza è aperta altrove (web platform, app mobile, un secondo processo) | **una sola sessione per account**: lock di istanza singola, logout dalle altre sessioni |
| `403` codice 99 su modifica/cancellazione | richiesta condizionale senza `If-Match` | GET dell'ordine, leggi l'`ETag`, poi PUT/DELETE |
| `412 Precondition Failed` | `ETag` vecchio, l'ordine è cambiato nel frattempo | ri-fetch e riprova |
| `409` codice 100 in apertura | `orderCode` già usato | è una protezione, non un errore: l'ordine originale era già passato, va riconciliato non rimandato |
| `429` | oltre il rate limit | backoff esponenziale, e leggere le metriche dal WebSocket invece che con polling REST |
| WebSocket che si chiude con codice 1013 | il client non consuma i messaggi abbastanza in fretta | ridurre le sottoscrizioni o processare più velocemente |

Il client in `dxtrade_client.py` implementa già ping/keepalive, re-login sul
401 e backoff su 429 e 5xx.

### B. Guasti di processo o di host — questi sì, servono un watchdog

Processo morto, processo vivo ma bloccato, host riavviato, rete giù, DNS o TLS
che falliscono. È il territorio di `service_watchdog.py`.

## 2. Il buco nella supervisione attuale

Sulla Contabo il bridge è supervisionato da `TG_TradinGo_BridgeAtLogon`, che
gira al logon e si ripete **ogni minuto**, con `MultipleInstancesPolicy =
IgnoreNew`. Il launcher `run_bridge_task.cmd` conta i `python.exe` la cui riga
di comando contiene `tradingo_bridge.py` ed esce se ne trova almeno uno.

Funziona per il processo morto. **Non funziona per il processo bloccato**: un
bridge con l'event loop fermo, una sessione Telethon caduta o una share di rete
appesa resta un `python.exe` vivo, quindi il Task Scheduler lo considera sano e
non lo riavvia mai. L'ingrediente per accorgersene esiste già — il bridge
scrive `tradingo_heartbeat.json` ogni 30 secondi, e l'EA lo controlla con
`InpHeartbeatMaxAgeSec` — ma nessuno lo usa lato VPS.

## 3. `service_watchdog.py`

Controlla **processo vivo e heartbeat fresco**, e riavvia quando serve.

```bash
python service_watchdog.py --config watchdog_config.json --once
python service_watchdog.py --config watchdog_config.json --dry-run   # non tocca nulla
python service_watchdog.py --config watchdog_config.json --interval 60
```

Configurazione: [`watchdog_config.example.json`](../watchdog_config.example.json).

| Esito | Quando | Cosa fa |
|-------|--------|---------|
| `OK` | processo vivo e heartbeat entro `max_age_sec` | niente |
| `WAIT_GRACE` | riavviato da meno di `grace_sec` | aspetta, non valuta l'heartbeat |
| `RESTART_PROCESS_DEAD` | nessun processo corrisponde | avvia `start_command` |
| `RESTART_HEARTBEAT_STALE` | processo vivo, heartbeat fermo o assente | termina per PID, poi riavvia |
| `ALERT_FLAPPING` | oltre `max_restarts_per_hour` riavvii nell'ultima ora | **non** riavvia, alza un allarme |
| `ALERT_NO_START_COMMAND` | processo assente e nessun comando di avvio | solo allarme |

Tre scelte che contano più del riavvio in sé:

* **L'età si legge dal timestamp dentro il file, non dall'mtime.** Su una
  share di rete l'mtime può essere aggiornato da una copia mentre il servizio
  è morto.
* **I processi si terminano per PID**, mai con un match sul nome: un
  `taskkill` per nome spegnerebbe anche gli altri Python della macchina.
* **Anti-flapping.** Su un guasto persistente (credenziali scadute, disco
  pieno) un watchdog ingenuo entra in un ciclo di riavvii che peggiora le
  cose. Dopo N tentativi in un'ora smette e chiede aiuto.

Il pattern di installazione è lo stesso già in uso: un task che si ripete ogni
minuto e invoca il watchdog con `--once`. Il watchdog non resta residente,
quindi non c'è un secondo processo da supervisionare.

### Una trappola scoperta provandolo

La prima versione riportava "processo attivo" per qualsiasi pattern. Su Windows
la sonda elenca le command line di tutti i processi, e la sonda stessa aveva il
pattern nella propria riga di comando: **trovava se stessa**. Con quel bug il
watchdog non avrebbe mai riavviato un servizio morto. Ora il pattern viaggia in
una variabile d'ambiente (`TG_WATCHDOG_PATTERN`), mai negli argomenti, e c'è un
test di regressione che lo verifica.

## 4. Dove far girare una connessione API di trading

Vincolo che viene prima di tutti gli altri: **una sola sessione per account**.
Due host che si collegano con la stessa utenza producono il `409
SERVICE_ERROR` descritto sopra. Quindi non "Contabo *o* Gamehosting a seconda
del momento": va scelto un host e reso esclusivo.

Stato misurato dei due host:

| | Contabo `100.110.249.72` | Gamehosting `100.74.9.8` |
|---|---|---|
| Accesso amministrativo | SSH porta 2222, script di deploy già pronti | nessun accesso diretto |
| Tailscale | endpoint della rete | raggiungibile ma **tramite relay `ams`**, non in diretta |
| Share SMB `\\100.74.9.8\tradingo` | — | ancora in errore, credenziali mai impostate |
| Automazione esistente | Task Scheduler, autostart, heartbeat, deploy da repo | da costruire da zero |

Il relay Tailscale su Gamehosting non è un dettaglio: significa che NAT o
firewall impediscono il collegamento diretto, quindi il traffico passa da un
nodo intermedio ad Amsterdam. Per una share di file è fastidioso, per una
connessione di trading è una dipendenza in più che non controlliamo.

**Latenza verso Velotrade**, misurata dalla Contabo:

```
dx.velotrade.com -> 18.136.125.157 / 54.251.225.56
                    aws-velotrade-prod-lb...elb.ap-southeast-1.amazonaws.com
TCP connect: 244 ms  244 ms  242 ms  249 ms  248 ms
POST HTTPS completa (con handshake TLS): ~490-530 ms
```

La piattaforma è su **AWS Singapore**. I 244 ms sono geografia, non un problema
della Contabo: da qualsiasi VPS europeo il risultato è lo stesso. Due
conseguenze pratiche:

* usare il **WebSocket** e tenere viva la sessione, invece di aprire una
  connessione nuova per ogni chiamata: si paga l'handshake una volta sola;
* se una strategia è sensibile alla qualità dell'esecuzione, la risposta non è
  Contabo contro Gamehosting ma **un VPS a Singapore**, che porterebbe i 244 ms
  a pochi millisecondi.

## 5. Riavviare un connettore di trading non è come riavviare un servizio web

Un riavvio cieco mentre ci sono posizioni aperte e un segnale in coda può
generare ordini doppi. Le due protezioni:

1. **Idempotenza**: `orderCode` deterministico. Un rinvio dello stesso ordine
   viene rifiutato dalla piattaforma con `409` codice 100.
2. **Riconciliazione prima di operare**: alla ripartenza il connettore deve
   leggere posizioni e ordini aperti e ricostruire lo stato **prima** di
   eseguire qualsiasi segnale. È lo stesso principio di
   `InpIgnoreExistingOnInit` sull'EA, che all'attach non riesegue i JSON già
   presenti.
