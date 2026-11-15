"""Constants for the Grandstream GWN integration."""

DOMAIN = "grandstream_gwn"

CONF_APP_ID = "app_id"
CONF_APP_SECRET = "app_secret"
CONF_VERIFY_SSL = "verify_ssl"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_AWAY_DELAY = "away_delay"
CONF_DETAIL_INTERVAL = "detail_interval"

DEFAULT_URL = "https://192.168.1.10:8443"
DEFAULT_SCAN_INTERVAL = 30  # seconds; connects/disconnects show up immediately
DEFAULT_AWAY_DELAY = 120  # seconds a client must be gone before "not_home"
DEFAULT_DETAIL_INTERVAL = 300  # seconds between per-AP CPU/memory/temperature polls

SERVICE_BLOCK_CLIENT = "block_client"
SERVICE_UNBLOCK_CLIENT = "unblock_client"
ATTR_MAC = "mac"
