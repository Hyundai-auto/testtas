import os
import asyncio
import logging
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
        self._warm_page = None
        self._lock = asyncio.Lock()

    async def start(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--single-process']
            )
            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            )
            logger.info("Browser iniciado")
            asyncio.create_task(self.pre_warm())
        except Exception as e:
            logger.error(f"Erro start: {e}")

    async def pre_warm(self):
        async with self._lock:
            if self._warm_page: return
            try:
                page = await self.context.new_page()
                await page.goto(PUSHINPAY_URL, wait_until='domcontentloaded', timeout=60000)
                self._warm_page = page
                logger.info("Página pré-aquecida pronta")
            except Exception as e:
                logger.error(f"Erro pre_warm: {e}")

    async def get_page(self):
        async with self._lock:
            if self._warm_page:
                page = self._warm_page
                self._warm_page = None
                asyncio.create_task(self.pre_warm())
                return page
        page = await self.context.new_page()
        await page.goto(PUSHINPAY_URL, wait_until='domcontentloaded', timeout=60000)
        return page

browser_manager = BrowserManager()

@app.on_event("startup")
async def startup_event():
    await browser_manager.start()

async def automate_pushinpay(data: PixRequest):
    async with concurrency_lock:
        page = await browser_manager.get_page()
        try:
            # 1. Clicar em Confirmar
            await page.evaluate("""() => {
                const cb = document.querySelector('input[type="checkbox"]');
                if(cb) cb.click();
                const btns = Array.from(document.querySelectorAll('button'));
                const confirmBtn = btns.find(b => b.innerText.toUpperCase().includes('CONFIRMAR') || b.innerText.toUpperCase().includes('PAGAMENTO'));
                if(confirmBtn) confirmBtn.click();
            }""")
            
            # 2. Aguardar a geração do PIX
            # Procuramos pelo código copia e cola ou QR Code
            logger.info("Aguardando extração do PIX...")
            
            # Tenta encontrar o código copia e cola na página final
            # Na PushinPay, geralmente há um input ou elemento com o código longo
            pix_data = None
            for _ in range(30): # Tenta por 30 segundos
                pix_data = await page.evaluate("""() => {
                    // Tenta encontrar o input que contém o código PIX (geralmente longo e começa com 000201)
                    const inputs = Array.from(document.querySelectorAll('input, textarea'));
                    const pixInput = inputs.find(i => i.value && i.value.startsWith('000201'));
                    if(pixInput) return { code: pixInput.value };
                    
                    // Tenta encontrar por texto em algum elemento
                    const allElements = Array.from(document.querySelectorAll('p, span, div'));
                    const pixText = allElements.find(e => e.innerText && e.innerText.startsWith('000201'));
                    if(pixText) return { code: pixText.innerText.trim() };

                    // Tenta encontrar o QR Code (imagem ou canvas)
                    const qrImg = document.querySelector('img[src*="qr"], canvas');
                    if(qrImg) {
                        // Se achou o QR mas não o código, tenta esperar mais um pouco
                        return { waiting: true };
                    }
                    return null;
                }""")
                
                if pix_data and pix_data.get('code'):
                    logger.info("Código PIX extraído com sucesso!")
                    return pix_data['code'], None
                
                await asyncio.sleep(1)
            
            return None, "Não foi possível extrair o código PIX da página."

        except Exception as e:
            logger.error(f"Erro extração: {e}")
            return None, str(e)
        finally:
            try: await page.close()
            except: pass

@app.post('/proxy/pix')
async def generate_pix(request: PixRequest):
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
