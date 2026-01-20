/**
 * Bybit Futures Real-time Candlestick Visualizer
 * Uses WebSocket for live updates and Lightweight Charts for rendering
 */

// ===== Helper Functions =====
function getCaseLabel(strategyCase) {
    // Convertir strategy_case numérico a etiqueta legible
    if (strategyCase === 11) return 'C1++';
    return `C${strategyCase || '?'}`;
}

// ===== Configuration =====
let currentSymbol = null; // Will be set from shared_config.json target_pairs

const CONFIG = {
    get symbol() { return currentSymbol; },
    defaultInterval: '1h', // Will be overridden by shared_config.json timeframe
    candleLimit: 1500,
    wsBaseUrl: 'wss://stream.bybit.com/v5/public/linear',
    restBaseUrl: 'https://api.bybit.com',
    reconnectDelay: 3000,
    updateInterval: 1000 // Update display every second
};

// Timeframe mapping for Bybit API (different format than Binance)
const TIMEFRAME_MAP = {
    '1m': '1',
    '5m': '5',
    '15m': '15',
    '1h': '60',
    '4h': '240',
    '1d': 'D'
};

// ===== Global State =====
let chart = null;
let candleSeries = null;
let zigzagLineSeries = null;
let fibonacciLines = [];
let currentInterval = '1h';
let ws = null;
let tickerWs = null;
let markPriceWs = null;
let candleData = [];
let lastPrice = null;
let reconnectTimer = null;
let zigzagPoints = []; // Store ZigZag pivot points

// ===== Manual Fibonacci Drawing =====
let manualFibMode = false;
let manualFibPoints = []; // [{price, time}, {price, time}]
let manualFibLines = [];
let selectedManualFib = null; // For deletion

// ===== Visibility State =====
let showAutoFib = true;
let showZigZag = true; // Toggle for ZigZag visibility

// ===== All Trading Pairs Cache =====
let allTradingPairs = [];
let autocompleteSelectedIndex = -1;
let showTradeLines = true;
let tradeLinesSeries = []; // Store references to trade lines for clearing
let lastTradesData = null; // Store last trades data for redrawing

// ===== RSI Indicator (Etiqueta) =====
let showRSI = true;
let rsiValue = 0;

// ZigZag configuration per timeframe (less bars for smaller timeframes)
function getZigZagConfig() {
    const configs = {
        '1m': { deviation: 0.3, depth: 5, backstep: 2 },
        '5m': { deviation: 0.5, depth: 5, backstep: 2 },
        '15m': { deviation: 1, depth: 5, backstep: 2 },
        '1h': { deviation: 2, depth: 8, backstep: 3 },
        '4h': { deviation: 3, depth: 10, backstep: 3 },
        '1d': { deviation: 5, depth: 10, backstep: 3 }
    };
    return configs[currentInterval] || configs['1h'];
}

// Shared configuration (loaded from shared_config.json)
let sharedConfig = null;

async function loadSharedConfig() {
    try {
        // Add cache-busting timestamp to force fresh load
        const response = await fetch(`/shared_config.json?t=${Date.now()}`);
        if (response.ok) {
            sharedConfig = await response.json();
            window.sharedConfig = sharedConfig; // Make available globally
            console.log('✅ Shared config loaded:', sharedConfig);
        }
    } catch (e) {
        console.log('⚠️ Could not load shared_config.json, using defaults');
    }
}

// Get config value with fallback
function getConfig(section, key, defaultValue) {
    if (sharedConfig && sharedConfig[section] && sharedConfig[section][key] !== undefined) {
        return sharedConfig[section][key];
    }
    return defaultValue;
}

// Fibonacci levels for SHORT entries (retracements up after a drop)
// Niveles predeterminados - se pueden editar y guardar en localStorage
const DEFAULT_FIBONACCI_LEVELS = [
    { level: 0.236, color: 'rgba(255, 235, 59, 0.8)', label: '23.6%', visible: true },
    { level: 0.382, color: 'rgba(255, 152, 0, 0.8)', label: '38.2%', visible: true },
    { level: 0.5, color: 'rgba(156, 39, 176, 0.8)', label: '50%', visible: true },
    { level: 0.55, color: 'rgba(76, 175, 80, 0.9)', label: '55% TP', visible: true },
    { level: 0.60, color: 'rgba(139, 195, 74, 0.9)', label: '60% TP', visible: true },
    { level: 0.618, color: 'rgba(233, 30, 99, 0.9)', label: '61.8% ⭐', visible: true },
    { level: 0.69, color: 'rgba(255, 193, 7, 0.9)', label: '69% 🔶', visible: false },
    { level: 0.75, color: 'rgba(0, 188, 212, 0.9)', label: '75% 🟢', visible: false },
    { level: 0.786, color: 'rgba(244, 67, 54, 0.9)', label: '78.6% ENTRY', visible: true },
    { level: 0.9, color: 'rgba(183, 28, 28, 0.9)', label: '90% ⛔', visible: true }
];

// Cargar niveles desde localStorage o usar predeterminados
let FIBONACCI_LEVELS = loadFibonacciLevels();

function loadFibonacciLevels() {
    try {
        const saved = localStorage.getItem('fibonacciLevels');
        if (saved) {
            return JSON.parse(saved);
        }
    } catch (e) {
        console.log('Error loading Fibonacci levels from localStorage');
    }
    return JSON.parse(JSON.stringify(DEFAULT_FIBONACCI_LEVELS));
}

function saveFibonacciLevels() {
    try {
        localStorage.setItem('fibonacciLevels', JSON.stringify(FIBONACCI_LEVELS));
        console.log('✅ Fibonacci levels saved to localStorage');
    } catch (e) {
        console.log('Error saving Fibonacci levels');
    }
}

function getVisibleFibonacciLevels() {
    return FIBONACCI_LEVELS.filter(f => f.visible !== false);
}

// Connection tracking - track each WebSocket separately
let wsConnections = {
    kline: false,
    ticker: false,
    markPrice: false
};

// ===== DOM Elements =====
const elements = {
    chartContainer: document.getElementById('chartContainer'),
    chartOverlay: document.getElementById('chartOverlay'),
    connectionStatus: document.getElementById('connectionStatus'),
    currentPrice: document.getElementById('currentPrice'),
    priceChange: document.getElementById('priceChange'),
    timeDisplay: document.getElementById('timeDisplay'),
    volume24h: document.getElementById('volume24h'),
    high24h: document.getElementById('high24h'),
    low24h: document.getElementById('low24h'),
    ohlcOpen: document.getElementById('ohlcOpen'),
    ohlcHigh: document.getElementById('ohlcHigh'),
    ohlcLow: document.getElementById('ohlcLow'),
    ohlcClose: document.getElementById('ohlcClose'),
    ohlcVolume: document.getElementById('ohlcVolume'),
    markPrice: document.getElementById('markPrice'),
    indexPrice: document.getElementById('indexPrice'),
    fundingRate: document.getElementById('fundingRate'),
    openInterest: document.getElementById('openInterest'),
    lastUpdate: document.getElementById('lastUpdate')
};

// ===== Chart Setup =====
function initChart() {
    // Create main chart
    chart = LightweightCharts.createChart(elements.chartContainer, {
        layout: {
            background: { type: 'solid', color: '#0a0e17' },
            textColor: '#d1d4dc',
            fontFamily: "'Inter', sans-serif",
            fontSize: 12
        },
        grid: {
            vertLines: { color: 'rgba(42, 46, 57, 0.5)', style: 1 },
            horzLines: { color: 'rgba(42, 46, 57, 0.5)', style: 1 }
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                width: 1,
                color: 'rgba(41, 98, 255, 0.5)',
                style: 2,
                labelBackgroundColor: '#2962ff'
            },
            horzLine: {
                width: 1,
                color: 'rgba(41, 98, 255, 0.5)',
                style: 2,
                labelBackgroundColor: '#2962ff'
            }
        },
        rightPriceScale: {
            borderColor: '#2a2e39',
            scaleMargins: { top: 0.05, bottom: 0.05 }
        },
        timeScale: {
            borderColor: '#2a2e39',
            timeVisible: true,
            secondsVisible: false
        },
        handleScroll: { vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true }
    });

    // Create candlestick series
    candleSeries = chart.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
        priceFormat: { type: 'price', precision: 4, minMove: 0.0001 }
    });

    // Subscribe to crosshair move for OHLC display
    chart.subscribeCrosshairMove(handleCrosshairMove);

    // Handle resize
    window.addEventListener('resize', handleResize);
    handleResize();
}

function handleResize() {
    if (chart && elements.chartContainer) {
        chart.applyOptions({
            width: elements.chartContainer.clientWidth,
            height: elements.chartContainer.clientHeight
        });
    }
}

function handleCrosshairMove(param) {
    if (!param.time || !param.seriesData.size) {
        // Show current candle data
        if (candleData.length > 0) {
            const lastCandle = candleData[candleData.length - 1];
            updateOHLCDisplay(lastCandle);
        }
        return;
    }

    const data = param.seriesData.get(candleSeries);
    if (data) {
        updateOHLCDisplay(data);
    }
}

function updateOHLCDisplay(data) {
    if (!data) return;

    const formatPrice = (price) => parseFloat(price).toFixed(4);
    const formatVolume = (vol) => {
        if (vol >= 1e6) return (vol / 1e6).toFixed(2) + 'M';
        if (vol >= 1e3) return (vol / 1e3).toFixed(2) + 'K';
        return vol.toFixed(2);
    };

    elements.ohlcOpen.textContent = formatPrice(data.open);
    elements.ohlcHigh.textContent = formatPrice(data.high);
    elements.ohlcLow.textContent = formatPrice(data.low);
    elements.ohlcClose.textContent = formatPrice(data.close);

    if (data.volume) {
        elements.ohlcVolume.textContent = formatVolume(data.volume);
    }

    // Color based on direction
    const isUp = data.close >= data.open;
    elements.ohlcClose.className = `ohlc-value ${isUp ? 'high' : 'low'}`;
}

// ===== Data Fetching =====
async function fetchHistoricalData() {
    try {
        showLoading(true, 'Verificando símbolo...');

        // Verificar que tengamos un símbolo válido
        if (!CONFIG.symbol) {
            console.error('❌ No hay símbolo configurado');
            showLoading(true, '❌ Error: No hay símbolo configurado');
            return;
        }

        showLoading(true, `Descargando datos de ${CONFIG.symbol}...`);

        // Bybit API endpoint for klines
        const bybitInterval = TIMEFRAME_MAP[currentInterval] || '60';
        const url = `${CONFIG.restBaseUrl}/v5/market/kline?category=linear&symbol=${CONFIG.symbol}&interval=${bybitInterval}&limit=${CONFIG.candleLimit}`;
        
        console.log(`📡 Fetching data for ${CONFIG.symbol}...`);
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        
        if (data.retCode !== 0) {
            throw new Error(`Bybit API error: ${data.retMsg}`);
        }

        // Bybit returns data in descending order (newest first), need to reverse
        const klines = data.result.list.reverse();
        
        candleData = klines.map(k => ({
            time: Math.floor(parseInt(k[0]) / 1000),
            open: parseFloat(k[1]),
            high: parseFloat(k[2]),
            low: parseFloat(k[3]),
            close: parseFloat(k[4]),
            volume: parseFloat(k[5])
        }));

        // Update chart
        candleSeries.setData(candleData);

        // Fit content
        chart.timeScale().fitContent();

        // Draw ZigZag and Fibonacci levels
        await drawZigZag();

        showLoading(false);

        console.log(`Loaded ${candleData.length} candles for ${currentInterval}`);

        // Fetch and display RSI
        fetchAndUpdateRSI();

    } catch (error) {
        console.error('Error fetching historical data:', error);
        showLoading(true, `❌ Error: ${error.message}`);
    }
}

// ===== RSI Functions =====
async function fetchAndUpdateRSI() {
    try {
        // Fetch 5m candles for RSI calculation (Bybit API)
        const url = `${CONFIG.restBaseUrl}/v5/market/kline?category=linear&symbol=${CONFIG.symbol}&interval=5&limit=100`;
        const response = await fetch(url);
        if (!response.ok) return;

        const data = await response.json();
        if (data.retCode !== 0) return;
        
        // Bybit returns descending order
        const klines = data.result.list.reverse();
        const closes = klines.map(k => parseFloat(k[4]));

        rsiValue = calculateRSI(closes, 14);
        updateRSIDisplay();

    } catch (error) {
        console.error('Error fetching RSI data:', error);
    }
}

function calculateRSI(closes, period = 14) {
    if (closes.length < period + 1) return 50;

    const deltas = [];
    for (let i = 1; i < closes.length; i++) {
        deltas.push(closes[i] - closes[i - 1]);
    }

    const gains = deltas.map(d => d > 0 ? d : 0);
    const losses = deltas.map(d => d < 0 ? -d : 0);

    let avgGain = gains.slice(0, period).reduce((a, b) => a + b, 0) / period;
    let avgLoss = losses.slice(0, period).reduce((a, b) => a + b, 0) / period;

    for (let i = period; i < gains.length; i++) {
        avgGain = (avgGain * (period - 1) + gains[i]) / period;
        avgLoss = (avgLoss * (period - 1) + losses[i]) / period;
    }

    if (avgLoss === 0) return 100;
    const rs = avgGain / avgLoss;
    return 100 - (100 / (1 + rs));
}

function updateRSIDisplay() {
    // Remove existing RSI display
    const existingRSI = document.getElementById('rsiDisplay');
    if (existingRSI) existingRSI.remove();

    if (!showRSI) return;

    // Create RSI display element (etiqueta flotante)
    const rsiDisplay = document.createElement('div');
    rsiDisplay.id = 'rsiDisplay';
    rsiDisplay.style.cssText = `
        position: absolute;
        top: 60px;
        right: 300px;
        background: ${rsiValue >= 70 ? 'rgba(239, 83, 80, 0.9)' : rsiValue <= 30 ? 'rgba(38, 166, 154, 0.9)' : 'rgba(33, 150, 243, 0.9)'};
        color: white;
        padding: 8px 16px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 600;
        z-index: 1000;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    `;
    rsiDisplay.innerHTML = `RSI(5m): ${rsiValue.toFixed(1)} ${rsiValue >= 70 ? '🔴' : rsiValue <= 30 ? '🟢' : '⚪'}`;

    const chartWrapper = document.querySelector('.chart-wrapper');
    if (chartWrapper) {
        chartWrapper.appendChild(rsiDisplay);
    }
}

async function fetch24hStats() {
    try {
        // Bybit API for 24h ticker
        const url = `${CONFIG.restBaseUrl}/v5/market/tickers?category=linear&symbol=${CONFIG.symbol}`;
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.retCode !== 0 || !data.result.list.length) return;
        
        // Bybit format
        const ticker = data.result.list[0];
        update24hStats({
            volume: ticker.volume24h,
            highPrice: ticker.highPrice24h,
            lowPrice: ticker.lowPrice24h,
            priceChangePercent: ticker.price24hPcnt ? (parseFloat(ticker.price24hPcnt) * 100).toFixed(2) : '0'
        });
    } catch (error) {
        console.error('Error fetching 24h stats:', error);
    }
}

function update24hStats(data) {
    const formatPrice = (price) => {
        const p = parseFloat(price);
        return isNaN(p) ? '--' : p.toFixed(4);
    };
    const formatVolume = (vol) => {
        const v = parseFloat(vol);
        if (isNaN(v)) return '--';
        if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
        if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
        if (v >= 1e3) return (v / 1e3).toFixed(2) + 'K';
        return v.toFixed(2);
    };

    // WebSocket uses: q (quote volume), h (high), l (low), P (price change percent)
    // REST API uses: quoteVolume, highPrice, lowPrice, priceChangePercent
    const volume = data.q || data.quoteVolume || data.volume;
    const high = data.h || data.highPrice;
    const low = data.l || data.lowPrice;
    const changePercent = data.P || data.priceChangePercent;

    if (volume) elements.volume24h.textContent = formatVolume(volume);
    if (high) elements.high24h.textContent = formatPrice(high);
    if (low) elements.low24h.textContent = formatPrice(low);

    // Update price change
    if (changePercent !== undefined) {
        const priceChangePercent = parseFloat(changePercent);
        if (!isNaN(priceChangePercent)) {
            elements.priceChange.textContent = `${priceChangePercent >= 0 ? '+' : ''}${priceChangePercent.toFixed(2)}%`;
            elements.priceChange.className = `price-change ${priceChangePercent >= 0 ? 'positive' : 'negative'}`;
        }
    }
}

