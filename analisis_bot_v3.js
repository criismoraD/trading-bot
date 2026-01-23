// JavaScript para Simulador Pro V2 - Trading Bot

let rawData = null;
let processedTradesGlobal = [];
let limitOrdersGlobal = [];
let cancelledOrdersGlobal = [];
let selectedTradeIndex = -1;
let selectedLimitOrderIndex = -1;
let selectedCancelledOrderIndex = -1;
let currentTimeframe = '1';

// Chart variables
let chart = null;
let candleSeries = null;
let chartLines = [];
let candleDataGlobal = [];
let marketDataCache = {}; // Cache for all symbols: { "BTCUSDT": [candles...] }
let isBulkLoading = false;

let isBulkDataLoaded = false; // Flag to force "RUN" state until we have real data

async function bulkFetchCandles(trades) {
    isBulkDataLoaded = false;

    // Collect unique symbols
    const symbolSet = new Set();
    trades.forEach(t => {
        if (t.symbol) symbolSet.add(t.symbol);
    });
    const symbols = Array.from(symbolSet);

    console.log(`🔄 Loading candle data for ${symbols.length} symbols...`);

    // Show loading toast
    const loadingToast = document.createElement('div');
    loadingToast.className = "fixed bottom-4 right-4 bg-blue-600 text-white px-4 py-2 rounded shadow-lg z-50 animate-pulse";
    loadingToast.id = "loadingToast";
    loadingToast.textContent = `Cargando datos de ${symbols.length} pares...`;
    document.body.appendChild(loadingToast);

    // Try local candle service first (Flask API on port 5001)
    let useLocalService = false;
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);
        const healthCheck = await fetch('http://localhost:5001/', { signal: controller.signal });
        clearTimeout(timeoutId);
        if (healthCheck.ok) useLocalService = true;
    } catch (e) {
        console.log("ℹ️ Candle service not running, trying JSON files...");
    }

    if (useLocalService) {
        // FAST PATH: Load from local Flask API
        // BULK PATH: Carga masiva en una sola petición
        console.log("✅ Using candle service API (BULK MODE)");
        loadingToast.textContent = `Cargando desde API local (Ultra Fast)...`;

        try {
            const response = await fetch('http://localhost:5001/api/candles/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbols: symbols })
            });

            if (response.ok) {
                const data = await response.json();
                let loadedCount = 0;

                Object.entries(data).forEach(([sym, candles]) => {
                    if (candles && candles.length > 0) {
                        marketDataCache[sym] = candles;
                        loadedCount++;
                    }
                });

                console.log(`📥 Bulk load complete: ${loadedCount} symbols`);
            } else {
                console.warn(`⚠️ Bulk API error: ${response.status}. Falling back to individual.`);
                // Fallback to individual fetching if bulk fails (e.g. old server version)
                const batchSize = 5;
                for (let i = 0; i < symbols.length; i += batchSize) {
                    // ... (keep fallback logic if needed, but for now we assume update works)
                    // Simplified fallback logging
                    console.error("Bulk fetch failed, please restart candle_service.py");
                }
            }
        } catch (e) {
            console.error("Error in bulk fetch:", e);
        }
    } else {
        // FALLBACK: User removed JSON file loading requirement.
        console.log("ℹ️ Local API not running. Skipping bulk load as per user request.");
        loadingToast.textContent = "API no disponible. Use el gráfico para cargar individualmente.";
    }

    loadingToast.remove();
    isBulkDataLoaded = true;
    console.log(`✅ Loaded data for ${Object.keys(marketDataCache).length} symbols`);
}

function formatDateShort(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    // Usar UTC para consistencia con trades.json
    const day = d.getUTCDate().toString().padStart(2, '0');
    const month = (d.getUTCMonth() + 1).toString().padStart(2, '0');
    const hours = d.getUTCHours().toString().padStart(2, '0');
    const mins = d.getUTCMinutes().toString().padStart(2, '0');
    return `${day}/${month} ${hours}:${mins}`;
}

// ========== MODE SWITCH ==========
function switchMode() {
    const isVisor = document.getElementById('modeSwitch').checked;
    if (isVisor) {
        window.location.href = 'index.html';
    }
}

// ========== SLIDER FUNCTIONS ==========
function updateSliderValue(slider) {
    const valSpan = document.getElementById(slider.id + '_val');
    if (valSpan) {
        // Mostrar decimales para filtros de ganancia
        if (slider.id.includes('filter')) {
            valSpan.textContent = parseFloat(slider.value).toFixed(1);
        } else {
            valSpan.textContent = slider.value;
        }
    }
    // Actualizar líneas del gráfico en tiempo real
    updateChartLinesFromSlider();
}

function updateChartLinesFromSlider() {
    // Solo actualizar si hay un trade seleccionado
    if (selectedTradeIndex < 0 || !processedTradesGlobal[selectedTradeIndex]) return;

    const item = processedTradesGlobal[selectedTradeIndex];
    const t = item.t;
    const cID = t.strategy_case;

    // Recalcular TP/SL con los nuevos valores de los sliders
    const settings = getCaseSettings(cID);
    const fibRange = (t.fib_high || 0) - (t.fib_low || 0);
    const newTpPrice = (t.fib_low || 0) + fibRange * settings.tp;
    const newSlPrice = settings.sl > 0 ? (t.fib_low || 0) + fibRange * settings.sl : Infinity;

    // Actualizar info panel
    document.getElementById('infoTP').textContent = '$' + newTpPrice.toFixed(4);
    document.getElementById('infoSL').textContent = newSlPrice === Infinity ? '∞' : '$' + newSlPrice.toFixed(4);

    // Actualizar item temporal para redibujar
    const updatedItem = { ...item, tpPrice: newTpPrice, slPrice: newSlPrice };
    drawTradeLines(updatedItem);
    updateChartMarkers(item, newTpPrice, newSlPrice);
}

function adjustSlider(sliderId, delta) {
    const slider = document.getElementById(sliderId);
    if (!slider) return;
    const step = parseFloat(slider.step) || 1;
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    let newValue = parseFloat(slider.value) + (delta * step);
    newValue = Math.max(min, Math.min(max, newValue));
    slider.value = newValue;
    updateSliderValue(slider);
    runSimulation();
}

function resetSlider(sliderId, defaultVal) {
    const slider = document.getElementById(sliderId);
    if (!slider) return;

    // Intentar obtener valor desde sharedConfig si existe
    let finalVal = defaultVal;
    if (sharedConfig && sharedConfig.strategies) {
        // Mapear sliderId a config key
        // IDs son tp_c1, sl_c1, tp_c1pp, sl_c1pp, etc.
        const parts = sliderId.split('_'); // ['tp', 'c1']
        if (parts.length === 2) {
            const type = parts[0]; // tp or sl
            const caseName = parts[1]; // c1, c1pp, c2, etc

            if (sharedConfig.strategies[caseName] && sharedConfig.strategies[caseName][type]) {
                // Config guarda decimales (0.51), slider usa enteros (51)
                finalVal = Math.round(sharedConfig.strategies[caseName][type] * 100);
            }
        }
    }

    slider.value = finalVal;
    updateSliderValue(slider);
    runSimulation();
}

