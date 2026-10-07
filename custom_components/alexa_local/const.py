"""Constants for Alexa Local."""

from typing import Final

DOMAIN: Final = "alexa_local"
NAME: Final = "Alexa Local"

CONF_LABEL: Final = "label"
CONF_LOCALE: Final = "locale"
CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_SECRET: Final = "client_secret"
CONF_REGION: Final = "region"
CONF_HIDE_COVER_POWER: Final = "hide_cover_power"

DEFAULT_LABEL: Final = "alexa"
DEFAULT_LOCALE: Final = "en-US"

REGION_AUTO: Final = "auto"
EVENT_GATEWAYS: Final = {
    "NA": "https://api.amazonalexa.com/v3/events",
    "EU": "https://api.eu.amazonalexa.com/v3/events",
    "FE": "https://api.fe.amazonalexa.com/v3/events",
}
# AWS region the Lambda runs in -> Alexa event gateway region.
AWS_REGION_TO_GATEWAY: Final = {
    "us-east-1": "NA",
    "eu-west-1": "EU",
    "us-west-2": "FE",
}

HTTP_ENDPOINT: Final = "/api/alexa_local/smart_home"
REGION_HEADER: Final = "X-Alexa-Local-Region"

# Subentry types
SUBENTRY_INVERTED_COVER: Final = "inverted_cover"
SUBENTRY_MAPPED_CONTROL: Final = "mapped_control"

# Inverted cover subentry data
CONF_SOURCE: Final = "source"
CONF_NAME: Final = "name"
CONF_INVERT_POSITION: Final = "invert_position"
CONF_INVERT_TILT: Final = "invert_tilt"

# Mapped control subentry data
CONF_TARGET: Final = "target"
CONF_NAMES: Final = "names"
CONF_KIND: Final = "kind"
KIND_OPTIONS: Final = "options"
KIND_LINEAR: Final = "linear"
CONF_OPTION_MAP: Final = "option_map"
CONF_PRESETS: Final = "presets"
CONF_ALEXA_MIN: Final = "alexa_min"
CONF_ALEXA_MAX: Final = "alexa_max"
CONF_SOURCE_MIN: Final = "source_min"
CONF_SOURCE_MAX: Final = "source_max"
CONF_PRECISION: Final = "precision"
CONF_PERCENT: Final = "percent"

OPTION_DOMAINS: Final = ("select", "input_select")
NUMBER_DOMAINS: Final = ("number", "input_number")

SIGNAL_EXPOSED_UPDATED: Final = f"{DOMAIN}_exposed_updated"