// ===== WebSocket Connections =====
function connectWebSockets() {
    // Bybit usa un solo WebSocket con múltiples suscripciones
    if (ws) {
        ws.close();
    }

    ws = new WebSocket(CONFIG.wsBaseUrl);

    ws.onopen = () => {
        console.log('Bybit WebSocket connected');
        
        // Suscribirse a kline, ticker y tickers (mark price)
        const interval = TIMEFRAME_MAP[currentInterval] || '240';
        const symbol = CONFIG.symbol;
        
        const subscribeMsg = {
            op: "subscribe",
            args: [
                `kline.${interval}.${symbol}`,
                `tickers.${symbol}`
            ]
        };
        
        ws.send(JSON.stringify(subscribeMsg));
        console.log('Subscribed to:', subscribeMsg.args);
        
        wsConnections.kline = true;
        wsConnections.ticker = true;
        wsConnections.markPrice = true;
        updateConnectionStatus();
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        // Kline update
        if (data.topic && data.topic.startsWith('kline.')) {
            if (data.data && data.data.length > 0) {
                const kline = data.data[0];
                handleBybitKlineUpdate(kline);
            }
        }
        
        // Ticker update (incluye mark price, funding, etc.)
        if (data.topic && data.topic.startsWith('tickers.')) {
            if (data.data) {
                handleBybitTickerUpdate(data.data);
            }
        }
        
        // Pong response
        if (data.op === 'pong') {
            console.log('Pong received');
        }
    };

    ws.onerror = (error) => {
        console.error('Bybit WebSocket error:', error);
    };

    ws.onclose = () => {
        console.log('Bybit WebSocket closed');
        wsConnections.kline = false;
        wsConnections.ticker = false;
        wsConnections.markPrice = false;
        updateConnectionStatus();
        scheduleReconnect();
    };
    
    // Ping cada 20 segundos para mantener la conexión viva
    if (window.bybitPingInterval) {
        clearInterval(window.bybitPingInterval);
    }
    window.bybitPingInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ op: "ping" }));
        }
    }, 20000);
}

// Ya no necesitamos funciones separadas para cada WebSocket
function connectKlineWS() {
    // Manejado por connectWebSockets()
}

function connectTickerWS() {
    // Manejado por connectWebSockets()
}

function connectMarkPriceWS() {
    // Manejado por connectWebSockets()
}

function handleBybitKlineUpdate(kline) {
    const candle = {
        time: Math.floor(parseInt(kline.start) / 1000),
        open: parseFloat(kline.open),
        high: parseFloat(kline.high),
        low: parseFloat(kline.low),
        close: parseFloat(kline.close),
        volume: parseFloat(kline.volume)
    };

    // Update or add candle
    const lastIndex = candleData.length - 1;
    const isNewCandle = lastIndex < 0 || candleData[lastIndex].time !== candle.time;

    if (isNewCandle) {
        candleData.push(candle);
    } else {
        candleData[lastIndex] = candle;
    }

    // Always update ZigZag to reflect real-time price changes (avoids "ghost" points)
    if (showZigZag) {
        drawZigZag();
    }

    // Update chart
    candleSeries.update(candle);

    // Update OHLC display
    updateOHLCDisplay(candle);

    // Update last update time
    elements.lastUpdate.textContent = new Date().toLocaleTimeString();
}

function handleBybitTickerUpdate(data) {
    // Update current price with animation
    const newPrice = parseFloat(data.lastPrice);
    const priceElement = elements.currentPrice;

    if (lastPrice !== null && priceElement) {
        const isUp = newPrice > lastPrice;
        priceElement.classList.remove('bullish', 'bearish');
        priceElement.classList.add(isUp ? 'bullish' : 'bearish');
    }

    if (priceElement) {
        priceElement.textContent = newPrice.toFixed(4);
    }
    lastPrice = newPrice;

    // Update 24h stats from ticker
    if (data.price24hPcnt !== undefined) {
        const change = parseFloat(data.price24hPcnt) * 100;
        if (elements.priceChange) {
            elements.priceChange.textContent = change.toFixed(2) + '%';
            elements.priceChange.style.color = change >= 0 ? '#26a69a' : '#ef5350';
        }
    }
    
    if (data.highPrice24h && elements.high24h) {
        elements.high24h.textContent = parseFloat(data.highPrice24h).toFixed(4);
    }
    
    if (data.lowPrice24h && elements.low24h) {
        elements.low24h.textContent = parseFloat(data.lowPrice24h).toFixed(4);
    }
    
    if (data.volume24h && elements.volume24h) {
        const vol = parseFloat(data.volume24h);
        elements.volume24h.textContent = vol > 1000000 ? (vol / 1000000).toFixed(2) + 'M' : vol.toFixed(0);
    }
    
    // Mark price y funding rate
    if (data.markPrice && elements.markPrice) {
        elements.markPrice.textContent = parseFloat(data.markPrice).toFixed(4);
    }
    
    if (data.indexPrice && elements.indexPrice) {
        elements.indexPrice.textContent = parseFloat(data.indexPrice).toFixed(4);
    }
    
    if (data.fundingRate && elements.fundingRate) {
        const fundingRate = parseFloat(data.fundingRate) * 100;
        elements.fundingRate.textContent = fundingRate.toFixed(4) + '%';
        elements.fundingRate.style.color = fundingRate >= 0 ? '#26a69a' : '#ef5350';
    }
}

// Mantener las funciones originales para compatibilidad pero redirigir
function handleKlineUpdate(kline) {
    // Para compatibilidad con formato Binance
    const candle = {
        time: Math.floor(kline.t / 1000),
        open: parseFloat(kline.o),
        high: parseFloat(kline.h),
        low: parseFloat(kline.l),
        close: parseFloat(kline.c),
        volume: parseFloat(kline.v)
    };

    // Update or add candle
    const lastIndex = candleData.length - 1;
    const isNewCandle = lastIndex < 0 || candleData[lastIndex].time !== candle.time;

    if (isNewCandle) {
        candleData.push(candle);
    } else {
        candleData[lastIndex] = candle;
    }

    // Always update ZigZag to reflect real-time price changes (avoids "ghost" points)
    if (showZigZag) {
        drawZigZag();
    }

    // Update chart
    candleSeries.update(candle);

    // Update OHLC display
    updateOHLCDisplay(candle);

    // Update last update time
    elements.lastUpdate.textContent = new Date().toLocaleTimeString();
}

// handleTickerUpdate y handleMarkPriceUpdate eliminadas - ahora se usa handleBybitTickerUpdate

function scheduleReconnect() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
    }

    reconnectTimer = setTimeout(() => {
        console.log('Attempting to reconnect...');
        connectWebSockets();
    }, CONFIG.reconnectDelay);
}

function updateConnectionStatus() {
    const statusEl = elements.connectionStatus;
    const textEl = statusEl.querySelector('.status-text');

    // Count active connections
    const activeCount = Object.values(wsConnections).filter(v => v).length;
    const totalCount = Object.keys(wsConnections).length;

    statusEl.classList.remove('connected', 'disconnected');

    if (activeCount === totalCount) {
        // All connected
        statusEl.classList.add('connected');
        textEl.textContent = 'Conectado';
    } else if (activeCount > 0) {
        // Partially connected - still show as connected
        statusEl.classList.add('connected');
        textEl.textContent = `Conectado (${activeCount}/${totalCount})`;
    } else {
        // All disconnected
        statusEl.classList.add('disconnected');
        textEl.textContent = 'Desconectado';
    }
}

// ===== UI Helpers =====
function showLoading(show, message = '') {
    elements.chartOverlay.classList.toggle('hidden', !show);
    const detailEl = document.getElementById('loadingDetail');
    if (detailEl && message) {
        detailEl.textContent = message;
    }
}

function updateTime() {
    const now = new Date();
    elements.timeDisplay.textContent = now.toLocaleTimeString('es-ES', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// ===== Event Handlers =====
function setupEventListeners() {
    // Timeframe buttons
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const tf = btn.dataset.tf;
            if (tf === currentInterval) return;

            // Update UI
            document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Update interval and reconnect
            currentInterval = tf;
            await fetchHistoricalData();
            connectKlineWS();
        });
    });

    // Reset zoom button
    document.getElementById('resetZoom').addEventListener('click', () => {
        chart.timeScale().fitContent();
    });

    // Screenshot button
    document.getElementById('screenshotBtn').addEventListener('click', () => {
        const link = document.createElement('a');
        link.download = `${CONFIG.symbol}_${currentInterval}_${Date.now()}.png`;
        link.href = chart.takeScreenshot().toDataURL('image/png');
        link.click();
    });

    // Toggle Auto Fibonacci
    const toggleAutoFibBtn = document.getElementById('toggleAutoFibBtn');
    if (toggleAutoFibBtn) {
        toggleAutoFibBtn.addEventListener('click', () => {
            showAutoFib = !showAutoFib;
            toggleAutoFibBtn.classList.toggle('active', showAutoFib);

            if (showAutoFib) {
                drawZigZag(candleData);
            } else {
                clearFibonacciLines();
            }
        });
    }

    // Toggle ZigZag visibility
    const toggleZigZagBtn = document.getElementById('toggleZigZagBtn');
    if (toggleZigZagBtn) {
        toggleZigZagBtn.addEventListener('click', () => {
            showZigZag = !showZigZag;
            toggleZigZagBtn.classList.toggle('active', showZigZag);
            drawZigZag();
        });
    }

    // Toggle Trade Lines
    const toggleTradesBtn = document.getElementById('toggleTradesBtn');
    if (toggleTradesBtn) {
        toggleTradesBtn.addEventListener('click', () => {
            showTradeLines = !showTradeLines;
            toggleTradesBtn.classList.toggle('active', showTradeLines);

            if (showTradeLines) {
                loadTradesPanel(); // Reload to redraw lines
            } else {
                // Clear position lines (positionPriceLines array)
                positionPriceLines.forEach(line => {
                    try { candleSeries.removePriceLine(line); } catch (e) { }
                });
                positionPriceLines = [];
            }
        });
    }

    // Toggle RSI
    const toggleRSIBtn = document.getElementById('toggleRSIBtn');
    if (toggleRSIBtn) {
        toggleRSIBtn.addEventListener('click', () => {
            showRSI = !showRSI;
            toggleRSIBtn.classList.toggle('active', showRSI);
            updateRSIDisplay();
        });
    }

    // Pair selector dropdown
    document.getElementById('pairSelect').addEventListener('change', async (e) => {
        const newSymbol = e.target.value;
        if (newSymbol === currentSymbol) return;

        console.log(`Changing symbol from ${currentSymbol} to ${newSymbol}`);
        await changeSymbol(newSymbol);
    });

    // ===== Enhanced Pair Search with Autocomplete =====
    setupPairAutocomplete();

    // Load trades panel
    loadTradesPanel();

    // Setup manual Fibonacci drawing
    setupManualFibonacci();
}

// ===== Manual Fibonacci Drawing =====
function setupManualFibonacci() {
    // Keyboard listener for Delete/Suprimir key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Delete' || e.key === 'Supr' || e.keyCode === 46) {
            deleteManualFibonacci();
        }
        // Toggle draw mode with 'F' key
        if (e.key === 'f' || e.key === 'F') {
            toggleFibonacciDrawMode();
        }
    });

    // Click listener for chart
    if (chart) {
        chart.subscribeClick(handleChartClick);
    }
}

function toggleFibonacciDrawMode() {
    manualFibMode = !manualFibMode;
    manualFibPoints = [];

    const status = manualFibMode ? '🎨 Modo dibujo Fibonacci ACTIVADO - Click 2 puntos' : '📊 Modo dibujo DESACTIVADO';
    console.log(status);

    // Show visual feedback
    showToast(manualFibMode ? 'Modo Fibonacci: Click para High, luego Low' : 'Modo Fibonacci desactivado');
}

function handleChartClick(param) {
    if (!manualFibMode || !param.point || !param.time) return;

    const price = candleSeries.coordinateToPrice(param.point.y);
    const time = param.time;

    manualFibPoints.push({ price, time });
    console.log(`📍 Punto ${manualFibPoints.length}: ${price.toFixed(4)} @ ${new Date(time * 1000).toLocaleString()}`);

    if (manualFibPoints.length === 2) {
        drawManualFibonacci();
        manualFibMode = false;
        manualFibPoints = [];
        showToast('Fibonacci dibujado - Presiona Suprimir para eliminar');
    }
}

function drawManualFibonacci() {
    const point1 = manualFibPoints[0];
    const point2 = manualFibPoints[1];

    // Determine High and Low
    const highPrice = Math.max(point1.price, point2.price);
    const lowPrice = Math.min(point1.price, point2.price);
    const highTime = point1.price > point2.price ? point1.time : point2.time;
    const lowTime = point1.price < point2.price ? point1.time : point2.time;

    const range = highPrice - lowPrice;
    if (range <= 0) return;

    // Clear previous manual Fibonacci
    clearManualFibonacci();

    // Draw Fibonacci lines
    const levels = [
        { level: 0, color: '#26a69a', label: '0% (Low)' },
        { level: 0.236, color: '#ffeb3b', label: '23.6%' },
        { level: 0.382, color: '#ff9800', label: '38.2%' },
        { level: 0.5, color: '#9c27b0', label: '50%' },
        { level: 0.58, color: '#2196f3', label: '58%' },
        { level: 0.618, color: '#e91e63', label: '61.8% ⭐' },
        { level: 0.786, color: '#f44336', label: '78.6%' },
        { level: 1, color: '#ef5350', label: '100% (High)' }
    ];

    levels.forEach(({ level, color, label }) => {
        const price = lowPrice + (range * level);
        const line = candleSeries.createPriceLine({
            price: price,
            color: color,
            lineWidth: level === 0.618 || level === 0.786 ? 2 : 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: `MF ${label}`,
        });
        manualFibLines.push(line);
    });

    console.log(`✅ Fibonacci manual dibujado: High ${highPrice.toFixed(4)} -> Low ${lowPrice.toFixed(4)}`);
}

function clearManualFibonacci() {
    manualFibLines.forEach(line => {
        try {
            candleSeries.removePriceLine(line);
        } catch (e) { }
    });
    manualFibLines = [];
}

function deleteManualFibonacci() {
    if (manualFibLines.length > 0) {
        clearManualFibonacci();
        showToast('Fibonacci manual eliminado');
        console.log('🗑️ Fibonacci manual eliminado');
    }
}

function showToast(message) {
    // Create or update toast element
    let toast = document.getElementById('fibToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'fibToast';
        toast.style.cssText = `
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(33, 150, 243, 0.95);
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            z-index: 10000;
            transition: opacity 0.3s;
        `;
        document.body.appendChild(toast);
    }

    toast.textContent = message;
    toast.style.opacity = '1';

    setTimeout(() => {
        toast.style.opacity = '0';
    }, 3000);
}

// ===== Trades Panel =====
let tradesPollingInterval = null;

function loadTradesPanel() {
    // Try to load from trades.json
    fetch('trades.json')
        .then(response => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return response.json();
        })
        .then(data => {
            if (data && typeof data === 'object') {
                updateTradesPanel(data);
            }
        })
        .catch(err => {
            console.log('trades.json not available (this is normal with file:// protocol):', err.message);
            console.log('TIP: Run a local server for full functionality: python -m http.server 8000');
        });
}

// Start polling for trade updates every 5 seconds
function startTradesPolling() {
    if (tradesPollingInterval) clearInterval(tradesPollingInterval);
    tradesPollingInterval = setInterval(loadTradesPanel, 1000);
    loadTradesPanel(); // Load immediately
}

