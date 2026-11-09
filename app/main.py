from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.graphs.veeduria_graph import VeeduriaScraperGraph

app = FastAPI(title="Veeduría Perú - ScrapeGraph API")

# AQUÍ ELIMINAS EL PROBLEMA DE CORS PARA TU FRONTEND WEB O ANDROID
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite que tu app React o móvil consuma tu API sin bloqueos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/veeduria/obra/{cui}")
async def auditar_obra_por_cui(cui: str):
    if not cui.isdigit() or len(cui) < 6 or len(cui) > 8:
        raise HTTPException(status_code=400, detail="Formato de CUI inválido.")

    graph = VeeduriaScraperGraph(cui=cui)
    resultado = await graph.run()

    if not resultado["cui_existe_oficialmente"]:
        raise HTTPException(
            status_code=404,
            detail=f"El CUI {cui} no existe en el Banco de Inversiones del MEF ni en el SSI."
        )

    return resultado
