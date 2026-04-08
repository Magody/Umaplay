# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🚀 Quick Start Commands

### Development Workflow
```bash
# Start the backend server (serves API and frontend)
python main.py

# Start frontend development server (for UI changes)
cd web
npm run dev
# Visit http://localhost:5173

# Build frontend for production
cd web
npm run build
# Served automatically by backend at http://127.0.0.1:8000
```

### Testing & Linting
```bash
# Run Python tests
pytest

# Run frontend linting
cd web
npm run lint

# Run Python linting (ruff)
ruff check .

# Auto-fix linting issues
ruff check . --fix
```

### Data Management
```bash
# Rebuild event catalog (after modifying datasets/in_game/events.json)
python build_catalog.py

# Collect training data
python collect_data_training.py --help
```

## 🏗️ Architecture Overview

### Core Components
- **`main.py`**: Entry point - starts backend server, hotkey listener, and coordinates bot execution
- **`core/`**: Main Python logic including:
  - `core/actions/`: Scenario-specific agents (URA, Unity Cup, team trials, etc.)
  - `core/perception/`: Computer vision (OCR, YOLO, template matching)
  - `core/controllers/`: Game controller abstractions (Steam, ADB/SCRCPY, BlueStacks)
  - `core/utils/`: Utilities (logger, settings, event processing, skill matching)
  - `core/settings.py`: Configuration management (environment variables + config.json)
- **`server/`**: FastAPI backend serving:
  - REST API endpoints (`/config`, `/api/*`)
  - Static frontend files (served from `web/dist/`)
  - Admin endpoints (`/admin/*`)
- **`web/`**: React + TypeScript frontend:
  - State managed by Zustand (`src/store/configStore.ts`)
  - API communication via React Query (`src/services/api.ts`)
  - Built with Vite, served by FastAPI backend

### Configuration Flow
1. Frontend (`web/`) allows editing configuration via UI
2. Configuration auto-saves to LocalStorage and can be persisted to backend via "Save config" button
3. Backend saves to `config.json` at repo root
4. On bot start (`main.py`), `Settings.apply_config()` loads and applies configuration
5. Runtime preset extracted via `Settings.extract_runtime_preset()` for agent initialization

### Hotkey System
- **F2**: Start/stop main bot (URA or Unity Cup based on active scenario)
- **F7**: Start/stop Team Trials agent
- **F8**: Start/stop Daily Races agent  
- **F9**: Start/stop Roulette/Prize Derby agent
- Hotkeys configurable via Settings.HOTKEY (default F2)

## 🔧 Key Development Areas

### Adding New Configuration Options
1. **Frontend**: 
   - Add field to Zod schema: `web/src/models/config.schema.ts`
   - Add UI component: `web/src/components/general/` or `web/src/components/presets/`
   - Connect to Zustand store via appropriate setter
2. **Backend**:
   - Add handling in `core/settings.py.Settings.apply_config()`
   - Access via `Settings.YOUR_OPTION_NAME`

### Adding New Skills/Races Data
1. Place JSON files in `datasets/in_game/`:
   - `skills.json` - served at `/api/skills`
   - `races.json` - served at `/api/races` 
   - `events.json` - served at `/api/events` (processed by `build_catalog.py`)
2. Update corresponding frontend services if needed (`web/src/services/api.ts`)

### Modifying Bot Behavior
- **Scenario Agents**: Edit `core/actions/ura/agent.py` or `core/actions/unity_cup/agent.py`
- **Navigation Agents**: Edit `core/agent_nav.py` 
- **Perception/Templates**: Modify `core/perception/` modules
- **Game Controllers**: Edit `core/controllers/` implementations
- **Utilities**: Update `core/utils/` as needed

### Event Processing Logic
- Event matching/scoring: `core/utils/event_processor.py`
- Catalog building: `core/utils/event_processor.py` (build_catalog function)
- User preferences: `core/utils/event_processor.py` (UserPrefs class)

## 📁 Important Directories
- `datasets/in_game/` - Game data (skills, races, events)
- `web/public/` - Static assets served by frontend
- `models/` - YOLO/ML model files
- `debug/` - Training data storage
- `prefs/` - Configuration files (config.json, nav.json)
- `build/` - Generated event catalog

## ⚙️ Environment Variables
Configure via `Umaplay_` prefixed environment variables or edit `config.json`:
- `Umaplay_MODE`: steam|scrcpy|bluestack|adb (default: steam)
- `Umaplay_DEBUG`: true|false (enables debug logging)
- `Umaplay_PORT`: Backend port (default: 8000)
- `Umaplay_HOTKEY`: Hotkey for bot start/stop (default: F2)
- `Umaplay_USE_EXTERNAL_PROCESSOR`: Enable remote OCR/YOL0 processing
- `Umaplay_EXTERNAL_PROCESSOR_URL`: URL for external processor
- `Umaplay_USE_ADB`: Use ADB instead of SCRCPY for Android
- `Umaplay_ADB_DEVICE`: ADB device identifier (default: localhost:5555)

## 🐛 Debugging
- Enable debug logging: Set `Umaplay_DEBUG=true` or `DEBUG=true` in environment
- Check logs: Console output shows `[BOT]`, `[SERVER]`, `[PERCEPTION]` prefixes
- Emergency stop: Hotkeys will stop respective agents; or use task manager to kill Python processes
- Config validation: Backend validates and normalizes all config on load

## 🔄 Development Tips
1. **Frontend changes**: Use `npm run dev` in `web/` for hot reload
2. **Backend changes**: Restart `python main.py` to pick up changes
3. **Data changes**: Re-run `python build_catalog.py` after modifying events.json
4. **Testing**: Isolate changes to specific modules; most logic is testable in unit tests
5. **Profiling**: Look for performance bottlenecks in perception/core loops