function updateTradesPanel(data) {
    const tradesList = document.getElementById('tradesList');
    const tradesCount = document.getElementById('tradesCount');
    const accountBalance = document.getElementById('accountBalance');
    const accountPnl = document.getElementById('accountPnl');

    if (!tradesList) return;

    // Update account info
    if (data.balance !== undefined) {
        accountBalance.textContent = `$${data.balance.toFixed(2)}`;
    }

    // Calculate total PnL from history
    const totalPnl = (data.history || []).reduce((sum, trade) => sum + (trade.pnl || 0), 0);
    accountPnl.textContent = `$${totalPnl.toFixed(2)}`;
    accountPnl.className = totalPnl >= 0 ? 'trade-pnl positive' : 'trade-pnl negative';

    // Build trades list
    const openPositions = Object.entries(data.open_positions || {}).map(([id, pos]) => ({ ...pos, id, type: 'POSITION' }));
    const pendingOrders = Object.entries(data.pending_orders || {}).map(([id, ord]) => ({ ...ord, id, type: 'LIMIT' }));
    const allTrades = [...openPositions, ...pendingOrders];

    // Store for redrawing
    lastTradesData = {
        trades: allTrades,
        balance: data.balance,
        history: data.history
    };

    tradesCount.textContent = allTrades.length;

    if (allTrades.length === 0) {
        tradesList.innerHTML = '<div class="no-trades">Sin operaciones abiertas</div>';
        // Clear trade lines from chart
        clearTradeLines();
        return;
    }

    tradesList.innerHTML = allTrades.map(trade => {
        // ... (render html code remains same)
        const isPosition = trade.type === 'POSITION';
        const typeClass = isPosition ? 'position' : 'limit';
        const typeLabel = isPosition ? 'POSICIÓN' : 'LÍMITE';
        const pnlClass = (trade.pnl || 0) >= 0 ? 'positive' : 'negative';

        return `
            <div class="trade-item ${typeClass}" onclick="navigateToSymbol('${trade.symbol}')">
                <div class="trade-header">
                    <span class="trade-symbol">${trade.symbol}</span>
                    <span class="trade-type ${typeClass === 'position' ? 'open' : 'limit'}">${typeLabel} ${trade.side}</span>
                </div>
                <div class="trade-details">
                    <div class="detail-row">
                        <span>Precio:</span>
                        <span>$${parseFloat(trade.price || trade.entry_price).toFixed(4)}</span>
                    </div>
                    <div class="detail-row">
                        <span>TP:</span>
                        <span>$${trade.take_profit ? parseFloat(trade.take_profit).toFixed(4) : '-'}</span>
                    </div>
                    ${isPosition ? `
                    <div class="detail-row">
                        <span>PnL:</span>
                        <span class="${pnlClass}">$${(trade.pnl || 0).toFixed(2)}</span>
                    </div>` : ''}
                </div>
            </div>
        `;
    }).join('');

    // NOTE: Lines are drawn by drawPositionLines() in the trades panel system
    // Don't call drawTradeLines here to avoid duplicates
    // drawTradeLines(allTrades);
}


// ===== Trade Lines on Chart =====



function clearTradeLines() {
    // Remove all trade price lines
    tradeLinesSeries.forEach(line => {
        try {
            candleSeries.removePriceLine(line);
        } catch (e) {
            // Line may already be removed
        }
    });
    tradeLinesSeries = [];
}

function drawTradeLines(trades) {
    clearTradeLines();

    if (!candleSeries || !trades || trades.length === 0) return;

    trades.forEach(trade => {
        // Only draw lines for current symbol
        if (trade.symbol !== currentSymbol) return;

        const isPosition = trade.type === 'POSITION';
        const price = isPosition ? trade.entry_price : trade.price;
        const tpPrice = trade.take_profit;

        // Entry/Order line
        const entryLine = candleSeries.createPriceLine({
            price: parseFloat(price),
            color: isPosition ? '#ef5350' : '#ff9800', // Red for position, orange for limit
            lineWidth: 2,
            lineStyle: isPosition ? 0 : 2, // Solid for position, dashed for limit
            axisLabelVisible: true,
            title: isPosition ? 'ENTRY' : 'LIMIT',
        });
        tradeLinesSeries.push(entryLine);

        // Take Profit line
        if (tpPrice) {
            const tpLine = candleSeries.createPriceLine({
                price: parseFloat(tpPrice),
                color: '#26a69a', // Green
                lineWidth: 1,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: 'TP',
            });
            tradeLinesSeries.push(tpLine);
        }

        // Stop Loss line (only for positions)
        const slPrice = trade.stop_loss;
        if (isPosition && slPrice) {
            const slLine = candleSeries.createPriceLine({
                price: parseFloat(slPrice),
                color: '#ef5350', // Red
                lineWidth: 1,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: 'SL',
            });
            tradeLinesSeries.push(slLine);
        }
    });

    console.log(`📊 Dibujadas ${tradeLinesSeries.length} líneas de trades en el gráfico`);
}

function navigateToSymbol(symbol) {
    if (symbol && symbol !== currentSymbol) {
        changeSymbol(symbol);
        document.getElementById('pairSelect').value = symbol;
    }
}

// ===== Change Symbol =====
async function changeSymbol(newSymbol) {
    currentSymbol = newSymbol;
    lastPrice = null;

    // Clear ping interval
    if (window.bybitPingInterval) {
        clearInterval(window.bybitPingInterval);
        window.bybitPingInterval = null;
    }

    // Close WebSocket connection
    if (ws) { ws.close(); ws = null; }

    // Reset connection status
    wsConnections = { kline: false, ticker: false, markPrice: false };
    updateConnectionStatus();

    // Fetch new data
    await fetchHistoricalData();
    await fetch24hStats();
    await fetchOpenInterest();

    // Reconnect WebSockets with new symbol
    connectWebSockets();

    console.log(`Now tracking ${newSymbol}`);
}

// ===== Fetch Open Interest =====
async function fetchOpenInterest() {
    try {
        // Bybit API for open interest
        const url = `${CONFIG.restBaseUrl}/v5/market/open-interest?category=linear&symbol=${CONFIG.symbol}&intervalTime=5min&limit=1`;
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.retCode !== 0 || !data.result.list.length) return;

        const oi = parseFloat(data.result.list[0].openInterest);
        elements.openInterest.textContent = oi >= 1e6
            ? (oi / 1e6).toFixed(2) + 'M'
            : oi >= 1e3
                ? (oi / 1e3).toFixed(2) + 'K'
                : oi.toFixed(2);
    } catch (error) {
        console.error('Error fetching open interest:', error);
    }
}

// ===== ZigZag Algorithm - VERSIÓN ROBUSTA =====
function calculateZigZag(data) {
    const config = getZigZagConfig();
    if (data.length < config.depth * 2) return [];

    const deviation = config.deviation / 100;
    const depth = config.depth;

    // ===== FASE 1: Encontrar TODOS los pivotes potenciales =====
    const potentialPivots = [];

    for (let i = depth; i < data.length - 1; i++) {
        let isHigh = true;
        let isLow = true;

        // Comparar con las velas en la ventana
        const start = Math.max(0, i - depth);
        const end = Math.min(data.length, i + depth + 1);
        
        for (let j = start; j < end; j++) {
            if (j === i) continue;
            if (data[j].high >= data[i].high) isHigh = false;
            if (data[j].low <= data[i].low) isLow = false;
        }

        if (isHigh) {
            potentialPivots.push({ index: i, price: data[i].high, type: 'high' });
        }
        if (isLow) {
            potentialPivots.push({ index: i, price: data[i].low, type: 'low' });
        }
    }

    // También agregar extremos de las últimas velas
    const lastN = Math.min(depth, data.length - 1);
    if (lastN > 0) {
        const lastSectionStart = data.length - lastN;
        let maxIdx = lastSectionStart;
        let minIdx = lastSectionStart;
        
        for (let i = lastSectionStart; i < data.length; i++) {
            if (data[i].high > data[maxIdx].high) maxIdx = i;
            if (data[i].low < data[minIdx].low) minIdx = i;
        }

        // Solo añadir si no existen ya
        if (!potentialPivots.some(p => p.index === maxIdx && p.type === 'high')) {
            potentialPivots.push({ index: maxIdx, price: data[maxIdx].high, type: 'high' });
        }
        if (!potentialPivots.some(p => p.index === minIdx && p.type === 'low')) {
            potentialPivots.push({ index: minIdx, price: data[minIdx].low, type: 'low' });
        }
    }

    if (potentialPivots.length === 0) return [];

    // Ordenar por índice
    potentialPivots.sort((a, b) => a.index - b.index);

    // ===== FASE 2: Construir ZigZag alternando y respetando desviación =====
    const zigzag = [];
    let lastType = null;
    let lastPrice = null;

    for (const pivot of potentialPivots) {
        if (zigzag.length === 0) {
            zigzag.push(pivot);
            lastType = pivot.type;
            lastPrice = pivot.price;
            continue;
        }

        if (pivot.type === lastType) {
            // Reemplazar si es más extremo
            if (lastType === 'high' && pivot.price > zigzag[zigzag.length - 1].price) {
                zigzag[zigzag.length - 1] = pivot;
                lastPrice = pivot.price;
            } else if (lastType === 'low' && pivot.price < zigzag[zigzag.length - 1].price) {
                zigzag[zigzag.length - 1] = pivot;
                lastPrice = pivot.price;
            }
        } else {
            // Tipo diferente - verificar desviación
            const priceChange = Math.abs(pivot.price - lastPrice) / lastPrice;

            if (priceChange >= deviation) {
                zigzag.push(pivot);
                lastType = pivot.type;
                lastPrice = pivot.price;
            } else {
                // Verificar si es mejor que el anterior del mismo tipo
                if (zigzag.length >= 2 && zigzag[zigzag.length - 2].type === pivot.type) {
                    if (pivot.type === 'high' && pivot.price > zigzag[zigzag.length - 2].price) {
                        zigzag[zigzag.length - 2] = pivot;
                    } else if (pivot.type === 'low' && pivot.price < zigzag[zigzag.length - 2].price) {
                        zigzag[zigzag.length - 2] = pivot;
                    }
                }
            }
        }
    }

    // ===== FASE 3: Validar alternancia =====
    const finalZigzag = [];
    for (const pivot of zigzag) {
        if (finalZigzag.length === 0) {
            finalZigzag.push(pivot);
        } else if (pivot.type !== finalZigzag[finalZigzag.length - 1].type) {
            finalZigzag.push(pivot);
        } else {
            if (pivot.type === 'high' && pivot.price > finalZigzag[finalZigzag.length - 1].price) {
                finalZigzag[finalZigzag.length - 1] = pivot;
            } else if (pivot.type === 'low' && pivot.price < finalZigzag[finalZigzag.length - 1].price) {
                finalZigzag[finalZigzag.length - 1] = pivot;
            }
        }
    }

    // ===== FASE 4: Convertir a formato de pivots =====
    return finalZigzag.map(pivot => ({
        time: data[pivot.index].time,
        price: pivot.price,
        type: pivot.type,
        index: pivot.index
    }));
}

function clearFibonacciLines() {
    // Remove existing Fibonacci lines
    fibonacciLines.forEach(line => {
        try {
            chart.removeSeries(line);
        } catch (e) { }
    });
    fibonacciLines = [];
}