// ========== DOWNLOAD FROM MV ==========
async function downloadFromMV() {
    const btn = document.querySelector('.download-mv-btn');
    const originalText = btn.innerHTML;

    btn.classList.add('loading');
    btn.innerHTML = '<span>⏳</span> Descargando...';

    try {
        // Descargar trades.json desde el mismo servidor web
        // El web_server.py sirve los archivos del directorio
        const response = await fetch('trades.json?nocache=' + Date.now());

        if (!response.ok) {
            throw new Error(`Error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        // Procesar los datos
        rawData = data;
        document.getElementById('dashboard').classList.remove('hidden');
        document.getElementById('refreshBtn').classList.remove('hidden');
        runSimulation();

        btn.innerHTML = '<span>✅</span> Cargado!';
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.classList.remove('loading');
        }, 2000);

    } catch (error) {
        console.error('Error descargando trades.json:', error);
        btn.classList.remove('loading');
        btn.innerHTML = '<span>❌</span> Error';

        setTimeout(() => {
            btn.innerHTML = originalText;
        }, 2000);

        // Mostrar mensaje de error
        alert(`No se pudo cargar trades.json.\n\nAsegúrate de:\n1. Que el archivo trades.json exista\n2. Que estés accediendo desde el servidor web (no abriendo el HTML directamente)\n\nError: ${error.message}`);
    }
}

function toggleSL(caseNum) {
    const suffix = `c${caseNum}`;
    const checkbox = document.getElementById(`sl_enabled_${suffix}`);
    const slider = document.getElementById(`sl_${suffix}`);
    const valSpan = document.getElementById(`sl_${suffix}_val`);
    const container = document.getElementById(`sl_container_${suffix}`);

    if (!checkbox || !slider) return;

    if (checkbox.checked) {
        slider.disabled = false;
        slider.classList.remove('sl-disabled');
        if (container) container.querySelectorAll('label, span').forEach(el => el.classList.remove('sl-disabled'));
        valSpan.textContent = slider.value;
    } else {
        slider.disabled = true;
        slider.classList.add('sl-disabled');
        if (container) container.querySelectorAll('label, span').forEach(el => el.classList.add('sl-disabled'));
        valSpan.textContent = 'OFF';
    }

    // Actualizar líneas del gráfico
    updateChartLinesFromSlider();
}

// ========== FILTER/SORT ==========
let casePriority = 0;
let sortMode = 'none';
let caseFilters = new Set(); // Para selección múltiple de casos

function toggleCaseFilter(caseNum) {
    if (caseNum === 0) {
        // "Todos" - limpiar todos los filtros
        caseFilters.clear();
        document.querySelectorAll('#btnCase1, #btnCase3, #btnCase4').forEach(btn => btn.classList.remove('active'));  // C2 eliminado
        document.getElementById('btnCaseAll')?.classList.add('active');
    } else {
        // Toggle individual case
        document.getElementById('btnCaseAll')?.classList.remove('active');
        const btnId = `btnCase${caseNum}`;
        const btn = document.getElementById(btnId);

        if (caseFilters.has(caseNum)) {
            caseFilters.delete(caseNum);
            btn?.classList.remove('active');
        } else {
            caseFilters.add(caseNum);
            btn?.classList.add('active');
        }

        // Si no hay ningún filtro seleccionado, activar "Todos"
        if (caseFilters.size === 0) {
            document.getElementById('btnCaseAll')?.classList.add('active');
        }
    }
    runSimulation();
}

function setCasePriority(caseNum) {
    casePriority = caseNum;
    document.querySelectorAll('#btnCaseAll, #btnCase1, #btnCase11, #btnCase3, #btnCase4').forEach(btn => btn.classList.remove('active'));  // C2 eliminado
    const btnId = caseNum === 0 ? 'btnCaseAll' : `btnCase${caseNum}`;
    document.getElementById(btnId)?.classList.add('active');
    runSimulation();
}

function setSortMode(mode) {
    sortMode = mode;
    document.querySelectorAll('[id^="btnSort"]').forEach(btn => btn.classList.remove('active'));
    const btnMap = {
        'none': 'btnSortNone',
        'pnl_real_desc': 'btnSortPnlRealDesc',
        'pnl_real_asc': 'btnSortPnlRealAsc',
        'pnl_flot_desc': 'btnSortPnlFlotDesc',
        'pnl_flot_asc': 'btnSortPnlFlotAsc',
        'fib_desc': 'btnSortFibDesc'
    };
    document.getElementById(btnMap[mode])?.classList.add('active');
    runSimulation();
}

function ignoreAll() {
    ['C1', 'C3', 'C4'].forEach(c => {  // C2 eliminado
        const cb = document.getElementById(`ignore${c}`);
        if (cb) cb.checked = true;
    });
    runSimulation();
}

function unignoreAll() {
    ['C1', 'C11', 'C3', 'C4'].forEach(c => {  // C2 eliminado
        const cb = document.getElementById(`ignore${c}`);
        if (cb) cb.checked = false;
    });
    runSimulation();
}

async function resetDefaults() {
    // Recargar config desde disco si es posible
    await loadSharedConfig();

    // Reset SL toggles
    [1, 3, 4].forEach(c => {  // C2 eliminado
        const cb = document.getElementById(`sl_enabled_c${c}`);
        if (cb) { cb.checked = true; toggleSL(c); }
    });

    // Valores por defecto (se sobreescriben con shared_config si está disponible)
    let defaults = {
        'tp_c1': 51, 'sl_c1': 67,
        'tp_c3': 50, 'sl_c3': 105,  // C2 eliminado
        'tp_c4': 50, 'sl_c4': 105,
        'guide_line': 65
    };

    // Si hay configuración compartida, usarla
    if (sharedConfig && sharedConfig.strategies) {
        const s = sharedConfig.strategies;
        if (s.c1) { defaults['tp_c1'] = Math.round(s.c1.tp * 100); defaults['sl_c1'] = Math.round(s.c1.sl * 100); }
        // C2 eliminado
        if (s.c3) { defaults['tp_c3'] = Math.round(s.c3.tp * 100); defaults['sl_c3'] = Math.round(s.c3.sl * 100); }
        if (s.c4) { defaults['tp_c4'] = Math.round(s.c4.tp * 100); defaults['sl_c4'] = Math.round(s.c4.sl * 100); }
    }

    Object.entries(defaults).forEach(([id, val]) => {
        const el = document.getElementById(id);
        if (el) { el.value = val; updateSliderValue(el); }
    });

    // Unignore all
    unignoreAll();
    setCasePriority(0);
    setSortMode('none');
    runSimulation();
}

// ========== DATA LOADING ==========
let sharedConfig = null;

async function loadSharedConfig() {
    try {
        const response = await fetch('/shared_config.json?nocache=' + Date.now(), { cache: 'no-store' });
        if (response.ok) {
            sharedConfig = await response.json();
            console.log('✅ Configuración compartida cargada');
            // Aplicar configuración automáticamente al inicio
            applySharedConfig();
        }
    } catch (e) {
        console.log('⚠️ No se pudo cargar shared_config.json, usando valores por defecto');
    }
}

function applySharedConfig() {
    if (!sharedConfig || !sharedConfig.strategies) return;

    const strategies = sharedConfig.strategies;
    const mapping = {
        'c1': { tp: 'tp_c1', sl: 'sl_c1' },
        // 'c2' eliminado
        'c3': { tp: 'tp_c3', sl: 'sl_c3' },
        'c4': { tp: 'tp_c4', sl: 'sl_c4' }
    };

    for (const [key, ids] of Object.entries(mapping)) {
        const config = strategies[key];
        if (config) {
            // TP (convertir de decimal a porcentaje)
            const tpEl = document.getElementById(ids.tp);
            if (tpEl && config.tp) {
                tpEl.value = Math.round(config.tp * 100);
                updateSliderValue(tpEl);
            }
            // SL (convertir de decimal a porcentaje)
            const slEl = document.getElementById(ids.sl);
            if (slEl && config.sl) {
                slEl.value = Math.round(config.sl * 100);
                updateSliderValue(slEl);
            }
        }
    }
}

async function autoLoadTradesJson() {
    const filesToTry = ['/trades.json'];
    for (const fileName of filesToTry) {
        try {
            const response = await fetch(fileName + '?nocache=' + Date.now(), { cache: 'no-store' });
            if (response.ok) {
                rawData = await response.json();
                document.getElementById('dashboard').classList.remove('hidden');
                document.getElementById('refreshBtn').classList.remove('hidden');

                // Extract all trades for bulk fetch
                let allTradesForFetch = [];
                if (rawData.history) allTradesForFetch = allTradesForFetch.concat(rawData.history);
                if (rawData.open_positions) allTradesForFetch = allTradesForFetch.concat(Object.values(rawData.open_positions));
                if (rawData.pending_orders) allTradesForFetch = allTradesForFetch.concat(Object.values(rawData.pending_orders));

                // 1. Initial simulation (quick render with limited info)
                runSimulation();

                // 2. Fetch all candles in background then re-simulate
                await bulkFetchCandles(allTradesForFetch);

                // 3. Re-run simulation with full market data
                runSimulation();

                console.log('✅ Cargado:', fileName);
                return;
            }
        } catch (e) { console.error("Error loading/processing " + fileName, e); }
    }
}

async function manualRefresh() {
    const btn = document.getElementById('refreshBtn');
    btn.innerHTML = '⏳...';
    btn.disabled = true;
    await autoLoadTradesJson();
    btn.innerHTML = '🔄 Actualizar';
    btn.disabled = false;
}

document.getElementById('fileInput').addEventListener('change', function (e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function (e) {
        try {
            rawData = JSON.parse(e.target.result);
            document.getElementById('dashboard').classList.remove('hidden');
            runSimulation();
        } catch (err) { alert("Error al leer JSON"); }
    };
    reader.readAsText(file);
});

// ========== CASE SETTINGS ==========
function getCaseSettings(caseId) {
    let c = [1, 3, 4].includes(caseId) ? caseId : 1;  // C2 eliminado
    const limits = {
        1: { tpMax: 0.61, slMin: 0.62 },
        // 2 eliminado
        3: { tpMax: 0.78, slMin: 0.79 },
        4: { tpMax: 0.90, slMin: 0.90 }
    };
    const limit = limits[c];

    let tp = (parseFloat(document.getElementById(`tp_c${c}`)?.value) || 50) / 100;
    const slEnabled = document.getElementById(`sl_enabled_c${c}`)?.checked ?? true;
    let sl = slEnabled ? (parseFloat(document.getElementById(`sl_c${c}`)?.value) || 100) / 100 : 0;

    if (tp > limit.tpMax) tp = limit.tpMax;
    if (sl > 0 && sl < limit.slMin) sl = limit.slMin;

    return { tp, sl };
}

// ========== SIMULATION ==========
function runSimulation() {
    if (!rawData) return;

    const ignoredCases = {
        1: document.getElementById('ignoreC1')?.checked || false,
        // 2 eliminado
        3: document.getElementById('ignoreC3')?.checked || false,
        4: document.getElementById('ignoreC4')?.checked || false
    };

    let stats = {
        realized: 0, floating: 0,
        wins: 0, losses: 0, closedTrades: 0, openTrades: 0,
        grossWin: 0, grossLoss: 0, totalTrades: 0
    };

    // Filtro de texto (búsqueda) - AHORA SOLO VISUAL
    const searchInput = document.getElementById('searchInput');
    const searchQuery = searchInput ? searchInput.value.toUpperCase() : '';

    // Configuración general
    // (Filtros de ganancia eliminados)

    // Settings actuales para C1 (para la nueva regla)
    const c1Settings = getCaseSettings(1);

    let caseStats = {};
    [1, 3, 4].forEach(i => {  // C2 eliminado
        caseStats[i] = { total: 0, realizedPnl: 0, floatingPnl: 0, wins: 0, losses: 0, grossWin: 0, grossLoss: 0 };
    });

    // Unify trades
    let allTrades = [];
    if (rawData.history) rawData.history.forEach(t => {
        t._src = 'HIST';
        // LOGIC CHANGED: We removed pre/post close fields.
        // Default to entry/close prices. 
        // Detailed simulation now happens via Chart Candles (simulateTradePath)

        t._max = Math.max(t.entry_price, t.close_price || t.entry_price);
        t._min = Math.min(t.entry_price, t.close_price || t.entry_price);
        allTrades.push(t);
    });
    if (rawData.open_positions) Object.values(rawData.open_positions).forEach(t => {
        t._src = 'OPEN';
        const effectivePrice = (t.current_price && t.current_price > 0) ? t.current_price : t.entry_price;
        t._max = Math.max(t.entry_price, effectivePrice);
        t._min = Math.min(t.entry_price, effectivePrice);
        if (!t.fib_high) t.fib_high = t.entry_price * 1.05;
        if (!t.fib_low) t.fib_low = t.entry_price * 0.95;
        allTrades.push(t);
    });

    // Process trades
    let processedTrades = [];

    allTrades.forEach(t => {
        stats.totalTrades++;
        let cID = t.strategy_case || 1;

        // Determinar si está ignorado por checkbox
        let isIgnored = ignoredCases[cID] || false;
        if (t.isCorrected) isIgnored = true;

        const s = getCaseSettings(cID);
        const range = t.fib_high - t.fib_low;
        const tpPrice = t.fib_low + (range * s.tp);
        let slPrice = s.sl > 0 ? t.fib_low + (range * s.sl) : Infinity;

        // CALCULAR NIVEL FIB DE ENTRADA
        let fibEntryLevel = 0;
        if (t.fib_level) {
            fibEntryLevel = parseFloat(t.fib_level);
        } else if (t.fib_high && t.fib_low && t.entry_price) {
            if (range > 0) {
                fibEntryLevel = (t.entry_price - t.fib_low) / range;
            }
        }

        // REGLA GENERAL (Todos los casos): Si Fib Entry >= SL Slider, ignorar
        // Esto filtra trades que entraron en una zona peor que el SL configurado
        if (s.sl > 0) {
            if (fibEntryLevel >= s.sl) {
                isIgnored = true;
            }
        }

        let status = "", rPnl = 0, fPnl = 0, css = "";
        let isClosed = false;

        const slIsValid = slPrice > t.entry_price;
        let hitSL = (s.sl > 0) && slIsValid && (t._max >= slPrice);

        const originalReason = (t.reason || '').toUpperCase();

        // ELIMINADO: Fallback a min/max price guardados.
        // Ahora dependemos 100% de la simulación de velas.
        // OVERRIDE: CANDLE SIMULATION (High Fidelity)
        // Use bulk cache if available (for all trades), or global chart data for selected
        let candlesForSim = marketDataCache[t.symbol];

        const potProfit = (t.entry_price - tpPrice) * t.quantity;

        // Fallback to active chart data if selected and matches symbol (and no cache yet)
        if (!candlesForSim && selectedTradeIndex >= 0 && processedTradesGlobal[selectedTradeIndex] && processedTradesGlobal[selectedTradeIndex].t === t) {
            const chartSym = document.getElementById('chartSymbol')?.textContent;
            if (chartSym && (chartSym === t.symbol || chartSym.startsWith(t.symbol))) {
                candlesForSim = candleDataGlobal;
            }
        }

        if (candlesForSim) {
            const simResult = simulateTradePath(t, tpPrice, slPrice, candlesForSim);
            if (simResult) {
                if (simResult.status.includes('SL')) {
                    status = "SL ❌"; rPnl = (t.entry_price - slPrice) * t.quantity; isClosed = true; css = "bg-loss"; fPnl = 0; hitSL = true;
                } else if (simResult.status.includes('TP')) {
                    status = "TP ✅"; rPnl = potProfit; isClosed = true; css = "bg-win"; fPnl = 0;
                } else {
                    status = "RUN ⏳";
                    fPnl = (t.entry_price - simResult.lastPrice) * t.quantity;
                    css = "bg-run"; isClosed = false; rPnl = 0;
                }
            } else {
                status = "ERROR ⚠️";
            }
        } else {
            status = isBulkDataLoaded ? "NO DATA ⚠️" : "CARGANDO... ⏳";
            css = isBulkDataLoaded ? "bg-gray-800 text-gray-500" : "bg-run";
            fPnl = 0; rPnl = 0; isClosed = false;
        }

        // NO calcular stats aquí - se hará después de los filtros

        processedTrades.push({ t, cID, s, tpPrice, slPrice, status, css, rPnl, fPnl, isIgnored, isClosed, fibEntryLevel, hitSL, originalReason });
    });

    // Filter by case (selección múltiple)
    let displayTrades = processedTrades;
    if (caseFilters.size > 0) {
        displayTrades = processedTrades.filter(p => caseFilters.has(p.cID));
    }

    // Filter by text (buscar par)
    const filterText = (document.getElementById('filterText')?.value || '').toUpperCase().trim();
    if (filterText) {
        displayTrades = displayTrades.filter(p => p.t.symbol.toUpperCase().includes(filterText));
    }

    // Modificación: Filtros de ganancia ELIMINADOS por solicitud del usuario.
    // No se aplica ningún filtro de profit.

    // Ahora calcular stats DESPUÉS de todos los filtros
    // Solo incluir trades que NO están ignorados NI filtrados por ganancia
    displayTrades.forEach(p => {
        const { cID, rPnl, fPnl, isIgnored, isClosed, filteredByProfit } = p;

        stats.totalTrades++;

        if (!isIgnored) {
            caseStats[cID].total++;
            if (isClosed) {
                stats.closedTrades++; stats.realized += rPnl;
                caseStats[cID].realizedPnl += rPnl;
                if (rPnl > 0) {
                    stats.wins++; stats.grossWin += rPnl;
                    caseStats[cID].wins++;
                    caseStats[cID].grossWin += rPnl;
                }
                else {
                    stats.losses++; stats.grossLoss += Math.abs(rPnl);
                    caseStats[cID].losses++;
                    caseStats[cID].grossLoss += Math.abs(rPnl);
                }
            } else {
                stats.openTrades++; stats.floating += fPnl;
                caseStats[cID].floatingPnl += fPnl;
            }
        }
    });

    // Filtrar para visualización (excluir trades filtrados por ganancia)
    // displayTrades = displayTrades.filter(p => !p.filteredByProfit); // YA NO SE USA, ahora son isIgnored

    // Sort
    if (sortMode === 'pnl_real_desc') {
        displayTrades.sort((a, b) => b.rPnl - a.rPnl);
    } else if (sortMode === 'pnl_real_asc') {
        displayTrades.sort((a, b) => a.rPnl - b.rPnl);
    } else if (sortMode === 'pnl_flot_desc') {
        displayTrades.sort((a, b) => b.fPnl - a.fPnl);
    } else if (sortMode === 'pnl_flot_asc') {
        displayTrades.sort((a, b) => a.fPnl - b.fPnl);
    } else if (sortMode === 'fib_desc') {
        displayTrades.sort((a, b) => (b.fibEntryLevel || 0) - (a.fibEntryLevel || 0));
    }

    processedTradesGlobal = displayTrades;

    // Separar trades ignorados de no ignorados
    // APLICAR FILTRO DE BÚSQUEDA AQUÍ (SOLO VISUAL)
    const activeTrades = displayTrades.filter(p => !p.isIgnored && (!searchQuery || p.t.symbol.includes(searchQuery)));
    const ignoredTrades = displayTrades.filter(p => p.isIgnored && (!searchQuery || p.t.symbol.includes(searchQuery)));

    // Render tabla principal (solo NO ignorados)
    const tbody = document.getElementById('tradesTableBody');
    tbody.innerHTML = '';

    activeTrades.forEach((item) => {
        // Obtener índice real en processedTradesGlobal
        const idx = processedTradesGlobal.indexOf(item);
        const { t, cID, status, css, rPnl, fPnl, tpPrice, slPrice, hitSL, originalReason } = item;
        const caseDisplay = `C${cID}`;
        const rowClass = `row-c${cID}`;
        const selectedClass = idx === selectedTradeIndex ? 'selected' : '';

        // Colors for PnL columns
        const rPnlColor = rPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
        const fPnlColor = fPnl >= 0 ? 'var(--accent-blue)' : 'var(--accent-orange)';

        // R:R calculation
        let rrDisplay = '-';
        let rrColor = 'var(--text-muted)';
        if (slPrice !== Infinity && slPrice > t.entry_price) {
            const reward = t.entry_price - tpPrice;
            const risk = slPrice - t.entry_price;
            if (risk > 0) {
                const rr = reward / risk;
                rrDisplay = '1:' + rr.toFixed(1);
                rrColor = rr >= 1 ? 'var(--accent-green)' : (rr >= 0.5 ? 'var(--accent-yellow)' : 'var(--accent-red)');
            }
        }

        // Debug info for tooltip
        let rowTitle = '';
        try {
            const localRange = (t.fib_high && t.fib_low) ? (t.fib_high - t.fib_low) : 0;
            const maxFib = localRange > 0 ? ((t._max - t.fib_low) / localRange * 100).toFixed(1) + '%' : '-';
            rowTitle = `Max Reached: $${(t._max || 0).toFixed(4)} (${maxFib})
SL Trigger: $${slPrice !== Infinity ? slPrice.toFixed(4) : '∞'}
Status: ${status} (HitSL: ${hitSL})
Original Reason: '${originalReason}'`;
        } catch (e) { console.error("Tooltip calc error", e); }

        tbody.innerHTML += `
                    <tr class="${rowClass} ${selectedClass}" onclick="selectTrade(${idx})" data-idx="${idx}" title="${rowTitle}">
                        <td class="py-1 px-2">
                            <div class="font-bold text-xs flex items-center gap-1">
                                ${t.symbol} 
                                ${t.isCorrected ? '<span title="Data Corrupta Corregida (Spike ignorado)">⚠️</span>' : ''}
                            </div>
                            <div class="text-[9px] text-slate-400 font-normal leading-tight">
                                ${(() => {
                // Fecha
                let dateStr = t.entry_time;
                if (!dateStr && t.executions && t.executions.length > 0) {
                    dateStr = t.executions[0].time;
                }
                const dateDisplay = formatDateShort(dateStr);

                // Fib Entry Calc
                let fibDisplay = '-';
                if (t.fib_level) {
                    fibDisplay = (parseFloat(t.fib_level) * 100).toFixed(1);
                } else if (t.fib_high && t.fib_low && t.entry_price) {
                    const range = t.fib_high - t.fib_low;
                    if (range > 0) {
                        const fibVal = (t.entry_price - t.fib_low) / range;
                        fibDisplay = (fibVal * 100).toFixed(1);
                    }
                }

                return `${dateDisplay} <span style="color: var(--text-muted)">|</span> Fib: ${fibDisplay}`;
            })()}
                            </div>
                        </td>
                        <td class="text-center py-1"><span class="badge text-[9px]" style="padding: 2px 6px;">${caseDisplay}</span></td>
                        <td class="text-center py-1"><span class="badge ${css} text-[9px]" style="padding: 2px 6px;">${status}</span></td>
                        <td class="text-right font-mono font-bold text-xs py-1" style="color: ${rPnlColor}">${rPnl !== 0 ? '$' + rPnl.toFixed(2) : '-'}</td>
                        <td class="text-right font-mono text-xs py-1" style="color: ${fPnlColor}">${fPnl !== 0 ? '$' + fPnl.toFixed(2) : '-'}</td>
                        <td class="text-center font-mono text-xs py-1" style="color: ${rrColor}">${rrDisplay}</td>
                    </tr>
                `;
    });

    // Render lista de ignorados
    const ignoredSection = document.getElementById('ignoredTradesSection');
    const ignoredBody = document.getElementById('ignoredTradesBody');
    const ignoredCount = document.getElementById('ignoredCount');

    if (ignoredTrades.length > 0) {
        ignoredSection.classList.remove('hidden');
        ignoredCount.textContent = `(${ignoredTrades.length})`;
        ignoredBody.innerHTML = '';

        ignoredTrades.forEach((item) => {
            const idx = processedTradesGlobal.indexOf(item); // Obtener índice para el gráfico
            const { t, cID, status, css, rPnl, fPnl } = item;
            const caseDisplay = cID == 11 ? 'C1++' : `C${cID}`;
            const rowClass = cID == 11 ? 'row-c11' : `row-c${cID}`;
            const rPnlColor = rPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            const fPnlColor = fPnl >= 0 ? 'var(--accent-blue)' : 'var(--accent-orange)';

            ignoredBody.innerHTML += `
                        <tr class="${rowClass} cursor-pointer hover:brightness-110" onclick="selectTrade(${idx})">
                            <td class="py-1 px-2 font-bold text-xs flex items-center gap-1">
                                ${t.symbol}
                                ${t.isCorrected ? '<span title="Data Corrupta (Ignorado)">⚠️</span>' : ''}
                            </td>
                            <td class="text-center py-1"><span class="badge text-[9px]" style="padding: 2px 6px;">${caseDisplay}</span></td>
                            <td class="text-center py-1"><span class="badge ${css} text-[9px]" style="padding: 2px 6px;">${status}</span></td>
                            <td class="text-right font-mono text-xs py-1" style="color: ${rPnlColor}">${rPnl !== 0 ? '$' + rPnl.toFixed(2) : '-'}</td>
                            <td class="text-right font-mono text-xs py-1" style="color: ${fPnlColor}">${fPnl !== 0 ? '$' + fPnl.toFixed(2) : '-'}</td>
                        </tr>
                    `;
        });
    } else {
        ignoredSection.classList.add('hidden');
    }

    // Render lista de Limit Orders
    const limitSection = document.getElementById('limitOrdersSection');
    const limitBody = document.getElementById('limitOrdersBody');
    const limitCount = document.getElementById('limitCount');
    // Populate global array with filters
    limitOrdersGlobal = [];
    if (rawData.pending_orders) {
        Object.values(rawData.pending_orders).forEach(o => {
            // 1. Filter by Case
            const cID = o.strategy_case || 1;
            if (caseFilters.size > 0 && !caseFilters.has(cID)) {
                return; // Skip logic
            }
            // 2. Filter by Search Text
            if (filterText && !o.symbol.toUpperCase().includes(filterText)) {
                return; // Skip logic
            }
            limitOrdersGlobal.push(o);
        });
    }

    if (limitOrdersGlobal.length > 0) {
        limitSection.classList.remove('hidden');
        limitCount.textContent = `(${limitOrdersGlobal.length})`;
        limitBody.innerHTML = '';

        limitOrdersGlobal.forEach((o, idx) => {
            const cID = o.strategy_case || 1;
            const caseDisplay = cID == 11 ? 'C1++' : `C${cID}`;
            const rowClass = cID == 11 ? 'row-c11' : `row-c${cID}`;

            // Distancia al precio actual (si existe)
            let distDisplay = '-';
            if (o.current_price && o.price) {
                const dist = ((o.price - o.current_price) / o.current_price) * 100;
                distDisplay = dist.toFixed(2) + '%';
            } else if (rawData.open_positions && rawData.open_positions[0]?.current_price && o.price) {
                // Fallback: usar precio de primera posicion si hay
                const curr = rawData.open_positions[0].current_price;
                const dist = ((o.price - curr) / curr) * 100;
                distDisplay = dist.toFixed(2) + '%';
            }

            // Format created_at (use UTC)
            let createdDisplay = '-';
            if (o.created_at) {
                const d = new Date(o.created_at);
                if (!isNaN(d.getTime())) {
                    const day = d.getUTCDate().toString().padStart(2, '0');
                    const month = (d.getUTCMonth() + 1).toString().padStart(2, '0');
                    const hours = d.getUTCHours().toString().padStart(2, '0');
                    const mins = d.getUTCMinutes().toString().padStart(2, '0');
                    createdDisplay = `${day}/${month} ${hours}:${mins}`;
                }
            }

            // Format creation_fib_level
            let fibDisplay = '-';
            if (o.creation_fib_level != null) {
                fibDisplay = (o.creation_fib_level * 100).toFixed(1) + '%';
            }

            limitBody.innerHTML += `
                        <tr class="${rowClass} cursor-pointer hover:brightness-110" onclick="selectLimitOrder(${idx})">
                            <td class="py-1 px-2 font-bold text-xs">${o.symbol}</td>
                            <td class="text-center py-1"><span class="badge text-[9px]" style="padding: 2px 6px;">${caseDisplay}</span></td>
                            <td class="text-center py-1 text-[9px] text-slate-400">${createdDisplay}</td>
                            <td class="text-center py-1 text-[9px] text-purple-400 font-mono">${fibDisplay}</td>
                            <td class="text-right font-mono text-xs py-1 text-amber-400">$${o.price.toFixed(4)}</td>
                            <td class="text-right font-mono text-xs py-1 text-slate-400">${distDisplay}</td>
                        </tr>
                    `;
        });
    } else {
        limitSection.classList.add('hidden');
    }

    // Render lista de Cancelled Orders
    const cancelledSection = document.getElementById('cancelledOrdersSection');
    const cancelledBody = document.getElementById('cancelledOrdersBody');
    const cancelledCount = document.getElementById('cancelledCount');

    cancelledOrdersGlobal = [];
    if (rawData.cancelled_history && rawData.cancelled_history.length > 0) {
        cancelledOrdersGlobal = rawData.cancelled_history;
    }

    if (cancelledOrdersGlobal.length > 0) {
        cancelledSection.classList.remove('hidden');
        cancelledCount.textContent = `(${cancelledOrdersGlobal.length})`;
        cancelledBody.innerHTML = '';

        cancelledOrdersGlobal.forEach((o, idx) => {
            const cID = o.strategy_case || 1;
            const caseDisplay = cID == 11 ? 'C1++' : `C${cID}`;
            const rowClass = cID == 11 ? 'row-c11' : `row-c${cID}`;

            // Format created_at (UTC)
            let createdDisplay = '-';
            if (o.created_at) {
                const d = new Date(o.created_at);
                if (!isNaN(d.getTime())) {
                    const day = d.getUTCDate().toString().padStart(2, '0');
                    const month = (d.getUTCMonth() + 1).toString().padStart(2, '0');
                    const hours = d.getUTCHours().toString().padStart(2, '0');
                    const mins = d.getUTCMinutes().toString().padStart(2, '0');
                    createdDisplay = `${day}/${month} ${hours}:${mins}`;
                }
            }

            // Format cancelled_at (UTC)
            let cancelledDisplay = '-';
            if (o.cancelled_at) {
                const d = new Date(o.cancelled_at);
                if (!isNaN(d.getTime())) {
                    const day = d.getUTCDate().toString().padStart(2, '0');
                    const month = (d.getUTCMonth() + 1).toString().padStart(2, '0');
                    const hours = d.getUTCHours().toString().padStart(2, '0');
                    const mins = d.getUTCMinutes().toString().padStart(2, '0');
                    cancelledDisplay = `${day}/${month} ${hours}:${mins}`;
                }
            }

            // Reason
            const reasonDisplay = o.cancel_reason || '-';

            cancelledBody.innerHTML += `
                        <tr class="${rowClass} opacity-60 cursor-pointer hover:brightness-110" onclick="selectCancelledOrder(${idx})">
                            <td class="py-1 px-2 font-bold text-xs">${o.symbol}</td>
                            <td class="text-center py-1"><span class="badge text-[9px]" style="padding: 2px 6px;">${caseDisplay}</span></td>
                            <td class="text-center py-1 text-[9px] text-slate-400">${createdDisplay}</td>
                            <td class="text-center py-1 text-[9px] text-red-400">${cancelledDisplay}</td>
                            <td class="text-left py-1 text-[9px] text-slate-500 truncate max-w-[150px]" title="${reasonDisplay}">${reasonDisplay}</td>
                        </tr>
                    `;
        });
    } else {
        cancelledSection.classList.add('hidden');
    }

    // Update KPIs
    const gWR = stats.closedTrades > 0 ? ((stats.wins / stats.closedTrades) * 100).toFixed(1) + '%' : '0%';
    const gPF = stats.grossLoss > 0 ? (stats.grossWin / stats.grossLoss).toFixed(2) : (stats.grossWin > 0 ? '∞' : '0.00');
    const gTotal = stats.realized + stats.floating;

    document.getElementById('kpi_realized_pnl').innerText = `$${stats.realized.toFixed(2)}`;
    document.getElementById('kpi_realized_pnl').className = `kpi-value text-base mt-0.5 ${stats.realized >= 0 ? 'positive' : 'negative'}`;
    document.getElementById('kpi_floating_pnl').innerText = `$${stats.floating.toFixed(2)}`;
    document.getElementById('kpi_floating_pnl').style.color = stats.floating >= 0 ? 'var(--accent-blue)' : 'var(--accent-orange)';
    document.getElementById('kpi_total_pnl').innerText = `$${gTotal.toFixed(2)}`;
    document.getElementById('kpi_total_pnl').className = `kpi-value text-base mt-0.5 ${gTotal >= 0 ? 'positive' : 'negative'}`;
    document.getElementById('kpi_winrate').innerText = gWR;
    document.getElementById('kpi_trades_info').innerText = `${stats.closedTrades}/${stats.totalTrades}`;
    document.getElementById('kpi_pf').innerText = gPF;

    // Update Case Performance Table
    updateCasePerformanceTable(caseStats);

    document.getElementById('filter_stats_text').innerText = `Total: ${activeTrades.length} trades`;
}

function updateCasePerformanceTable(caseStats) {
    const tbody = document.getElementById('casePerformanceTableBody');
    if (!tbody) return;

    tbody.innerHTML = '';
    const caseColors = { 1: 'var(--accent-blue)', 2: 'var(--accent-yellow)', 3: 'var(--accent-orange)', 4: 'var(--accent-red)' };
    const caseNames = { 1: 'Caso 1', 2: 'Caso 2', 3: 'Caso 3', 4: 'Caso 4' };

    let totalStats = { total: 0, closed: 0, wins: 0, grossWin: 0, grossLoss: 0, realized: 0, floating: 0 };

    [1, 3, 4].forEach(i => {
        const s = caseStats[i];
        if (s.total === 0) return;

        const closed = s.wins + s.losses;
        const winRate = closed > 0 ? ((s.wins / closed) * 100).toFixed(1) : '-';
        const pf = s.grossLoss > 0 ? (s.grossWin / s.grossLoss).toFixed(2) : (s.grossWin > 0 ? '∞' : '-');
        const total = s.realizedPnl + s.floatingPnl;

        // Acumular totales
        totalStats.total += s.total;
        totalStats.closed += closed;
        totalStats.wins += s.wins;
        totalStats.grossWin += s.grossWin;
        totalStats.grossLoss += s.grossLoss;
        totalStats.realized += s.realizedPnl;
        totalStats.floating += s.floatingPnl;

        tbody.innerHTML += `
                    <tr style="background: rgba(0, 0, 0, 0.15); transition: all 0.2s;" onmouseover="this.style.background='rgba(0,0,0,0.3)'" onmouseout="this.style.background='rgba(0,0,0,0.15)'">
                        <td class="py-2 px-3 font-bold text-sm" style="color: ${caseColors[i]}; border-left: 3px solid ${caseColors[i]};">${caseNames[i]}</td>
                        <td class="py-2 px-3 text-center text-sm text-slate-300">${closed} / ${s.total}</td>
                        <td class="py-2 px-3 text-center text-sm font-semibold" style="color: ${parseFloat(winRate) >= 50 ? 'var(--accent-green)' : (winRate === '-' ? 'var(--text-muted)' : 'var(--accent-red)')}">${winRate}${winRate !== '-' ? '%' : ''}</td>
                        <td class="py-2 px-3 text-center text-sm font-mono" style="color: ${parseFloat(pf) >= 1 ? 'var(--accent-green)' : (pf === '-' || pf === '∞' ? 'var(--text-muted)' : 'var(--accent-red)')}">${pf}</td>
                        <td class="py-2 px-3 text-right text-sm font-mono font-semibold" style="color: ${s.realizedPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">$${s.realizedPnl.toFixed(2)}</td>
                        <td class="py-2 px-3 text-right text-sm font-mono" style="color: ${s.floatingPnl >= 0 ? 'var(--accent-blue)' : 'var(--accent-orange)'}">$${s.floatingPnl.toFixed(2)}</td>
                        <td class="py-2 px-3 text-right text-sm font-mono font-bold" style="color: ${total >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">$${total.toFixed(2)}</td>
                    </tr>
                `;
    });

    // Fila de TOTAL
    const totalWinRate = totalStats.closed > 0 ? ((totalStats.wins / totalStats.closed) * 100).toFixed(1) : '-';
    const totalPF = totalStats.grossLoss > 0 ? (totalStats.grossWin / totalStats.grossLoss).toFixed(2) : (totalStats.grossWin > 0 ? '∞' : '-');
    const totalPnl = totalStats.realized + totalStats.floating;

    tbody.innerHTML += `
                <tr style="background: linear-gradient(90deg, rgba(168, 85, 247, 0.15) 0%, rgba(0, 212, 255, 0.1) 100%); border-top: 2px solid rgba(168, 85, 247, 0.3);">
                    <td class="py-2 px-3 font-bold text-sm" style="color: var(--accent-purple); border-left: 3px solid var(--accent-purple);">📊 TOTAL</td>
                    <td class="py-2 px-3 text-center text-sm font-bold text-white">${totalStats.closed} / ${totalStats.total}</td>
                    <td class="py-2 px-3 text-center text-sm font-bold" style="color: ${parseFloat(totalWinRate) >= 50 ? 'var(--accent-green)' : 'var(--accent-red)'}">${totalWinRate}%</td>
                    <td class="py-2 px-3 text-center text-sm font-mono font-bold" style="color: ${parseFloat(totalPF) >= 1 ? 'var(--accent-green)' : 'var(--accent-red)'}">${totalPF}</td>
                    <td class="py-2 px-3 text-right text-sm font-mono font-bold" style="color: ${totalStats.realized >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">$${totalStats.realized.toFixed(2)}</td>
                    <td class="py-2 px-3 text-right text-sm font-mono font-bold" style="color: ${totalStats.floating >= 0 ? 'var(--accent-blue)' : 'var(--accent-orange)'}">$${totalStats.floating.toFixed(2)}</td>
                    <td class="py-2 px-3 text-right text-sm font-mono font-bold" style="color: ${totalPnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">$${totalPnl.toFixed(2)}</td>
                </tr>
            `;
}

function toggleCaseTable() {
    const container = document.getElementById('caseTableContainer');
    const btn = document.getElementById('btnToggleCaseTable');
    if (container.style.display === 'none') {
        container.style.display = 'block';
        btn.textContent = 'Ocultar';
    } else {
        container.style.display = 'none';
        btn.textContent = 'Mostrar';
    }
}

function toggleIgnoredList() {
    const container = document.getElementById('ignoredTradesContainer');
    const btn = document.getElementById('btnToggleIgnored');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        btn.textContent = 'Ocultar';
    } else {
        container.classList.add('hidden');
        btn.textContent = 'Mostrar';
    }
}

function toggleMainList() {
    const container = document.getElementById('mainTradesContainer');
    const btn = document.getElementById('btnToggleMain');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        btn.textContent = '(Ocultar)';
    } else {
        container.classList.add('hidden');
        btn.textContent = '(Mostrar)';
    }
}

function toggleLimitList() {
    const container = document.getElementById('limitOrdersContainer');
    const btn = document.getElementById('btnToggleLimit');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        btn.textContent = 'Ocultar';
    } else {
        container.classList.add('hidden');
        btn.textContent = 'Mostrar';
    }
}

function toggleCancelledList() {
    const container = document.getElementById('cancelledOrdersContainer');
    const btn = document.getElementById('btnToggleCancelled');
    if (container.classList.contains('hidden')) {
        container.classList.remove('hidden');
        btn.textContent = 'Ocultar';
    } else {
        container.classList.add('hidden');
        btn.textContent = 'Mostrar';
    }
}

// ========== CHART ==========
function selectTrade(idx) {
    selectedTradeIndex = idx;

    // Update selection visual
    document.querySelectorAll('#tradesTableBody tr').forEach(tr => tr.classList.remove('selected'));
    document.querySelector(`#tradesTableBody tr[data-idx="${idx}"]`)?.classList.add('selected');

    const item = processedTradesGlobal[idx];
    if (item) {
        showTradeOnChart(item);
    }
}

async function showTradeOnChart(item) {
    const t = item.t;

    // Update header
    document.getElementById('chartSymbol').textContent = t.symbol;
    const caseBadge = document.getElementById('chartCaseBadge');
    caseBadge.textContent = item.cID == 11 ? 'C1++' : `C${item.cID}`;
    caseBadge.classList.remove('hidden');

    // Show info panel
    document.getElementById('chartInfo').style.display = 'flex';
    document.getElementById('infoEntry').textContent = '$' + t.entry_price.toFixed(4);
    document.getElementById('infoTP').textContent = '$' + item.tpPrice.toFixed(4);
    document.getElementById('infoSL').textContent = item.slPrice === Infinity ? '∞' : '$' + item.slPrice.toFixed(4);
    document.getElementById('infoFibLow').textContent = '$' + (t.fib_low || 0).toFixed(4);
    document.getElementById('infoFibHigh').textContent = '$' + (t.fib_high || 0).toFixed(4);

    // Initialize chart if needed
    if (!chart) {
        initChart();
    }

    // Load candle data
    await loadCandleData(t.symbol);

    // AUTO-SYNC: Update trade min/max from chart data (fixes "RUN" vs Chart reality)
    if (candleDataGlobal && candleDataGlobal.length > 0) {
        const entryTimeStr = t.entry_time || (t.executions && t.executions[0] ? t.executions[0].time : null);
        if (entryTimeStr) {
            const entryTime = new Date(entryTimeStr).getTime() / 1000;

            // Calculate buffer based on timeframe to ensure we include the entry candle
            // currentTimeframe is in minutes (e.g. '15', '60', 'D' is represented as 'D'?) 
            // Bybit interval 'D' = ? API says 'D'. parseInt('D') is NaN.
            // Handle 'D', 'M', 'W'? Bybit API used in loadCandleData: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, M, W
            // We only have buttons for 1, 5, 15, 60, 240. So simple parsing is fine.
            let tfMinutes = parseInt(currentTimeframe);
            if (isNaN(tfMinutes)) tfMinutes = 1440; // Default to 1 day for non-numeric

            const bufferSeconds = tfMinutes * 60;

            // Find candles starting from entry (minus buffer to catch the open candle)
            const relevantCandles = candleDataGlobal.filter(c => c.time >= (entryTime - bufferSeconds));

            if (relevantCandles.length > 0) {
                const mins = relevantCandles.map(c => c.low);
                const maxs = relevantCandles.map(c => c.high);
                const trueMin = Math.min(...mins);
                const trueMax = Math.max(...maxs);

                // Update raw data seamlessly
                // FORCE OVERWRITE: Use chart as source of truth.
                // Corrects both "missing post_close" and "bad pre_close spike"
                // Since we are "Simulating", the chart history IS the reality.
                t.min_price_pre_close = trueMin;
                t.max_price_pre_close = trueMax;
                t.min_price_post_close = trueMin;
                t.max_price_post_close = trueMax;

                // Force simulation refresh to update Status (SL -> TP)
                // This fixes the "list says RUN but chart shows TP" bug
                runSimulation();

                // Restore selection (list order might have changed due to PnL update)
                const newIdx = processedTradesGlobal.findIndex(p => p.t === t);
                if (newIdx >= 0) {
                    selectedTradeIndex = newIdx;
                    // Re-apply visual selection
                    document.querySelectorAll('#tradesTableBody tr').forEach(tr => tr.classList.remove('selected'));
                    document.querySelector(`#tradesTableBody tr[data-idx="${newIdx}"]`)?.classList.add('selected');
                }
            }
        }
    }

    // Draw lines
    drawTradeLines(item);

    // 5. Draw simulation markers
    updateChartMarkers(item, item.tpPrice, item.slPrice);
}

function updateChartMarkers(tItem, tpPrice, slPrice) {
    if (!candleSeries || !candleDataGlobal || candleDataGlobal.length === 0) return;

    const t = tItem.t || tItem; // Handle both wrapper and raw trade if needed (usually wrapper)

    // Re-run simulation to get exact time of hit
    const simResult = simulateTradePath(t, tpPrice, slPrice, candleDataGlobal);
    let markers = [];

    // Marker for Entry
    const entryTimeStr = t.opened_at || t.entry_time || (t.executions && t.executions[0] ? t.executions[0].time : null);

    if (entryTimeStr) {
        // FORCE UTC: Append 'Z' if not present to ensure it's treated as UTC, not Local
        const hasTimezone = entryTimeStr.includes('Z') || entryTimeStr.includes('+');
        const entryTs = new Date(hasTimezone ? entryTimeStr : entryTimeStr + 'Z').getTime() / 1000;

        // Find closest candle for visual marker
        const entryCandle = candleDataGlobal.find(c => Math.abs(c.time - entryTs) < 60) || candleDataGlobal.reduce((prev, curr) => Math.abs(curr.time - entryTs) < Math.abs(prev.time - entryTs) ? curr : prev, candleDataGlobal[0]);

        if (entryCandle) {
            markers.push({
                time: entryCandle.time,
                position: 'aboveBar',
                color: '#f68410',
                shape: 'arrowDown',
                text: 'ENTRY',
                size: 2  // Marcador más grande
            });
        }

        // Marker for Result
        if (simResult && simResult.resultTime) {
            const shape = simResult.status.includes('SL') ? 'square' : 'circle';
            const color = simResult.status.includes('SL') ? '#ef4444' : '#22c55e';
            const text = simResult.status.includes('SL') ? 'SL' : 'TP';
            const position = simResult.status.includes('SL') ? 'aboveBar' : 'belowBar';

            markers.push({
                time: simResult.resultTime,
                position: position,
                color: color,
                shape: shape,
                text: text
            });
        }
    }

    // VALIDATION: Lightweight Charts requires markers to be sorted by time ASC
    markers = markers.filter(m => m.time != null && !isNaN(m.time))
        .sort((a, b) => a.time - b.time);

    candleSeries.setMarkers(markers);
}

function selectLimitOrder(idx) {
    const item = limitOrdersGlobal[idx];
    if (!item) return;

    selectedLimitOrderIndex = idx;
    selectedTradeIndex = -1; // Deselect normal trade

    // Deselect main table rows
    const rows = document.querySelectorAll('#tradesTableBody tr');
    rows.forEach(r => r.classList.remove('selected'));

    // Update chart info
    document.getElementById('chartSymbol').textContent = item.symbol + ' (LIMIT)';
    document.getElementById('chartCaseBadge').textContent = item.strategy_case ? `C${item.strategy_case}` : '-';
    document.getElementById('chartCaseBadge').classList.remove('hidden');

    // Show basic info
    document.getElementById('chartInfo').style.display = 'flex';
    document.getElementById('infoEntry').textContent = '$' + item.price.toFixed(4);
    document.getElementById('infoTP').textContent = item.take_profit ? '$' + item.take_profit.toFixed(4) : '-';
    document.getElementById('infoSL').textContent = item.stop_loss ? '$' + item.stop_loss.toFixed(4) : '-';
    document.getElementById('infoFibLow').textContent = '-';
    document.getElementById('infoFibHigh').textContent = '-';

    // 1. Clear previous lines
    if (chartLines) {
        chartLines.forEach(line => {
            try { candleSeries.removePriceLine(line); } catch (e) { }
        });
    }
    chartLines = [];

    // 2. Define lines to draw
    const linesToDraw = [
        { price: item.price, color: '#ff9800', title: 'ENTRY (LIMIT)', lineWidth: 2, lineStyle: 0 }
    ];
    if (item.take_profit) linesToDraw.push({ price: item.take_profit, color: '#22c55e', title: 'TP', lineWidth: 2, lineStyle: 2 });
    if (item.stop_loss) linesToDraw.push({ price: item.stop_loss, color: '#ef4444', title: 'SL', lineWidth: 2, lineStyle: 2 });

    if (item.fib_high && item.fib_low) {
        // 100% & 0%
        linesToDraw.push({ price: item.fib_high, color: 'rgba(244, 67, 54, 0.8)', title: '100%', lineWidth: 1, lineStyle: 0 });
        linesToDraw.push({ price: item.fib_low, color: 'rgba(76, 175, 80, 0.8)', title: '0%', lineWidth: 1, lineStyle: 0 });

        // Inner Levels
        const range = item.fib_high - item.fib_low;
        const levels = [
            { pct: 0.236, label: '23.6%', color: '#4caf50' },
            { pct: 0.382, label: '38.2%', color: '#8bc34a' },
            { pct: 0.500, label: '50.0%', color: '#ffeb3b' },
            { pct: 0.618, label: '61.8%', color: '#ff9800' },
            { pct: 0.786, label: '78.6%', color: '#ff5722' }
        ];

        levels.forEach(l => {
            linesToDraw.push({
                price: item.fib_low + (range * l.pct),
                color: l.color,
                title: l.label,
                lineWidth: 1,
                lineStyle: 2 // Dashed
            });
        });
    }

    // 3. Initialize chart if needed
    if (!chart) initChart();

    // 4. Draw lines AFTER chart is ready
    linesToDraw.forEach(l => {
        // Ensure candleSeries exists
        if (candleSeries) {
            const lineObj = candleSeries.createPriceLine({
                price: l.price,
                color: l.color,
                lineWidth: l.lineWidth || 1,
                lineStyle: l.lineStyle || 0,
                axisLabelVisible: true,
                title: l.title
            });
            chartLines.push(lineObj);
        }
    });

    // 5. Add creation_fib_level line if available
    if (item.creation_fib_level != null && item.fib_high && item.fib_low) {
        const range = item.fib_high - item.fib_low;
        const creationPrice = item.fib_low + (range * item.creation_fib_level);
        if (candleSeries) {
            const creationLine = candleSeries.createPriceLine({
                price: creationPrice,
                color: '#a855f7', // Purple
                lineWidth: 2,
                lineStyle: 1, // Dotted
                axisLabelVisible: true,
                title: `CREACIÓN (${(item.creation_fib_level * 100).toFixed(1)}%)`
            });
            chartLines.push(creationLine);
        }
    }

    // 6. Load candles and then add creation marker
    (async () => {
        await loadCandleData(item.symbol);
        // Add creation marker after candles are loaded
        if (item.created_at && candleSeries && candleDataGlobal && candleDataGlobal.length > 0) {
            const hasTimezone = item.created_at.includes('Z') || item.created_at.includes('+');
            const creationTs = new Date(hasTimezone ? item.created_at : item.created_at + 'Z').getTime() / 1000;

            // Find closest candle
            const creationCandle = candleDataGlobal.find(c => Math.abs(c.time - creationTs) < 60)
                || candleDataGlobal.reduce((prev, curr) =>
                    Math.abs(curr.time - creationTs) < Math.abs(prev.time - creationTs) ? curr : prev,
                    candleDataGlobal[0]);

            if (creationCandle) {
                candleSeries.setMarkers([{
                    time: creationCandle.time,
                    position: 'aboveBar',
                    color: '#a855f7', // Purple
                    shape: 'arrowDown',
                    text: 'CREACIÓN',
                    size: 2
                }]);
            }
        }
    })();
}

function selectCancelledOrder(idx) {
    const item = cancelledOrdersGlobal[idx];
    if (!item) return;

    selectedCancelledOrderIndex = idx;
    selectedLimitOrderIndex = -1;
    selectedTradeIndex = -1;

    // Update chart info
    document.getElementById('chartSymbol').textContent = item.symbol + ' (CANCELADA)';
    document.getElementById('chartCaseBadge').textContent = item.strategy_case ? `C${item.strategy_case}` : '-';
    document.getElementById('chartCaseBadge').classList.remove('hidden');

    // Show basic info
    document.getElementById('chartInfo').style.display = 'flex';
    document.getElementById('infoEntry').textContent = item.price ? '$' + item.price.toFixed(4) : '-';
    document.getElementById('infoTP').textContent = item.take_profit ? '$' + item.take_profit.toFixed(4) : '-';
    document.getElementById('infoSL').textContent = item.stop_loss ? '$' + item.stop_loss.toFixed(4) : '-';
    document.getElementById('infoFibLow').textContent = item.fib_low ? '$' + item.fib_low.toFixed(4) : '-';
    document.getElementById('infoFibHigh').textContent = item.fib_high ? '$' + item.fib_high.toFixed(4) : '-';

    // 1. Clear previous lines
    if (chartLines) {
        chartLines.forEach(line => {
            try { candleSeries.removePriceLine(line); } catch (e) { }
        });
    }
    chartLines = [];

    // 2. Define lines to draw
    const linesToDraw = [];
    if (item.price) linesToDraw.push({ price: item.price, color: '#ef4444', title: 'ENTRY (CANCELADA)', lineWidth: 2, lineStyle: 1 });
    if (item.take_profit) linesToDraw.push({ price: item.take_profit, color: '#22c55e', title: 'TP', lineWidth: 2, lineStyle: 2 });
    if (item.stop_loss) linesToDraw.push({ price: item.stop_loss, color: '#ef4444', title: 'SL', lineWidth: 2, lineStyle: 2 });

    if (item.fib_high && item.fib_low) {
        // 100% & 0%
        linesToDraw.push({ price: item.fib_high, color: 'rgba(244, 67, 54, 0.8)', title: '100%', lineWidth: 1, lineStyle: 0 });
        linesToDraw.push({ price: item.fib_low, color: 'rgba(76, 175, 80, 0.8)', title: '0%', lineWidth: 1, lineStyle: 0 });

        // Inner Levels
        const range = item.fib_high - item.fib_low;
        const levels = [
            { pct: 0.236, label: '23.6%', color: '#4caf50' },
            { pct: 0.382, label: '38.2%', color: '#8bc34a' },
            { pct: 0.500, label: '50.0%', color: '#ffeb3b' },
            { pct: 0.618, label: '61.8%', color: '#ff9800' },
            { pct: 0.786, label: '78.6%', color: '#ff5722' }
        ];

        levels.forEach(l => {
            linesToDraw.push({
                price: item.fib_low + (range * l.pct),
                color: l.color,
                title: l.label,
                lineWidth: 1,
                lineStyle: 2 // Dashed
            });
        });
    }

    // 3. Initialize chart if needed
    if (!chart) initChart();

    // 4. Draw lines
    linesToDraw.forEach(l => {
        if (candleSeries) {
            const lineObj = candleSeries.createPriceLine({
                price: l.price,
                color: l.color,
                lineWidth: l.lineWidth || 1,
                lineStyle: l.lineStyle || 0,
                axisLabelVisible: true,
                title: l.title
            });
            chartLines.push(lineObj);
        }
    });

    // 5. Add creation_fib_level line if available
    if (item.creation_fib_level != null && item.fib_high && item.fib_low) {
        const range = item.fib_high - item.fib_low;
        const creationPrice = item.fib_low + (range * item.creation_fib_level);
        if (candleSeries) {
            const creationLine = candleSeries.createPriceLine({
                price: creationPrice,
                color: '#a855f7',
                lineWidth: 2,
                lineStyle: 1,
                axisLabelVisible: true,
                title: `CREACIÓN (${(item.creation_fib_level * 100).toFixed(1)}%)`
            });
            chartLines.push(creationLine);
        }
    }

    // 6. Load candles and add markers
    (async () => {
        await loadCandleData(item.symbol);

        let markers = [];

        // Creation marker
        if (item.created_at && candleSeries && candleDataGlobal && candleDataGlobal.length > 0) {
            const hasTimezone = item.created_at.includes('Z') || item.created_at.includes('+');
            const creationTs = new Date(hasTimezone ? item.created_at : item.created_at + 'Z').getTime() / 1000;
            const creationCandle = candleDataGlobal.find(c => Math.abs(c.time - creationTs) < 60)
                || candleDataGlobal.reduce((prev, curr) =>
                    Math.abs(curr.time - creationTs) < Math.abs(prev.time - creationTs) ? curr : prev,
                    candleDataGlobal[0]);
            if (creationCandle) {
                markers.push({
                    time: creationCandle.time,
                    position: 'aboveBar',
                    color: '#a855f7',
                    shape: 'arrowDown',
                    text: 'CREACIÓN',
                    size: 2
                });
            }
        }

        // Cancellation marker
        if (item.cancelled_at && candleSeries && candleDataGlobal && candleDataGlobal.length > 0) {
            const hasTimezone = item.cancelled_at.includes('Z') || item.cancelled_at.includes('+');
            const cancelTs = new Date(hasTimezone ? item.cancelled_at : item.cancelled_at + 'Z').getTime() / 1000;
            const cancelCandle = candleDataGlobal.find(c => Math.abs(c.time - cancelTs) < 60)
                || candleDataGlobal.reduce((prev, curr) =>
                    Math.abs(curr.time - cancelTs) < Math.abs(prev.time - cancelTs) ? curr : prev,
                    candleDataGlobal[0]);
            if (cancelCandle) {
                markers.push({
                    time: cancelCandle.time,
                    position: 'belowBar',
                    color: '#ef4444',
                    shape: 'square',
                    text: 'CANCELADA',
                    size: 2
                });
            }
        }

        // Sort and set markers
        markers = markers.filter(m => m.time != null).sort((a, b) => a.time - b.time);
        if (candleSeries) candleSeries.setMarkers(markers);
    })();
}

function initChart() {
    const container = document.getElementById('chartContainer');
    container.innerHTML = '';

    chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: Math.max(500, container.clientHeight - 10),
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#94a3b8'
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.05)' }
        },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: 'rgba(255, 255, 255, 0.1)' },
        timeScale: { borderColor: 'rgba(255, 255, 255, 0.1)', timeVisible: true, position: 'top' }
    });

    candleSeries = chart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
        priceFormat: {
            type: 'price',
            precision: 6,
            minMove: 0.000001
        }
    });

    // ZigZag Series
    zigzagSeries = chart.addLineSeries({
        color: 'rgba(255, 255, 255, 0.6)',
        lineWidth: 2,
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        priceLineVisible: false
    });

    // Resize handler
    window.addEventListener('resize', () => {
        if (chart) chart.applyOptions({ width: container.clientWidth });
    });
}

