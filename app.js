/**
 * Binance Futures WLD/USDT Real-time Candlestick Visualizer
 * Uses WebSocket for live updates and Lightweight Charts for rendering
 */

// ===== Configuration =====
let currentSymbol = null; // Will be set from shared_config.json target_pairs

const CONFIG = {
    get symbol() { return currentSymbol; },
    defaultInterval: '1h', // Will be overridden by shared_config.json timeframe
    candleLimit: 1500,
    wsBaseUrl: 'wss://fstream.binance.com/ws',
    restBaseUrl: 'https://fapi.binance.com',
    reconnectDelay: 3000,
    updateInterval: 1000 // Update display every second
};

// Timeframe mapping for API
const TIMEFRAME_MAP = {
    '1m': '1m',
    '5m': '5m',
    '15m': '15m',
    '1h': '1h',
    '4h': '4h',
    '1d': '1d'
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

// ===== All Trading Pairs Cache =====
let allTradingPairs = [];
let autocompleteSelectedIndex = -1;
let showTradeLines = true;
let tradeLinesSeries = []; // Store references to trade lines for clearing
let lastTradesData = null; // Store last trades data for redrawing

// ===== RSI Indicator =====
let showRSI = true;
let rsiValue = 0;
let rsiLine = null;

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
    // Create chart
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
    if (chart) {
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
        showLoading(true);

        const url = `${CONFIG.restBaseUrl}/fapi/v1/klines?symbol=${CONFIG.symbol}&interval=${currentInterval}&limit=${CONFIG.candleLimit}`;
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        candleData = data.map(k => ({
            time: Math.floor(k[0] / 1000),
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
        drawZigZag();

        showLoading(false);

        console.log(`Loaded ${candleData.length} candles for ${currentInterval}`);

        // Fetch and display RSI
        fetchAndUpdateRSI();

    } catch (error) {
        console.error('Error fetching historical data:', error);
        showLoading(false);
    }
}

// ===== RSI Functions =====
async function fetchAndUpdateRSI() {
    try {
        // Fetch 5m candles for RSI calculation
        const url = `${CONFIG.restBaseUrl}/fapi/v1/klines?symbol=${CONFIG.symbol}&interval=5m&limit=100`;
        const response = await fetch(url);
        if (!response.ok) return;

        const data = await response.json();
        const closes = data.map(k => parseFloat(k[4]));

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

    // Create RSI display element
    const rsiDisplay = document.createElement('div');
    rsiDisplay.id = 'rsiDisplay';
    rsiDisplay.style.cssText = `
        position: absolute;
        top: 60px;
        right: 300px;
        background: ${rsiValue >= 75 ? 'rgba(239, 83, 80, 0.9)' : rsiValue <= 25 ? 'rgba(38, 166, 154, 0.9)' : 'rgba(33, 150, 243, 0.9)'};
        color: white;
        padding: 8px 16px;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 600;
        z-index: 1000;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    `;
    rsiDisplay.innerHTML = `RSI(5m): ${rsiValue.toFixed(1)} ${rsiValue >= 75 ? '🔴' : rsiValue <= 25 ? '🟢' : '⚪'}`;

    const chartWrapper = document.querySelector('.chart-wrapper');
    if (chartWrapper) {
        chartWrapper.appendChild(rsiDisplay);
    }
}

async function fetch24hStats() {
    try {
        const url = `${CONFIG.restBaseUrl}/fapi/v1/ticker/24hr?symbol=${CONFIG.symbol}`;
        const response = await fetch(url);
        const data = await response.json();

        update24hStats(data);
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
    connectKlineWS();
    connectTickerWS();
    connectMarkPriceWS();
}

function connectKlineWS() {
    if (ws) {
        ws.close();
    }

    const streamName = `${CONFIG.symbol.toLowerCase()}@kline_${currentInterval}`;
    ws = new WebSocket(`${CONFIG.wsBaseUrl}/${streamName}`);

    ws.onopen = () => {
        console.log('Kline WebSocket connected');
        wsConnections.kline = true;
        updateConnectionStatus();
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.k) {
            handleKlineUpdate(data.k);
        }
    };

    ws.onerror = (error) => {
        console.error('Kline WebSocket error:', error);
    };

    ws.onclose = () => {
        console.log('Kline WebSocket closed');
        wsConnections.kline = false;
        updateConnectionStatus();
        scheduleReconnect();
    };
}

function connectTickerWS() {
    if (tickerWs) {
        tickerWs.close();
    }

    const streamName = `${CONFIG.symbol.toLowerCase()}@ticker`;
    tickerWs = new WebSocket(`${CONFIG.wsBaseUrl}/${streamName}`);

    tickerWs.onopen = () => {
        console.log('Ticker WebSocket connected');
        wsConnections.ticker = true;
        updateConnectionStatus();
    };

    tickerWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleTickerUpdate(data);
    };

    tickerWs.onerror = (error) => {
        console.error('Ticker WebSocket error:', error);
    };

    tickerWs.onclose = () => {
        console.log('Ticker WebSocket closed');
        wsConnections.ticker = false;
        updateConnectionStatus();
    };
}

function connectMarkPriceWS() {
    if (markPriceWs) {
        markPriceWs.close();
    }

    const streamName = `${CONFIG.symbol.toLowerCase()}@markPrice@1s`;
    markPriceWs = new WebSocket(`${CONFIG.wsBaseUrl}/${streamName}`);

    markPriceWs.onopen = () => {
        console.log('Mark Price WebSocket connected');
        wsConnections.markPrice = true;
        updateConnectionStatus();
    };

    markPriceWs.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleMarkPriceUpdate(data);
    };

    markPriceWs.onerror = (error) => {
        console.error('Mark Price WebSocket error:', error);
    };

    markPriceWs.onclose = () => {
        console.log('Mark Price WebSocket closed');
        wsConnections.markPrice = false;
        updateConnectionStatus();
    };
}

function handleKlineUpdate(kline) {
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

function handleTickerUpdate(data) {
    // Update current price with animation
    const newPrice = parseFloat(data.c);
    const priceElement = elements.currentPrice;

    if (lastPrice !== null) {
        const isUp = newPrice > lastPrice;
        priceElement.classList.remove('bullish', 'bearish');
        priceElement.classList.add(isUp ? 'bullish' : 'bearish');
    }

    priceElement.textContent = newPrice.toFixed(4);
    lastPrice = newPrice;

    // Update 24h stats
    update24hStats(data);
}

function handleMarkPriceUpdate(data) {
    const formatPrice = (price) => parseFloat(price).toFixed(4);
    const formatFunding = (rate) => (parseFloat(rate) * 100).toFixed(4) + '%';

    elements.markPrice.textContent = formatPrice(data.p);
    elements.indexPrice.textContent = formatPrice(data.i);
    elements.fundingRate.textContent = formatFunding(data.r);

    // Color funding rate
    const fundingRate = parseFloat(data.r);
    elements.fundingRate.style.color = fundingRate >= 0 ? '#26a69a' : '#ef5350';
}

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
function showLoading(show) {
    elements.chartOverlay.classList.toggle('hidden', !show);
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
    tradesPollingInterval = setInterval(loadTradesPanel, 5000);
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

    // Draw lines on chart
    drawTradeLines(allTrades);
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

    // Close all WebSocket connections
    if (ws) { ws.close(); ws = null; }
    if (tickerWs) { tickerWs.close(); tickerWs = null; }
    if (markPriceWs) { markPriceWs.close(); markPriceWs = null; }

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
        const url = `${CONFIG.restBaseUrl}/fapi/v1/openInterest?symbol=${CONFIG.symbol}`;
        const response = await fetch(url);
        const data = await response.json();

        const oi = parseFloat(data.openInterest);
        elements.openInterest.textContent = oi >= 1e6
            ? (oi / 1e6).toFixed(2) + 'M'
            : oi >= 1e3
                ? (oi / 1e3).toFixed(2) + 'K'
                : oi.toFixed(2);
    } catch (error) {
        console.error('Error fetching open interest:', error);
    }
}

// ===== ZigZag Algorithm =====
function calculateZigZag(data) {
    const config = getZigZagConfig();
    if (data.length < config.depth * 2) return [];

    const pivots = [];
    const deviation = config.deviation / 100;
    const depth = config.depth;

    let lastPivotIndex = 0;
    let lastPivotPrice = data[0].close;
    let lastPivotType = null; // 'high' or 'low'

    // Find initial direction
    let maxPrice = data[0].high;
    let minPrice = data[0].low;
    let maxIndex = 0;
    let minIndex = 0;

    for (let i = 1; i < Math.min(depth * 2, data.length); i++) {
        if (data[i].high > maxPrice) {
            maxPrice = data[i].high;
            maxIndex = i;
        }
        if (data[i].low < minPrice) {
            minPrice = data[i].low;
            minIndex = i;
        }
    }

    // Determine initial pivot
    if (maxIndex < minIndex) {
        // Started with a high
        pivots.push({ time: data[maxIndex].time, price: maxPrice, type: 'high', index: maxIndex });
        lastPivotIndex = maxIndex;
        lastPivotPrice = maxPrice;
        lastPivotType = 'high';
    } else {
        // Started with a low
        pivots.push({ time: data[minIndex].time, price: minPrice, type: 'low', index: minIndex });
        lastPivotIndex = minIndex;
        lastPivotPrice = minPrice;
        lastPivotType = 'low';
    }

    // Find subsequent pivots
    for (let i = lastPivotIndex + 1; i < data.length; i++) {
        const candle = data[i];

        if (lastPivotType === 'high') {
            // Looking for a low
            if (candle.low < lastPivotPrice * (1 - deviation)) {
                // Check if this is a valid low (not too close to last pivot)
                if (i - lastPivotIndex >= depth) {
                    // Find the actual lowest point in this swing
                    let lowestPrice = candle.low;
                    let lowestIndex = i;

                    for (let j = lastPivotIndex + 1; j <= i; j++) {
                        if (data[j].low < lowestPrice) {
                            lowestPrice = data[j].low;
                            lowestIndex = j;
                        }
                    }

                    pivots.push({ time: data[lowestIndex].time, price: lowestPrice, type: 'low', index: lowestIndex });
                    lastPivotIndex = lowestIndex;
                    lastPivotPrice = lowestPrice;
                    lastPivotType = 'low';
                }
            } else if (candle.high > lastPivotPrice) {
                // Update the high
                pivots[pivots.length - 1] = { time: candle.time, price: candle.high, type: 'high', index: i };
                lastPivotIndex = i;
                lastPivotPrice = candle.high;
            }
        } else {
            // Looking for a high
            if (candle.high > lastPivotPrice * (1 + deviation)) {
                // Check if this is a valid high
                if (i - lastPivotIndex >= depth) {
                    // Find the actual highest point in this swing
                    let highestPrice = candle.high;
                    let highestIndex = i;

                    for (let j = lastPivotIndex + 1; j <= i; j++) {
                        if (data[j].high > highestPrice) {
                            highestPrice = data[j].high;
                            highestIndex = j;
                        }
                    }

                    pivots.push({ time: data[highestIndex].time, price: highestPrice, type: 'high', index: highestIndex });
                    lastPivotIndex = highestIndex;
                    lastPivotPrice = highestPrice;
                    lastPivotType = 'high';
                }
            } else if (candle.low < lastPivotPrice) {
                // Update the low
                pivots[pivots.length - 1] = { time: candle.time, price: candle.low, type: 'low', index: i };
                lastPivotIndex = i;
                lastPivotPrice = candle.low;
            }
        }
    }

    return pivots;
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

function drawZigZag() {
    // Remove existing zigzag line
    if (zigzagLineSeries) {
        chart.removeSeries(zigzagLineSeries);
        zigzagLineSeries = null;
    }

    // Clear markers
    candleSeries.setMarkers([]);

    // Clear Fibonacci lines
    clearFibonacciLines();

    // Get configured timeframe from sharedConfig or default to current
    const configuredTimeframe = (sharedConfig && sharedConfig.scanner && sharedConfig.scanner.timeframe)
        ? sharedConfig.scanner.timeframe
        : '1h';

    // Only draw ZigZag and Fibonacci on the configured timeframe
    if (currentInterval !== configuredTimeframe) {
        console.log(`ZigZag/Fibonacci only available on ${configuredTimeframe.toUpperCase()} timeframe`);
        return;
    }

    // Calculate ZigZag points
    zigzagPoints = calculateZigZag(candleData);

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
    drawFibonacciForShort();
}

function drawFibonacciForShort() {
    // Fibonacci lines are already cleaned in drawZigZag
    // This function should only be called from drawZigZag when on 1h

    // Check visibility flag
    if (!showAutoFib) return;

    if (zigzagPoints.length < 2) return;

    // IMPROVED FIBONACCI LOGIC:
    // 1. Start from the rightmost HIGH
    // 2. Find ALL lows to the right of that high
    // 3. Choose the LOW with the MINIMUM value
    // 4. Check if 61.8% was touched (only candles to the right of the HIGH)
    // 5. If touched -> move to the next HIGH to the left, accumulate more lows
    // 6. Choose the LOWEST of ALL accumulated lows
    // 7. Repeat until finding a valid swing (61.8% not touched)

    let swingHigh = null;
    let swingLow = null;

    // Get all HIGH points sorted by index (position in time)
    const highPoints = zigzagPoints
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

    // Iterate through HIGHs from right to left
    for (let h = 0; h < highPoints.length; h++) {
        const currentHigh = highPoints[h];

        // Find ALL lows that are to the RIGHT of this high (after it in time)
        const lowsAfterHigh = lowPoints.filter(low => low.index > currentHigh.index);

        if (lowsAfterHigh.length === 0) {
            console.log(`No lows after High at ${currentHigh.price.toFixed(4)}`);
            continue;
        }

        // Find the LOW with the MINIMUM VALUE among all lows after this high
        const lowestLow = lowsAfterHigh.reduce((min, low) =>
            low.price < min.price ? low : min
            , lowsAfterHigh[0]);

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

        const lastCandleIndex = candleData.length - 1;
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
        // Instead of invalidating entire swing, determine minimum valid case
        // Touched 61.8% -> Cases 1,2 invalidated -> minValidCase = 3
        // Touched 78.6% -> Cases 1,2,3 invalidated -> minValidCase = 4
        let minValidCase = 1;  // Default: all cases valid

        const fib786Level = lowestLow.price + (range * 0.786);
        let hasTouched786 = false;
        const excludeFromIndex786 = Math.max(lowestLow.index + 1, lastCandleIndex - 2);

        for (let k = lowestLow.index + 1; k < excludeFromIndex786; k++) {
            if (candleData[k].high >= fib786Level) {
                hasTouched786 = true;
                console.log(`   ⚠️ 78.6% TOUCHED at index ${k} - Cases 1,2,3 invalidated, only Case 4 valid`);
                break;
            }
        }

        // Check 61.8% (use invalidatedBySecondary from config check above)
        let hasTouched618 = false;
        const excludeFromIndex618 = Math.max(lowestLow.index + 1, lastCandleIndex - 2);
        for (let k = lowestLow.index + 1; k < excludeFromIndex618; k++) {
            if (candleData[k].high >= fib618Level) {
                hasTouched618 = true;
                break;
            }
        }

        // Determine minValidCase
        if (hasTouched786) {
            minValidCase = 4;  // Only Case 4 valid
        } else if (hasTouched618) {
            minValidCase = 3;  // Cases 3 and 4 valid
            console.log(`   ⚠️ 61.8% TOUCHED - Cases 1,2 invalidated, Cases 3,4 still valid`);
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
            console.log(`   Lows considered: ${lowsAfterHigh.length}`);
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
}

function updateZigZagOnNewCandle() {
    // Recalculate ZigZag with new data
    drawZigZag();
}

// ===== Initialization =====
async function init() {
    console.log('Initializing WLD/USDT Futures Visualizer...');

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

    // Populate pair selector from config or Binance API
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
            // Si target_pairs está vacío, obtener TODOS los pares de Binance Futures
            console.log('📡 Obteniendo lista de pares desde Binance API...');
            try {
                const response = await fetch('https://fapi.binance.com/fapi/v1/exchangeInfo');
                if (response.ok) {
                    const data = await response.json();
                    pairs = data.symbols
                        .filter(s => s.status === 'TRADING' && s.quoteAsset === 'USDT')
                        .map(s => s.symbol)
                        .filter(s => !EXCLUDED_PAIRS.includes(s)) // Excluir stablecoins
                        .sort();
                    console.log(`✅ Encontrados ${pairs.length} pares USDT (sin stablecoins)`);
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
            html += `
            <div class="trade-item position clickable" data-symbol="${pos.symbol}" onclick="navigateToSymbol('${pos.symbol}')" style="cursor: pointer;">
                <div class="trade-item-header">
                    <span class="trade-symbol">${pos.symbol} <span style="font-size:0.8em; color:#aaa; margin-left:4px;">(C${pos.strategy_case || '?'})</span></span>
                    <span class="trade-type position">POSICIÓN ${pos.side}</span>
                </div>
                <div class="trade-details">
                    <span>Precio: $${(pos.entry_price || 0).toFixed(4)}</span>
                    <span>TP: $${(pos.take_profit || 0).toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span>Qty: ${(pos.quantity || 0).toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span class="trade-pnl ${pnlClass}">PnL: $${(pos.unrealized_pnl || 0).toFixed(4)}</span>
                </div>
            </div>
        `;
        });

        // Pending orders
        orders.forEach(([id, order]) => {
            html += `
            <div class="trade-item limit clickable" data-symbol="${order.symbol}" onclick="navigateToSymbol('${order.symbol}')" style="cursor: pointer;">
                <div class="trade-item-header">
                    <span class="trade-symbol">${order.symbol} <span style="font-size:0.8em; color:#aaa; margin-left:4px;">(C${order.strategy_case || '?'})</span></span>
                    <span class="trade-type limit">LÍMITE ${order.side}</span>
                </div>
                <div class="trade-details">
                    <span>Precio: $${(order.price || 0).toFixed(4)}</span>
                    <span>TP: $${(order.take_profit || 0).toFixed(4)}</span>
                </div>
                <div class="trade-details">
                    <span style="font-size: 0.9em; color: #888;">Qty: ${(order.quantity || 0).toFixed(2)}</span>
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
                    <span>${trade.symbol} <small>(C${trade.strategy_case || '?'})</small></span>
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
    let tradesPanelInterval = setInterval(loadTradesPanel, 2000);

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
            tradesPanelInterval = setInterval(loadTradesPanel, 2000);
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
let filteredTrades = [];
let currentTradeIndex = 0;
let currentCaseFilter = 'all';

function setupAnalysisMode() {
    const toggle = document.getElementById('analysisModeToggle');
    const controls = document.getElementById('analysisControls');
    const fileInput = document.getElementById('analysisFileInput');
    const navigation = document.getElementById('analysisNavigation');
    const caseButtons = document.querySelectorAll('.case-btn');
    const prevBtn = document.getElementById('prevTradeBtn');
    const nextBtn = document.getElementById('nextTradeBtn');

    if (!toggle) return;

    // Toggle modo análisis
    toggle.addEventListener('change', async () => {
        analysisMode = toggle.checked;
        controls.style.display = analysisMode ? 'flex' : 'none';
        navigation.style.display = analysisMode ? 'flex' : 'none';

        if (analysisMode) {
            // Pausar polling de trades.json normal
            if (window.pauseTradesPolling) window.pauseTradesPolling();
            // Si ya hay archivo
            if (fileInput.files.length > 0) await processAnalysisFile(fileInput.files[0]);
        } else {
            // Volver al modo normal - reanudar polling
            clearAnalysisLines();
            analysisData = null;
            filteredTrades = [];
            if (window.resumeTradesPolling) window.resumeTradesPolling();
        }
    });

    // Cambiar archivo
    fileInput.addEventListener('change', async (e) => {
        if (analysisMode && e.target.files.length > 0) {
            await processAnalysisFile(e.target.files[0]);
        }
    });

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
    prevBtn.addEventListener('click', () => navigateTrade(-1));
    nextBtn.addEventListener('click', () => navigateTrade(1));

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

        console.log(`📊 Cargado local: ${file.name}: ${analysisData.history?.length || 0} trades cerradas`);

        // Actualizar panel de operaciones con datos del archivo de análisis
        updateAnalysisTradesPanel(analysisData);

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

        showToast(`Cargado: ${analysisData.history?.length || 0} trades`);
    } catch (error) {
        console.error('Error procesando archivo JSON:', error);
        showToast('Error al leer el archivo JSON', 'error');
    }
}

// Actualizar panel de operaciones con datos del archivo de análisis (solo lectura)
function updateAnalysisTradesPanel(data) {
    const tradesList = document.getElementById('tradesList');
    const tradesCount = document.getElementById('tradesCount');
    const accountBalance = document.getElementById('accountBalance');
    const accountPnl = document.getElementById('accountPnl');
    const marginBalance = document.getElementById('marginBalance');

    if (!tradesList) return;

    // Actualizar balance del archivo
    if (accountBalance && data.balance !== undefined) {
        accountBalance.textContent = `$${data.balance.toFixed(2)}`;
    }

    // Calcular PnL total del historial
    const totalPnl = (data.history || []).reduce((sum, t) => sum + (t.pnl || 0), 0);
    if (accountPnl) {
        accountPnl.textContent = `$${totalPnl.toFixed(4)}`;
        accountPnl.className = totalPnl >= 0 ? 'pnl-value positive' : 'pnl-value negative';
    }

    if (marginBalance) {
        marginBalance.textContent = `$${(data.balance || 0).toFixed(2)}`;
    }

    // Posiciones abiertas del archivo (si existen - congeladas)
    const openPositions = Object.entries(data.open_positions || {});
    const pendingOrders = Object.entries(data.pending_orders || {});
    const allTrades = [...openPositions, ...pendingOrders];

    if (tradesCount) {
        tradesCount.textContent = allTrades.length;
    }

    // Siempre limpiar y mostrar estado del archivo de análisis
    if (allTrades.length === 0) {
        tradesList.innerHTML = `
            <div class="no-trades" style="text-align: center; padding: 10px;">
                <div>🔬 <b>Modo Análisis</b></div>
                <div style="font-size: 10px; color: #888; margin-top: 4px;">
                    ${data.history?.length || 0} trades cerradas
                </div>
                <div style="font-size: 9px; color: #666; margin-top: 4px;">
                    Sin posiciones abiertas en este archivo
                </div>
            </div>`;
        return;
    }

    let html = '';

    // Mostrar posiciones (congeladas - snapshot)
    openPositions.forEach(([id, pos]) => {
        html += `
        <div class="trade-item position" style="opacity: 0.7; cursor: default;">
            <div class="trade-item-header">
                <span class="trade-symbol">${pos.symbol} <span style="font-size:0.8em; color:#aaa;">(C${pos.strategy_case || '?'})</span></span>
                <span class="trade-type position">📷 SNAPSHOT</span>
            </div>
            <div class="trade-details">
                <span>Entry: $${(pos.entry_price || 0).toFixed(4)}</span>
                <span>TP: $${(pos.take_profit || 0).toFixed(4)}</span>
            </div>
        </div>
    `;
    });

    // Mostrar órdenes pendientes (congeladas - snapshot)
    pendingOrders.forEach(([id, order]) => {
        html += `
        <div class="trade-item limit" style="opacity: 0.7; cursor: default;">
            <div class="trade-item-header">
                <span class="trade-symbol">${order.symbol} <span style="font-size:0.8em; color:#aaa;">(C${order.strategy_case || '?'})</span></span>
                <span class="trade-type limit">📷 SNAPSHOT</span>
            </div>
            <div class="trade-details">
                <span>Precio: $${(order.price || 0).toFixed(4)}</span>
                <span>TP: $${(order.take_profit || 0).toFixed(4)}</span>
            </div>
        </div>
    `;
    });

    tradesList.innerHTML = html;
}

function applyAnalysisFilter() {
    if (!analysisData || !analysisData.history) {
        filteredTrades = [];
        updateAnalysisStats();
        updateAnalysisHistoryList();
        return;
    }

    if (currentCaseFilter === 'all') {
        filteredTrades = [...analysisData.history];
    } else {
        const caseNum = parseInt(currentCaseFilter);
        filteredTrades = analysisData.history.filter(t => t.strategy_case === caseNum);
    }

    currentTradeIndex = 0;
    updateAnalysisStats();
    updateAnalysisHistoryList();

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
    const winners = filteredTrades.filter(t => (t.pnl || 0) > 0).length;
    const totalPnl = filteredTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
    const winRate = total > 0 ? (winners / total * 100).toFixed(1) : 0;

    totalEl.textContent = total;
    winRateEl.textContent = `${winRate}%`;
    pnlEl.textContent = `$${totalPnl.toFixed(2)}`;
    pnlEl.style.color = totalPnl >= 0 ? 'var(--color-bullish)' : 'var(--color-bearish)';
}

function updateAnalysisHistoryList() {
    const historyList = document.getElementById('historyList');
    const historyCount = document.getElementById('historyCount');
    const navInfo = document.getElementById('tradeNavInfo');

    if (!historyList) return;

    historyCount.textContent = filteredTrades.length;
    navInfo.textContent = filteredTrades.length > 0 ? `${currentTradeIndex + 1}/${filteredTrades.length}` : '0/0';

    if (filteredTrades.length === 0) {
        historyList.innerHTML = '<div class="no-history">Sin trades para este filtro</div>';
        return;
    }

    let html = '';
    filteredTrades.forEach((trade, index) => {
        const pnlClass = (trade.pnl || 0) >= 0 ? 'positive' : 'negative';
        const reasonClass = trade.reason === 'TP' ? 'tp' : 'sl';
        const selectedClass = index === currentTradeIndex ? 'selected' : '';

        html += `
        <div class="history-item ${selectedClass}" data-index="${index}" onclick="selectAnalysisTrade(${index})">
            <div class="trade-row">
                <span>${trade.symbol} <small>(C${trade.strategy_case || '?'})</small></span>
                <span class="trade-reason ${reasonClass}">${trade.reason}</span>
            </div>
            <div class="trade-row">
                <span>$${(trade.entry_price || 0).toFixed(4)} → $${(trade.close_price || 0).toFixed(4)}</span>
                <span class="trade-pnl ${pnlClass}">$${(trade.pnl || 0).toFixed(4)}</span>
            </div>
        </div>
    `;
    });

    historyList.innerHTML = html;

    // Scroll al trade seleccionado
    const selectedItem = historyList.querySelector('.history-item.selected');
    if (selectedItem) {
        selectedItem.scrollIntoView({ block: 'nearest' });
    }
}

function selectAnalysisTrade(index) {
    currentTradeIndex = index;
    updateAnalysisHistoryList();
    showTradeOnChart(filteredTrades[index]);
}

function navigateTrade(direction) {
    if (filteredTrades.length === 0) return;

    currentTradeIndex += direction;
    if (currentTradeIndex < 0) currentTradeIndex = filteredTrades.length - 1;
    if (currentTradeIndex >= filteredTrades.length) currentTradeIndex = 0;

    updateAnalysisHistoryList();
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
    const entryLine = candleSeries.createPriceLine({
        price: trade.entry_price,
        color: '#ff9800',
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `ENTRY ${trade.executions?.length > 1 ? '(avg)' : ''}`
    });
    analysisLines.push(entryLine);

    // Close (cyan)
    const closeLine = candleSeries.createPriceLine({
        price: trade.close_price,
        color: '#00bcd4',
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: 'CLOSE'
    });
    analysisLines.push(closeLine);

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

    // SL (rojo)
    if (trade.stop_loss) {
        const slLine = candleSeries.createPriceLine({
            price: trade.stop_loss,
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

// Start the application
document.addEventListener('DOMContentLoaded', () => {
    init();
    setupAnalysisMode();
    setupFibonacciEditor();
});