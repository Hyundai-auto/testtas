import os
import asyncio
import logging
import random
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from playwright.async_api import async_playwright
from typing import Optional, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

concurrency_lock = asyncio.Lock()

# ============================================================
# CONFIGURAÇÃO PUSHINPAY
# ============================================================
PUSHINPAY_URL = "https://app.pushinpay.com.br/service/pay/A1D84A4C-312D-4A77-A804-4134784C458D"

class PixRequest(BaseModel):
    payer_name: Optional[str] = None
    payer_cpf: Optional[str] = None
    payer_phone: Optional[str] = None
    subtotal: Optional[Any] = None

class BrowserManager:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    async def start(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox', 
                    '--disable-dev-shm-usage', 
                    '--single-process',
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            # Contexto com User Agent realista para evitar bloqueios
            self.context = await self.browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            )
            logger.info("Browser iniciado")
        except Exception as e:
            logger.error(f"Erro start: {e}")

    async def get_new_page(self):
        page = await self.context.new_page()
        # Não bloqueamos CSS nem JS para garantir que a página carregue 100% e o botão funcione
        await page.goto(PUSHINPAY_URL, wait_until='networkidle', timeout=60000)
        return page

browser_manager = BrowserManager()

@app.on_event("startup")
async def startup_event():
    await browser_manager.start()

async def automate_pushinpay(data: PixRequest):
    async with concurrency_lock:
        page = await browser_manager.get_new_page()
        try:
            # 1. Pequeno delay para simular humano
            await asyncio.sleep(random.uniform(1.5, 3.0))

            # 2. Clicar no checkbox de termos (usando seletor mais específico se possível)
            # Na PushinPay o checkbox costuma ser um input ou uma label próxima
            checkbox = await page.query_selector('input[type="checkbox"]')
            if checkbox:
                await checkbox.click()
                logger.info("Checkbox de termos clicado")
            else:
                # Fallback: tenta clicar na label que contém o texto "Confirmo"
                await page.evaluate("() => { const l = Array.from(document.querySelectorAll('label')).find(x => x.innerText.includes('Confirmo')); if(l) l.click(); }")

            await asyncio.sleep(0.5)

            # 3. Clicar no botão de Confirmar Pagamento
            # Procuramos por um botão que contenha "Confirmar" ou "Pagamento"
            confirm_btn = await page.query_selector('button:has-text("Confirmar"), button:has-text("PAGAMENTO")')
            if confirm_btn:
                await confirm_btn.click()
                logger.info("Botão de confirmação clicado")
            else:
                # Fallback via JS
                await page.evaluate("() => { const b = Array.from(document.querySelectorAll('button')).find(x => x.innerText.toUpperCase().includes('CONFIRMAR') || x.innerText.toUpperCase().includes('PAGAMENTO')); if(b) b.click(); }")

            # 4. Aguardar o código PIX aparecer (ele aparece na mesma página ou redireciona)
            logger.info("Aguardando código PIX...")
            
            pix_code = None
            # Tentamos encontrar o código por até 30 segundos
            for i in range(30):
                pix_code = await page.evaluate("""() => {
                    // 1. Procura em inputs/textareas (comum para copia e cola)
                    const fields = Array.from(document.querySelectorAll('input, textarea, p, span, div'));
                    const code = fields.find(f => {
                        const val = f.value || f.innerText || "";
                        return val.trim().startsWith('000201') && val.length > 50;
                    });
                    
                    if(code) return code.value || code.innerText;
                    return null;
                }""")
                
                if pix_code:
                    logger.info(f"PIX extraído na tentativa {i+1}")
                    return pix_code.strip(), None
                
                await asyncio.sleep(1)
            
            # Se falhar, tira um log da página para debug interno (Markdown do conteúdo)
            content = await page.content()
            logger.error(f"Falha ao encontrar PIX. Conteúdo parcial: {content[:500]}")
            return None, "A página da PushinPay não gerou o código a tempo. Tente novamente."

        except Exception as e:
            logger.error(f"Erro na automação: {e}")
            return None, "Erro de conexão com o processador de pagamentos."
        finally:
            try: await page.close()
            except: pass

@app.post('/proxy/pix')
async def generate_pix(request: PixRequest):
    logger.info(f"Iniciando processo para: {request.payer_name}")
    pix_code, error = await automate_pushinpay(request)
    if pix_code:
        return JSONResponse({'success': True, 'pixCode': pix_code})
    return JSONResponse({'success': False, 'error': error}, status_code=400)

BASE_DIR = Path(__file__).parent
@app.get("/")
async def read_index():
    return FileResponse(BASE_DIR / "static" / "index.html")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