async function loadCandleData(symbol) {
    try {
        // Aumentado a 1000 velas para más contexto
        const url = `https://api.bybit.com/v5/market/kline?category=linear&symbol=${symbol}&interval=${currentTimeframe}&limit=1000`;
        const response = await fetch(url);
        const data = await response.json();

        if (data.retCode === 0 && data.result?.list) {
            const candles = data.result.list.reverse().map(c => ({
                time: parseInt(c[0]) / 1000,
                open: parseFloat(c[1]),
                high: parseFloat(c[2]),
                low: parseFloat(c[3]),
                close: parseFloat(c[4])
            }));

            candleDataGlobal = candles; // Save for markers
            candleSeries.setData(candles);

            // Calcular y dibujar ZigZag
            const zigzagPoints = calculateZigZag(candles, currentTimeframe);
            zigzagSeries.setData(zigzagPoints);

            chart.timeScale().fitContent();
        }
    } catch (error) {
        console.error('Error loading candles:', error);
    }
}

function drawTradeLines(item) {
    // Clear previous lines
    chartLines.forEach(line => {
        try { candleSeries.removePriceLine(line); } catch (e) { }
    });
    chartLines = [];

    // Clear previous markers
    try { candleSeries.setMarkers([]); } catch (e) { }

    if (!item || !item.t) return;
    const t = item.t;

    // Entry line
    if (t.entry_price) {
        // Calcular nivel de Fibonacci del entry
        let entryFibLevel = '';
        if (t.fib_high && t.fib_low && t.fib_high > t.fib_low) {
            const fibLevel = ((t.entry_price - t.fib_low) / (t.fib_high - t.fib_low) * 100).toFixed(1);
            entryFibLevel = ` (${fibLevel}%)`;
        }

        chartLines.push(candleSeries.createPriceLine({
            price: t.entry_price,
            color: '#ff9800',
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: true,
            title: 'ENTRY' + entryFibLevel
        }));
    }

    // TP line
    if (item.tpPrice) {
        chartLines.push(candleSeries.createPriceLine({
            price: item.tpPrice,
            color: '#22c55e',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'TP'
        }));
    }

    // SL line
    if (item.slPrice && item.slPrice !== Infinity) {
        chartLines.push(candleSeries.createPriceLine({
            price: item.slPrice,
            color: '#ef4444',
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'SL'
        }));
    }

    // Fibonacci levels
    if (t.fib_high && t.fib_low) {
        // 0% (Low)
        chartLines.push(candleSeries.createPriceLine({
            price: t.fib_low,
            color: 'rgba(76, 175, 80, 0.8)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '0%'
        }));

        // 100% (High)
        chartLines.push(candleSeries.createPriceLine({
            price: t.fib_high,
            color: 'rgba(244, 67, 54, 0.8)',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: '100%'
        }));

        // Key Fibonacci levels
        const fibLevels = [
            { level: 0.236, color: '#4caf50', label: '23.6%' },
            { level: 0.382, color: '#8bc34a', label: '38.2%' },
            { level: 0.5, color: '#ffeb3b', label: '50%' },
            { level: 0.618, color: '#ff9800', label: '61.8%' },
            { level: 0.786, color: '#ff5722', label: '78.6%' }
        ];

        const range = t.fib_high - t.fib_low;
        fibLevels.forEach(fib => {
            const price = t.fib_low + (range * fib.level);
            chartLines.push(candleSeries.createPriceLine({
                price: price,
                color: fib.color,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: fib.label
            }));
        });
    }

    // Guide Line (Visual Helper)
    const guideSlider = document.getElementById('guide_line');
    if (guideSlider && t.fib_high && t.fib_low) {
        const guideVal = parseFloat(guideSlider.value) || 0;
        const range = t.fib_high - t.fib_low;
        const guidePrice = t.fib_low + (range * (guideVal / 100));

        chartLines.push(candleSeries.createPriceLine({
            price: guidePrice,
            color: '#3b82f6', // Blue-500
            lineWidth: 1,
            lineStyle: 3, // Dotted
            axisLabelVisible: true,
            title: `Guide ${guideVal}%`
        }));
    }
}

