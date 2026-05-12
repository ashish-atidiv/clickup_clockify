# ClickUp-Clockify Data Sync - Usage Guide

This guide explains how to use the configurable incremental loading feature for the ClickUp to Clockify data synchronization tool.

## Table of Contents
- [Quick Start](#quick-start)
- [Command Line Arguments](#command-line-arguments)
- [Configuration Priority](#configuration-priority)
- [How Incremental Loading Works](#how-incremental-loading-works)
- [Common Use Cases](#common-use-cases)
- [Troubleshooting](#troubleshooting)

## Quick Start

### Basic Usage (Default Settings)
```bash
# Activate virtual environment
cd C:\Users\Ashish Agrawal\Documents\Codes\codebase
.\venv\Scripts\activate
cd gcloud

# Run with default settings (1 day lookback, 4 hour buffer)
python main.py
```

### Custom Lookback Period
```bash
# Look back 7 days instead of 1
python main.py --lookback-days 7

# Look back 30 days
python main.py -d 30
```

### Custom Buffer Time
```bash
# Use 1 hour buffer instead of 4 hours
python main.py --buffer-seconds 3600

# Use 2 hour buffer (7200 seconds)
python main.py -b 7200
```

### Combined Arguments
```bash
# 14 day lookback with 2 hour buffer
python main.py --lookback-days 14 --buffer-seconds 7200

# Using short flags
python main.py -d 14 -b 7200
```

## Command Line Arguments

### `--lookback-days` or `-d`
**Type:** Integer
**Default:** 1 (from config.yaml)
**Purpose:** Number of days to look back for incremental loading on first run

**When used:** Only applies on the **first run** when no previous pull_date exists in BigQuery.

**Example:**
```bash
python main.py --lookback-days 7
```
This will fetch all tasks created in the last 7 days on the first run.

### `--buffer-seconds` or `-b`
**Type:** Integer
**Default:** 14400 (4 hours, from config.yaml)
**Purpose:** Buffer time in seconds to subtract from the last pull_date to catch late updates

**When used:** Applies on **all subsequent runs** after the first run.

**Example:**
```bash
python main.py --buffer-seconds 7200
```
This will fetch tasks from 2 hours before the last sync time (catches tasks that were updated after they were created).

### View Help
```bash
python main.py --help
```

## Configuration Priority

The tool uses a 3-tier priority system for configuration:

1. **Command Line Arguments** (Highest Priority)
   - `--lookback-days` and `--buffer-seconds` flags
   - Override all other settings
   - Best for one-time runs or testing

2. **config.yaml** (Medium Priority)
   ```yaml
   incremental_loading:
     lookback_days: 1
     buffer_seconds: 14400
   ```
   - Used when no CLI arguments provided
   - Good for permanent configuration changes

3. **Hardcoded Defaults** (Lowest Priority - Fallback)
   - lookback_days: 1
   - buffer_seconds: 14400 (4 hours)
   - Only used if config.yaml is missing the settings

**Example Priority Resolution:**
```bash
# config.yaml has: lookback_days: 1
python main.py --lookback-days 30
# Result: Uses 30 days (CLI overrides config)

python main.py
# Result: Uses 1 day (from config.yaml)
```

## How Incremental Loading Works

### First Run (No Previous Data in BigQuery)

When the script runs for the first time (or BigQuery table is empty):

```
Fetch Start Date = Current Time - lookback_days
```

**Example with default settings:**
- Current time: 2025-01-20 12:00 PM
- lookback_days: 1
- Fetch start: 2025-01-19 12:00 PM
- **Result:** Fetches all tasks created since Jan 19, 12:00 PM

**Example with custom lookback:**
```bash
python main.py --lookback-days 7
```
- Current time: 2025-01-20 12:00 PM
- lookback_days: 7
- Fetch start: 2025-01-13 12:00 PM
- **Result:** Fetches all tasks created since Jan 13, 12:00 PM

### Subsequent Runs (Data Exists in BigQuery)

After the first run, subsequent runs use the buffer logic:

```
Fetch Start Date = max(pull_date from BigQuery) - buffer_seconds
```

**Example with default settings:**
- Last pull_date in DB: 2025-01-20 12:00 PM
- buffer_seconds: 14400 (4 hours)
- Fetch start: 2025-01-20 08:00 AM (4 hours earlier)
- **Result:** Fetches all tasks created since Jan 20, 08:00 AM

**Why the buffer?**
The buffer accounts for:
- Tasks that were created but not immediately synced
- Tasks that were updated after creation
- Timezone differences and clock skew
- API delays or retries

**Example with custom buffer:**
```bash
python main.py --buffer-seconds 3600
```
- Last pull_date in DB: 2025-01-20 12:00 PM
- buffer_seconds: 3600 (1 hour)
- Fetch start: 2025-01-20 11:00 AM (1 hour earlier)
- **Result:** Fetches all tasks created since Jan 20, 11:00 AM

### What Gets Synced

**ClickUp Tasks:**
- Uses incremental loading (only new/updated tasks)
- Filtered by `date_created_gt` parameter in ClickUp API

**ClickUp Spaces and Lists:**
- Uses full refresh (all spaces and lists every time)
- No incremental loading applied

**Asana Sync:**
- Uses full refresh (all data every time)
- Incremental loading not implemented for Asana

## Common Use Cases

### Initial Data Load (Historical Data)
Load the last 30 days of tasks on first run:
```bash
python main.py --lookback-days 30
```

### Frequent Syncs (Hourly Runs)
Reduce buffer to 1 hour for hourly sync jobs:
```bash
python main.py --buffer-seconds 3600
```

### Daily Syncs with Safety Buffer
Use default 4-hour buffer for daily runs:
```bash
python main.py
# Or explicitly:
python main.py --buffer-seconds 14400
```

### Testing/Debugging
Test with a small lookback period:
```bash
python main.py --lookback-days 1 --buffer-seconds 0
```

### Large Historical Import
Import all tasks from the last year (first run only):
```bash
python main.py --lookback-days 365
```

### Catching Up After Downtime
If sync was down for 3 days, use larger buffer:
```bash
python main.py --buffer-seconds 259200  # 3 days in seconds
```

## Troubleshooting

### Issue: "No tasks fetched" on first run

**Cause:** Lookback period might be too short

**Solution:**
```bash
# Increase lookback period
python main.py --lookback-days 7
```

### Issue: Duplicate tasks being created

**Cause:** Tasks already exist in Clockify but buffer is fetching them again

**Solution:**
The system should handle duplicates automatically by checking:
1. Clockify API for existing tasks with matching IDs in notes
2. BigQuery for previously created tasks

If duplicates persist, check `clickup_task` and `clockify_task` tables in BigQuery.

### Issue: Missing recent tasks

**Cause:** Buffer might be too small or timezone issues

**Solution:**
```bash
# Increase buffer to 8 hours
python main.py --buffer-seconds 28800
```

### Issue: How to force full refresh

**Current Limitation:** There's no `--full-refresh` flag yet.

**Workaround:**
1. Use a very large lookback period:
   ```bash
   python main.py --lookback-days 365
   ```
2. Or manually clear the BigQuery table and run again

### Viewing Current Configuration

Check console output when script runs:
```
Incremental loading configuration: lookback_days=7, buffer_seconds=14400
```

This shows which values are being used (from CLI, config, or defaults).

### Checking What Data Will Be Fetched

**First Run:**
Look for this message:
```
No previous pull_date found. Using lookback: 7 days from now
```

**Subsequent Runs:**
Look for this message:
```
Previous pull_date found: 2025-01-20 12:00:00. Applying 14400s buffer
```

## Configuration File Reference

Edit `config.yaml` to change default values:

```yaml
dev_mode: False

prod:
  incremental_loading:
    lookback_days: 1          # Default for first run
    buffer_seconds: 14400     # Default for subsequent runs (4 hours)

dev:
  incremental_loading:
    lookback_days: 1          # Can be different for dev environment
    buffer_seconds: 14400
```

## Technical Details

### Time Conversions
- Command line uses **seconds** for buffer
- Internal function uses **minutes** (automatically converted)
- BigQuery stores timestamps as **strings** in format `YYYY-MM-DD HH:MM:SS`
- ClickUp API uses **Unix timestamps in milliseconds**

### Data Flow
1. Parse CLI arguments (or use config defaults)
2. Query BigQuery for `max(pull_date)` from `clickup_task` table
3. If no pull_date exists (first run):
   - Calculate: `datetime.now() - timedelta(days=lookback_days)`
4. If pull_date exists (subsequent run):
   - Calculate: `pull_date - timedelta(seconds=buffer_seconds)`
5. Convert to Unix timestamp (milliseconds)
6. Call ClickUp API with `date_created_gt={timestamp}`
7. Fetch and sync tasks
8. Write to BigQuery with current `pull_date`

### Files Involved
- `main.py:13-41` - Argument parsing
- `main.py:307-328` - Main function with parameter passing
- `utils/utils.py:245-320` - Incremental loading implementation
- `utils/utils.py:449-470` - Unix timestamp conversion with buffer
- `config.yaml` - Default configuration values

## Examples Summary

```bash
# Default (1 day, 4 hour buffer)
python main.py

# 7 days on first run
python main.py --lookback-days 7

# 1 hour buffer on subsequent runs
python main.py --buffer-seconds 3600

# 30 days first run, 2 hour buffer subsequent runs
python main.py -d 30 -b 7200

# View help
python main.py --help
```

## Support

For issues or questions:
1. Check console output for configuration messages
2. Verify BigQuery table contents
3. Review this documentation
4. Check `CLAUDE.md` for project overview
