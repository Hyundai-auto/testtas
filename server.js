const express = require('express');
const puppeteer = require('puppeteer');
const cors = require('cors');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

app.post('/api/generate-pix', async (req, res) => {
    const { name, email, cpf } = req.body;
    const checkoutUrl = 'https://app.pushinpay.com.br/service/pay/A1D84A4C-312D-4A77-A804-4134784C458D';

    console.log(`Iniciando geração de PIX para: ${email}`);

    let browser;
    try {
        // Configurações específicas para rodar no Render/Heroku
        browser = await puppeteer.launch({
            headless: "new",
            executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || null,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--single-process',
                '--disable-gpu'
            ]
        });

        const page = await browser.newPage();
        
        // Bloquear recursos desnecessários para carregar mais rápido
        await page.setRequestInterception(true);
        page.on('request', (req) => {
            if(['image', 'stylesheet', 'font'].includes(req.resourceType())){
                req.abort();
            } else {
                req.continue();
            }
        });

        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        console.log(`Navegando para o checkout...`);
        await page.goto(checkoutUrl, { waitUntil: 'networkidle0', timeout: 60000 });

        // Tentar clicar no checkbox de termos
        try {
            await page.waitForSelector('input[type="checkbox"]', { timeout: 5000 });
            await page.click('input[type="checkbox"]');
        } catch (e) {
            console.log("Checkbox não encontrado ou já marcado.");
        }

        // Clicar no botão de pagamento
        console.log("Clicando em confirmar pagamento...");
        await page.evaluate(() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const payBtn = buttons.find(b => b.innerText.includes('CONFIRMAR') || b.innerText.includes('PAGAMENTO'));
            if (payBtn) payBtn.click();
        });

        // Aguardar o código PIX aparecer
        console.log("Aguardando código PIX...");
        await page.waitForFunction(() => {
            return document.body.innerText.includes('000201');
        }, { timeout: 45000 });

        const pixCode = await page.evaluate(() => {
            const match = document.body.innerText.match(/000201[a-zA-Z0-9]+/);
            return match ? match[0] : null;
        });

        if (pixCode) {
            console.log("PIX extraído com sucesso!");
            res.json({ pix_code: pixCode });
        } else {
            res.status(400).json({ error: 'Não foi possível extrair o código PIX.' });
        }

    } catch (error) {
        console.error('Erro detalhado:', error);
        res.status(500).json({ error: 'Erro ao processar pagamento: ' + error.message });
    } finally {
        if (browser) await browser.close();
    }
});

app.listen(PORT, () => {
    console.log(`Servidor ativo na porta ${PORT}`);
});