// Función para obtener ZigZag desde el backend (usa la misma lógica que el bot)
async function fetchZigZagFromAPI(symbol, timeframe) {
    try {
        const response = await fetch(`/api/zigzag?symbol=${symbol}&timeframe=${timeframe}&limit=200`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const data = await response.json();
        if (data.error) throw new Error(data.error);
        
        // Convertir a formato esperado por el frontend
        return data.points.map(p => ({
            index: p.index,
            time: p.time,
            price: p.price,
            type: p.type
        }));
    } catch (error) {
        console.warn('⚠️ API ZigZag no disponible, usando cálculo local:', error.message);
        return null; // Fallback a cálculo local
    }
}

async function drawZigZag() {
    try {
        // Remove existing zigzag line
        if (zigzagLineSeries) {
            chart.removeSeries(zigzagLineSeries);
            zigzagLineSeries = null;
        }

        // Clear markers
        candleSeries.setMarkers([]);

        // Clear Fibonacci lines
        clearFibonacciLines();

        // If ZigZag is hidden, just clear and return
        if (!showZigZag) {
            console.log('ZigZag hidden by user');
            return;
        }

        // Get configured timeframe from sharedConfig or default to current
        const configuredTimeframe = (sharedConfig && sharedConfig.scanner && sharedConfig.scanner.timeframe)
            ? sharedConfig.scanner.timeframe
            : '1h';

        // Only draw ZigZag and Fibonacci on the configured timeframe
        if (currentInterval !== configuredTimeframe) {
            console.log(`ZigZag/Fibonacci only available on ${configuredTimeframe.toUpperCase()} timeframe`);
            return;
        }

        // Intentar obtener ZigZag desde la API del backend (misma lógica que el bot)
        let apiPoints = null;
        if (currentSymbol) {
            apiPoints = await fetchZigZagFromAPI(currentSymbol, currentInterval);
        }
        
        if (apiPoints && apiPoints.length >= 2) {
            // Usar puntos de la API (garantiza consistencia con el bot)
            zigzagPoints = apiPoints;
            console.log(`✅ ZigZag from API: ${zigzagPoints.length} points`);
        } else {
            // Fallback: calcular localmente si la API no está disponible
            zigzagPoints = calculateZigZag(candleData);
            console.log(`⚠️ ZigZag local fallback: ${zigzagPoints.length} points`);
        }

        if (zigzagPoints.length < 2) return;

        // Create line series for ZigZag
        zigzagLineSeries = chart.addLineSeries({
            color: '#00e5ff',
            lineWidth: 2,
            lineStyle: 0, // Solid
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false
        });

        // Set zigzag data
        const zigzagData = zigzagPoints.map(p => ({
            time: p.time,
            value: p.price
        }));

        zigzagLineSeries.setData(zigzagData);

        // Add markers for pivot points
        const markers = zigzagPoints.map(p => ({
            time: p.time,
            position: p.type === 'high' ? 'aboveBar' : 'belowBar',
            color: p.type === 'high' ? '#ef5350' : '#26a69a',
            shape: p.type === 'high' ? 'arrowDown' : 'arrowUp',
            text: p.type === 'high' ? 'H' : 'L'
        }));

        candleSeries.setMarkers(markers);

        // Draw Fibonacci levels for SHORT opportunities
        // This will also draw a line to the REAL low (not just ZigZag points)
        drawFibonacciForShort();
    } catch (error) {
        console.error('Error in drawZigZag:', error);
    }
}

function drawFibonacciForShort() {
    try {
        // Fibonacci lines are already cleaned in drawZigZag
        // This function should only be called from drawZigZag when on 1h

        // Check visibility flag
        if (!showAutoFib) return;

        if (zigzagPoints.length < 2) return;

        // IMPROVED FIBONACCI LOGIC:
        // 1. Start from the rightmost HIGH (but skip if last ZigZag point is HIGH)
        // 2. Find ALL lows to the right of that high
        // 3. Choose the LOW with the MINIMUM value
        // 4. Check if 61.8% was touched (only candles to the right of the HIGH)
        // 5. If touched -> move to the next HIGH to the left, accumulate more lows
        // 6. Choose the LOWEST of ALL accumulated lows
        // 7. Repeat until finding a valid swing (61.8% not touched)

        let swingHigh = null;
        let swingLow = null;

        // Get all HIGH points sorted by index (position in time)
        let highPoints = zigzagPoints
            .filter(p => p.type === 'high')
            .sort((a, b) => b.index - a.index); // Most recent first

    // Get all LOW points sorted by index
    const lowPoints = zigzagPoints
        .filter(p => p.type === 'low')
        .sort((a, b) => b.index - a.index); // Most recent first

    if (highPoints.length === 0 || lowPoints.length === 0) {
        console.log('No highs or lows found');
        return;
    }

    // ===== REGLA: Si el último punto ZigZag es un HIGH, ignorarlo =====
    // Esto evita medir swings muy pequeños desde picos recientes
    const lastZigZag = zigzagPoints.reduce((max, p) => p.index > max.index ? p : max);
    const skipFirstHigh = lastZigZag.type === 'high';
    
    if (skipFirstHigh && highPoints.length > 1) {
        console.log(`⚠️ Último punto ZigZag es HIGH (${lastZigZag.price.toFixed(4)}) - Ignorando, usando siguiente High`);
        highPoints = highPoints.slice(1); // Saltar el primer High (más reciente)
    }

    // Iterate through HIGHs from right to left
    for (let h = 0; h < highPoints.length; h++) {
        const currentHigh = highPoints[h];
        const lastCandleIndex = candleData.length - 1;

        // Verificar que hay velas después del High
        if (currentHigh.index >= lastCandleIndex) {
            console.log(`No candles after High at ${currentHigh.price.toFixed(4)}`);
            continue;
        }

        // ===== BUSCAR EL LOW REAL (precio mínimo de TODAS las velas después del High) =====
        // No solo entre puntos ZigZag, sino el precio más bajo real
        let lowestPrice = Infinity;
        let lowestIndex = currentHigh.index + 1;

        for (let k = currentHigh.index + 1; k <= lastCandleIndex; k++) {
            if (candleData[k].low < lowestPrice) {
                lowestPrice = candleData[k].low;
                lowestIndex = k;
            }
        }

        if (lowestPrice === Infinity) {
            console.log(`No valid low found after High at ${currentHigh.price.toFixed(4)}`);
            continue;
        }

        // Crear objeto Low con el precio mínimo real
        const lowestLow = {
            index: lowestIndex,
            time: candleData[lowestIndex].time,
            price: lowestPrice,
            type: 'low'
        };

        // Calculate 61.8% level
        const range = currentHigh.price - lowestLow.price;

        if (range <= 0) {
            console.log(`Invalid range: High ${currentHigh.price.toFixed(4)} is not above Low ${lowestLow.price.toFixed(4)}`);
            continue;
        }

        const fib618Level = lowestLow.price + (range * 0.618);
        const fib58Level = lowestLow.price + (range * 0.58);

        // ===== SECONDARY INVALIDATION LEVEL - CONFIGURABLE =====
        // Get from shared config (default: 1.0 means disabled)
        const secondaryInvalidationLevel = (window.sharedConfig?.fibonacci?.invalidation_level_secondary) ?? 1.0;
        let invalidatedBySecondary = false;

        if (secondaryInvalidationLevel < 1.0) {
            const fibSecondaryLevel = lowestLow.price + (range * secondaryInvalidationLevel);
            const excludeFromIndexSec = Math.max(lowestLow.index + 1, candleData.length - 3);

            for (let k = lowestLow.index + 1; k < excludeFromIndexSec; k++) {
                if (candleData[k].high >= fibSecondaryLevel) {
                    invalidatedBySecondary = true;
                    console.log(`   ⛔ ${(secondaryInvalidationLevel * 100).toFixed(1)}% TOUCHED at index ${k} - INVALIDATING SWING`);
                    break;
                }
            }

            if (invalidatedBySecondary) {
                console.log(`   ⛔ Swing invalidated by ${(secondaryInvalidationLevel * 100).toFixed(1)}% touch - Moving to next High...`);
                continue; // Skip to next High
            }
        }

        // SPECIAL RULE FOR 58% LEVEL:
        // Check candles BETWEEN the Lowest Low and the current candle (excluding current)
        // If any INTERMEDIATE candle touches 58% -> invalidate, move to next High
        // NEW: If current candle OR 1 previous candle is at 58%+ -> Fibonacci stays valid

        const currentCandle = candleData[lastCandleIndex];

        // Count intermediate candles that touch 58% (from Low to 3 candles before current)
        // We exclude the last 3 candles (current + 2 previous) from this check
        let intermediatesTouching58 = 0;
        const excludeFromIndex = Math.max(lowestLow.index + 1, lastCandleIndex - 2);

        for (let k = lowestLow.index + 1; k < excludeFromIndex; k++) {
            if (candleData[k].high >= fib58Level) {
                intermediatesTouching58++;
            }
        }

        // Check if current candle OR the 2 previous candles is at 58%+
        let recentCandlesAt58 = false;
        const startCheck58 = Math.max(0, lastCandleIndex - 2);
        for (let k = startCheck58; k <= lastCandleIndex; k++) {
            if (candleData[k].high >= fib58Level) {
                recentCandlesAt58 = true;
                break;
            }
        }

        // ===== 75% LEVEL CHECK FOR CASE 2/3 =====
        const fib75Level = lowestLow.price + (range * 0.75);

        // Check intermediate candles for 75% (excluding last 3)
        let intermediatesTouching75 = 0;
        const excludeFromIndex75 = Math.max(lowestLow.index + 1, lastCandleIndex - 2);
        for (let k = lowestLow.index + 1; k < excludeFromIndex75; k++) {
            if (candleData[k].high >= fib75Level) {
                intermediatesTouching75++;
            }
        }

        // Check if current candle OR 2 previous are at 75%+
        let recentCandlesAt75 = false;
        const startCheck75 = Math.max(0, lastCandleIndex - 2);
        for (let k = startCheck75; k <= lastCandleIndex; k++) {
            if (candleData[k].high >= fib75Level) {
                recentCandlesAt75 = true;
                break;
            }
        }

        // Invalidate only if intermediate candles touched 75%
        const invalidatedBy75 = intermediatesTouching75 > 0;

        // Invalidate if intermediate candles touched 58% (excluding last 3)
        const invalidatedBy58 = intermediatesTouching58 > 0;

        // ===== 90% LEVEL CHECK - IMMEDIATE INVALIDATION =====
        // If ANY candle (including current) from Low to present touches 90%, invalidate
        const fib90Level = lowestLow.price + (range * 0.90);
        let invalidatedBy90 = false;

        for (let k = lowestLow.index + 1; k <= lastCandleIndex; k++) {
            if (candleData[k].high >= fib90Level) {
                invalidatedBy90 = true;
                console.log(`   ⛔ 90% TOUCHED at index ${k} (${candleData[k].high.toFixed(4)} >= ${fib90Level.toFixed(4)}) - INVALIDATING SWING`);
                break;
            }
        }

        // If 90% is touched, skip to next High immediately
        if (invalidatedBy90) {
            console.log(`   ⛔ Swing invalidated by 90% level - Moving to next High...`);
            continue; // Skip to next High
        }

        // ===== MIN VALID CASE - PARTIAL INVALIDATION =====
        // LÓGICA CORREGIDA:
        // - Tocó 61.8% → Casos válidos: 2, 3, 4 (minValidCase = 2)
        // - Tocó 69%   → Casos válidos: 3, 4    (minValidCase = 3)
        // - Tocó 78.6% → Casos válidos: 4       (minValidCase = 4)
        let minValidCase = 1;  // Default: all cases valid

        const fib69Level = lowestLow.price + (range * 0.69);
        const fib786Level = lowestLow.price + (range * 0.786);
        let hasTouched618 = false;
        let hasTouched69 = false;
        let hasTouched786 = false;
        const excludeFromIndexMinCase = Math.max(lowestLow.index + 1, lastCandleIndex - 2);

        for (let k = lowestLow.index + 1; k < excludeFromIndexMinCase; k++) {
            if (candleData[k].high >= fib786Level) {
                hasTouched786 = true;
            }
            if (candleData[k].high >= fib69Level) {
                hasTouched69 = true;
            }
            if (candleData[k].high >= fib618Level) {
                hasTouched618 = true;
            }
        }

        // Determine minValidCase (LÓGICA CORREGIDA)
        if (hasTouched786) {
            minValidCase = 4;  // Only Case 4 valid
            console.log(`   ⚠️ 78.6% TOUCHED - Only Case 4 valid`);
        } else if (hasTouched69) {
            minValidCase = 3;  // Cases 3 and 4 valid
            console.log(`   ⚠️ 69% TOUCHED - Cases 3,4 valid`);
        } else if (hasTouched618) {
            minValidCase = 2;  // Cases 2, 3 and 4 valid
            console.log(`   ⚠️ 61.8% TOUCHED - Cases 2,3,4 valid`);
        }

        if (invalidatedBy58) {
            console.log(`   ⚠️ 58% touched by ${intermediatesTouching58} intermediate candle(s)`);
        } else if (recentCandlesAt58) {
            console.log(`   🔵 Last 3 candles are at 58%+ - Fibonacci MAINTAINED!`);
        } else {
            console.log(`   ✅ No candles have touched 58% yet - Setup still valid, waiting for entry`);
        }

        // 75% check for Caso 2/3 eligibility
        // If recent candles are at 75%+, we OVERRIDE the 61.8% invalidation because we're in entry zone
        const overrideBy75 = !invalidatedBy75 && recentCandlesAt75;

        if (overrideBy75) {
            console.log(`   🟢 75% OVERRIDE: Last 3 candles at 75%+, entry zone active - CASE 2/3 READY!`);
        }

        // Valid swing conditions:
        // SYNCED WITH PYTHON BOT:
        // Swing is valid if 90% not touched
        // minValidCase indicates from which case entry is valid

        const isValidSwing = true; // If we reached here, all invalidation checks passed

        if (isValidSwing) {
            // Found a valid swing!
            swingHigh = currentHigh;
            swingLow = lowestLow;
            console.log(`✅ VALID Fibonacci Swing Found:`);
            console.log(`   High: ${swingHigh.price.toFixed(4)} (index ${swingHigh.index})`);
            console.log(`   Lowest Low: ${swingLow.price.toFixed(4)} (index ${swingLow.index})`);
            console.log(`   58% Level: ${fib58Level.toFixed(4)}`);
            console.log(`   61.8% Level: ${fib618Level.toFixed(4)}`);
            console.log(`   Recent candles at 58%+: ${recentCandlesAt58 ? 'YES - Entry zone!' : 'No'}`);
            console.log(`   Low found at candle index: ${lowestLow.index}`);
            break;
        } else if (invalidatedBy58) {
            console.log(`❌ Swing invalidated (58% touched by intermediate candles):`);
            console.log(`   High: ${currentHigh.price.toFixed(4)} -> Lowest Low: ${lowestLow.price.toFixed(4)}`);
            console.log(`   58%: ${fib58Level.toFixed(4)} was touched by ${intermediatesTouching58} candle(s). Moving to next High...`);
        }
    }

    if (!swingHigh || !swingLow) {
        console.log('⚠️ No valid swing for SHORT Fibonacci (all levels already touched/invalidated)');
        return;
    }

    // Verificar que swingLow tenga time válido
    if (!swingLow.time && swingLow.index !== undefined && candleData[swingLow.index]) {
        swingLow.time = candleData[swingLow.index].time;
    }

    if (!swingLow.time || !swingHigh.time) {
        console.log('⚠️ Invalid swing times');
        return;
    }

    const range = swingHigh.price - swingLow.price;
    const lastCandleTime = candleData[candleData.length - 1].time;

    // Project lines to the right (extend beyond current candle)
    const extendedTime = lastCandleTime + (lastCandleTime - swingLow.time);

    // Draw Fibonacci retracement levels
    getVisibleFibonacciLevels().forEach((fib, index) => {
        const fibPrice = swingLow.price + (range * fib.level);

        const fibLine = chart.addLineSeries({
            color: fib.color,
            lineWidth: 2,
            lineStyle: 2, // Dashed
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: true,
            title: fib.label
        });

        // Draw from swing low to extended future
        fibLine.setData([
            { time: swingLow.time, value: fibPrice },
            { time: extendedTime, value: fibPrice }
        ]);

        fibonacciLines.push(fibLine);
    });

    // Draw the 0% and 100% reference lines
    const zeroLine = chart.addLineSeries({
        color: 'rgba(76, 175, 80, 0.9)',
        lineWidth: 2,
        lineStyle: 0,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: true,
        title: '0% (Low)'
    });
    zeroLine.setData([
        { time: swingLow.time, value: swingLow.price },
        { time: extendedTime, value: swingLow.price }
    ]);
    fibonacciLines.push(zeroLine);

    const hundredLine = chart.addLineSeries({
        color: 'rgba(244, 67, 54, 0.9)',
        lineWidth: 2,
        lineStyle: 0,
        crosshairMarkerVisible: false,
        priceLineVisible: false,
        lastValueVisible: true,
        title: '100% (High)'
    });
    hundredLine.setData([
        { time: swingHigh.time, value: swingHigh.price },
        { time: extendedTime, value: swingHigh.price }
    ]);
    fibonacciLines.push(hundredLine);

    // Log the SHORT opportunity zone
    console.log('📉 SHORT Fibonacci Levels:');
    console.log(`   Swing High: ${swingHigh.price.toFixed(4)}`);
    console.log(`   Swing Low: ${swingLow.price.toFixed(4)}`);
    getVisibleFibonacciLevels().forEach(fib => {
        const price = swingLow.price + (range * fib.level);
        console.log(`   ${fib.label}: ${price.toFixed(4)}`);
    });
    } catch (error) {
        console.error('Error in drawFibonacciForShort:', error);
    }
}

function updateZigZagOnNewCandle() {
    // Recalculate ZigZag with new data
    drawZigZag();
}

// ===== Initialization =====
async function init() {
    console.log('Initializing WLD/USDT Futures Visualizer...');
    showLoading(true, 'Cargando configuración...');

    // Load shared configuration first
    await loadSharedConfig();

    // Apply timeframe from config
    applyTimeframeFromConfig();

    function applyTimeframeFromConfig() {
        // Leer timeframe de shared_config
        if (sharedConfig && sharedConfig.scanner && sharedConfig.scanner.timeframe) {
            const configTimeframe = sharedConfig.scanner.timeframe;
            currentInterval = configTimeframe;
            console.log(`✅ Timeframe sincronizado: ${configTimeframe}`);

            // Actualizar botón activo en UI
            document.querySelectorAll('.tf-btn').forEach(btn => {
                btn.classList.remove('active');
                if (btn.dataset.tf === configTimeframe) {
                    btn.classList.add('active');
                }
            });
        }
    }

    // Populate pair selector from config or Bybit API
    showLoading(true, 'Obteniendo lista de pares...');
    await populatePairSelect();

    async function populatePairSelect() {
        const pairSelect = document.getElementById('pairSelect');
        const pairSearch = document.getElementById('pairSearch');
        const datalist = document.getElementById('availablePairs');

        if (!pairSelect) return;

        // Limpiar opciones existentes
        pairSelect.innerHTML = '<option value="" disabled>Cargando pares...</option>';
        if (datalist) datalist.innerHTML = '';

        // Obtener pares de la configuración
        let pairs = [];

        // Pares excluidos (stablecoins y otros)
        const EXCLUDED_PAIRS = [
            'USDCUSDT', 'TUSDUSDT', 'BUSDUSDT', 'FDUSDUSDT',
            'USDPUSDT', 'BTCDOMUSDT', 'DAIUSDT', 'EURUSDT', 'GBPUSDT'
        ];

        if (sharedConfig && sharedConfig.scanner && sharedConfig.scanner.target_pairs && sharedConfig.scanner.target_pairs.length > 0) {
            pairs = sharedConfig.scanner.target_pairs;
        } else {
            // Si target_pairs está vacío, obtener TODOS los pares de Bybit Futures
            console.log('📡 Obteniendo lista de pares desde Bybit API...');
            try {
                const response = await fetch('https://api.bybit.com/v5/market/tickers?category=linear');
                if (response.ok) {
                    const data = await response.json();
                    if (data.retCode === 0) {
                        pairs = data.result.list
                            .filter(s => s.symbol.endsWith('USDT'))
                            .map(s => s.symbol)
                            .filter(s => !EXCLUDED_PAIRS.includes(s)) // Excluir stablecoins
                            .sort();
                        console.log(`✅ Encontrados ${pairs.length} pares USDT (sin stablecoins)`);
                    }
                }
            } catch (e) {
                console.error('❌ Error obteniendo pares:', e);
                pairs = ['BTCUSDT', 'ETHUSDT']; // Fallback mínimo
            }
        }

        // Limpiar y crear opciones
        pairSelect.innerHTML = '';

        // Guardar todos los pares globalmente para el autocompletado
        allTradingPairs = pairs.map(pair => ({
            symbol: pair,
            display: pair.replace('USDT', '/USDT')
        }));

        pairs.forEach(pair => {
            const option = document.createElement('option');
            option.value = pair;
            const display = pair.replace('USDT', '/USDT');
            option.textContent = display;

            if (pair === currentSymbol) {
                option.selected = true;
            }

            pairSelect.appendChild(option);

            if (datalist) {
                const dataOption = document.createElement('option');
                dataOption.value = pair;
                datalist.appendChild(dataOption);
            }
        });

        // Usar el primer par como símbolo activo
        if (pairs.length > 0 && !currentSymbol) {
            currentSymbol = pairs[0];
            pairSelect.value = currentSymbol;
            if (pairSearch) pairSearch.value = currentSymbol;
            console.log(`📊 Símbolo actual: ${currentSymbol}`);
        } else if (currentSymbol) {
            pairSelect.value = currentSymbol;
        }

        // Fallback si aún no hay símbolo
        if (!currentSymbol) {
            currentSymbol = 'BTCUSDT';
            console.log('⚠️ Usando BTCUSDT como fallback');
        }
        
        console.log(`🎯 Símbolo inicial configurado: ${currentSymbol}`);
    }

    // ===== Navigation Functions =====
    // Global function for clicking on trades to navigate to their chart
    window.navigateToSymbol = async function (symbol) {
        console.log(`Navigating to ${symbol}...`);
        await changeSymbol(symbol);

        // Update selector dropdowns
        const pairSelect = document.getElementById('pairSelect');
        const pairSearch = document.getElementById('pairSearch');
        if (pairSelect) pairSelect.value = symbol;
        if (pairSearch) pairSearch.value = symbol;

        showToast(`Cambiado a ${symbol}`);
    };

    // ===== Trading Panel Functions =====
    let lastTradesData = null;

    async function loadTradesPanel() {
        try {
            const response = await fetch('/trades.json');
            if (!response.ok) {
                console.log('trades.json not available yet');
                return;
            }
            const data = await response.json();
            lastTradesData = data;
            updateTradingPanel(data);
        } catch (error) {
            console.log('Error loading trades:', error.message);
        }
    }

    function updateTradingPanel(data) {
        // Update Balance
        const balanceEl = document.getElementById('accountBalance');
        if (balanceEl && data.balance !== undefined) {
            balanceEl.textContent = `$${data.balance.toFixed(2)}`;
        }

        // Calculate unrealized PnL from open positions
        let unrealizedPnl = 0;
        if (data.open_positions) {
            Object.values(data.open_positions).forEach(pos => {
                unrealizedPnl += pos.unrealized_pnl || 0;
            });
        }

        // Update PnL
        const pnlEl = document.getElementById('accountPnl');
        if (pnlEl) {
            pnlEl.textContent = `$${unrealizedPnl.toFixed(4)}`;
            pnlEl.classList.remove('positive', 'negative');
            pnlEl.classList.add(unrealizedPnl >= 0 ? 'positive' : 'negative');
        }

        // Update Margin Balance
        const marginBalanceEl = document.getElementById('marginBalance');
        if (marginBalanceEl) {
            const marginBalance = (data.balance || 0) + unrealizedPnl;
            marginBalanceEl.textContent = `$${marginBalance.toFixed(2)}`;
        }

        // Update trades count
        const tradesCount = document.getElementById('tradesCount');
        const openCount = Object.keys(data.open_positions || {}).length +
            Object.keys(data.pending_orders || {}).length;
        if (tradesCount) {
            tradesCount.textContent = openCount;
        }

        // Update trades list
        updateTradesList(data);

        // Draw position lines on chart for current symbol
        drawPositionLines(data);

        // Update history
        updateHistoryList(data.history || []);
    }

    // Store price lines to remove them later
    let positionPriceLines = [];

    function drawPositionLines(data) {
        // Remove old lines
        positionPriceLines.forEach(line => {
            try {
                candleSeries.removePriceLine(line);
            } catch (e) { }
        });
        positionPriceLines = [];

        // Respect the toggle state
        if (!showTradeLines) return;

        if (!candleSeries || !data.open_positions) {
            return;
        }

        // Detect current symbol from global config or variable
        const activeSymbol = currentSymbol || CONFIG.symbol;

        // Draw lines for positions matching current symbol
        Object.values(data.open_positions).forEach(pos => {
            // Normalize symbols just in case
            if (pos.symbol.toUpperCase() !== activeSymbol.toUpperCase()) {
                // console.log(`⏩ Ignorando posición ${pos.symbol} (Actual: ${activeSymbol})`);
                return;
            }

            // Entry line (orange)
            const entryLine = candleSeries.createPriceLine({
                price: pos.entry_price,
                color: '#ff9800',
                lineWidth: 2,
                lineStyle: 0, // Solid
                axisLabelVisible: true,
                title: 'ENTRY'
            });
            positionPriceLines.push(entryLine);

            // TP line (green)
            const tpLine = candleSeries.createPriceLine({
                price: pos.take_profit,
                color: '#4caf50',
                lineWidth: 2,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: 'TP'
            });
            positionPriceLines.push(tpLine);

            // SL line (red) - calculate from Fibonacci if not set
            let slPrice = pos.stop_loss;
            if (!slPrice && pos.fib_high && pos.fib_low) {
                // Calculate SL based on strategy case
                const range = pos.fib_high - pos.fib_low;
                const caseNum = pos.strategy_case === 11 ? 1 : pos.strategy_case;
                // SL levels: C1/C2=110%, C3=94%, C4=93%
                const slLevels = { 1: 1.10, 2: 1.10, 3: 0.94, 4: 0.93 };
                const slRatio = slLevels[caseNum] || 1.10;
                slPrice = pos.fib_low + (range * slRatio);
            }
            if (slPrice) {
                const slLine = candleSeries.createPriceLine({
                    price: slPrice,
                    color: '#ef5350',
                    lineWidth: 2,
                    lineStyle: 2, // Dashed
                    axisLabelVisible: true,
                    title: 'SL'
                });
                positionPriceLines.push(slLine);
            }
        });

        // Draw lines for pending orders matching current symbol
        Object.values(data.pending_orders || {}).forEach(order => {
            if (order.symbol !== currentSymbol) return;

            // Limit order line (blue)
            const limitLine = candleSeries.createPriceLine({
                price: order.price,
                color: '#2196f3',
                lineWidth: 2,
                lineStyle: 1, // Dotted
                axisLabelVisible: true,
                title: 'LIMIT'
            });
            positionPriceLines.push(limitLine);

            // TP line for limit order (green)
            const tpLine = candleSeries.createPriceLine({
                price: order.take_profit,
                color: '#4caf50',
                lineWidth: 1,
                lineStyle: 2, // Dashed
                axisLabelVisible: true,
                title: 'TP'
            });
            positionPriceLines.push(tpLine);
        });
    }

    function updateTradesList(data) {
        const tradesList = document.getElementById('tradesList');
        if (!tradesList) return;

        const positions = Object.entries(data.open_positions || {});
        const orders = Object.entries(data.pending_orders || {});

        if (positions.length === 0 && orders.length === 0) {
            tradesList.innerHTML = '<div class="no-trades">Sin operaciones abiertas</div>';
            return;
        }

        let html = '';

        // Open positions
        positions.forEach(([id, pos]) => {
            const pnlClass = (pos.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
            const strategyCase = pos.strategy_case || 0;
            const executions = pos.executions || [];
            const hasMultipleExecutions = executions.length > 1;
            
            // Calcular ganancia/pérdida potencial en USD
            const entryPrice = pos.entry_price || 0;
            const tpPrice = pos.take_profit || 0;
            const slPrice = pos.stop_loss || 0;
            const qty = pos.quantity || 0;
            const margin = pos.margin || 0;
            const leverage = pos.leverage || 10;
            
            // Para SHORT: ganancia = (entry - tp) * qty, pérdida = (sl - entry) * qty
            const potentialProfit = (entryPrice - tpPrice) * qty;
            const potentialLoss = slPrice > 0 ? (slPrice - entryPrice) * qty : 0;
            
            // Estrella si tiene múltiples ejecuciones (fusionada)
            const starIcon = hasMultipleExecutions ? ' ⭐' : '';
            const marginInfo = hasMultipleExecutions ? ` (x${executions.length})` : '';
            
            // Siempre mostrar SL para todos los casos
            const slText = slPrice > 0 
                ? `<span style="color: #ef5350;">SL: -$${Math.abs(potentialLoss).toFixed(4)}</span>`
                : `<span style="color: #888;">SL: --</span>`;
            
            const pnlValue = pos.unrealized_pnl || 0;
            const pnlSign = pnlValue >= 0 ? '+' : '';
            
            html += `
            <div class="trade-item position clickable" data-symbol="${pos.symbol}" onclick="navigateToSymbol('${pos.symbol}')" style="cursor: pointer;">
                <div class="trade-item-header">
                    <span class="trade-symbol">${pos.symbol}${starIcon} <span style="font-size:0.8em; color:#aaa; margin-left:4px;">(${getCaseLabel(strategyCase)})</span></span>
                    <span class="trade-type position">POSICIÓN ${pos.side}</span>
                </div>
                <div class="trade-details">
                    <span>Precio: $${entryPrice.toFixed(4)}</span>
                    <span style="color: #4caf50;">TP: +$${potentialProfit.toFixed(4)}</span>
                    ${slText}
                </div>
                <div class="trade-details">
                    <span>Qty: ${qty.toFixed(4)}${marginInfo}</span>
                    <span class="trade-pnl ${pnlClass}">PnL: ${pnlSign}$${pnlValue.toFixed(4)}</span>
                </div>
            </div>
        `;
        });

        // Pending orders
        orders.forEach(([id, order]) => {
            const strategyCase = order.strategy_case || 0;
            const entryPrice = order.price || 0;
            const tpPrice = order.take_profit || 0;
            const qty = order.quantity || 0;
            
            // Para SHORT: ganancia potencial = (entry - tp) * qty
            const potentialProfit = (entryPrice - tpPrice) * qty;
            
            html += `
            <div class="trade-item limit clickable" data-symbol="${order.symbol}" onclick="navigateToSymbol('${order.symbol}')" style="cursor: pointer;">
                <div class="trade-item-header">
                    <span class="trade-symbol">${order.symbol} <span style="font-size:0.8em; color:#aaa; margin-left:4px;">(${getCaseLabel(strategyCase)})</span></span>
                    <span class="trade-type limit">LÍMITE ${order.side}</span>
                </div>
                <div class="trade-details">
                    <span>Precio: $${entryPrice.toFixed(4)}</span>
                    <span style="color: #4caf50;">TP: +$${potentialProfit.toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span style="font-size: 0.9em; color: #888;">Qty: ${qty.toFixed(2)}</span>
                </div>
            </div>
        `;
        });

        tradesList.innerHTML = html;
    }

    function updateHistoryList(history) {
        const historyList = document.getElementById('historyList');
        const historyCount = document.getElementById('historyCount');

        if (!historyList) return;

        if (historyCount) {
            historyCount.textContent = history.length;
        }

        if (history.length === 0) {
            historyList.innerHTML = '<div class="no-history">Sin trades cerradas</div>';
            return;
        }

        // Show last 10 trades (most recent first)
        const recentHistory = history.slice(-10).reverse();

        let html = '';
        recentHistory.forEach(trade => {
            const pnlClass = (trade.pnl || 0) >= 0 ? 'positive' : 'negative';
            const reasonClass = trade.reason === 'TP' ? 'tp' : 'sl';
            html += `
            <div class="history-item">
                <div class="trade-row">
                    <span>${trade.symbol} <small>(${getCaseLabel(trade.strategy_case)})</small></span>
                    <span class="trade-reason ${reasonClass}">${trade.reason}</span>
                </div>
                <div class="trade-row">
                    <span>${trade.side}</span>
                    <span class="trade-pnl ${pnlClass}">$${(trade.pnl || 0).toFixed(4)}</span>
                </div>
                <div class="trade-row" style="font-size: 0.8em; color: #888;">
                   <span>Qty: ${(trade.quantity || 0).toFixed(2)}</span>
                   <span>Min: $${(trade.min_pnl || 0).toFixed(4)}</span>
                </div>
            </div>
        `;
        });

        historyList.innerHTML = html;
    }

    // Refresh panel every 2 seconds
    let tradesPanelInterval = setInterval(loadTradesPanel, 1000);

    // Exponer función para pausar/reanudar polling desde modo análisis
    window.pauseTradesPolling = () => {
        if (tradesPanelInterval) {
            clearInterval(tradesPanelInterval);
            tradesPanelInterval = null;
            console.log('⏸️ Trades polling pausado (modo análisis)');
        }
    };

    window.resumeTradesPolling = () => {
        if (!tradesPanelInterval) {
            tradesPanelInterval = setInterval(loadTradesPanel, 1000);
            loadTradesPanel(); // Cargar inmediatamente
            console.log('▶️ Trades polling reanudado');
        }
    };

    // ===== Initialization =====
    initChart();

    // Setup event listeners
    setupEventListeners();

    // Fetch initial data
    await fetchHistoricalData();
    await fetch24hStats();
    await fetchOpenInterest();

    // Connect WebSockets
    connectWebSockets();

    // Update time every second
    updateTime();
    setInterval(updateTime, 1000);

    // Refresh open interest every 30 seconds
    setInterval(fetchOpenInterest, 30000);

    console.log('Initialization complete!');
}

// ===== Enhanced Pair Search Autocomplete =====
function setupPairAutocomplete() {
    const pairSearch = document.getElementById('pairSearch');
    const autocomplete = document.getElementById('pairAutocomplete');

    if (!pairSearch || !autocomplete) return;

    let debounceTimer = null;

    // Input event - filter pairs
    pairSearch.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            const query = e.target.value.toUpperCase().trim();

            if (query.length === 0) {
                hideAutocomplete();
                return;
            }

            const filtered = allTradingPairs
                .filter(p => p.symbol.includes(query) || p.display.includes(query))
                .slice(0, 15); // Limitar a 15 resultados

            renderAutocomplete(filtered, query);
        }, 100);
    });

    // Focus event - show dropdown if there's text
    pairSearch.addEventListener('focus', () => {
        if (pairSearch.value.length > 0) {
            const query = pairSearch.value.toUpperCase().trim();
            const filtered = allTradingPairs
                .filter(p => p.symbol.includes(query))
                .slice(0, 15);
            renderAutocomplete(filtered, query);
        }
    });

    // Keyboard navigation
    pairSearch.addEventListener('keydown', (e) => {
        const items = autocomplete.querySelectorAll('.autocomplete-item');

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            autocompleteSelectedIndex = Math.min(autocompleteSelectedIndex + 1, items.length - 1);
            updateAutocompleteSelection(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            autocompleteSelectedIndex = Math.max(autocompleteSelectedIndex - 1, 0);
            updateAutocompleteSelection(items);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (autocompleteSelectedIndex >= 0 && items[autocompleteSelectedIndex]) {
                const symbol = items[autocompleteSelectedIndex].dataset.symbol;
                selectPair(symbol);
            } else if (pairSearch.value.toUpperCase().endsWith('USDT')) {
                selectPair(pairSearch.value.toUpperCase());
            }
        } else if (e.key === 'Escape') {
            hideAutocomplete();
            pairSearch.blur();
        }
    });

    // Click outside to close
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.pair-search-container')) {
            hideAutocomplete();
        }
    });

    function renderAutocomplete(pairs, query) {
        if (pairs.length === 0) {
            autocomplete.innerHTML = '<div class="autocomplete-empty">No se encontraron pares</div>';
            autocomplete.classList.add('active');
            return;
        }

        autocomplete.innerHTML = pairs.map((pair, index) => {
            const highlightedName = pair.display.replace(
                new RegExp(`(${query})`, 'gi'),
                '<span class="highlight">$1</span>'
            );

            return `
                <div class="autocomplete-item${index === autocompleteSelectedIndex ? ' selected' : ''}" 
                     data-symbol="${pair.symbol}" 
                     data-index="${index}">
                    <span class="pair-name">${highlightedName}</span>
                    <span class="pair-badge-mini">PERP</span>
                </div>
            `;
        }).join('');

        autocomplete.classList.add('active');
        autocompleteSelectedIndex = -1;

        // Add click listeners
        autocomplete.querySelectorAll('.autocomplete-item').forEach(item => {
            item.addEventListener('click', () => {
                selectPair(item.dataset.symbol);
            });

            item.addEventListener('mouseenter', () => {
                autocompleteSelectedIndex = parseInt(item.dataset.index);
                updateAutocompleteSelection(autocomplete.querySelectorAll('.autocomplete-item'));
            });
        });
    }

    function updateAutocompleteSelection(items) {
        items.forEach((item, i) => {
            item.classList.toggle('selected', i === autocompleteSelectedIndex);
        });

        // Scroll into view
        if (items[autocompleteSelectedIndex]) {
            items[autocompleteSelectedIndex].scrollIntoView({ block: 'nearest' });
        }
    }

    function hideAutocomplete() {
        autocomplete.classList.remove('active');
        autocompleteSelectedIndex = -1;
    }

    async function selectPair(symbol) {
        hideAutocomplete();
        pairSearch.value = '';

        if (symbol && symbol !== currentSymbol) {
            await changeSymbol(symbol);

            const pairSelect = document.getElementById('pairSelect');
            if (pairSelect) pairSelect.value = symbol;

            showToast(`Cambiado a ${symbol.replace('USDT', '/USDT')}`);
        }
    }
}

