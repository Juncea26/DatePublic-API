import httpx
from typing import Dict, Any
from .base_node import BaseNode

class CkanMefValidationNode(BaseNode):
    CKAN_URL = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1/datastore_search"
    RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

    def __init__(self):
        super().__init__("CkanMefValidationNode")

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        cui = state["cui"]
        client: httpx.AsyncClient = state["http_client"]

        # Filtro indexado exacto (responde en < 1.5s desde servidor backend)
        params = {
            "resource_id": self.RESOURCE_ID,
            "filters": f'{{"CODIGO_UNICO":"{cui}"}}',
            "limit": 1
        }
        try:
            resp = await client.get(self.CKAN_URL, params=params, timeout=20.0)
            data = resp.json()
            records = data.get("result", {}).get("records", [])
            state["ckan_validado"] = len(records) > 0
            state["datos_ckan"] = records[0] if records else None
        except Exception as e:
            state["ckan_error"] = str(e)
            state["ckan_validado"] = False
        return state


class SsiMefScraperNode(BaseNode):
    def __init__(self):
        super().__init__("SsiMefScraperNode")

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        cui = state["cui"]
        client: httpx.AsyncClient = state["http_client"]

        # El SSI tiene endpoints internos que devuelven datos financieros, SEACE e INFOBRAS
        headers = {
            "Origin": "https://ofi5.mef.gob.pe",
            "Referer": f"https://ofi5.mef.gob.pe/ssi/?codigo={cui}",
            "X-Requested-With": "XMLHttpRequest"
        }
        try:
            # Ejemplo de consumo directo al backend del SSI sin pasar por CORS de navegador
            resp = await client.post(
                "https://ofi5.mef.gob.pe/inviertews/Dashboard/traeDetInvSSI",
                data={"id": cui, "tipo": "CUI"},
                headers=headers,
                timeout=15.0
            )
            if resp.status_code == 200:
                ssi_json = resp.json()
                state["ssi_data"] = ssi_json
                # Si el SSI ya trae el proyecto, confirmamos que el CUI existe oficialmente
                if ssi_json:
                    state["cui_existe_oficialmente"] = True
        except Exception as e:
            state["ssi_error"] = str(e)
        return state
