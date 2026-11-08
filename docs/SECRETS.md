# Gestione dei segreti — TG TradinGo

L'archivio segreti usa **Windows DPAPI** (scope utente) per cifrare API key e
password in `C:\TG_TradinGo\secrets\store.dpapi`.  
Il file è leggibile solo dall'utente Windows che lo ha creato; non può essere
aperto su un'altra macchina anche copiando i file di profilo.

## Dove sono i segreti

| Percorso | Contenuto |
|----------|-----------|
| `C:\TG_TradinGo\secrets\store.dpapi` | Archivio cifrato (DPAPI) |
| `C:\TG_TradinGo\secrets\store.meta.json` | Nomi chiave in chiaro (mai valori) |

Override del percorso: variabile d'ambiente `TRADINGO_SECRETS_FILE`.

## Chiavi richieste

| Chiave | Fonte originale |
|--------|----------------|
| `TELEGRAM_API_ID` | `tradingo_config.json` → `telegram.api_id` |
| `TELEGRAM_API_HASH` | `tradingo_config.json` → `telegram.api_hash` |
| `ANTHROPIC_API_KEY` | variabile d'ambiente Machine |
| `ANTHROPIC_API_KEY_BACKUP` | variabile d'ambiente Machine |
| `GEMINI_API_KEY` | variabile d'ambiente Machine |
| `TELEGRAM_AGENT_BOT_TOKEN` | variabile d'ambiente Machine |
| `WEBAPP_SECRET_KEY` | `webapp_config.json` → `secret_key` |
| `DXTRADE_PASSWORD` | variabile d'ambiente Machine |

## Aggiungere o ruotare un segreto

```powershell
cd C:\TG_TradinGo
python C:\TG_TradinGo\secrets_store.py set NOME_CHIAVE
# oppure da pipeline:
echo "nuovo-valore" | python C:\TG_TradinGo\secrets_store.py set NOME_CHIAVE
```

Verificare che tutte le chiavi richieste siano presenti:

```powershell
python C:\TG_TradinGo\secrets_store.py export-check
```

## Visualizzare le chiavi presenti (senza valori)

```powershell
python C:\TG_TradinGo\secrets_store.py list
```

## Leggere un valore (con conferma esplicita)

```powershell
python C:\TG_TradinGo\secrets_store.py get NOME_CHIAVE --reveal
```

## Eliminare una chiave

```powershell
python C:\TG_TradinGo\secrets_store.py delete NOME_CHIAVE
```

## Importare da variabili d'ambiente esistenti

```powershell
python C:\TG_TradinGo\secrets_store.py import-env ANTHROPIC_API_KEY GEMINI_API_KEY
```

## Migrazione iniziale

Usare lo script PowerShell fornito:

```powershell
# Anteprima senza modifiche
powershell -ExecutionPolicy Bypass -File scripts\migrate_secrets_to_store.ps1 -WhatIf

# Esecuzione effettiva
powershell -ExecutionPolicy Bypass -File scripts\migrate_secrets_to_store.ps1
```

Lo script:
1. Fa il backup di `tradingo_config.json` e `webapp_config.json`
2. Legge le chiavi da JSON e variabili d'ambiente Machine
3. Le scrive nell'archivio DPAPI
4. Sostituisce i valori nei JSON con il sentinel `@secrets`
5. Applica i permessi ACL (`icacls`) sulla cartella `secrets/`
6. Rimuove le variabili d'ambiente Machine **solo** dopo che `export-check` passa

## Uso in script/CLI esterni (with_secrets.ps1)

Per eseguire comandi che richiedono le API key come variabili d'ambiente:

```powershell
# Esempio: Claude Code
powershell -ExecutionPolicy Bypass -File scripts\with_secrets.ps1 `
    claude --model claude-sonnet-5 "analizza il bridge"

# Esempio: Gemini CLI
powershell -ExecutionPolicy Bypass -File scripts\with_secrets.ps1 `
    gemini "analizza il bridge"
```

## Fallback e compatibilità

Se una chiave non è nell'archivio, `store.get()` legge `os.environ[NOME]` e
logga un WARNING. Questo mantiene la compatibilità durante la migrazione e
permette l'esecuzione dei test su Linux (dove DPAPI non è disponibile).

## Sentinel `@secrets` nei file di configurazione

I file `tradingo_config.example.json` e `webapp_config.example.json` usano il
valore `"@secrets"` per indicare che il campo deve essere risolto dall'archivio:

```json
"telegram": {
  "api_id": "@secrets",
  "api_hash": "@secrets"
}
```

Il bridge rileva il sentinel e chiama `store.require("TELEGRAM_API_ID")`.

## Limitazioni

- DPAPI è disponibile **solo su Windows**: su Linux `is_available()` restituisce
  False e il sistema usa solo le variabili d'ambiente.
- Il file `store.dpapi` è legato all'account Windows corrente; non può essere
  usato da un utente diverso sulla stessa macchina.
- Non fare backup del file `store.dpapi` su cloud storage non cifrato.
