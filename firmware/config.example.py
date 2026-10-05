# Copy to config.py, fill in, then: mpremote cp firmware/config.py :config.py
# config.py holds secrets and is git-ignored. Never commit it.
WIFI_SSID = "your-network"          # 2.4 GHz only
WIFI_PASSWORD = "your-password"
WIFI_TX_POWER_DBM = 8.5             # lower TX power helps the SuperMini's antenna; None to skip

# From `lua devices credential --device-name <name> --out .env.device`
AGENT_ID = "baseAgent_agent_0000000000000_xxxxxxxxx"
DEVICE_NAME = "my-board"
DEVICE_CREDENTIAL = "api_00000000-0000-0000-0000-000000000000.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