// ========== CANDLE SIMULATION ==========
// ========== CANDLE SIMULATION (REWRITTEN) ==========
function simulateTradePath(t, tpPrice, slPrice, candles) {
    if (!candles || candles.length === 0) return null;

    // 1. Determine Candle Interval (Dynamic)
    let intervalMinutes = 1; // Default
    if (currentTimeframe === '1') intervalMinutes = 1;
    else if (currentTimeframe === '5') intervalMinutes = 5;
    else if (currentTimeframe === '15') intervalMinutes = 15;
    else if (currentTimeframe === '60' || currentTimeframe.toLowerCase() === '1h') intervalMinutes = 60;
    else if (currentTimeframe === '240' || currentTimeframe.toLowerCase() === '4h') intervalMinutes = 240;
    else if (currentTimeframe === 'D' || currentTimeframe.toLowerCase() === '1d') intervalMinutes = 1440;

    const intervalSeconds = intervalMinutes * 60;

    // 2. Find Entry Candle
    const entryTimeStr = t.entry_time || (t.executions && t.executions[0] ? t.executions[0].time : null);
    if (!entryTimeStr) return null;

    // FORCE UTC Parsing
    const hasTimezone = entryTimeStr.includes('Z') || entryTimeStr.includes('+');
    const entryTime = new Date(hasTimezone ? entryTimeStr : entryTimeStr + 'Z').getTime() / 1000;

    // FIND THE CANDLE THAT CONTAINS THE ENTRY TIME
    // Candle covers [time, time + interval)
    let startIndex = candles.findIndex(c => entryTime >= c.time && entryTime < (c.time + intervalSeconds));

    // Fallback: If exact container not found, find next candle immediately after
    if (startIndex === -1) {
        startIndex = candles.findIndex(c => c.time >= entryTime);
    }

    // Fallback 2: If entry is newer than all candles
    if (startIndex === -1) {
        const lastCandle = candles[candles.length - 1];
        return { status: "RUN ⏳", lastPrice: lastCandle.close, reason: "FUTURE_ENTRY" };
    }

    // 3. Simulation Loop (Strict SHORT Logic)
    // SHORT: SL if High >= SL. TP if Low <= TP.
    for (let i = startIndex; i < candles.length; i++) {
        const c = candles[i];

        // Ensure SL/TP are valid numbers
        const slHit = (slPrice !== Infinity && slPrice > 0) && (c.high >= slPrice);
        const tpHit = (tpPrice > 0) && (c.low <= tpPrice);

        if (slHit && tpHit) {
            // Both hit in same candle -> Assume WORST CASE (SL)
            // Or prioritize based on open/close if granular, but Worst Case is safest for sim.
            return { status: "SL ❌", reason: "SL_CANDLE_BOTH", lastPrice: slPrice, resultTime: c.time };
        }
        if (slHit) {
            return { status: "SL ❌", reason: "SL_CANDLE", lastPrice: slPrice, resultTime: c.time };
        }
        if (tpHit) {
            return { status: "TP ✅", reason: "TP_CANDLE", lastPrice: tpPrice, resultTime: c.time };
        }
    }

    // If loop finishes without hit:
    const lastPrice = candles[candles.length - 1].close;
    return { status: "RUN ⏳", reason: "END_OF_DATA", lastPrice: lastPrice };
}