// ===== Modo Análisis de Historial =====
let analysisMode = false;
let analysisData = null;
let filteredTrades = [];  // Ahora incluye abiertas + cerradas
let currentTradeIndex = 0;
let currentCaseFilter = 'all';

function setupAnalysisMode() {
    const toggle = document.getElementById('analysisModeToggle');
    const normalContent = document.getElementById('normalModeContent');
    const analysisContent = document.getElementById('analysisModeContent');
    const fileInput = document.getElementById('analysisFileInput');
    const caseButtons = document.querySelectorAll('.case-btn');
    const prevBtn = document.getElementById('prevTradeBtn');
    const nextBtn = document.getElementById('nextTradeBtn');

    if (!toggle) return;

    // Toggle modo análisis
    toggle.addEventListener('change', async () => {
        analysisMode = toggle.checked;
        
        // Cambiar visibilidad de contenidos
        if (normalContent) normalContent.style.display = analysisMode ? 'none' : 'block';
        if (analysisContent) analysisContent.style.display = analysisMode ? 'flex' : 'none';

        if (analysisMode) {
            // Pausar polling de trades.json normal
            if (window.pauseTradesPolling) window.pauseTradesPolling();
            // Si ya hay archivo cargado
            if (fileInput && fileInput.files.length > 0) {
                await processAnalysisFile(fileInput.files[0]);
            }
        } else {
            // Volver al modo normal - reanudar polling
            clearAnalysisLines();
            analysisData = null;
            filteredTrades = [];
            if (window.resumeTradesPolling) window.resumeTradesPolling();
        }
    });

    // Cambiar archivo
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            if (analysisMode && e.target.files.length > 0) {
                await processAnalysisFile(e.target.files[0]);
            }
        });
    }

    // Filtros por caso
    caseButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            caseButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentCaseFilter = btn.dataset.case;
            applyAnalysisFilter();
        });
    });

    // Navegación
    if (prevBtn) prevBtn.addEventListener('click', () => navigateTrade(-1));
    if (nextBtn) nextBtn.addEventListener('click', () => navigateTrade(1));

    // Atajos de teclado
    document.addEventListener('keydown', (e) => {
        if (!analysisMode) return;
        if (e.key === 'ArrowLeft') navigateTrade(-1);
        if (e.key === 'ArrowRight') navigateTrade(1);
    });
}

