# sweep_research

Pacchetto isolato per la ricerca S0 sugli sweep XAUUSD. Esecuzione:

```bash
/home/ubuntu/venv-sweep/bin/python -m sweep_research run \
  --stage s0 --config sweep_research/config/xauusd.yaml
```

Event study S1 sul file `events.parquet` prodotto da S0:

```bash
/home/ubuntu/venv-sweep/bin/python -m sweep_research run \
  --stage s1 --config sweep_research/config/xauusd.yaml
```

Il codice non importa moduli del repository principale e legge il layout
Parquet QLAB direttamente.