function changeTimeframe(tf) {
    currentTimeframe = tf;
    document.querySelectorAll('.tf-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tf === tf);
    });

    if (selectedTradeIndex >= 0 && processedTradesGlobal[selectedTradeIndex]) {
        const item = processedTradesGlobal[selectedTradeIndex];
        loadCandleData(item.t.symbol).then(() => {
            drawTradeLines(item);
            // Apply 1m Visibility Rule
            if (candleSeries) {
                const showMark = (currentTimeframe === '1');
                candleSeries.applyOptions({
                    priceLineVisible: showMark,
                    lastValueVisible: showMark
                });
            }
            // Update Markers relative to new timeframe data
            updateChartMarkers(item, item.tpPrice, item.slPrice);
        });
    } else if (selectedLimitOrderIndex >= 0 && limitOrdersGlobal[selectedLimitOrderIndex]) {
        // Re-call selectLimitOrder to redraw with new timeframe
        selectLimitOrder(selectedLimitOrderIndex);
    } else if (selectedCancelledOrderIndex >= 0 && cancelledOrdersGlobal[selectedCancelledOrderIndex]) {
        // Re-call selectCancelledOrder to redraw with new timeframe
        selectCancelledOrder(selectedCancelledOrderIndex);
    }
}

// ========== ZIGZAG ALGORITHM ==========
const ZIGZAG_CONFIGS = {
    "1m": { "deviation": 0.3, "depth": 5, "backstep": 2 },
    "5m": { "deviation": 0.5, "depth": 5, "backstep": 2 },
    "15m": { "deviation": 1, "depth": 5, "backstep": 2 },
    "1h": { "deviation": 2, "depth": 8, "backstep": 3 },
    "4h": { "deviation": 3, "depth": 10, "backstep": 3 },
    "1d": { "deviation": 5, "depth": 10, "backstep": 3 }
};

