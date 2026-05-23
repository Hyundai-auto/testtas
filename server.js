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

    let browser;
    try {
        browser = await puppeteer.launch({
            headless: "new",
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });
        const page = await browser.newPage();
        
        // Configurar User Agent para evitar bloqueios
        await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

        console.log(`Navegando para: ${checkoutUrl}`);
        await page.goto(checkoutUrl, { waitUntil: 'networkidle2' });

        // Esperar e preencher campos se existirem (PushinPay às vezes pede dados antes)
        // Mas no link fornecido, parece ser um checkout direto de produto.
        
        // 1. Aceitar termos se necessário
        try {
            const checkbox = await page.$('input[type="checkbox"]');
            if (checkbox) {
                await page.click('input[type="checkbox"]');
                console.log('Checkbox de termos clicado.');
            }
        } catch (e) {}

        // 2. Clicar no botão de confirmar pagamento
        console.log('Clicando em Confirmar Pagamento...');
        await page.evaluate(() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            const payBtn = buttons.find(b => b.textContent.includes('Confirmar Pagamento'));
            if (payBtn) payBtn.click();
        });

        // 3. Esperar a geração do PIX e extrair o código
        console.log('Aguardando código PIX...');
        
        // Aumentar o timeout pois a geração pode demorar
        await page.waitForFunction(() => {
            // Procura por textos comuns de código PIX ou botões de copiar
            const bodyText = document.body.innerText;
            return bodyText.includes('000201') || document.querySelector('.pix-code') || document.querySelector('[copy]');
        }, { timeout: 30000 });

        const pixData = await page.evaluate(() => {
            // Tenta encontrar o código PIX "Copia e Cola"
            // Geralmente começa com 000201
            const bodyText = document.body.innerText;
            const pixMatch = bodyText.match(/000201[a-zA-Z0-9]+/);
            
            // Tenta encontrar o QR Code (imagem ou canvas)
            const qrImg = document.querySelector('img[src*="qr"], canvas');
            const qrSource = qrImg ? (qrImg.src || qrImg.toDataURL()) : null;

            return {
                pix_code: pixMatch ? pixMatch[0] : null,
                qr_code: qrSource
            };
        });

        if (pixData.pix_code) {
            console.log('PIX gerado com sucesso!');
            res.json(pixData);
        } else {
            console.log('Falha ao extrair código PIX.');
            res.status(400).json({ error: 'Não foi possível extrair o código PIX.' });
        }

    } catch (error) {
        console.error('Erro no processamento:', error.message);
        res.status(500).json({ error: 'Erro ao gerar o pagamento. Tente novamente.' });
    } finally {
        if (browser) await browser.close();
    }
});

app.listen(PORT, () => {
    console.log(`Servidor rodando na porta ${PORT}`);
});
