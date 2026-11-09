from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(title="DatePublic API")

# Permite llamadas desde cualquier frontend sin bloqueo CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CKAN_URL = "https://api.datosabiertos.mef.gob.pe/DatosAbiertos/v1/datastore_search"
RESOURCE_ID = "f9cc4ba0-931a-4b70-86c9-eacbd8c68596"

@app.get("/")
def inicio():
    return {"estado": "API operativa", "servicio": "DatePublic"}

@app.get("/api/v1/obra/{cui}")
async def consultar_cui(cui: str):
    params = {
        "resource_id": RESOURCE_ID,
        "filters": f'{{"CODIGO_UNICO":"{cui}"}}',
        "limit": 1
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    async with httpx.AsyncClient(headers=headers, verify=False, timeout=20.0) as client:
        try:
            resp = await client.get(CKAN_URL, params=params)
            data = resp.json()
            records = data.get("result", {}).get("records", [])
            if not records:
                raise HTTPException(status_code=404, detail="CUI no encontrado en el Banco de Inversiones")
            return {
                "cui": cui,
                "encontrado": True,
                "datos": records[0]
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