function calculateZigZag(data, timeframe) {
    const config = ZIGZAG_CONFIGS[timeframe] || ZIGZAG_CONFIGS["1h"];
    const deviation = config.deviation / 100;
    const depth = config.depth;

    if (data.length < depth * 2) return [];

    let potentialPivots = [];

    // Fase 1: Encontrar pivotes locales
    for (let i = depth; i < data.length - 1; i++) {
        let isHigh = true;
        let isLow = true;

        for (let j = Math.max(0, i - depth); j <= Math.min(data.length - 1, i + depth); j++) {
            if (j === i) continue;
            if (data[j].high >= data[i].high) isHigh = false;
            if (data[j].low <= data[i].low) isLow = false;
        }

        if (isHigh) potentialPivots.push({ index: i, price: data[i].high, type: 'high', time: data[i].time });
        if (isLow) potentialPivots.push({ index: i, price: data[i].low, type: 'low', time: data[i].time });
    }

    if (potentialPivots.length === 0) return [];

    // Fase 2: Filtrar por desviación y alternancia
    let zigzag = [];
    let lastType = null;
    let lastPrice = null;

    potentialPivots.forEach(pivot => {
        if (zigzag.length === 0) {
            zigzag.push(pivot);
            lastType = pivot.type;
            lastPrice = pivot.price;
            return;
        }

        if (pivot.type === lastType) {
            // Update si es más extremo
            if (lastType === 'high' && pivot.price > zigzag[zigzag.length - 1].price) {
                zigzag[zigzag.length - 1] = pivot;
                lastPrice = pivot.price;
            } else if (lastType === 'low' && pivot.price < zigzag[zigzag.length - 1].price) {
                zigzag[zigzag.length - 1] = pivot;
                lastPrice = pivot.price;
            }
        } else {
            const change = Math.abs(pivot.price - lastPrice) / lastPrice;
            if (change >= deviation) {
                zigzag.push(pivot);
                lastType = pivot.type;
                lastPrice = pivot.price;
            } else {
                // Check if better than previous of same type (simplified backstep logic)
                if (zigzag.length >= 2 && zigzag[zigzag.length - 2].type === pivot.type) {
                    const prev = zigzag[zigzag.length - 2];
                    if (pivot.type === 'high' && pivot.price > prev.price) {
                        zigzag[zigzag.length - 2] = pivot;
                    } else if (pivot.type === 'low' && pivot.price < prev.price) {
                        zigzag[zigzag.length - 2] = pivot;
                    }
                }
            }
        }
    });

    // Convert format for Lightweight Charts (time, value)
    return zigzag.map(p => ({ time: p.time, value: p.price }));
}

