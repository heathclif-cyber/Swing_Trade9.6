import re
import math

css_content = """
        /* ==========================================
           🎨 MATERIAL DESIGN 3 / GOOGLE STYLE SYSTEM
           ========================================== */
        :root {
            /* Google Colors */
            --google-blue: #1a73e8;
            --google-blue-hover: #174ea6;
            --google-blue-light: #e8f0fe;
            --google-red: #ea4335;
            --google-red-light: #fce8e6;
            --google-yellow: #fbbc04;
            --google-green: #34a853;
            --google-green-light: #e6f4ea;
            --google-gray-50: #f8f9fa;
            --google-gray-100: #f1f3f4;
            --google-gray-200: #e8eaed;
            --google-gray-300: #dadce0;
            --google-gray-800: #3c4043;
            --google-gray-900: #202124;

            --bg-primary: #f8f9fa;
            --bg-secondary: #ffffff;
            --bg-card: #ffffff;
            --bg-card-hover: #f1f3f4;
            --border: #dadce0;
            --border-active: var(--google-blue);

            --text-primary: #202124;
            --text-secondary: #5f6368;
            --text-muted: #80868b;

            --accent-cyan: var(--google-blue);
            --accent-blue: var(--google-blue);
            --accent-purple: #8ab4f8;
            --accent-green: var(--google-green);
            --accent-red: var(--google-red);
            --accent-orange: var(--google-yellow);
            --accent-pink: #f538a0;

            --shadow-card: 0 1px 2px 0 rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15);
            --shadow-hover: 0 1px 3px 0 rgba(60,64,67,0.3), 0 4px 8px 3px rgba(60,64,67,0.15);

            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --radius-xl: 24px;

            --font-sans: 'Inter', 'Roboto', 'Google Sans', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', 'Roboto Mono', monospace;
        }

        /* ==========================================
           🔧 RESET & BASE
           ========================================== */
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        html { scroll-behavior: smooth; }

        body {
            font-family: var(--font-sans);
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            -webkit-font-smoothing: antialiased;
            line-height: 1.5;
        }

        body::before { display: none; } /* Remove dark mode particles */

        /* ==========================================
           📐 LAYOUT
           ========================================== */
        .app-container {
            max-width: 1480px;
            margin: 0 auto;
            padding: 24px;
        }

        /* ==========================================
           🏠 HEADER
           ========================================== */
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 24px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-xl);
            margin-bottom: 24px;
            box-shadow: var(--shadow-card);
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 16px;
        }

        .header-logo {
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--google-blue-light);
            color: var(--google-blue);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }

        .header-title {
            font-size: 22px;
            font-weight: 500;
            color: var(--text-primary);
            letter-spacing: -0.5px;
        }

        .header-subtitle {
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 400;
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .header-timestamp {
            font-family: var(--font-mono);
            font-size: 13px;
            color: var(--text-secondary);
            background: var(--bg-primary);
            padding: 8px 16px;
            border-radius: 24px;
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
        }

        /* Buttons (Material Outlined & Contained) */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 10px 24px;
            border: 1px solid var(--border);
            border-radius: 24px;
            font-family: var(--font-sans);
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            background: transparent;
            color: var(--google-blue);
        }
        .btn:hover {
            background: var(--google-blue-light);
            border-color: var(--google-blue-light);
        }

        .btn-primary {
            background: var(--google-blue);
            color: white;
            border: none;
            box-shadow: 0 1px 2px 0 rgba(60,64,67,0.3);
        }
        .btn-primary:hover {
            background: var(--google-blue-hover);
            box-shadow: 0 1px 3px 1px rgba(60,64,67,0.15), 0 1px 2px 0 rgba(60,64,67,0.3);
        }

        .btn-pdf { color: var(--google-gray-800); }
        .btn-pdf:hover { background: var(--google-gray-100); }

        .btn-csv { color: var(--google-green); }
        .btn-csv:hover { background: var(--google-green-light); }

        .btn-loading {
            pointer-events: none;
            opacity: 0.6;
        }

        /* ==========================================
           📊 HERO STATUS STRIP
           ========================================== */
        .status-strip {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .status-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 20px;
            box-shadow: var(--shadow-card);
            transition: box-shadow 0.2s ease;
        }
        .status-card:hover {
            box-shadow: var(--shadow-hover);
        }

        .status-label {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }

        .status-value {
            font-family: var(--font-sans);
            font-size: 28px;
            font-weight: 400;
            color: var(--text-primary);
        }

        .status-tag {
            display: inline-block;
            font-size: 12px;
            font-weight: 500;
            padding: 4px 12px;
            border-radius: 12px;
            margin-top: 4px;
        }

        .tag-active { background: var(--google-green-light); color: var(--google-green); }
        .tag-killed { background: var(--google-red-light); color: var(--google-red); }
        .tag-positive { color: var(--google-green); }
        .tag-negative { color: var(--google-red); }

        /* ==========================================
           📦 SECTION CARDS
           ========================================== */
        .section {
            margin-bottom: 24px;
        }

        .section-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 16px;
        }

        .section-icon {
            font-size: 20px;
        }

        .section-title {
            font-size: 18px;
            font-weight: 500;
            color: var(--text-primary);
        }

        .section-badge {
            font-family: var(--font-sans);
            font-size: 11px;
            font-weight: 500;
            color: var(--text-secondary);
            background: var(--google-gray-100);
            padding: 4px 12px;
            border-radius: 12px;
            margin-left: auto;
        }

        /* Material Tabs Navigation */
        .tabs {
            display: flex;
            border-bottom: 1px solid var(--border);
            margin-bottom: 16px;
            overflow-x: auto;
            flex-wrap: nowrap;
        }

        .tab-btn {
            padding: 12px 24px;
            font-size: 14px;
            font-weight: 500;
            color: var(--text-secondary);
            background: transparent;
            border: none;
            border-bottom: 3px solid transparent;
            cursor: pointer;
            transition: all 0.2s ease;
            font-family: var(--font-sans);
            white-space: nowrap;
        }
        .tab-btn:hover {
            color: var(--google-blue);
            background: var(--google-blue-light);
        }
        .tab-btn.active {
            color: var(--google-blue);
            border-bottom-color: var(--google-blue);
        }

        /* Data table */
        .data-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            overflow: hidden;
            box-shadow: var(--shadow-card);
        }

        .table-scroll {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }

        thead th {
            position: sticky;
            top: 0;
            background: var(--bg-card);
            color: var(--text-secondary);
            font-weight: 500;
            padding: 16px;
            text-align: right;
            white-space: nowrap;
            border-bottom: 1px solid var(--border);
        }

        thead th:first-child { text-align: left; }

        tbody td {
            padding: 14px 16px;
            font-family: var(--font-mono);
            font-size: 13px;
            color: var(--text-primary);
            text-align: right;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }

        tbody td:first-child {
            text-align: left;
            font-family: var(--font-sans);
            color: var(--text-secondary);
        }

        tbody tr:hover { background: var(--google-gray-50); }
        tbody tr:last-child td { border-bottom: none; }

        .val-pos { color: var(--google-green); font-weight: 500;}
        .val-neg { color: var(--google-red); font-weight: 500;}
        .val-warn { color: var(--google-yellow); font-weight: 500;}
        .val-highlight { color: var(--text-primary); font-weight: 700; }

        /* ==========================================
           🎯 COMPUTED PANELS (Grid)
           ========================================== */
        .panel-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 16px;
        }

        .panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 24px;
            box-shadow: var(--shadow-card);
            transition: box-shadow 0.2s ease;
        }
        .panel:hover {
            box-shadow: var(--shadow-hover);
        }

        .panel-title {
            font-size: 15px;
            font-weight: 500;
            color: var(--text-primary);
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .panel-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
        }
        .panel-row:last-child { border-bottom: none; }

        .panel-row-label {
            font-size: 14px;
            color: var(--text-secondary);
        }

        .panel-row-value {
            font-family: var(--font-mono);
            font-size: 14px;
            font-weight: 500;
            color: var(--text-primary);
        }

        /* Indicator Badge */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 12px;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 500;
            font-family: var(--font-sans);
        }

        .badge-green { background: var(--google-green-light); color: var(--google-green); }
        .badge-red { background: var(--google-red-light); color: var(--google-red); }
        .badge-orange { background: #fef7e0; color: #ea8600; }
        .badge-cyan { background: var(--google-blue-light); color: var(--google-blue); }

        /* ==========================================
           ⏳ LOADING STATE
           ========================================== */
        .loading-overlay {
            position: fixed;
            inset: 0;
            background: rgba(255, 255, 255, 0.9);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            transition: opacity 0.3s ease;
        }

        .loading-overlay.hidden {
            opacity: 0;
            pointer-events: none;
        }

        /* Material Circular Progress */
        .spinner {
            width: 48px;
            height: 48px;
            border: 4px solid var(--google-gray-200);
            border-top-color: var(--google-blue);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        .loading-text {
            margin-top: 16px;
            font-size: 16px;
            color: var(--text-secondary);
        }

        /* ==========================================
           📱 RESPONSIVE
           ========================================== */
        @media (max-width: 768px) {
            .app-container { padding: 16px; }
            .header { flex-direction: column; gap: 16px; align-items: stretch; }
            .header-right { flex-wrap: wrap; justify-content: flex-start; }
            .status-strip { grid-template-columns: 1fr 1fr; }
            .panel-grid { grid-template-columns: 1fr; }
            .status-value { font-size: 24px; }
        }

        /* ==========================================
           🖨️ PDF EXPORT STYLES
           ========================================== */
        @media print {
            body { background: #fff !important; color: #000 !important; }
            .btn, .tabs, .loading-overlay { display: none !important; }
            .status-card, .panel, .data-card { box-shadow: none !important; border: 1px solid #ddd !important; }
        }

        /* Pulse animation for live dot */
        .live-dot {
            width: 8px;
            height: 8px;
            background: var(--google-green);
            border-radius: 50%;
            display: inline-block;
            animation: pulse 2s ease infinite;
            margin-right: 8px;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(1.2); }
        }

        /* Alert banner at bottom */
        .alert-banner {
            position: fixed;
            bottom: 24px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 900;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            box-shadow: 0 3px 5px -1px rgba(0,0,0,.2), 0 6px 10px 0 rgba(0,0,0,.14), 0 1px 18px 0 rgba(0,0,0,.12);
            display: none;
        }

        .alert-banner.show { display: block; animation: slideUp 0.3s ease; }
        .alert-banner.danger {
            background: var(--google-red);
            color: white;
        }
        .alert-banner.success {
            background: var(--google-green);
            color: white;
        }

        .select-pair {
            background: var(--bg-card);
            color: var(--text-primary);
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 8px 16px;
            font-family: var(--font-sans);
            font-size: 14px;
            cursor: pointer;
            outline: none;
        }
        .select-pair:hover { background: var(--google-gray-50); }

        /* ==========================================
           💰 ENTRY PRICE MODAL
           ========================================== */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.4);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 2000;
        }
        .modal-overlay.show { display: flex; animation: fadeIn 0.2s ease; }

        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

        .modal {
            background: var(--bg-card);
            border-radius: var(--radius-lg);
            padding: 32px;
            width: 90%;
            max-width: 500px;
            max-height: 85vh;
            overflow-y: auto;
            box-shadow: 0 11px 15px -7px rgba(0,0,0,.2), 0 24px 38px 3px rgba(0,0,0,.14), 0 9px 46px 8px rgba(0,0,0,.12);
        }

        .modal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
        }

        .modal-title {
            font-size: 20px;
            font-weight: 500;
            color: var(--text-primary);
        }

        .modal-close {
            background: transparent;
            border: none;
            color: var(--text-secondary);
            font-size: 24px;
            cursor: pointer;
            padding: 4px;
            border-radius: 50%;
        }
        .modal-close:hover { background: var(--google-gray-100); }

        .modal-form { display: grid; gap: 20px; margin-bottom: 24px; }

        .form-group { display: flex; flex-direction: column; gap: 8px; }

        .form-label {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-secondary);
        }

        .form-input, .form-select {
            background: transparent;
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 12px 16px;
            color: var(--text-primary);
            font-family: var(--font-sans);
            font-size: 16px;
            outline: none;
            transition: border-color 0.2s;
        }
        .form-input:focus, .form-select:focus {
            border-color: var(--google-blue);
            border-width: 2px;
            padding: 11px 15px; /* Offset border width */
        }
        
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

        .btn-save {
            background: var(--google-blue);
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 24px;
            font-family: var(--font-sans);
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            width: 100%;
            box-shadow: 0 1px 2px rgba(0,0,0,0.3);
        }
        .btn-save:hover { background: var(--google-blue-hover); box-shadow: 0 1px 3px rgba(0,0,0,0.15); }

        .entry-list-title {
            font-size: 14px;
            font-weight: 500;
            color: var(--text-secondary);
            margin-bottom: 16px;
        }

        .entry-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            margin-bottom: 8px;
        }

        .entry-item-symbol { font-family: var(--font-sans); font-size: 15px; font-weight: 500; color: var(--text-primary); }
        .entry-item-details { font-size: 12px; color: var(--text-secondary); }
        .entry-item-price { font-family: var(--font-mono); font-size: 16px; font-weight: 500; color: var(--google-green); }

        .btn-delete-entry {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--google-red);
            border-radius: 4px;
            padding: 6px 12px;
            font-size: 12px;
            cursor: pointer;
            font-weight: 500;
        }
        .btn-delete-entry:hover { background: var(--google-red-light); border-color: var(--google-red-light); }

        .entry-empty { text-align: center; color: var(--text-secondary); font-size: 14px; padding: 24px; border: 1px dashed var(--border); border-radius: 8px; }

        .btn-entry { border: 1px solid var(--border); color: var(--text-primary); }
        .btn-entry:hover { background: var(--google-gray-50); }

        .status-value.editable { cursor: pointer; border-bottom: 1px dashed var(--text-secondary); }
        .status-value.editable:hover { color: var(--google-blue); border-bottom-color: var(--google-blue); }
"""

with open(r'd:\Apps Dev\Swing_Trade9.6\templates\dashboard.html', 'r', encoding='utf-8') as f:
    html = f.read()

# Replace between strictly <style> and </style>
html = re.sub(r'<style>.*?</style>', f'<style>\n{css_content}\n    </style>', html, flags=re.DOTALL)

with open(r'd:\Apps Dev\Swing_Trade9.6\templates\dashboard.html', 'w', encoding='utf-8') as f:
    f.write(html)
