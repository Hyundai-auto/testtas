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

# Lock para evitar sobrecarga de memória no Render (processa um por vez)
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
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox', 
                    '--disable-dev-shm-usage', 
                    '--single-process'
                ]
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
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "font"] else route.continue_())
                await page.goto(PUSHINPAY_URL, wait_until='domcontentloaded', timeout=60000)
                self._warm_page = page
                logger.info("Pool: Página pronta")
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
            # 1. Tenta preencher dados se os campos estiverem visíveis
            # Usamos um script JS para detectar e preencher Nome, CPF e Telefone se existirem
            await page.evaluate(f"""(d) => {{
                const inputs = Array.from(document.querySelectorAll('input'));
                
                // Busca campos por placeholder ou label aproximada
                const nameInput = inputs.find(i => i.placeholder?.toLowerCase().includes('nome') || i.name?.toLowerCase().includes('name'));
                const cpfInput = inputs.find(i => i.placeholder?.toLowerCase().includes('cpf') || i.name?.toLowerCase().includes('cpf'));
                const phoneInput = inputs.find(i => i.placeholder?.toLowerCase().includes('tel') || i.placeholder?.toLowerCase().includes('cel') || i.name?.toLowerCase().includes('phone'));

                if(nameInput && d.payer_name) {{ nameInput.value = d.payer_name; nameInput.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
                if(cpfInput && d.payer_cpf) {{ cpfInput.value = d.payer_cpf; cpfInput.dispatchEvent(new Event('input', {{ bubbles: true }})); }}
                if(phoneInput && d.payer_phone) {{ phoneInput.value = d.payer_phone; phoneInput.dispatchEvent(new Event('input', {{ bubbles: true }})); }}

                // Clica no checkbox de termos
                const cb = document.querySelector('input[type="checkbox"]');
                if(cb) cb.click();

                // Clica no botão de confirmar
                const btns = Array.from(document.querySelectorAll('button'));
                const confirmBtn = btns.find(b => b.innerText.toUpperCase().includes('CONFIRMAR') || b.innerText.toUpperCase().includes('PAGAMENTO'));
                if(confirmBtn) confirmBtn.click();
            }}""", {"payer_name": data.payer_name, "payer_cpf": data.payer_cpf, "payer_phone": data.payer_phone})

            # 2. Aguarda a mudança de URL ou carregamento do QR Code
            # Aumentamos para 35 segundos para ser extremamente resiliente no Render
            logger.info("Aguardando geração do PIX...")
            await page.wait_for_load_state('networkidle', timeout=35000)
            
            final_url = page.url
            if final_url != PUSHINPAY_URL:
                logger.info(f"Sucesso! Redirecionando para: {final_url}")
                return final_url, None
            else:
                # Se a URL não mudou, talvez o QR Code apareceu na mesma página
                # Verificamos se há algum elemento de PIX ou QR Code
                has_pix = await page.evaluate("() => document.body.innerText.includes('PIX') || !!document.querySelector('canvas') || !!document.querySelector('img[src*=\"qr\"]')")
                if has_pix:
                    return final_url, None
                
                return None, "A página não processou o pagamento. Verifique os dados."

        except Exception as e:
            logger.error(f"Erro na automação: {e}")
            return None, "O sistema demorou a responder. Tente novamente em instantes."
        finally:
            try: await page.close()
            except: pass

@app.post('/proxy/pix')
async def generate_pix(request: PixRequest):
    logger.info(f"Iniciando geração PIX para: {request.payer_name}")
    pix_url, error = await automate_pushinpay(request)
    if pix_url:
        return JSONResponse({'success': True, 'pixUrl': pix_url})
    return JSONResponse({'success': False, 'error': error}, status_code=400)

@app.get('/health')
async def health():
    return {"status": "ok"}

BASE_DIR = Path(__file__).parent
@app.get("/")
async def read_index():
    return FileResponse(BASE_DIR / "static" / "index.html")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