async function processAnalysisFile(file) {
    try {
        const text = await file.text();
        analysisData = JSON.parse(text);

        const openCount = Object.keys(analysisData.open_positions || {}).length;
        const closedCount = analysisData.history?.length || 0;
        console.log(`📊 Cargado local: ${file.name}: ${openCount} abiertas, ${closedCount} cerradas`);

        // Reset filtros
        currentCaseFilter = 'all';
        document.querySelectorAll('.case-btn').forEach(b => b.classList.remove('active'));
        document.querySelector('.case-btn[data-case="all"]')?.classList.add('active');

        applyAnalysisFilter();

        // Actualizar etiqueta
        const label = document.querySelector('label[for="analysisFileInput"]');
        if (label) {
            const truncate = (str, n) => (str.length > n) ? str.substr(0, n - 1) + '...' : str;
            label.textContent = `📂 ${truncate(file.name, 20)}`;
        }

        showToast(`Cargado: ${openCount} abiertas + ${closedCount} cerradas`);
    } catch (error) {
        console.error('Error procesando archivo JSON:', error);
        showToast('Error al leer el archivo JSON', 'error');
    }
}

function applyAnalysisFilter() {
    if (!analysisData) {
        filteredTrades = [];
        updateAnalysisStats();
        updateAnalysisUnifiedList();
        return;
    }

    // Combinar posiciones abiertas + órdenes límite + historial cerrado
    const openPositions = Object.entries(analysisData.open_positions || {}).map(([id, pos]) => ({
        ...pos,
        _type: 'open',
        _id: id
    }));
    
    // Añadir órdenes límite pendientes
    const pendingOrders = Object.entries(analysisData.pending_orders || {}).map(([id, order]) => ({
        ...order,
        _type: 'pending',
        _id: id
    }));
    
    const closedTrades = (analysisData.history || []).map((trade, idx) => ({
        ...trade,
        _type: 'closed',
        _id: `closed_${idx}`
    }));

    let allTrades = [...openPositions, ...pendingOrders, ...closedTrades];

    // Aplicar filtro por caso
    if (currentCaseFilter !== 'all') {
        const caseNum = parseInt(currentCaseFilter);
        allTrades = allTrades.filter(t => t.strategy_case === caseNum);
    }

    filteredTrades = allTrades;
    currentTradeIndex = 0;
    updateAnalysisStats();
    updateAnalysisUnifiedList();

    if (filteredTrades.length > 0) {
        showTradeOnChart(filteredTrades[0]);
    }
}

function updateAnalysisStats() {
    const totalEl = document.getElementById('analysisTotalTrades');
    const winRateEl = document.getElementById('analysisWinRate');
    const pnlEl = document.getElementById('analysisTotalPnl');

    if (!totalEl) return;

    const total = filteredTrades.length;
    const closedTrades = filteredTrades.filter(t => t._type === 'closed');
    const openTrades = filteredTrades.filter(t => t._type === 'open');
    const pendingTrades = filteredTrades.filter(t => t._type === 'pending');
    
    const winners = closedTrades.filter(t => (t.pnl || 0) > 0).length;
    const totalPnl = closedTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
    const winRate = closedTrades.length > 0 ? (winners / closedTrades.length * 100).toFixed(1) : 0;

    totalEl.textContent = `${openTrades.length}🟢 ${pendingTrades.length}📋 ${closedTrades.length}✖`;
    winRateEl.textContent = `${winRate}%`;
    pnlEl.textContent = `$${totalPnl.toFixed(2)}`;
    pnlEl.style.color = totalPnl >= 0 ? 'var(--color-bullish)' : 'var(--color-bearish)';
}

function updateAnalysisUnifiedList() {
    const unifiedList = document.getElementById('analysisUnifiedList');
    const navInfo = document.getElementById('tradeNavInfo');

    if (!unifiedList) return;

    if (navInfo) {
        navInfo.textContent = filteredTrades.length > 0 ? `${currentTradeIndex + 1}/${filteredTrades.length}` : '0/0';
    }

    if (filteredTrades.length === 0) {
        unifiedList.innerHTML = '<div class="no-trades-analysis">Sin trades para este filtro</div>';
        return;
    }

    let html = '';
    filteredTrades.forEach((trade, index) => {
        const selectedClass = index === currentTradeIndex ? 'selected' : '';
        
        if (trade._type === 'open') {
            // Posición abierta - Calcular SL si no existe
            let slPrice = trade.stop_loss || 0;
            if (!slPrice && trade.fib_high && trade.fib_low) {
                const range = trade.fib_high - trade.fib_low;
                const caseNum = trade.strategy_case === 11 ? 1 : trade.strategy_case;
                const slLevels = { 1: 1.10, 2: 1.10, 3: 0.94, 4: 0.93 };
                const slRatio = slLevels[caseNum] || 1.10;
                slPrice = trade.fib_low + (range * slRatio);
            }
            
            html += `
            <div class="trade-item open-position ${selectedClass}" data-index="${index}" onclick="selectAnalysisTrade(${index})">
                <div class="trade-item-header">
                    <span class="trade-symbol">${trade.symbol}</span>
                    <span class="trade-badge case-badge">${getCaseLabel(trade.strategy_case)}</span>
                    <span class="trade-badge open-badge">🟢 ABIERTA</span>
                </div>
                <div class="trade-details">
                    <span>Entry: $${(trade.entry_price || 0).toFixed(4)}</span>
                    <span>TP: $${(trade.take_profit || 0).toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span>SL: $${slPrice.toFixed(4)}</span>
                    <span>Size: ${trade.size || 0}</span>
                </div>
            </div>
            `;
        } else if (trade._type === 'pending') {
            // Orden límite pendiente
            html += `
            <div class="trade-item pending-order ${selectedClass}" data-index="${index}" onclick="selectAnalysisTrade(${index})">
                <div class="trade-item-header">
                    <span class="trade-symbol">${trade.symbol}</span>
                    <span class="trade-badge case-badge">${getCaseLabel(trade.strategy_case)}</span>
                    <span class="trade-badge pending-badge">📋 LÍMITE</span>
                </div>
                <div class="trade-details">
                    <span>Price: $${(trade.price || 0).toFixed(4)}</span>
                    <span>TP: $${(trade.take_profit || 0).toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span>Qty: ${(trade.quantity || 0).toFixed(2)}</span>
                    <span>Margin: $${(trade.margin || 0).toFixed(2)}</span>
                </div>
            </div>
            `;
        } else {
            // Trade cerrado
            const pnlClass = (trade.pnl || 0) >= 0 ? 'positive' : 'negative';
            const reasonClass = trade.reason === 'TP' ? 'tp' : 'sl';
            
            html += `
            <div class="trade-item closed-trade ${selectedClass}" data-index="${index}" onclick="selectAnalysisTrade(${index})">
                <div class="trade-item-header">
                    <span class="trade-symbol">${trade.symbol}</span>
                    <span class="trade-badge case-badge">${getCaseLabel(trade.strategy_case)}</span>
                    <span class="trade-badge reason-badge ${reasonClass}">${trade.reason}</span>
                </div>
                <div class="trade-details">
                    <span>$${(trade.entry_price || 0).toFixed(4)} → $${(trade.close_price || 0).toFixed(4)}</span>
                    <span class="trade-pnl ${pnlClass}">$${(trade.pnl || 0).toFixed(4)}</span>
                </div>
            </div>
            `;
        }
    });

    unifiedList.innerHTML = html;

    // Scroll al trade seleccionado
    const selectedItem = unifiedList.querySelector('.trade-item.selected');
    if (selectedItem) {
        selectedItem.scrollIntoView({ block: 'nearest' });
    }
}

function selectAnalysisTrade(index) {
    currentTradeIndex = index;
    updateAnalysisUnifiedList();
    showTradeOnChart(filteredTrades[index]);
}

function navigateTrade(direction) {
    if (filteredTrades.length === 0) return;

    currentTradeIndex += direction;
    if (currentTradeIndex < 0) currentTradeIndex = filteredTrades.length - 1;
    if (currentTradeIndex >= filteredTrades.length) currentTradeIndex = 0;

    updateAnalysisUnifiedList();
    showTradeOnChart(filteredTrades[currentTradeIndex]);
}

// Líneas de análisis en el gráfico
let analysisLines = [];

function clearAnalysisLines() {
    analysisLines.forEach(line => {
        try {
            candleSeries.removePriceLine(line);
        } catch (e) { }
    });
    analysisLines = [];
}

async function showTradeOnChart(trade) {
    if (!trade || !candleSeries) return;

    // Cambiar al símbolo del trade si es diferente
    if (trade.symbol !== currentSymbol) {
        await changeSymbol(trade.symbol);
    }

    // Limpiar líneas anteriores
    clearAnalysisLines();

    // Dibujar líneas del trade
    // Entry (naranja)
    if (trade.entry_price) {
        const entryLine = candleSeries.createPriceLine({
            price: trade.entry_price,
            color: '#ff9800',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: `ENTRY ${trade.executions?.length > 1 ? '(avg)' : ''}`
        });
        analysisLines.push(entryLine);
    }

    // Close (cyan) - solo para trades cerrados
    if (trade.close_price) {
        const closeLine = candleSeries.createPriceLine({
            price: trade.close_price,
            color: '#00bcd4',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: 'CLOSE'
        });
        analysisLines.push(closeLine);
    }

    // TP (verde)
    if (trade.take_profit) {
        const tpLine = candleSeries.createPriceLine({
            price: trade.take_profit,
            color: '#4caf50',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'TP'
        });
        analysisLines.push(tpLine);
    }

    // SL (rojo) - calcular si no existe
    let slPrice = trade.stop_loss;
    if (!slPrice && trade.fib_high && trade.fib_low) {
        const range = trade.fib_high - trade.fib_low;
        const caseNum = trade.strategy_case === 11 ? 1 : trade.strategy_case;
        const slLevels = { 1: 1.10, 2: 1.10, 3: 0.94, 4: 0.93 };
        const slRatio = slLevels[caseNum] || 1.10;
        slPrice = trade.fib_low + (range * slRatio);
    }
    if (slPrice) {
        const slLine = candleSeries.createPriceLine({
            price: slPrice,
            color: '#f44336',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'SL'
        });
        analysisLines.push(slLine);
    }

    // Dibujar todos los niveles de Fibonacci del trade usando fib_high y fib_low
    if (trade.fib_high && trade.fib_low) {
        const fibRange = trade.fib_high - trade.fib_low;

        // Línea 0% (Low)
        const fibLowLine = candleSeries.createPriceLine({
            price: trade.fib_low,
            color: 'rgba(76, 175, 80, 0.9)',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '0% LOW'
        });
        analysisLines.push(fibLowLine);

        // Línea 100% (High)
        const fibHighLine = candleSeries.createPriceLine({
            price: trade.fib_high,
            color: 'rgba(244, 67, 54, 0.9)',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '100% HIGH'
        });
        analysisLines.push(fibHighLine);

        // Dibujar niveles de Fibonacci visibles
        getVisibleFibonacciLevels().forEach(fib => {
            const fibPrice = trade.fib_low + (fibRange * fib.level);
            const fibLine = candleSeries.createPriceLine({
                price: fibPrice,
                color: fib.color,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: fib.label
            });
            analysisLines.push(fibLine);
        });
    }

    // Ejecuciones individuales (puntos azules)
    if (trade.executions && trade.executions.length > 1) {
        trade.executions.forEach((exec, i) => {
            const execLine = candleSeries.createPriceLine({
                price: exec.price,
                color: '#2196f3',
                lineWidth: 1,
                lineStyle: 1,
                axisLabelVisible: true,
                title: `EXEC${i + 1}`
            });
            analysisLines.push(execLine);
        });
    }

    console.log(`📈 Mostrando trade: ${trade.symbol} C${trade.strategy_case} - ${trade.reason} ($${trade.pnl?.toFixed(4)})`);
}

