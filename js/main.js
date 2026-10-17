document.addEventListener("DOMContentLoaded", function () {
    // ==========================================
    // 1. CONFIGURACIÓN DE DATOS A FUEGO
    // ==========================================
    const initialData = {
        speed: "10KM/H",
        battery: 70,
        gForce: "5G",
        session: {
            start: "0:00",
            end: "0:00",
            duration: "0:00"
        },
        accelerometerPoints: [1.2, 1.8, 1.9, 3.2, 4.0, 5.0, 5.5, 4.8, 3.5, 2.1, 1.5],
        coordinates: [40.416775, -3.703790] // Ejemplo: Madrid
    };

    // ==========================================
    // 2. INICIALIZAR GRÁFICA (Chart.js)
    // ==========================================
    const ctx = document.getElementById('accelerometerChart').getContext('2d');
    
    const accelerometerChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['', '', '', '', '', '', '', '', '', '', ''],
            datasets: [{
                data: initialData.accelerometerPoints,
                borderColor: '#cc0000',      // Color rojo idéntico al diseño de Figma
                backgroundColor: '#cc0000',
                pointRadius: 4,
                pointHoverRadius: 6,
                borderWidth: 2,
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }  // Ocultar leyenda
            },
            scales: {
                x: {
                    grid: { color: '#cccccc' }
                },
                y: {
                    grid: { color: '#cccccc' },
                    beginAtZero: true
                }
            }
        }
    });

    // ==========================================
    // 3. INICIALIZAR MAPA (Leaflet API)
    // ==========================================
    const map = L.map('map').setView(initialData.coordinates, 13);

    // Servidor de mapas OpenStreetMap (gratuito)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '© OpenStreetMap'
    }).addTo(map);

    // Marcador inicial
    const marker = L.marker(initialData.coordinates).addTo(map);

    // ==========================================
    // 4. MÉTODOS PARA ACTUALIZAR DATOS EN EL FUTURO
    // (Llama a estas funciones cuando recibas datos de tu M5Stack/API)
    // ==========================================
    window.updateDashboard = function (newData) {
        if (newData.speed) {
            document.getElementById('speed-value').innerText = newData.speed;
        }
        if (newData.battery !== undefined) {
            document.getElementById('battery-level').style.width = newData.battery + '%';
            document.getElementById('battery-text').innerText = newData.battery + '%';
        }
        if (newData.gForce) {
            document.getElementById('gforce-value').innerText = newData.gForce;
        }
        if (newData.accelerometerPoints) {
            accelerometerChart.data.datasets[0].data = newData.accelerometerPoints;
            accelerometerChart.update();
        }
        if (newData.coordinates) {
            map.setView(newData.coordinates, 13);
            marker.setLatLng(newData.coordinates);
        }
    };
});