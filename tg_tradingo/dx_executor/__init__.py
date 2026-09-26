"""DX Executor: esegue i payload JSON del bridge TG TradinGo su un conto DXtrade.

Sostituisce l'EA MT5 per un conto che espone solo l'API DXtrade (es. prop
Velotrade): legge lo stesso ``signal_ch_*.json`` scritto dal bridge, senza
alcuna modifica al bridge o al contratto JSON.
"""

__version__ = "0.1.0"