// ===== Editor de Niveles Fibonacci =====
function setupFibonacciEditor() {
    const editBtn = document.getElementById('editFibLevelsBtn');
    const modal = document.getElementById('fibEditorModal');
    const closeBtn = document.getElementById('closeFibEditor');
    const saveBtn = document.getElementById('saveFibLevels');
    const resetBtn = document.getElementById('resetFibLevels');
    const addBtn = document.getElementById('addFibLevelBtn');

    if (!editBtn || !modal) return;

    // Abrir modal
    editBtn.addEventListener('click', () => {
        renderFibonacciLevelsList();
        modal.style.display = 'flex';
    });

    // Cerrar modal
    closeBtn.addEventListener('click', () => {
        modal.style.display = 'none';
    });

    // Click fuera del modal para cerrar
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.style.display = 'none';
        }
    });

    // Guardar cambios
    saveBtn.addEventListener('click', () => {
        saveFibonacciLevelsFromEditor();
        modal.style.display = 'none';
        // Redibujar Fibonacci
        drawZigZag();
        showToast('Niveles Fibonacci actualizados');
    });

    // Restaurar predeterminados
    resetBtn.addEventListener('click', () => {
        FIBONACCI_LEVELS = JSON.parse(JSON.stringify(DEFAULT_FIBONACCI_LEVELS));
        renderFibonacciLevelsList();
        showToast('Niveles restaurados a predeterminados');
    });

    // Añadir nuevo nivel
    addBtn.addEventListener('click', () => {
        const levelInput = document.getElementById('newFibLevel');
        const labelInput = document.getElementById('newFibLabel');
        const colorInput = document.getElementById('newFibColor');

        const level = parseFloat(levelInput.value);
        if (isNaN(level) || level < 0 || level > 200) {
            showToast('Nivel debe ser entre 0 y 200', 'error');
            return;
        }

        const newFib = {
            level: level / 100, // Convertir % a decimal
            color: hexToRgba(colorInput.value, 0.9),
            label: labelInput.value || `${level}%`,
            visible: true
        };

        FIBONACCI_LEVELS.push(newFib);
        FIBONACCI_LEVELS.sort((a, b) => a.level - b.level);

        // Limpiar inputs
        levelInput.value = '';
        labelInput.value = '';

        renderFibonacciLevelsList();
        showToast(`Nivel ${level}% añadido`);
    });
}

function renderFibonacciLevelsList() {
    const list = document.getElementById('fibLevelsList');
    if (!list) return;

    let html = '';
    FIBONACCI_LEVELS.forEach((fib, index) => {
        const levelPercent = (fib.level * 100).toFixed(1);
        const colorHex = rgbaToHex(fib.color);
        const hiddenClass = fib.visible === false ? 'hidden' : '';

        html += `
        <div class="fib-level-item ${hiddenClass}" data-index="${index}">
            <input type="checkbox" class="fib-visible-checkbox" ${fib.visible !== false ? 'checked' : ''} data-index="${index}">
            <input type="color" class="fib-level-color" value="${colorHex}" data-index="${index}">
            <input type="text" class="fib-level-value" value="${levelPercent}" data-index="${index}" readonly>
            <input type="text" class="fib-level-label" value="${fib.label}" data-index="${index}">
            <button class="fib-level-delete" data-index="${index}">🗑</button>
        </div>
    `;
    });

    list.innerHTML = html;

    // Event listeners para checkboxes
    list.querySelectorAll('.fib-visible-checkbox').forEach(cb => {
        cb.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.index);
            FIBONACCI_LEVELS[idx].visible = e.target.checked;
            e.target.closest('.fib-level-item').classList.toggle('hidden', !e.target.checked);
        });
    });

    // Event listeners para colores
    list.querySelectorAll('.fib-level-color').forEach(input => {
        input.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.index);
            FIBONACCI_LEVELS[idx].color = hexToRgba(e.target.value, 0.9);
        });
    });

    // Event listeners para labels
    list.querySelectorAll('.fib-level-label').forEach(input => {
        input.addEventListener('change', (e) => {
            const idx = parseInt(e.target.dataset.index);
            FIBONACCI_LEVELS[idx].label = e.target.value;
        });
    });

    // Event listeners para eliminar
    list.querySelectorAll('.fib-level-delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const idx = parseInt(e.target.dataset.index);
            FIBONACCI_LEVELS.splice(idx, 1);
            renderFibonacciLevelsList();
        });
    });
}

function saveFibonacciLevelsFromEditor() {
    saveFibonacciLevels();
}

// Utilidades de conversión de colores
function hexToRgba(hex, alpha = 1) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function rgbaToHex(rgba) {
    if (!rgba) return '#ff9800';
    const match = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!match) return '#ff9800';
    const r = parseInt(match[1]).toString(16).padStart(2, '0');
    const g = parseInt(match[2]).toString(16).padStart(2, '0');
    const b = parseInt(match[3]).toString(16).padStart(2, '0');
    return `#${r}${g}${b}`;
}

// Hacer función global para onclick
window.selectAnalysisTrade = selectAnalysisTrade;

// ========== ANALYZER MODULE (Simulador de Trading) ==========
// Datos del analizador
let analyzerRawData = null;
let analyzerProcessedTrades = [];
let analyzerCurrentFilter = 'all';
let analyzerSortMode = 'default';
let analyzerSelectedIndex = -1;

// ===== Mode Switch (Analizador / Visor) =====
function setupModeSwitch() {
    const modeSwitch = document.getElementById('modeSwitch');
    const analyzerPanel = document.getElementById('analyzerPanel');
    const tradesPanel = document.getElementById('tradesPanel');
    const labelAnalyzer = document.getElementById('modeLabelAnalyzer');
    const labelVisor = document.getElementById('modeLabelVisor');
    
    if (!modeSwitch) return;
    
    // Modo Analizador por defecto (switch OFF)
    modeSwitch.checked = false;
    if (labelAnalyzer) labelAnalyzer.classList.add('active');
    
    modeSwitch.addEventListener('change', () => {
        const isVisorMode = modeSwitch.checked;
        
        if (analyzerPanel) analyzerPanel.style.display = isVisorMode ? 'none' : 'flex';
        if (tradesPanel) tradesPanel.style.display = isVisorMode ? 'flex' : 'none';
        
        if (labelAnalyzer) labelAnalyzer.classList.toggle('active', !isVisorMode);
        if (labelVisor) labelVisor.classList.toggle('active', isVisorMode);
        
        if (isVisorMode) {
            // Modo Visor - reanudar polling de trades
            if (window.resumeTradesPolling) window.resumeTradesPolling();
            clearAnalysisLines();
        } else {
            // Modo Analizador - pausar polling
            if (window.pauseTradesPolling) window.pauseTradesPolling();
        }
    });
}

// ===== Analyzer Setup =====
function setupAnalyzer() {
    // File input
    const fileInput = document.getElementById('analyzerFileInput');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleAnalyzerFile(e.target.files[0]);
            }
        });
    }
    
    // Load from VM button
    const loadVMBtn = document.getElementById('loadFromVMBtn');
    if (loadVMBtn) {
        loadVMBtn.addEventListener('click', loadAnalyzerDataFromVM);
    }
    
    // Refresh button
    const refreshBtn = document.getElementById('refreshDataBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', loadAnalyzerDataFromVM);
    }
    
    // Filter buttons
    document.querySelectorAll('.filter-btn[data-filter]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn[data-filter]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            analyzerCurrentFilter = btn.dataset.filter;
            runAnalyzerSimulation();
        });
    });
    
    // Sort buttons
    document.querySelectorAll('.sort-btn[data-sort]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.sort-btn[data-sort]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            analyzerSortMode = btn.dataset.sort;
            runAnalyzerSimulation();
        });
    });
    
    // Ignore checkboxes
    ['C1', 'C1pp', 'C2', 'C3', 'C4', 'C5'].forEach(c => {
        const checkbox = document.getElementById(`ignore${c}`);
        if (checkbox) {
            checkbox.addEventListener('change', runAnalyzerSimulation);
        }
    });
    
    // Ignore all / Unignore all buttons
    const ignoreAllBtn = document.getElementById('ignoreAllBtn');
    const unignoreAllBtn = document.getElementById('unignoreAllBtn');
    
    if (ignoreAllBtn) {
        ignoreAllBtn.addEventListener('click', () => {
            ['C1', 'C1pp', 'C2', 'C3', 'C4', 'C5'].forEach(c => {
                const cb = document.getElementById(`ignore${c}`);
                if (cb) cb.checked = true;
            });
            runAnalyzerSimulation();
        });
    }
    
    if (unignoreAllBtn) {
        unignoreAllBtn.addEventListener('click', () => {
            ['C1', 'C1pp', 'C2', 'C3', 'C4', 'C5'].forEach(c => {
                const cb = document.getElementById(`ignore${c}`);
                if (cb) cb.checked = false;
            });
            runAnalyzerSimulation();
        });
    }
    
    // Setup sliders
    setupAnalyzerSliders();
    
    // Reset defaults button
    const resetBtn = document.getElementById('resetDefaultsBtn');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetAnalyzerDefaults);
    }
    
    // C5 toggle (off by default)
    const c5Toggle = document.getElementById('enableC5Averaging');
    if (c5Toggle) {
        c5Toggle.checked = false; // OFF por defecto
        c5Toggle.addEventListener('change', runAnalyzerSimulation);
    }
    
    // Auto-load trades.json
    loadAnalyzerDataFromVM();
}

function setupAnalyzerSliders() {
    // Sliders for each case including separate C1 and C1+
    const sliderConfigs = [
        { id: 'tpC1', valId: 'tpC1Val' },
        { id: 'slC1', valId: 'slC1Val', groupId: 'slGroupC1', enabledId: 'slEnabledC1' },
        { id: 'tpC1pp', valId: 'tpC1ppVal' },
        { id: 'slC1pp', valId: 'slC1ppVal', groupId: 'slGroupC1pp', enabledId: 'slEnabledC1pp' },
        { id: 'tpC2', valId: 'tpC2Val' },
        { id: 'slC2', valId: 'slC2Val', groupId: 'slGroupC2', enabledId: 'slEnabledC2' },
        { id: 'tpC3', valId: 'tpC3Val' },
        { id: 'slC3', valId: 'slC3Val', groupId: 'slGroupC3', enabledId: 'slEnabledC3' },
        { id: 'tpC4', valId: 'tpC4Val' },
        { id: 'slC4', valId: 'slC4Val', groupId: 'slGroupC4', enabledId: 'slEnabledC4' },
        { id: 'tpC5', valId: 'tpC5Val' },
        { id: 'slC5', valId: 'slC5Val', groupId: 'slGroupC5', enabledId: 'slEnabledC5' },
    ];
    
    sliderConfigs.forEach(config => {
        const slider = document.getElementById(config.id);
        const valEl = document.getElementById(config.valId);
        
        if (slider && valEl) {
            slider.addEventListener('input', () => {
                valEl.textContent = slider.value;
                runAnalyzerSimulation();
            });
        }
        
        // SL enabled toggle
        if (config.enabledId) {
            const enabledCb = document.getElementById(config.enabledId);
            const group = document.getElementById(config.groupId);
            if (enabledCb && group) {
                enabledCb.addEventListener('change', () => {
                    group.classList.toggle('disabled', !enabledCb.checked);
                    runAnalyzerSimulation();
                });
            }
        }
    });
}

async function loadAnalyzerDataFromVM() {
    const filesToTry = ['/2trades.json', '/trades.json'];
    const refreshBtn = document.getElementById('refreshDataBtn');
    
    for (const fileName of filesToTry) {
        try {
            const cacheBuster = Date.now() + '-' + Math.random();
            const response = await fetch(fileName + '?nocache=' + cacheBuster, {
                cache: 'no-store',
                headers: { 'Cache-Control': 'no-cache' }
            });
            
            if (response.ok) {
                analyzerRawData = await response.json();
                console.log('✅ Analyzer: ' + fileName + ' cargado');
                
                if (refreshBtn) refreshBtn.style.display = 'inline-flex';
                
                runAnalyzerSimulation();
                showToast(`Datos cargados: ${fileName}`);
                return;
            }
        } catch (e) {
            console.log('ℹ️ No se pudo cargar ' + fileName);
        }
    }
    console.log('ℹ️ No se encontró ningún archivo JSON');
}

async function handleAnalyzerFile(file) {
    try {
        const text = await file.text();
        analyzerRawData = JSON.parse(text);
        console.log('📂 Analyzer: Archivo cargado:', file.name);
        
        runAnalyzerSimulation();
        showToast(`Archivo cargado: ${file.name}`);
    } catch (error) {
        console.error('Error procesando archivo:', error);
        showToast('Error al leer archivo JSON', 'error');
    }
}

function getAnalyzerCaseSettings(caseId) {
    // C5 tiene su propia configuración
    if (caseId == 5) {
        let tp = (parseFloat(document.getElementById('tpC5')?.value) || 45) / 100;
        const slEnabled = document.getElementById('slEnabledC5')?.checked ?? true;
        let sl = slEnabled ? (parseFloat(document.getElementById('slC5')?.value) || 110) / 100 : 0;
        if (tp > 0.618) tp = 0.618;
        if (sl > 0 && sl < 0.62) sl = 0.62;
        return { tp, sl, tpMax: 0.618, slMin: 0.62 };
    }
    
    // C1+ tiene configuración independiente
    if (caseId == 11) {
        let tp = (parseFloat(document.getElementById('tpC1pp')?.value) || 40) / 100;
        const slEnabled = document.getElementById('slEnabledC1pp')?.checked ?? true;
        let sl = slEnabled ? (parseFloat(document.getElementById('slC1pp')?.value) || 80) / 100 : 0;
        if (tp > 0.61) tp = 0.61;
        if (sl > 0 && sl < 0.62) sl = 0.62;
        return { tp, sl, tpMax: 0.61, slMin: 0.62 };
    }
    
    // Límites por caso
    const limits = {
        1: { tpMax: 0.61, slMin: 0.62, tpDefault: 40, slDefault: 80 },
        2: { tpMax: 0.61, slMin: 0.69, tpDefault: 45, slDefault: 85 },
        3: { tpMax: 0.78, slMin: 0.79, tpDefault: 62, slDefault: 94 },
        4: { tpMax: 0.90, slMin: 0.90, tpDefault: 70, slDefault: 93 }
    };
    
    const c = [1, 2, 3, 4].includes(caseId) ? caseId : 1;
    const limit = limits[c];
    
    let tp = (parseFloat(document.getElementById(`tpC${c}`)?.value) || limit.tpDefault) / 100;
    const slEnabled = document.getElementById(`slEnabledC${c}`)?.checked ?? true;
    let sl = slEnabled ? (parseFloat(document.getElementById(`slC${c}`)?.value) || limit.slDefault) / 100 : 0;
    
    if (tp > limit.tpMax) tp = limit.tpMax;
    if (sl > 0 && sl < limit.slMin) sl = limit.slMin;
    
    return { tp, sl, tpMax: limit.tpMax, slMin: limit.slMin };
}