// ========== INIT ==========
document.addEventListener('DOMContentLoaded', async function () {
    document.querySelectorAll('input[type="range"]').forEach(s => updateSliderValue(s));
    // Cargar configuración compartida para sliders dinámicos
    if (typeof loadAndApplySliderConfig === 'function') {
        await loadAndApplySliderConfig();
    } else {
        console.warn("loadAndApplySliderConfig not found");
    }
    autoLoadTradesJson();
});

/* === Dynamic Slider Config === */
async function loadAndApplySliderConfig() {
    try {
        const response = await fetch('shared_config.json?t=' + new Date().getTime());
        if (!response.ok) throw new Error('Failed to load shared_config.json');
        const config = await response.json();

        // Update global sharedConfig so resetSlider can use it
        sharedConfig = config;

        const t = config.trading;
        const strategies = config.strategies || {};

        // Convert percentage 0.67 -> 67
        const c1_limit = Math.round(t.case_1_max_3_min * 100);
        const c3_limit = Math.round(t.case_3_max_4_min * 100);
        const c4_limit = Math.round(t.case_4_max * 100);

        // Get default values from strategies (convert 0.50 -> 50)
        const c1_tp_default = Math.round((strategies.c1?.tp || 0.50) * 100);
        const c1_sl_default = Math.round((strategies.c1?.sl || 0.88) * 100);
        const c3_tp_default = Math.round((strategies.c3?.tp || 0.51) * 100);
        const c3_sl_default = Math.round((strategies.c3?.sl || 1.05) * 100);
        const c4_tp_default = Math.round((strategies.c4?.tp || 0.56) * 100);
        const c4_sl_default = Math.round((strategies.c4?.sl || 1.05) * 100);

        // C1: Set range and default value
        setSliderRange('tp_c1', 0, c1_limit - 1, 'txt_max_tp_c1');
        setSliderValue('tp_c1', c1_tp_default);
        setSliderRange('sl_c1', c1_limit, 200, 'txt_min_sl_c1');
        setSliderValue('sl_c1', c1_sl_default);

        // C3: Set range and default value
        setSliderRange('tp_c3', 0, c3_limit - 1, 'txt_max_tp_c3');
        setSliderValue('tp_c3', c3_tp_default);
        setSliderRange('sl_c3', c3_limit, 200, 'txt_min_sl_c3');
        setSliderValue('sl_c3', c3_sl_default);

        // C4: Set range and default value
        setSliderRange('tp_c4', 0, c3_limit - 1, 'txt_max_tp_c4');
        setSliderValue('tp_c4', c4_tp_default);
        setSliderRange('sl_c4', c4_limit, 200, 'txt_min_sl_c4');
        setSliderValue('sl_c4', c4_sl_default);

        console.log('✅ Slider ranges and defaults updated from shared_config.json');

    } catch (e) {
        console.error('Error loading slider config:', e);
    }
}

function setSliderValue(inputId, value) {
    const input = document.getElementById(inputId);
    if (!input) return;

    // Clamp value to min/max
    const min = parseInt(input.min) || 0;
    const max = parseInt(input.max) || 200;
    value = Math.max(min, Math.min(max, value));

    input.value = value;

    // Update display
    const event = new Event('input');
    input.dispatchEvent(event);
}

function setSliderRange(inputId, min, max, textSpanId) {
    const input = document.getElementById(inputId);
    if (!input) return;

    input.min = min;
    input.max = max;

    // Adjust current value if out of bounds
    let val = parseInt(input.value);
    if (val > max) input.value = max;
    if (val < min) input.value = min;

    // Update text label span
    const span = document.getElementById(textSpanId);
    if (span) {
        if (textSpanId.includes('max')) span.textContent = max;
        else if (textSpanId.includes('min')) span.textContent = min;
    }

    // Trigger update UI
    const event = new Event('input');
    input.dispatchEvent(event);
}