function runAnalyzerSimulation() {
    if (!analyzerRawData) {
        updateAnalyzerUI([]);
        return;
    }
    
    // Get ignored cases
    const ignoredCases = {
        1: document.getElementById('ignoreC1')?.checked || false,
        11: document.getElementById('ignoreC1pp')?.checked || false,
        2: document.getElementById('ignoreC2')?.checked || false,
        3: document.getElementById('ignoreC3')?.checked || false,
        4: document.getElementById('ignoreC4')?.checked || false,
        5: document.getElementById('ignoreC5')?.checked || false
    };
    
    const enableC5 = document.getElementById('enableC5Averaging')?.checked || false;
    
    // Stats
    let stats = {
        realized: 0, floating: 0,
        wins: 0, losses: 0, closedTrades: 0, openTrades: 0,
        grossWin: 0, grossLoss: 0,
        pfSum: 0, pfCount: 0
    };
    
    // 1. Unificar Trades
    let allTrades = [];
    
    // History
    if (analyzerRawData.history) {
        analyzerRawData.history.forEach(t => {
            t._src = 'HIST';
            let impliedMaxDuring = t.max_price_pre_close || t.entry_price;
            t._max = impliedMaxDuring;
            let impliedMinDuring = t.min_price_pre_close || t.entry_price;
            let minPostClose = t.min_price_post_close || t.close_price;
            t._min = Math.min(impliedMinDuring, t.close_price, minPostClose);
            allTrades.push(t);
        });
    }
    
    // Open positions
    if (analyzerRawData.open_positions) {
        Object.values(analyzerRawData.open_positions).forEach(t => {
            t._src = 'OPEN';
            const effectivePrice = (t.current_price && t.current_price > 0) ? t.current_price : t.entry_price;
            let impliedMax = t.max_price_pre_close || t.entry_price;
            t._max = Math.max(impliedMax, effectivePrice);
            let impliedMin = t.min_price_pre_close || t.entry_price;
            t._min = Math.min(impliedMin, effectivePrice);
            if (!t.fib_high) t.fib_high = t.entry_price * 1.05;
            if (!t.fib_low) t.fib_low = t.entry_price * 0.95;
            allTrades.push(t);
        });
    }
    
    // C5 Detection (if enabled)
    let c5Trades = [];
    let tradesConvertedToC5 = new Set();
    
    if (enableC5) {
        let tradesBySymbol = {};
        allTrades.forEach(t => {
            const sym = t.symbol;
            if (!tradesBySymbol[sym]) tradesBySymbol[sym] = [];
            tradesBySymbol[sym].push(t);
        });
        
        for (const symbol in tradesBySymbol) {
            const symTrades = tradesBySymbol[symbol];
            const mainTrades = symTrades.filter(t => [2, 3, 4].includes(t.strategy_case));
            const c1ppTrades = symTrades.filter(t => t.strategy_case === 11);
            
            if (mainTrades.length === 0 || c1ppTrades.length === 0) continue;
            
            mainTrades.forEach(mainTrade => {
                c1ppTrades.forEach(c1ppTrade => {
                    const c1ppRange = c1ppTrade.fib_high - c1ppTrade.fib_low;
                    const c1ppEntry = c1ppTrade.fib_low + (c1ppRange * 0.618);
                    const mainCaseSettings = getAnalyzerCaseSettings(mainTrade.strategy_case);
                    const mainRange = mainTrade.fib_high - mainTrade.fib_low;
                    const mainSL = mainTrade.fib_low + (mainRange * mainCaseSettings.sl);
                    
                    if (mainSL < c1ppEntry) return;
                    
                    const mainTP = mainTrade.fib_low + (mainRange * mainCaseSettings.tp);
                    const mainHitTP = mainTrade._min <= mainTP;
                    if (mainHitTP) return;
                    
                    const c1ppExecuted = mainTrade._max >= c1ppEntry;
                    if (!c1ppExecuted) return;
                    
                    const mainQty = mainTrade.quantity || 1;
                    const c1ppQty = c1ppTrade.quantity || 1;
                    const avgEntry = (mainTrade.entry_price * mainQty + c1ppEntry * c1ppQty) / (mainQty + c1ppQty);
                    
                    const c5Trade = {
                        ...c1ppTrade,
                        symbol: symbol,
                        strategy_case: 5,
                        entry_price: avgEntry,
                        quantity: mainQty + c1ppQty,
                        margin: (mainTrade.margin || 2) + (c1ppTrade.margin || 2),
                        _src: mainTrade._src,
                        _max: Math.max(mainTrade._max, c1ppTrade._max || 0),
                        _min: Math.min(mainTrade._min, c1ppTrade._min || Infinity),
                        _avgExplanation: `Avg: $${mainTrade.entry_price.toFixed(4)} + $${c1ppEntry.toFixed(4)}`
                    };
                    
                    c5Trades.push(c5Trade);
                    tradesConvertedToC5.add(mainTrade.order_id);
                    tradesConvertedToC5.add(c1ppTrade.order_id);
                });
            });
        }
        
        allTrades = allTrades.filter(t => !tradesConvertedToC5.has(t.order_id));
        allTrades = allTrades.concat(c5Trades);
    }
    
    // Update C5 count
    const c5CountEl = document.getElementById('c5Count');
    if (c5CountEl) c5CountEl.textContent = c5Trades.length;
    
    // 2. Process trades
    let processedTrades = [];
    
    allTrades.forEach(t => {
        let cID = t.strategy_case || 1;
        const isIgnored = ignoredCases[cID] || false;
        
        const s = getAnalyzerCaseSettings(cID);
        const range = t.fib_high - t.fib_low;
        const tpPrice = t.fib_low + (range * s.tp);
        let slPrice = Infinity;
        if (s.sl > 0) slPrice = t.fib_low + (range * s.sl);
        
        // Simulation logic
        let status = "", rPnl = 0, fPnl = 0, statusClass = "";
        let isClosed = false;
        
        const slIsValid = slPrice > t.entry_price;
        const hitSL = (s.sl > 0) && slIsValid && (t._max >= slPrice);
        
        const originalReason = (t.reason || '').toUpperCase();
        const originalClosedBySL = t._src === 'HIST' && originalReason === 'SL';
        let effectiveMinForTP = t._min;
        if (originalClosedBySL && !hitSL) {
            effectiveMinForTP = t.min_price_pre_close || t.entry_price;
        }
        const hitTP = effectiveMinForTP <= tpPrice;
        const potProfit = (t.entry_price - tpPrice) * t.quantity;
        const potLoss = (s.sl > 0 && slIsValid) ? Math.abs((slPrice - t.entry_price) * t.quantity) : 0;
        
        if (hitSL) {
            status = "SL HIT ❌";
            rPnl = (t.entry_price - slPrice) * t.quantity;
            isClosed = true;
            statusClass = "loss";
        } else if (hitTP) {
            status = "TP HIT ✅";
            rPnl = potProfit;
            isClosed = true;
            statusClass = "win";
        } else {
            if (t._src === 'HIST') {
                if (originalReason === 'TP') {
                    status = "TP HIT ✅";
                    rPnl = t.pnl;
                    statusClass = "win";
                    isClosed = true;
                } else if (originalReason === 'SL' && !hitSL) {
                    const effectiveCurrentPrice = t.close_price || t.entry_price;
                    status = "RUNNING ⏳";
                    fPnl = (t.entry_price - effectiveCurrentPrice) * t.quantity;
                    statusClass = "running";
                    isClosed = false;
                } else if (originalReason === 'SL') {
                    status = "SL HIT ❌";
                    rPnl = t.pnl;
                    statusClass = "loss";
                    isClosed = true;
                } else {
                    status = `CLOSED`;
                    rPnl = t.pnl;
                    statusClass = t.pnl >= 0 ? "win" : "loss";
                    isClosed = true;
                }
            } else {
                status = "RUNNING ⏳";
                fPnl = (t.entry_price - t.current_price) * t.quantity;
                statusClass = "running";
                isClosed = false;
            }
        }
        
        // Stats (only if not ignored)
        if (!isIgnored) {
            if (isClosed) {
                stats.closedTrades++;
                stats.realized += rPnl;
                if (rPnl > 0) {
                    stats.wins++;
                    stats.grossWin += rPnl;
                } else {
                    stats.losses++;
                    stats.grossLoss += Math.abs(rPnl);
                }
            } else {
                stats.openTrades++;
                stats.floating += fPnl;
            }
        }
        
        processedTrades.push({
            trade: t,
            caseId: cID,
            settings: s,
            tpPrice,
            slPrice,
            status,
            statusClass,
            rPnl,
            fPnl,
            isIgnored,
            isClosed
        });
    });
    
    // 3. Apply visual filter (doesn't affect calculations)
    let displayTrades = processedTrades;
    if (analyzerCurrentFilter !== 'all') {
        const filterCase = parseInt(analyzerCurrentFilter);
        displayTrades = processedTrades.filter(t => t.caseId === filterCase);
    }
    
    // 4. Sort
    switch (analyzerSortMode) {
        case 'pnl_real_desc':
            displayTrades.sort((a, b) => b.rPnl - a.rPnl);
            break;
        case 'pnl_real_asc':
            displayTrades.sort((a, b) => a.rPnl - b.rPnl);
            break;
        case 'pnl_float_desc':
            displayTrades.sort((a, b) => b.fPnl - a.fPnl);
            break;
        case 'pnl_float_asc':
            displayTrades.sort((a, b) => a.fPnl - b.fPnl);
            break;
        default:
            // Keep original order
            break;
    }
    
    analyzerProcessedTrades = displayTrades;
    
    // Update UI
    updateAnalyzerKPIs(stats);
    updateAnalyzerTradesList(displayTrades);
}

function updateAnalyzerKPIs(stats) {
    const realizedEl = document.getElementById('kpiRealizedPnl');
    const floatingEl = document.getElementById('kpiFloatingPnl');
    const winRateEl = document.getElementById('kpiWinRate');
    const pfEl = document.getElementById('kpiPF');
    
    if (realizedEl) {
        realizedEl.textContent = `$${stats.realized.toFixed(2)}`;
        realizedEl.className = 'kpi-value ' + (stats.realized >= 0 ? 'positive' : 'negative');
    }
    
    if (floatingEl) {
        floatingEl.textContent = `$${stats.floating.toFixed(2)}`;
        floatingEl.className = 'kpi-value ' + (stats.floating >= 0 ? 'positive' : 'negative');
    }
    
    if (winRateEl) {
        const winRate = stats.closedTrades > 0 ? ((stats.wins / stats.closedTrades) * 100).toFixed(1) : '0';
        winRateEl.textContent = `${winRate}%`;
    }
    
    if (pfEl) {
        const pf = stats.grossLoss > 0 ? (stats.grossWin / stats.grossLoss).toFixed(2) : (stats.grossWin > 0 ? '∞' : '0.00');
        pfEl.textContent = pf;
    }
}

function updateAnalyzerTradesList(trades) {
    const list = document.getElementById('analyzerTradesList');
    if (!list) return;
    
    if (trades.length === 0) {
        list.innerHTML = '<div class="no-trades-msg">No hay trades para mostrar</div>';
        return;
    }
    
    let html = '';
    trades.forEach((item, index) => {
        const t = item.trade;
        const caseClass = `case-${item.caseId === 11 ? '11' : item.caseId}`;
        const selectedClass = index === analyzerSelectedIndex ? 'selected' : '';
        const ignoredClass = item.isIgnored ? 'ignored' : '';
        const caseLabel = item.caseId === 11 ? 'C1+' : (item.caseId === 5 ? 'C5' : `C${item.caseId}`);
        
        html += `
        <div class="analyzer-trade-item ${caseClass} ${selectedClass} ${ignoredClass}" 
             data-index="${index}" onclick="selectAnalyzerTrade(${index})">
            <div class="trade-item-row">
                <span class="trade-symbol">${t.symbol}</span>
                <span class="trade-case-badge">${caseLabel}</span>
                <span class="trade-status-badge ${item.statusClass}">${item.status}</span>
            </div>
            <div class="trade-item-row">
                <span style="font-size:10px;color:var(--text-tertiary)">Entry: $${t.entry_price.toFixed(4)}</span>
                <div class="trade-pnl-info">
                    <span class="trade-pnl-real ${item.rPnl >= 0 ? 'positive' : 'negative'}">
                        ${item.rPnl !== 0 ? '$' + item.rPnl.toFixed(2) : '-'}
                    </span>
                    <span class="trade-pnl-float ${item.fPnl >= 0 ? 'positive' : 'negative'}">
                        ${item.fPnl !== 0 ? '$' + item.fPnl.toFixed(2) : '-'}
                    </span>
                </div>
            </div>
        </div>
        `;
    });
    
    list.innerHTML = html;
}

function updateAnalyzerUI(trades) {
    updateAnalyzerTradesList(trades);
}

// Select trade and show on chart
window.selectAnalyzerTrade = async function(index) {
    analyzerSelectedIndex = index;
    
    // Update visual selection
    document.querySelectorAll('.analyzer-trade-item').forEach((el, i) => {
        el.classList.toggle('selected', i === index);
    });
    
    // Show trade on chart
    const item = analyzerProcessedTrades[index];
    if (item) {
        await showAnalyzerTradeOnChart(item);
    }
};

async function showAnalyzerTradeOnChart(item) {
    const t = item.trade;
    
    // Change symbol if different
    if (t.symbol !== currentSymbol) {
        await changeSymbol(t.symbol);
    }
    
    // Clear previous analysis lines
    clearAnalysisLines();
    
    // Draw trade lines
    if (t.entry_price && candleSeries) {
        const entryLine = candleSeries.createPriceLine({
            price: t.entry_price,
            color: '#ff9800',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: 'ENTRY'
        });
        analysisLines.push(entryLine);
    }
    
    // Close price (for closed trades)
    if (t.close_price && candleSeries) {
        const closeLine = candleSeries.createPriceLine({
            price: t.close_price,
            color: '#00bcd4',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: 'CLOSE'
        });
        analysisLines.push(closeLine);
    }
    
    // TP line
    if (item.tpPrice && candleSeries) {
        const tpLine = candleSeries.createPriceLine({
            price: item.tpPrice,
            color: '#4caf50',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'TP'
        });
        analysisLines.push(tpLine);
    }
    
    // SL line
    if (item.slPrice && item.slPrice !== Infinity && candleSeries) {
        const slLine = candleSeries.createPriceLine({
            price: item.slPrice,
            color: '#f44336',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'SL'
        });
        analysisLines.push(slLine);
    }
    
    // Fibonacci levels
    if (t.fib_high && t.fib_low && candleSeries) {
        const fibRange = t.fib_high - t.fib_low;
        
        // 0% Low
        const fibLowLine = candleSeries.createPriceLine({
            price: t.fib_low,
            color: 'rgba(76, 175, 80, 0.9)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '0%'
        });
        analysisLines.push(fibLowLine);
        
        // 100% High
        const fibHighLine = candleSeries.createPriceLine({
            price: t.fib_high,
            color: 'rgba(244, 67, 54, 0.9)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '100%'
        });
        analysisLines.push(fibHighLine);
        
        // Draw visible Fibonacci levels
        getVisibleFibonacciLevels().forEach(fib => {
            const fibPrice = t.fib_low + (fibRange * fib.level);
            const fibLine = candleSeries.createPriceLine({
                price: fibPrice,
                color: fib.color,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: fib.label
            });
            analysisLines.push(fibLine);
        });
    }
    
    console.log(`📈 Mostrando trade: ${t.symbol} C${item.caseId}`);
}

function resetAnalyzerDefaults() {
    // Reset sliders to default values
    const defaults = {
        'tpC1': 40, 'slC1': 80,
        'tpC1pp': 40, 'slC1pp': 80,
        'tpC2': 45, 'slC2': 85,
        'tpC3': 62, 'slC3': 94,
        'tpC4': 70, 'slC4': 93,
        'tpC5': 45, 'slC5': 110
    };
    
    Object.entries(defaults).forEach(([id, value]) => {
        const slider = document.getElementById(id);
        const valEl = document.getElementById(id + 'Val');
        if (slider) slider.value = value;
        if (valEl) valEl.textContent = value;
    });
    
    // Enable all SLs
    ['C1', 'C1pp', 'C2', 'C3', 'C4', 'C5'].forEach(c => {
        const cb = document.getElementById(`slEnabled${c}`);
        const group = document.getElementById(`slGroup${c}`);
        if (cb) cb.checked = true;
        if (group) group.classList.remove('disabled');
    });
    
    // Disable C5 averaging
    const c5Toggle = document.getElementById('enableC5Averaging');
    if (c5Toggle) c5Toggle.checked = false;
    
    // Unignore all
    ['C1', 'C1pp', 'C2', 'C3', 'C4', 'C5'].forEach(c => {
        const cb = document.getElementById(`ignore${c}`);
        if (cb) cb.checked = false;
    });
    
    // Reset filter and sort
    analyzerCurrentFilter = 'all';
    analyzerSortMode = 'default';
    document.querySelectorAll('.filter-btn[data-filter]').forEach(b => {
        b.classList.toggle('active', b.dataset.filter === 'all');
    });
    document.querySelectorAll('.sort-btn[data-sort]').forEach(b => {
        b.classList.toggle('active', b.dataset.sort === 'default');
    });
    
    runAnalyzerSimulation();
    showToast('Valores restablecidos');
}

// Start the application
document.addEventListener('DOMContentLoaded', () => {
    init();
    setupAnalysisMode();
    setupFibonacciEditor();
    setupModeSwitch();
    setupAnalyzer();
});
